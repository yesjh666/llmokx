#!/usr/bin/env python3
"""Telegram 监听管理 API"""
import os
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from app import config
from app.services.telegram_monitor import monitor
from app.services.message_pipeline import pipeline

router = APIRouter()

# 项目根目录
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
USERBOT_CONFIG_FILE = os.path.join(_BASE_DIR, "config", "telegram_userbot.json")


class MonitorConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    chat_ids: Optional[List[str]] = None
    chat_names: Optional[dict] = None
    min_message_length: Optional[int] = None
    keywords: Optional[List[str]] = None
    default_context: Optional[str] = None
    notify_on_signal: Optional[bool] = None
    userbot_config_file: Optional[str] = None


class AddChatRequest(BaseModel):
    chat_id: str
    name: str = ""


class RemoveChatRequest(BaseModel):
    chat_id: str


class UserbotConfigUpdate(BaseModel):
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    phone_number: Optional[str] = None
    session_file: Optional[str] = None
    enabled: Optional[bool] = None


@router.get("/config")
async def get_monitor_config():
    """获取监听配置"""
    return config.get_section("monitor")


@router.put("/config")
async def update_monitor_config(req: MonitorConfigUpdate):
    """更新监听配置"""
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="没有要更新的字段")
    success = config.update_section("monitor", data)
    return {"success": success, "message": "配置已更新" if success else "更新失败"}


@router.get("/status")
async def get_monitor_status():
    """获取监听状态"""
    status = monitor.get_status()
    status["stats"] = pipeline.get_stats()
    return status


@router.post("/start")
async def start_monitor():
    """启动 Telegram 监听"""
    if monitor.is_running():
        return {"success": True, "message": "监听已在运行中"}

    # 注入消息处理管道
    monitor.set_message_handler(pipeline.process_message)

    import asyncio
    asyncio.create_task(monitor.start())

    return {"success": True, "message": "监听启动中..."}


@router.post("/stop")
async def stop_monitor():
    """停止 Telegram 监听"""
    if not monitor.is_running():
        return {"success": True, "message": "监听未在运行"}

    await monitor.stop()
    return {"success": True, "message": "监听已停止"}


@router.post("/chats/add")
async def add_chat(req: AddChatRequest):
    """添加监听群"""
    monitor_cfg = config.get_section("monitor")
    chat_ids = monitor_cfg.get("chat_ids", [])
    chat_names = monitor_cfg.get("chat_names", {})

    if req.chat_id in chat_ids:
        raise HTTPException(status_code=400, detail="该群已在监听列表中")

    chat_ids.append(req.chat_id)
    if req.name:
        chat_names[req.chat_id] = req.name

    config.update_section("monitor", {
        "chat_ids": chat_ids,
        "chat_names": chat_names,
    })

    return {"success": True, "message": f"已添加监听群: {req.chat_id}"}


@router.post("/chats/remove")
async def remove_chat(req: RemoveChatRequest):
    """移除监听群"""
    monitor_cfg = config.get_section("monitor")
    chat_ids = monitor_cfg.get("chat_ids", [])
    chat_names = monitor_cfg.get("chat_names", {})

    if req.chat_id not in chat_ids:
        raise HTTPException(status_code=400, detail="该群不在监听列表中")

    chat_ids.remove(req.chat_id)
    chat_names.pop(req.chat_id, None)

    config.update_section("monitor", {
        "chat_ids": chat_ids,
        "chat_names": chat_names,
    })

    return {"success": True, "message": f"已移除监听群: {req.chat_id}"}


@router.post("/test")
async def test_pipeline(text: str = "BTC 做多 60000 止盈65000 止损58000"):
    """测试消息处理管道（手动输入消息模拟监听）"""
    result = await pipeline.process_message(
        text=text,
        source_chat="测试群",
        source_chat_id="test",
        sender="测试用户",
    )
    return result


# ==================== Userbot 配置 ====================

