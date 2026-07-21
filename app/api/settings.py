#!/usr/bin/env python3
"""系统设置相关API"""
import os
import time
import platform
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app import config

router = APIRouter()


class UpdateServerConfigRequest(BaseModel):
    auth_enabled: Optional[bool] = None
    username: Optional[str] = None
    password: Optional[str] = None


@router.get("/")
async def get_settings():
    """获取系统设置"""
    server_cfg = config.get_section("server")
    # 隐藏密码
    server_cfg = dict(server_cfg)
    server_cfg["password_configured"] = bool(server_cfg.get("password"))
    return server_cfg


@router.put("/")
async def update_settings(req: UpdateServerConfigRequest):
    """更新系统设置"""
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="没有要更新的字段")
    success = config.update_section("server", data)
    return {"success": success, "message": "设置已更新" if success else "更新失败"}


@router.get("/status")
async def get_status():
    """获取系统状态"""
    llm_cfg = config.get_section("llm_analysis")
    forward_cfg = config.get_section("forward")
    notify_cfg = config.get_section("notification")

    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "uptime": int(time.time()),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "modules": {
            "llm_analysis": {
                "enabled": llm_cfg.get("enabled", True),
                "model": llm_cfg.get("model", ""),
                "api_configured": bool(llm_cfg.get("api_key")),
            },
            "forward": {
                "enabled": forward_cfg.get("enabled", True),
                "targets_count": len(forward_cfg.get("targets", [])),
                "bot_token_configured": bool(forward_cfg.get("telegram_bot_token")),
            },
            "notification": {
                "enabled": notify_cfg.get("enabled", True),
                "wechat_configured": bool(notify_cfg.get("wechat", {}).get("target")),
                "telegram_configured": bool(notify_cfg.get("telegram", {}).get("bot_token")),
            },
        },
    }


@router.get("/all-config")
async def get_all_config():
    """获取所有配置（API Key脱敏）"""
    all_cfg = config.load_config()

    # 脱敏处理
    if "llm_analysis" in all_cfg and all_cfg["llm_analysis"].get("api_key"):
        ak = all_cfg["llm_analysis"]["api_key"]
        all_cfg["llm_analysis"]["api_key"] = ak[:8] + "****" + ak[-4:] if len(ak) > 12 else "****"
        all_cfg["llm_analysis"]["api_key_configured"] = True

    if "forward" in all_cfg and all_cfg["forward"].get("telegram_bot_token"):
        all_cfg["forward"]["telegram_bot_token"] = "****"
        all_cfg["forward"]["bot_token_configured"] = True

    if "server" in all_cfg and all_cfg["server"].get("password"):
        all_cfg["server"]["password"] = "****"

    return all_cfg


@router.post("/reload")
async def reload_config():
    """重新加载所有配置"""
    cfg = config.reload_config()
    return {"success": True, "message": "配置已重新加载"}
