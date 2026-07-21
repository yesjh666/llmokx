#!/usr/bin/env python3
"""
Telethon Client 统一管理器
- 全局唯一 client 实例，同时负责监听和发送
- 解决事件循环冲突和 client 生命周期混乱
- 支持登录状态共享
"""
import os
import json
import time
import asyncio
import logging
from typing import Dict, Any, Optional, Callable, List

from app import config
from app.core.logging_config import get_logger

logger = get_logger("monitor")


class TelethonClientManager:
    """Telethon Client 统一管理器"""

    def __init__(self):
        self._client = None
        self._connected = False
        self._authorized = False
        self._listening = False
        self._me = None
        self._lock = asyncio.Lock()
        self._message_handler: Optional[Callable] = None
        self._chat_ids: List[int] = []
        self._event_handler = None

    def _get_config(self) -> dict:
        """获取 userbot 配置"""
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cfg_path = os.path.join(base_dir, "config", "telegram_userbot.json")

        if not os.path.exists(cfg_path):
            return {}

        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取 userbot 配置失败: {e}")
            return {}

    def _get_session_path(self, session_file: str = "") -> str:
        """获取 session 文件绝对路径（写死在程序目录下）"""
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        session_file = os.path.join(base_dir, "config", "userbot_session")
        # 确保目录存在
        os.makedirs(os.path.dirname(session_file), exist_ok=True)
        return session_file

    def _secure_session_file(self, session_path: str):
        """设置 session 文件安全权限（仅所有者可读写）"""
        try:
            # session 文件本身
            if os.path.exists(session_path):
                os.chmod(session_path, 0o600)
            # .session 文件
            session_file = session_path + ".session"
            if os.path.exists(session_file):
                os.chmod(session_file, 0o600)
            # 配置文件
            cfg_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "config", "telegram_userbot.json"
            )
            if os.path.exists(cfg_path):
                os.chmod(cfg_path, 0o600)
        except Exception as e:
            logger.warning(f"设置文件权限失败: {e}")

    async def connect(self) -> tuple:
        """
        连接 Telegram

        Returns:
            tuple: (success: bool, message: str)
        """
        async with self._lock:
            if self._connected and self._client:
                return True, "已连接"

            cfg = self._get_config()
            api_id = cfg.get("api_id")
            api_hash = cfg.get("api_hash")

            if not api_id or not api_hash:
                return False, "api_id 或 api_hash 未配置"

            try:
                from telethon import TelegramClient
            except ImportError:
                return False, "telethon 未安装，请运行: pip install telethon"

            session_path = self._get_session_path()
            self._client = TelegramClient(session_path, api_id, api_hash)

            try:
                await self._client.connect()
                self._connected = True

                if await self._client.is_user_authorized():
                    self._authorized = True
                    self._me = await self._client.get_me()
                    self._secure_session_file(session_path)
                    logger.info(f"Telegram 已连接: {self._me.first_name} (ID: {self._me.id})")
                    return True, f"已连接: {self._me.first_name}"
                else:
                    self._authorized = False
                    return True, "已连接但未授权（需要登录）"

            except Exception as e:
                self._connected = False
                self._client = None
                return False, f"连接失败: {e}"

    async def disconnect(self):
        """断开连接"""
        async with self._lock:
            self._listening = False
            if self._client and self._connected:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
            self._client = None
            self._connected = False
            self._authorized = False
            self._me = None
            logger.info("Telegram 已断开")

    async def ensure_connected(self) -> tuple:
        """确保已连接，未连接则尝试连接"""
        if self._connected and self._client:
            return True, "已连接"
        return await self.connect()

    async def send_code_request(self, phone: str) -> tuple:
        """
        发送验证码请求

        Returns:
            tuple: (success, message, need_code)
        """
        success, msg = await self.ensure_connected()
        if not success:
            return False, msg, False

        if self._authorized:
            return True, f"已登录: {self._me.first_name if self._me else ''}", False

        try:
            await self._client.send_code_request(phone)
            return True, f"验证码已发送到 {phone}", True
        except Exception as e:
            return False, f"发送验证码失败: {e}", False

    async def sign_in(self, phone: str, code: str) -> tuple:
        """
        用验证码登录

        Returns:
            tuple: (success, message)
        """
        success, msg = await self.ensure_connected()
        if not success:
            return False, msg

        if self._authorized:
            me = self._me
            return True, f"已登录: {me.first_name if me else ''}"

        try:
            await self._client.sign_in(phone, code)
            self._authorized = True
            self._me = await self._client.get_me()
            session_path = self._get_session_path()
            self._secure_session_file(session_path)
            return True, f"登录成功: {self._me.first_name} (ID: {self._me.id})"
        except Exception as e:
            return False, f"登录失败: {e}"

    async def send_message(self, chat_id, text: str, parse_mode: str = "html") -> tuple:
        """
        发送消息（复用已连接的 client）

        Returns:
            tuple: (success, message)
        """
        success, msg = await self.ensure_connected()
        if not success:
            return False, msg

        if not self._authorized:
            return False, "未授权，请先登录"

        try:
            target_id = int(chat_id) if isinstance(chat_id, str) else chat_id
            await self._client.send_message(target_id, text, parse_mode=parse_mode)
            return True, "发送成功"
        except Exception as e:
            return False, f"发送失败: {e}"

    def set_message_handler(self, handler: Callable):
        """设置消息处理回调"""
        self._message_handler = handler

    async def start_listening(self, chat_ids: List) -> tuple:
        """
        开始监听群消息

        Args:
            chat_ids: 要监听的 chat ID 列表（int 或 str）

        Returns:
            tuple: (success, message)
        """
        success, msg = await self.ensure_connected()
        if not success:
            return False, msg

        if not self._authorized:
            return False, "未授权，无法监听"

        if not self._message_handler:
            return False, "未设置消息处理器"

        # 解析 chat ID
        parsed_ids = []
        for cid in chat_ids:
            try:
                parsed_ids.append(int(cid))
            except (ValueError, TypeError):
                parsed_ids.append(cid)

        self._chat_ids = parsed_ids

        try:
            from telethon import events

            # 移除旧的事件处理器
            if self._event_handler:
                self._client.remove_event_handler(self._event_handler)
                self._event_handler = None

            # 注册新的事件处理器
            @self._client.on(events.NewMessage(chats=parsed_ids))
            async def handler(event):
                await self._on_message(event)

            self._event_handler = handler
            self._listening = True
            logger.info(f"开始监听 {len(parsed_ids)} 个群: {parsed_ids}")
            return True, f"监听已启动 ({len(parsed_ids)} 个群)"

        except Exception as e:
            return False, f"启动监听失败: {e}"

    async def stop_listening(self):
        """停止监听"""
        self._listening = False
        if self._event_handler and self._client:
            try:
                self._client.remove_event_handler(self._event_handler)
            except Exception:
                pass
            self._event_handler = None
        logger.info("监听已停止")

    async def update_chats(self, chat_ids: List) -> tuple:
        """
        热更新监听群列表

        Returns:
            tuple: (success, message)
        """
        if not self._listening:
            return False, "监听未启动"

        await self.stop_listening()
        return await self.start_listening(chat_ids)

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

            if self._message_handler:
                asyncio.create_task(self._message_handler(
                    text=text,
                    source_chat=chat_name,
                    source_chat_id=chat_id,
                    sender=sender_name,
                ))
        except Exception as e:
            logger.error(f"处理监听消息异常: {e}")

    async def run_forever(self):
        """保持运行（在后台 task 中调用）"""
        success, msg = await self.ensure_connected()
        if not success:
            logger.error(f"连接失败: {msg}")
            return

        try:
            await self._client.run_until_disconnected()
        except Exception as e:
            logger.error(f"监听运行异常: {e}")
        finally:
            self._connected = False
            self._listening = False

    def get_status(self) -> Dict[str, Any]:
        """获取状态"""
        return {
            "connected": self._connected,
            "authorized": self._authorized,
            "listening": self._listening,
            "user": {
                "first_name": getattr(self._me, "first_name", "") if self._me else "",
                "id": getattr(self._me, "id", 0) if self._me else 0,
            } if self._me else None,
            "chat_ids": self._chat_ids,
        }


# 全局单例
client_manager = TelethonClientManager()
