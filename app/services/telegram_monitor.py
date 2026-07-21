#!/usr/bin/env python3
"""
Telegram 群监听服务 - 使用 Telethon 监听指定群消息
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

logger = get_logger("app")


class TelegramMonitor:
    """Telegram 群消息监听器"""

    def __init__(self):
        self._cfg = {}
        self._client = None
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

        # 加载 userbot 配置
        ub_cfg = self._load_userbot_config()
        if not ub_cfg:
            logger.error("Userbot 配置加载失败，无法启动监听")
            return

        try:
            from telethon import TelegramClient, events
        except ImportError:
            logger.error("telethon 未安装，请运行: pip install telethon")
            return

        api_id = ub_cfg.get("api_id")
        api_hash = ub_cfg.get("api_hash")
        session_file = ub_cfg.get("session_file", "config/userbot_session")

        if not all([api_id, api_hash]):
            logger.error("Userbot api_id 或 api_hash 未配置")
            return

        # 处理 session 文件路径
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if not os.path.isabs(session_file):
            session_file = os.path.join(base_dir, session_file)

        self._client = TelegramClient(session_file, api_id, api_hash)

        try:
            await self._client.start()
            me = await self._client.get_me()
            logger.info(f"Telegram 监听已连接: {me.first_name} (ID: {me.id})")
        except Exception as e:
            logger.error(f"Telegram 连接失败: {e}")
            self._client = None
            return

        # 解析监听的 chat ID 列表
        chat_ids = []
        for cid in self._monitored_chats:
            try:
                chat_ids.append(int(cid))
            except (ValueError, TypeError):
                chat_ids.append(cid)  # 保留用户名等字符串形式

        # 注册消息处理器
        @self._client.on(events.NewMessage(chats=chat_ids))
        async def handler(event):
            await self._on_message(event)

        self._running = True
        logger.info(f"Telegram 监听已启动，监听 {len(chat_ids)} 个群: {chat_ids}")

        # 保持运行
        await self._client.run_until_disconnected()

    async def stop(self):
        """停止监听"""
        if self._client and self._running:
            self._running = False
            await self._client.disconnect()
            self._client = None
            logger.info("Telegram 监听已停止")

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

    def _load_userbot_config(self) -> dict:
        """加载 userbot 配置"""
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cfg_file = self._cfg.get("userbot_config_file", "config/telegram_userbot.json")
        cfg_path = os.path.join(base_dir, cfg_file)

        if not os.path.exists(cfg_path):
            # 回退到 forward 的 userbot 配置
            fwd_cfg = config.get_section("forward")
            cfg_file = fwd_cfg.get("userbot_config_file", "config/telegram_userbot.json")
            cfg_path = os.path.join(base_dir, cfg_file)

        if not os.path.exists(cfg_path):
            logger.warning(f"Userbot 配置文件不存在: {cfg_path}")
            return {}

        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取 userbot 配置失败: {e}")
            return {}

    def get_status(self) -> Dict[str, Any]:
        """获取监听状态"""
        self._refresh_config()
        return {
            "enabled": self.is_enabled(),
            "running": self._running,
            "chat_ids": self._monitored_chats,
            "connected": self._client is not None and self._running,
        }


# 全局单例
monitor = TelegramMonitor()
