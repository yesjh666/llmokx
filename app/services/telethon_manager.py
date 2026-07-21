#!/usr/bin/env python3
"""
Telethon Client 统一管理器
- 完整登录流程：连接 → 发送验证码 → 验证码登录 → 2FA密码（如需要）
- session 文件自动创建和管理
- 断线自动重连
- 全局唯一 client，监听 + 发送共用
"""
import os
import json
import time
import asyncio
import logging
from typing import Dict, Any, Optional, Callable, List, Tuple

from app import config
from app.core.logging_config import get_logger

logger = get_logger("monitor")

# 登录状态
LOGIN_STATE_IDLE = "idle"               # 空闲
LOGIN_STATE_CONNECTED = "connected"      # 已连接，未登录
LOGIN_STATE_WAITING_CODE = "waiting_code"  # 等待验证码
LOGIN_STATE_WAITING_PASSWORD = "waiting_password"  # 等待2FA密码
LOGIN_STATE_AUTHORIZED = "authorized"    # 已授权
LOGIN_STATE_ERROR = "error"             # 错误


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
        self._chat_ids: List = []
        self._event_handler = None

        # 登录状态
        self._login_state = LOGIN_STATE_IDLE
        self._phone_code_hash = None      # 发送验证码后返回的 hash
        self._login_error = None

        # 断线重连
        self._reconnect_task = None
        self._should_reconnect = True
        self._reconnect_attempts = 0

    # ==================== 配置 ====================

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

    def _get_session_path(self) -> str:
        """获取 session 文件绝对路径（写死在程序 config 目录下）"""
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        session_path = os.path.join(base_dir, "config", "userbot_session")
        os.makedirs(os.path.dirname(session_path), exist_ok=True)
        return session_path

    def _secure_session_file(self):
        """设置 session 文件安全权限"""
        try:
            session_path = self._get_session_path()
            session_file = session_path + ".session"
            if os.path.exists(session_file):
                os.chmod(session_file, 0o600)
            journal_file = session_path + ".session-journal"
            if os.path.exists(journal_file):
                os.chmod(journal_file, 0o600)

            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            cfg_path = os.path.join(base_dir, "config", "telegram_userbot.json")
            if os.path.exists(cfg_path):
                os.chmod(cfg_path, 0o600)
        except Exception as e:
            logger.warning(f"设置文件权限失败: {e}")

    def _session_exists(self) -> bool:
        """检查 session 文件是否已存在（表示之前登录过）"""
        session_path = self._get_session_path()
        return os.path.exists(session_path + ".session")

    # ==================== 连接 ====================

    async def connect(self) -> Tuple[bool, str]:
        """连接 Telegram 服务器（不自动登录）"""
        async with self._lock:
            if self._connected and self._client:
                return True, "已连接"

            cfg = self._get_config()
            api_id = cfg.get("api_id")
            api_hash = cfg.get("api_hash")

            if not api_id or not api_hash:
                return False, "api_id 或 api_hash 未配置，请先在配置中填写"

            try:
                from telethon import TelegramClient
            except ImportError:
                return False, "telethon 未安装，请运行: pip install telethon"

            session_path = self._get_session_path()
            logger.info(f"连接 Telegram，session: {session_path}")

            try:
                self._client = TelegramClient(session_path, api_id, api_hash)
                await self._client.connect()
                self._connected = True

                if await self._client.is_user_authorized():
                    self._authorized = True
                    self._login_state = LOGIN_STATE_AUTHORIZED
                    self._me = await self._client.get_me()
                    self._secure_session_file()
                    logger.info(f"Telegram 已授权: {self._me.first_name} (ID: {self._me.id})")
                    return True, f"已登录: {self._me.first_name} (ID: {self._me.id})"
                else:
                    self._authorized = False
                    self._login_state = LOGIN_STATE_CONNECTED
                    return True, "已连接，需要登录"
            except Exception as e:
                self._connected = False
                self._client = None
                self._login_state = LOGIN_STATE_ERROR
                self._login_error = str(e)
                logger.error(f"连接 Telegram 失败: {e}")
                return False, f"连接失败: {e}"

    async def disconnect(self):
        """断开连接"""
        async with self._lock:
            self._should_reconnect = False
            self._listening = False
            if self._reconnect_task and not self._reconnect_task.done():
                self._reconnect_task.cancel()

            if self._client and self._connected:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
            self._client = None
            self._connected = False
            self._authorized = False
            self._me = None
            self._login_state = LOGIN_STATE_IDLE
            logger.info("Telegram 已断开")

    async def ensure_connected(self) -> Tuple[bool, str]:
        """确保已连接"""
        if self._connected and self._client:
            return True, "已连接"
        return await self.connect()

    # ==================== 登录流程 ====================

    async def start_login(self) -> Dict[str, Any]:
        """
        开始登录流程：
        - 如已授权 → 直接返回成功
        - 如有 session 文件 → 尝试自动恢复
        - 否则 → 发送验证码

        Returns:
            dict: {
                "success": bool,
                "state": str,       # connected/waiting_code/authorized/error
                "message": str,
                "need_code": bool,  # 是否需要输入验证码
                "phone": str,       # 手机号（脱敏）
            }
        """
        success, msg = await self.ensure_connected()
        if not success:
            return {"success": False, "state": LOGIN_STATE_ERROR, "message": msg}

        # 已授权
        if self._authorized and self._me:
            return {
                "success": True,
                "state": LOGIN_STATE_AUTHORIZED,
                "message": f"已登录: {self._me.first_name} (ID: {self._me.id})",
            }

        # 未授权，发送验证码
        cfg = self._get_config()
        phone = cfg.get("phone_number", "")

        if not phone:
            return {
                "success": False,
                "state": LOGIN_STATE_CONNECTED,
                "message": "未配置手机号，请先在配置中填写手机号",
            }

        try:
            self._phone_code_hash = await self._client.send_code_request(phone)
            self._login_state = LOGIN_STATE_WAITING_CODE
            masked_phone = phone[:3] + "****" + phone[-4:] if len(phone) > 7 else phone
            logger.info(f"验证码已发送到 {masked_phone}")
            return {
                "success": True,
                "state": LOGIN_STATE_WAITING_CODE,
                "message": f"验证码已发送到 {masked_phone}，请输入收到的验证码",
                "need_code": True,
                "phone": masked_phone,
            }
        except Exception as e:
            self._login_state = LOGIN_STATE_ERROR
            self._login_error = str(e)
            logger.error(f"发送验证码失败: {e}")
            return {"success": False, "state": LOGIN_STATE_ERROR, "message": f"发送验证码失败: {e}"}

    async def submit_code(self, code: str) -> Dict[str, Any]:
        """
        提交验证码

        可能的结果：
        - 成功登录（无2FA）
        - 需要2FA密码（账户开启了二次验证）
        - 验证码错误

        Returns:
            dict: {
                "success": bool,
                "state": str,           # authorized/waiting_password/error
                "message": str,
                "need_password": bool,  # 是否需要2FA密码
            }
        """
        success, msg = await self.ensure_connected()
        if not success:
            return {"success": False, "state": LOGIN_STATE_ERROR, "message": msg}

        if self._authorized and self._me:
            return {
                "success": True,
                "state": LOGIN_STATE_AUTHORIZED,
                "message": f"已登录: {self._me.first_name}",
            }

        cfg = self._get_config()
        phone = cfg.get("phone_number", "")

        if not phone:
            return {"success": False, "state": LOGIN_STATE_ERROR, "message": "未配置手机号"}

        from telethon.errors import (
            PhoneCodeInvalidError, PhoneCodeExpiredError,
            SessionPasswordNeededError, PhoneNumberBannedError,
        )

        try:
            await self._client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=self._phone_code_hash,
            )
            # 登录成功
            self._authorized = True
            self._login_state = LOGIN_STATE_AUTHORIZED
            self._me = await self._client.get_me()
            self._secure_session_file()
            logger.info(f"登录成功: {self._me.first_name} (ID: {self._me.id})")
            return {
                "success": True,
                "state": LOGIN_STATE_AUTHORIZED,
                "message": f"登录成功: {self._me.first_name} (ID: {self._me.id})",
            }

        except SessionPasswordNeededError:
            # 需要两步验证密码
            self._login_state = LOGIN_STATE_WAITING_PASSWORD
            logger.info("账户启用了两步验证，需要输入密码")
            return {
                "success": True,
                "state": LOGIN_STATE_WAITING_PASSWORD,
                "message": "账户启用了两步验证，请输入 Telegram 云端密码",
                "need_password": True,
            }

        except PhoneCodeInvalidError:
            self._login_state = LOGIN_STATE_WAITING_CODE
            return {"success": False, "state": LOGIN_STATE_WAITING_CODE, "message": "验证码无效，请重新输入"}

        except PhoneCodeExpiredError:
            self._login_state = LOGIN_STATE_WAITING_CODE
            return {"success": False, "state": LOGIN_STATE_WAITING_CODE, "message": "验证码已过期，请重新获取"}

        except PhoneNumberBannedError:
            self._login_state = LOGIN_STATE_ERROR
            return {"success": False, "state": LOGIN_STATE_ERROR, "message": "该手机号已被封禁"}

        except Exception as e:
            err_str = str(e)
            if "Session password needed" in err_str or "PASSWORD" in err_str.upper():
                self._login_state = LOGIN_STATE_WAITING_PASSWORD
                return {
                    "success": True,
                    "state": LOGIN_STATE_WAITING_PASSWORD,
                    "message": "账户启用了两步验证，请输入 Telegram 云端密码",
                    "need_password": True,
                }
            self._login_state = LOGIN_STATE_ERROR
            self._login_error = str(e)
            logger.error(f"验证码登录失败: {e}")
            return {"success": False, "state": LOGIN_STATE_ERROR, "message": f"登录失败: {e}"}

    async def submit_password(self, password: str) -> Dict[str, Any]:
        """
        提交两步验证密码

        Returns:
            dict: {"success": bool, "state": str, "message": str}
        """
        success, msg = await self.ensure_connected()
        if not success:
            return {"success": False, "state": LOGIN_STATE_ERROR, "message": msg}

        try:
            from telethon.errors import PasswordHashInvalidError
        except ImportError:
            PasswordHashInvalidError = Exception

        try:
            await self._client.sign_in(password=password)
            self._authorized = True
            self._login_state = LOGIN_STATE_AUTHORIZED
            self._me = await self._client.get_me()
            self._secure_session_file()
            logger.info(f"两步验证成功: {self._me.first_name} (ID: {self._me.id})")
            return {
                "success": True,
                "state": LOGIN_STATE_AUTHORIZED,
                "message": f"登录成功: {self._me.first_name} (ID: {self._me.id})",
            }

        except PasswordHashInvalidError:
            self._login_state = LOGIN_STATE_WAITING_PASSWORD
            return {"success": False, "state": LOGIN_STATE_WAITING_PASSWORD, "message": "密码错误，请重新输入"}

        except Exception as e:
            err_str = str(e)
            if "invalid" in err_str.lower() and "password" in err_str.lower():
                self._login_state = LOGIN_STATE_WAITING_PASSWORD
                return {"success": False, "state": LOGIN_STATE_WAITING_PASSWORD, "message": "密码错误，请重新输入"}
            self._login_state = LOGIN_STATE_ERROR
            self._login_error = str(e)
            logger.error(f"两步验证失败: {e}")
            return {"success": False, "state": LOGIN_STATE_ERROR, "message": f"两步验证失败: {e}"}

    def get_login_state(self) -> Dict[str, Any]:
        """获取当前登录状态"""
        return {
            "state": self._login_state,
            "connected": self._connected,
            "authorized": self._authorized,
            "session_exists": self._session_exists(),
            "user": {
                "first_name": getattr(self._me, "first_name", "") if self._me else "",
                "id": getattr(self._me, "id", 0) if self._me else 0,
            } if self._me else None,
            "error": self._login_error,
        }

    # ==================== 发送消息 ====================

    async def send_message(self, chat_id, text: str, parse_mode: str = "html") -> Tuple[bool, str]:
        """发送消息"""
        success, msg = await self.ensure_connected()
        if not success:
            return False, msg

        if not self._authorized:
            return False, "未授权，请先完成登录"

        try:
            target_id = int(chat_id) if isinstance(chat_id, str) else chat_id
            await self._client.send_message(target_id, text, parse_mode=parse_mode)
            return True, "发送成功"
        except Exception as e:
            return False, f"发送失败: {e}"

    # ==================== 监听 ====================

    def set_message_handler(self, handler: Callable):
        self._message_handler = handler

    async def start_listening(self, chat_ids: List) -> Tuple[bool, str]:
        """开始监听群消息"""
        success, msg = await self.ensure_connected()
        if not success:
            return False, msg

        if not self._authorized:
            return False, "未授权，无法监听，请先完成登录"

        if not self._message_handler:
            return False, "未设置消息处理器"

        parsed_ids = []
        for cid in chat_ids:
            try:
                parsed_ids.append(int(cid))
            except (ValueError, TypeError):
                parsed_ids.append(cid)

        self._chat_ids = parsed_ids

        try:
            from telethon import events

            if self._event_handler and self._client:
                self._client.remove_event_handler(self._event_handler)
                self._event_handler = None

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
        self._listening = False
        if self._event_handler and self._client:
            try:
                self._client.remove_event_handler(self._event_handler)
            except Exception:
                pass
            self._event_handler = None
        logger.info("监听已停止")

    async def update_chats(self, chat_ids: List) -> Tuple[bool, str]:
        """热更新监听群列表"""
        if not self._listening:
            return False, "监听未启动"
        await self.stop_listening()
        return await self.start_listening(chat_ids)

    async def _on_message(self, event):
        """收到消息处理"""
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
            logger.error(f"处理消息异常: {e}")

    # ==================== 断线重连 ====================

    async def run_forever(self):
        """保持运行 + 自动重连"""
        self._should_reconnect = True

        success, msg = await self.ensure_connected()
        if not success:
            logger.error(f"连接失败: {msg}")
            return

        while self._should_reconnect:
            try:
                if self._authorized and self._listening:
                    logger.info("Telegram 监听运行中...")
                    await self._client.run_until_disconnected()
                else:
                    # 未授权或未监听，等待
                    await asyncio.sleep(5)
                    continue

            except asyncio.CancelledError:
                logger.info("监听任务被取消")
                break

            except ConnectionError as e:
                logger.warning(f"连接断开: {e}")
            except Exception as e:
                logger.warning(f"监听异常: {e}")

            # 断开后检查是否需要重连
            if not self._should_reconnect:
                break

            self._connected = False
            self._reconnect_attempts += 1
            wait_time = min(5 * self._reconnect_attempts, 60)
            logger.info(f"将在 {wait_time} 秒后重连（第 {self._reconnect_attempts} 次）...")

            await asyncio.sleep(wait_time)

            # 尝试重连
            success, msg = await self.connect()
            if success:
                self._reconnect_attempts = 0
                logger.info("重连成功")
                if self._authorized and self._listening and self._chat_ids:
                    await self.start_listening(self._chat_ids)
            else:
                logger.warning(f"重连失败: {msg}")

        self._connected = False
        self._listening = False
        logger.info("监听已完全停止")

    def get_status(self) -> Dict[str, Any]:
        """获取完整状态"""
        return {
            "connected": self._connected,
            "authorized": self._authorized,
            "listening": self._listening,
            "login_state": self._login_state,
            "session_exists": self._session_exists(),
            "reconnect_attempts": self._reconnect_attempts,
            "user": {
                "first_name": getattr(self._me, "first_name", "") if self._me else "",
                "id": getattr(self._me, "id", 0) if self._me else 0,
            } if self._me else None,
            "chat_ids": self._chat_ids,
        }


# 全局单例
client_manager = TelethonClientManager()
