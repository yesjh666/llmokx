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
from app.services.telethon_manager import client_manager

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
    message_dedup_seconds: Optional[int] = None
    intent_dedup_seconds: Optional[int] = None


class AddChatRequest(BaseModel):
    chat_id: str
    name: str = ""


class RemoveChatRequest(BaseModel):
    chat_id: str


class UserbotConfigUpdate(BaseModel):
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    phone_number: Optional[str] = None
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

    # 热更新监听群
    if monitor.is_running():
        success, msg = await monitor.update_chats(chat_ids)
        if success:
            return {"success": True, "message": f"已添加监听群: {req.chat_id}（监听已热更新）"}

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

    # 热更新监听群
    if monitor.is_running():
        success, msg = await monitor.update_chats(chat_ids)
        if success:
            return {"success": True, "message": f"已移除监听群: {req.chat_id}（监听已热更新）"}

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
    """
    测试连接 + 启动登录流程
    - 如已授权 → 返回成功
    - 如有有效 session → 自动恢复
    - 否则 → 发送验证码，返回 need_code=True
    """
    result = await client_manager.start_login()
    return result


@router.post("/userbot/login")
async def login_with_code(code: str):
    """提交验证码登录（可能返回需要2FA密码）"""
    result = await client_manager.submit_code(code)
    return result


@router.post("/userbot/password")
async def login_with_password(password: str):
    """提交两步验证密码"""
    result = await client_manager.submit_password(password)
    return result


@router.get("/userbot/state")
async def get_login_state():
    """获取当前登录状态"""
    return client_manager.get_login_state()
