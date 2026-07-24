#!/usr/bin/env python3
"""升级管理相关API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app import config
from app.services import updater
from app.core.logging_config import get_logger

router = APIRouter()
logger = get_logger("app")


class UpdateConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    check_on_startup: Optional[bool] = None
    auto_install: Optional[bool] = None
    check_interval_hours: Optional[int] = None
    github_repo: Optional[str] = None
    method: Optional[str] = None
    asset_pattern: Optional[str] = None
    restart_command: Optional[str] = None
    notify_on_update: Optional[bool] = None


class RollbackRequest(BaseModel):
    backup_name: str


class RestartRequest(BaseModel):
    notify_message: Optional[str] = ""


@router.get("/version")
async def get_version():
    """获取当前版本"""
    return {
        "current_version": updater.get_current_version(),
        "update_enabled": updater.is_update_enabled(),
        "method": config.get_section("update").get("method", "release"),
    }


@router.get("/config")
async def get_config():
    """获取升级配置"""
    cfg = config.get_section("update")
    return cfg


@router.put("/config")
async def update_config(req: UpdateConfigRequest):
    """更新升级配置"""
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="没有要更新的字段")

    # 校验 method
    if data.get("method") and data["method"] not in ("release", "git"):
        raise HTTPException(status_code=400, detail="method 必须是 release 或 git")

    success = config.update_section("update", data)
    return {"success": success, "message": "配置已更新" if success else "更新失败"}


@router.get("/check")
async def check_for_updates():
    """
    检查是否有新版本
    按 method 配置选用 GitHub Releases 或 Git
    """
    if not updater.is_update_enabled():
        return {"has_update": False, "message": "自动升级已禁用"}

    result = await updater.check_update()
    return result


@router.post("/perform")
async def perform_update():
    """
    执行升级
    - release 方式：下载 → 备份 → 解压替换 → 更新依赖
    - git 方式：git pull → 更新依赖
    注意：升级后需要重启服务才能生效
    """
    if not updater.is_update_enabled():
        return {"success": False, "message": "自动升级已禁用"}

    logger.info("收到升级请求，开始执行...")
    result = await updater.perform_update()

    # 升级成功后，通知由前端调用 restartService 时通过 flag 文件触发
    # 这样通知是在服务重启成功后才发送
    if result.get("success"):
        new_ver = result.get("new_version", updater.get_current_version())
        method = result.get("method", "release")
        result["restart_notify"] = (
            f"✅ LLMOKX 已升级并重启\n\n"
            f"版本: {updater.get_current_version()} → {new_ver}\n"
            f"方式: {method}"
        )

    return result


@router.post("/restart")
async def restart_service(req: RestartRequest = RestartRequest()):
    """重启 systemd 服务，可通过 notify_message 让重启后自动发通知"""
    result = updater.restart_service(notify_message=req.notify_message or "")
    return result


@router.get("/backups")
async def list_backups():
    """列出所有备份"""
    return updater.list_backups()


@router.post("/rollback")
async def rollback(req: RollbackRequest):
    """回滚到指定备份"""
    result = updater.rollback(req.backup_name)
    if result.get("success"):
        result["rollback_success"] = True
        result["restart_notify"] = f"✅ LLMOKX 已回滚到 {req.backup_name} 并重启"
    return result


@router.delete("/backups/{backup_name}")
async def delete_backup(backup_name: str):
    """删除指定备份"""
    import shutil
    import os
    backup_path = os.path.join(updater.BACKUP_DIR, backup_name)
    if not os.path.exists(backup_path):
        raise HTTPException(status_code=404, detail="备份不存在")
    shutil.rmtree(backup_path)
    return {"success": True, "message": f"备份 {backup_name} 已删除"}