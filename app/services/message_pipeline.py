#!/usr/bin/env python3
"""
消息处理管道 - 串联 监听 → LLM分析 → 转发 → 通知
"""
import time
import logging
import asyncio
import hashlib
from typing import Dict, Any, Optional
from collections import OrderedDict

from app import config
from app.core.logging_config import get_logger
from app.services.llm_analyzer import analyzer
from app.services.intent_forwarder import forwarder
from app.services.notifier import notifier
from app.services.telegram_monitor import monitor

logger = get_logger("app")


class MessagePipeline:
    """消息处理管道"""

    # 最大并发 LLM 请求数
    MAX_CONCURRENT_LLM = 3
    # LLM 结果缓存时间（秒）
    CACHE_TTL = 60
    # 缓存最大条数
    CACHE_MAX = 100

    def __init__(self):
        self._stats = {
            "total_received": 0,
            "total_analyzed": 0,
            "total_forwarded": 0,
            "total_notified": 0,
            "total_cached": 0,
            "errors": 0,
            "last_message_time": None,
            "last_message_text": None,
            "last_intent": None,
        }
        self._llm_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_LLM)
        self._cache: OrderedDict = OrderedDict()  # text_hash -> (result, timestamp)

    def _get_cache_key(self, text: str) -> str:
        """生成缓存 key"""
        return hashlib.md5(text.strip().lower().encode()).hexdigest()

    def _get_cached(self, text: str) -> Optional[dict]:
        """获取缓存的分析结果"""
        key = self._get_cache_key(text)
        if key in self._cache:
            result, ts = self._cache[key]
            if time.time() - ts < self.CACHE_TTL:
                # 移到最后（LRU）
                self._cache.move_to_end(key)
                return result
            else:
                del self._cache[key]
        return None

    def _set_cached(self, text: str, result: dict):
        """缓存分析结果"""
        key = self._get_cache_key(text)
        self._cache[key] = (result, time.time())
        # 限制缓存大小
        while len(self._cache) > self.CACHE_MAX:
            self._cache.popitem(last=False)

    async def process_message(
        self,
        text: str,
        source_chat: str = "",
        source_chat_id: str = "",
        sender: str = "",
    ) -> Dict[str, Any]:
        """
        完整消息处理流程:
        1. 检查是否需要分析
        2. LLM 分析意图
        3. 转发到目标群
        4. 发送通知

        Args:
            text: 消息文本
            source_chat: 来源群名
            source_chat_id: 来源群ID
            sender: 发送者

        Returns:
            dict: 处理结果
        """
        start_time = time.time()
        self._stats["total_received"] += 1
        self._stats["last_message_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._stats["last_message_text"] = text[:200]

        result = {
            "received": True,
            "analyzed": False,
            "forwarded": False,
            "notified": False,
            "text": text[:200],
            "source_chat": source_chat,
            "sender": sender,
        }

        # 检查 LLM 分析是否启用
        if not analyzer.is_enabled():
            logger.info("LLM分析未启用，跳过消息处理")
            result["message"] = "LLM分析未启用"
            return result

        # 检查消息是否应该被过滤
        pipeline_cfg = config.get_section("monitor")
        min_length = pipeline_cfg.get("min_message_length", 5)
        if len(text.strip()) < min_length:
            logger.debug(f"消息过短({len(text)}字)，跳过")
            result["message"] = "消息过短"
            return result

        # 过滤掉非交易相关消息（快速过滤）
        keywords = pipeline_cfg.get("keywords", [])
        if keywords and not any(kw.lower() in text.lower() for kw in keywords):
            logger.debug("消息不含关键词，跳过")
            result["message"] = "不含关键词"
            return result

        # 检查缓存
        cached = self._get_cached(text)
        if cached:
            self._stats["total_cached"] += 1
            logger.info(f"[管道] 使用缓存结果: {text[:50]}...")
            result.update(cached)
            result["cached"] = True
            return result

        # Step 1: LLM 分析（带并发控制）
        context_str = pipeline_cfg.get("default_context", "无持仓无挂单")
        logger.info(f"[管道] 开始 LLM 分析: {text[:80]}...")

        async with self._llm_semaphore:
            analysis = await analyzer.analyze_intent(text, context_str)

        if not analysis.get("success"):
            logger.warning(f"LLM 分析失败: {analysis.get('error')}")
            self._stats["errors"] += 1
            result["error"] = analysis.get("error")
            result["message"] = "LLM分析失败"
            return result

        self._stats["total_analyzed"] += 1
        result["analyzed"] = True
        result["intents"] = analysis.get("intents", [])
        result["elapsed"] = analysis.get("elapsed", 0)

        intents = analysis.get("intents", [])
        if not intents:
            result["message"] = "分析完成，无意图"
            return result

        # 对每个意图执行转发
        forward_results = []
        for intent in intents:
            intent_name = intent.get("intent", "chat")
            skip_intents = config.get_section("forward").get("skip_intents", ["chat", "query"])

            if intent_name in skip_intents:
                logger.info(f"[管道] 意图 {intent_name} 在跳过列表中")
                continue

            fwd = await forwarder.forward_intent(intent, text, source_chat)
            forward_results.append(fwd)

            if fwd.get("success"):
                self._stats["total_forwarded"] += 1
                result["forwarded"] = True

        result["forward_results"] = forward_results

        # Step 3: 发送通知（如果配置了通知）
        notify_cfg = config.get_section("notification")
        if notify_cfg.get("enabled", False) and pipeline_cfg.get("notify_on_signal", True):
            has_trade_signal = any(
                f.get("success") for f in forward_results
            )
            if has_trade_signal:
                notify_msg = self._build_notify_message(
                    intents, text, source_chat, sender, result
                )
                try:
                    await notifier.send(notify_msg)
                    self._stats["total_notified"] += 1
                    result["notified"] = True
                except Exception as e:
                    logger.warning(f"通知发送失败: {e}")

        elapsed = time.time() - start_time
        result["total_elapsed"] = round(elapsed, 2)
        result["message"] = f"处理完成 ({len(intents)}个意图, {elapsed:.1f}s)"

        self._stats["last_intent"] = intents[0].get("intent", "") if intents else ""

        # 缓存成功结果
        self._set_cached(text, {
            "analyzed": True,
            "intents": intents,
            "elapsed": analysis.get("elapsed", 0),
        })

        logger.info(
            f"[管道] 处理完成: 意图={len(intents)} "
            f"转发={'成功' if result['forwarded'] else '跳过'} "
            f"耗时={elapsed:.2f}s"
        )

        return result

    def _build_notify_message(
        self, intents: list, text: str,
        source_chat: str, sender: str, result: dict
    ) -> str:
        """构建通知消息"""
        lines = ["📊 交易信号检测\n"]

        for intent in intents:
            intent_name = intent.get("intent", "unknown")
            symbol = intent.get("symbol", "")
            direction = intent.get("direction", "")
            confidence = intent.get("confidence", 0)

            intent_labels = {
                "open_position": "📈 开仓",
                "close_position": "📉 平仓",
                "cancel_orders": "❌ 撤单",
                "modify_tp": "🎯 修改止盈",
                "modify_sl": "🛡️ 修改止损",
            }
            label = intent_labels.get(intent_name, f"❓ {intent_name}")

            lines.append(f"{label}")
            if symbol:
                lines.append(f"  币对: {symbol}")
            if direction:
                lines.append(f"  方向: {direction}")
            if confidence:
                lines.append(f"  置信度: {confidence}")

        lines.append(f"\n💬 来源: {source_chat} ({sender})")
        lines.append(f"📝 内容: {text[:150]}")
        lines.append(f"⏰ {time.strftime('%H:%M:%S')}")

        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        """获取管道统计"""
        return dict(self._stats)


# 全局单例
pipeline = MessagePipeline()
