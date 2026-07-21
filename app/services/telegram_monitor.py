#!/usr/bin/env python3
"""
Telegram 群监听服务 - 使用统一 Telethon Client Manager
收到消息后交给 message_pipeline 处理（LLM分析 + 转发）
"""
import os
import json
import time
import logging
import asyncio
from typing import Dict, Any, List, Optional, Callable

from app import config
from app.core.logging_config import get_logger
from app.services.telethon_manager import client_manager

logger = get_logger("monitor")


class TelegramMonitor:
    """Telegram 群消息监听器（基于统一 client manager）"""

    def __init__(self):
        self._cfg = {}
        self._running = False
        self._task = None
        self._message_handler: Optional[Callable] = None
        self._monitored_chats: List[str] = []

    def _refresh_config(self):
        self._cfg = config.get_section("monitor")
        self._monitored_chats = self._cfg.get("chat_ids", [])

    def is_enabled(self) -> bool:
        self._refresh_config()
        return self._cfg.get("enabled", False)

    def is_running(self) -> bool:
        return self._running

    def set_message_handler(self, handler: Callable):
        """设置消息处理回调（由 message_pipeline 注入）"""
        self._message_handler = handler

    async def start(self):
        """启动监听"""
        if self._running:
            logger.warning("Telegram 监听已在运行中")
            return

        self._refresh_config()

        if not self.is_enabled():
            logger.info("Telegram 监听未启用")
            return

        if not self._monitored_chats:
            logger.warning("未配置监听群 chat_ids")
            return

        # 注入消息处理器到 client manager
        client_manager.set_message_handler(self._on_message)

        # 开始监听
        success, msg = await client_manager.start_listening(self._monitored_chats)
        if not success:
            logger.error(f"启动监听失败: {msg}")
            return

        self._running = True
        logger.info(f"Telegram 监听已启动，监听 {len(self._monitored_chats)} 个群")

        # 保持运行
        await client_manager.run_forever()

    async def stop(self):
        """停止监听"""
        if self._running:
            self._running = False
            await client_manager.stop_listening()
            logger.info("Telegram 监听已停止")

    async def update_chats(self, chat_ids: List[str]) -> tuple:
        """
        热更新监听群列表

        Returns:
            tuple: (success, message)
        """
        self._monitored_chats = chat_ids
        if not self._running:
            return False, "监听未启动，无需热更新"
        return await client_manager.update_chats(chat_ids)

    async def _on_message(self, event):
        """收到新消息时的处理"""
        try:
            chat = await event.get_chat()
            chat_id = str(event.chat_id)
            chat_name = getattr(chat, "title", None) or getattr(chat, "first_name", "未知")
            sender = await event.get_sender()
            sender_name = getattr(sender, "first_name", "") or ""
            text = event.message.text or ""

            if not text.strip():
                return

            logger.info(f"[监听] {chat_name}({chat_id}): {sender_name}: {text[:100]}")

            # 交给消息管道处理
            if self._message_handler:
                asyncio.create_task(self._message_handler(
                    text=text,
                    source_chat=chat_name,
                    source_chat_id=chat_id,
                    sender=sender_name,
                ))
        except Exception as e:
            logger.error(f"处理监听消息异常: {e}")

    def get_status(self) -> Dict[str, Any]:
        """获取监听状态"""
        self._refresh_config()
        client_status = client_manager.get_status()
        return {
            "enabled": self.is_enabled(),
            "running": self._running,
            "chat_ids": self._monitored_chats,
            "connected": client_status.get("connected", False),
            "authorized": client_status.get("authorized", False),
            "listening": client_status.get("listening", False),
            "user": client_status.get("user"),
        }


# 全局单例
monitor = TelegramMonitor()
