#!/usr/bin/env python3
"""
转发服务 - 将提取的意图转发到配置的目标
支持 Telegram Bot API 和 Telegram Userbot
"""
import os
import json
import time
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional

import httpx

from app import config
from app.core.logging_config import get_logger, log_forward

logger = get_logger("forward")


class IntentForwarder:
    """意图转发服务"""

    def __init__(self):
        self.config = config.get_section("forward")

    def _refresh_config(self):
        self.config = config.get_section("forward")

    def is_enabled(self) -> bool:
        """检查转发功能是否启用"""
        self._refresh_config()
        return self.config.get("enabled", True)

    async def forward_intent(
        self,
        intent_result: dict,
        text: str,
        source_chat: str = "未知群",
    ) -> Dict[str, Any]:
        """
        将提取的意图转发到配置的目标群

        Args:
            intent_result: LLM分析的单个意图结果
            text: 原始消息文本
            source_chat: 消息来源

        Returns:
            dict: {
                "success": bool,
                "forwarded_targets": list,
                "errors": list,
                "message": str
            }
        """
        self._refresh_config()

        if not self.is_enabled():
            return {
                "success": False,
                "forwarded_targets": [],
                "errors": ["转发功能已禁用"],
                "message": "转发功能已禁用",
            }

        targets = self.config.get("targets", [])
        if not targets:
            return {
                "success": False,
                "forwarded_targets": [],
                "errors": ["未配置转发目标"],
                "message": "未配置转发目标",
            }

        intent = intent_result.get("intent", "chat")
        skip_intents = self.config.get("skip_intents", ["chat", "query"])

        # 跳过闲聊和查询
        if intent in skip_intents:
            return {
                "success": False,
                "forwarded_targets": [],
                "errors": [],
                "message": f"意图{intent}在跳过列表中,不转发",
            }

        # 构建标准JSON信号
        signal_data = self._build_signal_data(intent_result, text, source_chat)
        msg = json.dumps(signal_data, ensure_ascii=False, indent=2)

        # 发送到每个目标
        forwarded = []
        errors = []

        for target_config in targets:
            try:
                success, info = await self._send_to_target(target_config, msg)
                if success:
                    forwarded.append(target_config.get("target", ""))
                    logger.info(f"转发成功: {target_config.get('target')} ({info})")
                else:
                    errors.append(f"{target_config.get('target')}: {info}")
                    logger.warning(f"转发失败: {target_config.get('target')} - {info}")
            except Exception as e:
                errors.append(f"{target_config.get('target')}: {str(e)}")
                logger.error(f"转发异常: {e}")

        result = {
            "success": len(forwarded) > 0,
            "forwarded_targets": forwarded,
            "errors": errors,
            "message": f"转发完成: 成功{len(forwarded)}个, 失败{len(errors)}个",
        }

        # 记录结构化日志
        log_forward(
            intent=intent,
            symbol=signal_data.get("symbol", ""),
            direction=signal_data.get("direction"),
            text=text,
            source_chat=source_chat,
            success=result["success"],
            forwarded_targets=forwarded,
            errors=errors,
            signal_data=signal_data,
        )

        return result

    def _build_signal_data(
        self,
        intent_result: dict,
        text: str,
        source_chat: str,
    ) -> dict:
        """构建标准JSON信号格式"""
        intent = intent_result.get("intent", "chat")
        symbol = intent_result.get("symbol", "BTC-USDT")
        direction = intent_result.get("direction")
        params = intent_result.get("params", {})
        confidence = intent_result.get("confidence", 0)
        reason = intent_result.get("reason", "")

        # 确保symbol带-SWAP后缀
        if symbol and "-SWAP" not in symbol:
            symbol = symbol + "-SWAP"

        signal_data = {
            "version": "1.0",
            "type": "TRADE_SIGNAL",
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "source": source_chat,
            "intent": intent,
            "symbol": symbol,
            "direction": direction,
            "confidence": confidence,
            "reason": reason,
            "original_text": text[:500],
            "params": {},
        }

        # 根据意图填充params
        if intent == "open_position":
            orders = params.get("orders", [])
            if orders:
                formatted_orders = []
                for o in orders:
                    order_dict = {
                        "type": o.get("type", "market"),
                        "leverage": o.get("leverage"),
                        "margin_pct": o.get("margin_pct"),
                    }
                    if o.get("price"):
                        order_dict["price"] = o.get("price")
                    formatted_orders.append(order_dict)
                signal_data["params"]["orders"] = formatted_orders

            sl = params.get("stop_loss")
            if sl:
                signal_data["params"]["stop_loss"] = sl

            tp_list = params.get("take_profit", [])
            if tp_list:
                signal_data["params"]["take_profit"] = tp_list

            if params.get("move_breakeven", False):
                signal_data["params"]["move_breakeven"] = True

        elif intent == "close_position":
            close_ratio = params.get("close_ratio")
            if close_ratio:
                signal_data["params"]["close_ratio"] = close_ratio
            if params.get("move_breakeven", False):
                signal_data["params"]["move_breakeven"] = True

        elif intent == "cancel_orders":
            signal_data["params"]["cancel_type"] = params.get("cancel_type", "all")

        elif intent == "modify_tp":
            tp_list = params.get("take_profit", [])
            if tp_list:
                signal_data["params"]["new_tp"] = tp_list

        elif intent == "modify_sl":
            sl = params.get("stop_loss")
            if sl:
                signal_data["params"]["new_sl"] = sl

        elif intent == "conditional_close_position":
            trigger = params.get("trigger_price")
            if trigger:
                signal_data["params"]["trigger_price"] = trigger
            close_ratio = params.get("close_ratio")
            if close_ratio:
                signal_data["params"]["close_ratio"] = close_ratio

        return signal_data

    async def _send_to_target(self, target_config: dict, msg: str) -> tuple:
        """发送消息到指定目标"""
        channel = target_config.get("channel", "openclaw-telegram")
        target = target_config.get("target")

        if not target:
            return False, "目标未配置"

        if channel == "telegram-bot" or channel == "openclaw-telegram":
            # 尝试 Userbot 发送（如果启用）
            if self.config.get("userbot_enabled", False):
                try:
                    success, info = await self._send_via_userbot(target, msg)
                    if success:
                        return True, f"Userbot: {info}"
                    logger.warning(f"Userbot发送失败: {info}, 尝试Bot API")
                except Exception as e:
                    logger.warning(f"Userbot异常: {e}, 尝试Bot API")

            # 降级: Bot API 发送
            bot_token = self.config.get("telegram_bot_token", "")
            if bot_token:
                return await self._send_via_bot_api(bot_token, target, msg)

            return False, "未配置Bot Token且Userbot不可用"

        else:
            # 其他通道（如微信）通过 openclaw message send
            return await self._send_via_openclaw(channel, target, msg)

    async def _send_via_bot_api(self, bot_token: str, chat_id: str, text: str) -> tuple:
        """通过 Telegram Bot API 发送"""
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=data)
            if resp.status_code == 200:
                return True, "Bot API发送成功"
            else:
                return False, f"Bot API返回 {resp.status_code}: {resp.text[:100]}"
        except Exception as e:
            return False, f"Bot API异常: {e}"

    async def _send_via_userbot(self, chat_id, text: str) -> tuple:
        """通过 Telegram Userbot 发送（以用户身份）"""
        userbot_config_file = self.config.get("userbot_config_file", "config/telegram_userbot.json")

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            userbot_config_file,
        )

        if not os.path.exists(config_path):
            return False, f"Userbot配置文件不存在: {config_path}"

        try:
            with open(config_path, "r") as f:
                ub_config = json.load(f)
        except Exception as e:
            return False, f"读取Userbot配置失败: {e}"

        if not ub_config.get("enabled", False):
            return False, "Userbot未启用"

        try:
            from telethon import TelegramClient
        except ImportError:
            return False, "telethon未安装"

        api_id = ub_config.get("api_id")
        api_hash = ub_config.get("api_hash")
        session_file = ub_config.get("session_file")

        if not all([api_id, api_hash, session_file]):
            return False, "Userbot配置不完整"

        target_id = int(chat_id) if isinstance(chat_id, str) else chat_id

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            client = TelegramClient(session_file, api_id, api_hash, loop=loop)

            async def send():
                await client.connect()
                if not await client.is_user_authorized():
                    return False, "Userbot未授权"
                await client.send_message(target_id, text, parse_mode="html")
                return True, "发送成功"

            success, msg = loop.run_until_complete(send())
            try:
                await client.disconnect()
            except Exception:
                pass
            loop.close()
            return success, msg
        except Exception as e:
            return False, f"Userbot发送异常: {e}"

    async def _send_via_openclaw(self, channel: str, target: str, text: str) -> tuple:
        """通过 openclaw message send 命令发送（其他通道）"""
        import shlex
        import subprocess

        safe_msg = shlex.quote(text)
        cmd = f"openclaw message send --channel {channel} --target {target} -m {safe_msg}"

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

            if proc.returncode == 0:
                return True, "openclaw发送成功"
            else:
                return False, f"openclaw失败: {stderr.decode()[:100]}"
        except asyncio.TimeoutError:
            return False, "openclaw发送超时"
        except Exception as e:
            return False, f"openclaw异常: {e}"

    def test_forward(self, target_config: dict) -> Dict[str, Any]:
        """测试转发目标连接"""
        test_msg = json.dumps({
            "version": "1.0",
            "type": "TEST_SIGNAL",
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "message": "这是一条测试转发消息",
        }, ensure_ascii=False, indent=2)

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            success, info = loop.run_until_complete(self._send_to_target(target_config, test_msg))
            loop.close()
            return {"success": success, "message": info}
        except Exception as e:
            return {"success": False, "message": str(e)}


# 全局单例
forwarder = IntentForwarder()
