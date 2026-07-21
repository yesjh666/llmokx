#!/usr/bin/env python3
"""
通知服务 - 双通道并行发送（微信 + Telegram）
支持 openclaw message send 命令、webhook、Telegram Bot API 三种方式
"""
import os
import json
import time
import asyncio
import logging
import shlex
from typing import Dict, Any, Optional, List

import httpx

from app import config
from app.core.logging_config import get_logger, log_notification

logger = get_logger("notification")


class Notifier:
    """双通道通知服务"""

    def __init__(self):
        self._cfg = config.get_section("notification")
        self._wechat_cfg = {}
        self._telegram_cfg = {}
        self._refresh_config()

    def _refresh_config(self):
        self._cfg = config.get_section("notification")
        self._wechat_cfg = self._cfg.get("wechat", {})
        self._telegram_cfg = self._cfg.get("telegram", {})

    def is_enabled(self) -> bool:
        self._refresh_config()
        return self._cfg.get("enabled", True)

    # ==================== 统一双通道入口 ====================

    async def send(self, msg: str) -> Dict[str, Any]:
        """
        统一双通道并行发送入口。
        根据配置并行发送微信和 Telegram，汇总结果返回。

        Returns:
            dict: {
                "success": bool,        # 任一通道成功即为 True
                "message": str,         # 汇总消息
                "channels": {           # 各通道详情
                    "wechat": {"success": bool, "message": str, "attempts": int},
                    "telegram": {"success": bool, "message": str, "attempts": int},
                },
                "attempts": int,        # 最大重试次数
            }
        """
        self._refresh_config()

        if not self.is_enabled():
            log_notification(message=msg, channel="all", success=False, attempts=0, error="通知功能已禁用")
            return {"success": False, "message": "通知功能已禁用", "channels": {}, "attempts": 0}

        if not msg or not msg.strip():
            log_notification(message=msg, channel="all", success=False, attempts=0, error="消息内容为空")
            return {"success": False, "message": "消息内容为空", "channels": {}, "attempts": 0}

        parallel = self._cfg.get("parallel", True)
        channels: List[str] = []
        if self._wechat_cfg.get("enabled", False):
            channels.append("wechat")
        if self._telegram_cfg.get("enabled", False):
            channels.append("telegram")

        if not channels:
            log_notification(message=msg, channel="all", success=False, attempts=0, error="无可用通知通道")
            return {"success": False, "message": "无可用通知通道（微信和Telegram均已禁用）", "channels": {}, "attempts": 0}

        start_time = time.time()

        if parallel and len(channels) > 1:
            results_list = await asyncio.gather(
                *[self._send_channel(ch, msg) for ch in channels],
                return_exceptions=True,
            )
            results = {ch: (r if isinstance(r, dict) else {"success": False, "message": str(r), "attempts": 0})
                       for ch, r in zip(channels, results_list)}
        else:
            results = {}
            for ch in channels:
                results[ch] = await self._send_channel(ch, msg)

        elapsed = time.time() - start_time
        any_success = any(r.get("success") for r in results.values())
        detail_msgs = [f"{ch}:{'✓' if r.get('success') else '✗'}" for ch, r in results.items()]
        summary = f"双通道结果[{','.join(detail_msgs)}]"

        log_notification(
            message=msg, channel=",".join(channels), success=any_success,
            attempts=self._cfg.get("max_retries", 3), elapsed=elapsed,
        )

        return {
            "success": any_success,
            "message": summary,
            "channels": results,
            "attempts": self._cfg.get("max_retries", 3),
        }

    async def _send_channel(self, channel: str, msg: str) -> Dict[str, Any]:
        """发送指定通道"""
        if channel == "wechat":
            return await self.send_wechat(msg)
        elif channel == "telegram":
            return await self.send_telegram(msg)
        return {"success": False, "message": f"未知通道: {channel}", "attempts": 0}

    # ==================== 微信通道 ====================

    async def send_wechat(self, msg: str) -> Dict[str, Any]:
        """发送微信通知（带重试）"""
        self._refresh_config()

        if not self._wechat_cfg.get("enabled", False):
            return {"success": False, "message": "微信通知已禁用", "attempts": 0}

        max_retries = self._cfg.get("max_retries", 3)
        retry_interval = self._cfg.get("retry_interval", 5)
        use_openclaw = self._wechat_cfg.get("use_openclaw", True)
        method = "webhook" if not use_openclaw else "openclaw"
        start_time = time.time()

        for attempt in range(1, max_retries + 1):
            try:
                if attempt > 1:
                    wait = attempt * retry_interval
                    logger.info(f"微信推送第{attempt}次重试,等待{wait}秒...")
                    await asyncio.sleep(wait)
                else:
                    await asyncio.sleep(2)

                if use_openclaw:
                    success, info = await self._send_wechat_via_openclaw(msg)
                else:
                    success, info = await self._send_wechat_via_webhook(msg)

                if success:
                    elapsed = time.time() - start_time
                    log_notification(
                        message=msg, channel="wechat", success=True, attempts=attempt,
                        method=method, elapsed=elapsed,
                    )
                    return {"success": True, "message": info, "attempts": attempt}
                else:
                    logger.warning(f"微信推送未确认(第{attempt}/{max_retries}次): {info}")

            except asyncio.TimeoutError:
                logger.warning(f"微信推送超时(第{attempt}/{max_retries}次)")
            except Exception as e:
                logger.warning(f"微信推送异常(第{attempt}/{max_retries}次): {e}")

        elapsed = time.time() - start_time
        fail_msg = f"微信推送失败,已重试{max_retries}次"
        log_notification(
            message=msg, channel="wechat", success=False, attempts=max_retries,
            method=method, error=fail_msg, elapsed=elapsed,
        )
        return {"success": False, "message": fail_msg, "attempts": max_retries}

    async def _send_wechat_via_openclaw(self, msg: str) -> tuple:
        """通过 openclaw message send 命令发送微信"""
        target = self._wechat_cfg.get("target", "")
        account = self._wechat_cfg.get("account", "")
        channel = self._wechat_cfg.get("channel", "openclaw-weixin")

        if not target:
            return False, "微信target未配置"

        safe_msg = shlex.quote(msg)
        cmd = f"openclaw message send --channel {channel} --account {account} --target {target} -m {safe_msg}"

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

            stdout_str = stdout.decode().strip()
            stderr_str = stderr.decode().strip()

            if proc.returncode == 0 and "Sent via" in stdout_str:
                return True, stdout_str
            else:
                return False, f"returncode={proc.returncode} stdout={stdout_str[:100]} stderr={stderr_str[:100]}"

        except asyncio.TimeoutError:
            return False, "openclaw命令超时"
        except Exception as e:
            return False, f"openclaw异常: {e}"

    async def _send_wechat_via_webhook(self, msg: str) -> tuple:
        """通过 webhook URL 发送（企业微信/钉钉/自定义webhook）"""
        webhook_url = self._wechat_cfg.get("webhook_url", "")
        if not webhook_url:
            return False, "webhook_url未配置"

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    webhook_url,
                    json={"content": msg},
                    headers={"Content-Type": "application/json"},
                )
            if resp.status_code == 200:
                return True, "webhook发送成功"
            else:
                return False, f"webhook返回 {resp.status_code}"
        except Exception as e:
            return False, f"webhook异常: {e}"

    # ==================== Telegram通道 ====================

    async def send_telegram(self, msg: str) -> Dict[str, Any]:
        """发送 Telegram 通知（带重试）"""
        self._refresh_config()

        if not self._telegram_cfg.get("enabled", False):
            return {"success": False, "message": "Telegram通知已禁用", "attempts": 0}

        bot_token = self._telegram_cfg.get("bot_token", "")
        chat_id = self._telegram_cfg.get("chat_id", "")

        if not bot_token or not chat_id:
            return {"success": False, "message": "Telegram bot_token或chat_id未配置", "attempts": 0}

        max_retries = self._cfg.get("max_retries", 3)
        retry_interval = self._cfg.get("retry_interval", 5)
        parse_mode = self._telegram_cfg.get("parse_mode", "HTML")
        disable_notification = self._telegram_cfg.get("disable_notification", False)
        start_time = time.time()

        for attempt in range(1, max_retries + 1):
            try:
                if attempt > 1:
                    wait = attempt * retry_interval
                    logger.info(f"Telegram推送第{attempt}次重试,等待{wait}秒...")
                    await asyncio.sleep(wait)
                else:
                    await asyncio.sleep(2)

                success, info = await self._send_telegram_bot_api(
                    bot_token, chat_id, msg, parse_mode, disable_notification
                )

                if success:
                    elapsed = time.time() - start_time
                    log_notification(
                        message=msg, channel="telegram", success=True, attempts=attempt,
                        method="bot_api", elapsed=elapsed,
                    )
                    return {"success": True, "message": info, "attempts": attempt}
                else:
                    logger.warning(f"Telegram推送未确认(第{attempt}/{max_retries}次): {info}")

            except asyncio.TimeoutError:
                logger.warning(f"Telegram推送超时(第{attempt}/{max_retries}次)")
            except Exception as e:
                logger.warning(f"Telegram推送异常(第{attempt}/{max_retries}次): {e}")

        elapsed = time.time() - start_time
        fail_msg = f"Telegram推送失败,已重试{max_retries}次"
        log_notification(
            message=msg, channel="telegram", success=False, attempts=max_retries,
            method="bot_api", error=fail_msg, elapsed=elapsed,
        )
        return {"success": False, "message": fail_msg, "attempts": max_retries}

    async def _send_telegram_bot_api(
        self, bot_token: str, chat_id: str, msg: str,
        parse_mode: str = "HTML", disable_notification: bool = False,
    ) -> tuple:
        """通过 Telegram Bot API sendMessage 发送"""
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=payload)

            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    return True, "Telegram发送成功"
                else:
                    return False, f"Telegram API错误: {data.get('description', '未知')}"
            else:
                return False, f"Telegram HTTP {resp.status_code}: {resp.text[:200]}"

        except asyncio.TimeoutError:
            return False, "Telegram请求超时"
        except Exception as e:
            return False, f"Telegram异常: {e}"

    # ==================== 测试 ====================

    async def test_notification(self, channel: str = "all") -> Dict[str, Any]:
        """
        测试通知发送

        Args:
            channel: "all" | "wechat" | "telegram"
        """
        test_msg = (
            "🔔 LLMOKX 通知测试\n\n"
            f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            "如果您收到此消息，说明通知功能正常工作。"
        )

        if channel == "wechat":
            return await self.send_wechat(test_msg)
        elif channel == "telegram":
            return await self.send_telegram(test_msg)
        else:
            return await self.send(test_msg)


# 全局单例
notifier = Notifier()
