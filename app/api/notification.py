#!/usr/bin/env python3
"""通知管理相关API - 双通道（微信 + Telegram）"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app import config
from app.services import notifier

router = APIRouter()


class WeChatConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    target: Optional[str] = None
    account: Optional[str] = None
    channel: Optional[str] = None
    use_openclaw: Optional[bool] = None
    webhook_url: Optional[str] = None


class TelegramConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    parse_mode: Optional[str] = None
    disable_notification: Optional[bool] = None


class UpdateNotificationConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    max_retries: Optional[int] = None
    retry_interval: Optional[int] = None
    parallel: Optional[bool] = None
    wechat: Optional[WeChatConfigUpdate] = None
    telegram: Optional[TelegramConfigUpdate] = None


class SendNotificationRequest(BaseModel):
    message: str
    channel: str = "all"


class TestNotificationRequest(BaseModel):
    channel: str = "all"


@router.get("/config")
async def get_config():
    """获取通知配置"""
    cfg = config.get_section("notification")
    return cfg


@router.put("/config")
async def update_config(req: UpdateNotificationConfigRequest):
    """更新通知配置（支持分通道更新）"""
    data = {}

    if req.enabled is not None:
        data["enabled"] = req.enabled
    if req.max_retries is not None:
        data["max_retries"] = req.max_retries
    if req.retry_interval is not None:
        data["retry_interval"] = req.retry_interval
    if req.parallel is not None:
        data["parallel"] = req.parallel

    # 嵌套更新 wechat 子配置
    if req.wechat is not None:
        wechat_data = {k: v for k, v in req.wechat.model_dump().items() if v is not None}
        if wechat_data:
            data["wechat"] = wechat_data

    # 嵌套更新 telegram 子配置
    if req.telegram is not None:
        telegram_data = {k: v for k, v in req.telegram.model_dump().items() if v is not None}
        if telegram_data:
            data["telegram"] = telegram_data

    if not data:
        raise HTTPException(status_code=400, detail="没有要更新的字段")

    success = config.update_section("notification", data)
    return {"success": success, "message": "配置已更新" if success else "更新失败"}


@router.post("/test")
async def test_notification(req: TestNotificationRequest = TestNotificationRequest()):
    """测试通知发送（可指定通道）"""
    result = await notifier.notifier.test_notification(channel=req.channel)
    return result


@router.post("/send")
async def send_notification(req: SendNotificationRequest):
    """发送自定义通知（可指定通道）"""
    if req.channel == "wechat":
        result = await notifier.notifier.send_wechat(req.message)
    elif req.channel == "telegram":
        result = await notifier.notifier.send_telegram(req.message)
    else:
        result = await notifier.notifier.send(req.message)
    return result
