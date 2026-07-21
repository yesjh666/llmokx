#!/usr/bin/env python3
"""Telegram 监听管理 API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from app import config
from app.services.telegram_monitor import monitor
from app.services.message_pipeline import pipeline

router = APIRouter()


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