def _load_userbot_config() -> dict:
    """读取 userbot 配置"""
    if not os.path.exists(USERBOT_CONFIG_FILE):
        return {"api_id": None, "api_hash": "", "phone_number": "", "session_file": "config/userbot_session", "enabled": False}
    try:
        with open(USERBOT_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_userbot_config(data: dict) -> bool:
    """保存 userbot 配置"""
    try:
        existing = _load_userbot_config()
        existing.update(data)
        # 移除说明字段
        existing.pop("说明", None)
        with open(USERBOT_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        return False


@router.get("/userbot")
async def get_userbot_config():
    """获取 Telegram Userbot 配置"""
    cfg = _load_userbot_config()
    # 脱敏
    result = dict(cfg)
    if result.get("api_hash"):
        h = result["api_hash"]
        result["api_hash"] = h[:4] + "****" + h[-4:] if len(h) > 8 else "****"
        result["api_hash_configured"] = True
    result.pop("说明", None)
    return result


@router.put("/userbot")
async def update_userbot_config(req: UserbotConfigUpdate):
    """更新 Telegram Userbot 配置"""
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="没有要更新的字段")
    success = _save_userbot_config(data)
    return {"success": success, "message": "配置已更新" if success else "更新失败"}


@router.post("/userbot/test")
async def test_userbot_connection():
    """测试 Telegram 连接（自动处理登录流程）"""
    cfg = _load_userbot_config()
    if not cfg.get("api_id") or not cfg.get("api_hash"):
        return {"success": False, "message": "api_id 或 api_hash 未配置"}

    try:
        from telethon import TelegramClient
    except ImportError:
        return {"success": False, "message": "telethon 未安装，请运行: pip install telethon"}

    api_id = cfg["api_id"]
    api_hash = cfg["api_hash"]
    session_file = cfg.get("session_file", "config/userbot_session")
    phone = cfg.get("phone_number", "")

    if not os.path.isabs(session_file):
        session_file = os.path.join(_BASE_DIR, session_file)

    try:
        client = TelegramClient(session_file, api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            if not phone:
                await client.disconnect()
                return {"success": False, "message": "未授权且未配置手机号，请先填写手机号"}

            # 发送验证码
            await client.send_code_request(phone)
            # 断开连接，等待用户输入验证码后调用 /userbot/login
            await client.disconnect()
            return {
                "success": False,
                "need_code": True,
                "message": f"验证码已发送到 {phone}，请在下方输入验证码",
            }

        me = await client.get_me()
        await client.disconnect()
        return {
            "success": True,
            "message": f"连接成功: {me.first_name} (ID: {me.id}, 手机: {me.phone})",
        }
    except Exception as e:
        return {"success": False, "message": f"连接失败: {e}"}


@router.post("/userbot/login")
async def login_with_code(code: str):
    """用验证码完成 Telegram 登录"""
    cfg = _load_userbot_config()
    if not cfg.get("api_id") or not cfg.get("api_hash"):
        return {"success": False, "message": "api_id 或 api_hash 未配置"}

    try:
        from telethon import TelegramClient
    except ImportError:
        return {"success": False, "message": "telethon 未安装"}

    api_id = cfg["api_id"]
    api_hash = cfg["api_hash"]
    session_file = cfg.get("session_file", "config/userbot_session")
    phone = cfg.get("phone_number", "")

    if not os.path.isabs(session_file):
        session_file = os.path.join(_BASE_DIR, session_file)

    try:
        client = TelegramClient(session_file, api_id, api_hash)
        await client.connect()

        if await client.is_user_authorized():
            me = await client.get_me()
            await client.disconnect()
            return {"success": True, "message": f"已登录: {me.first_name} (ID: {me.id})"}

        # 用验证码登录
        await client.sign_in(phone, code)
        me = await client.get_me()
        await client.disconnect()
        return {
            "success": True,
            "message": f"登录成功: {me.first_name} (ID: {me.id}, 手机: {me.phone})",
        }
    except Exception as e:
        return {"success": False, "message": f"登录失败: {e}"}
