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

    if result.get("success") and config.get_section("update").get("notify_on_update", True):
        # 通过通知服务推送升级消息
        try:
            from app.services import notifier
            msg = (
                f"🔄 LLMOKX 升级成功\n\n"
                f"新版本: {result.get('new_version', updater.get_current_version())}\n"
                f"方式: {result.get('method', 'release')}\n"
                f"消息: {result.get('message', '')}\n"
                f"请确认服务已重启"
            )
            await notifier.notifier.send(msg)
        except Exception as e:
            logger.warning(f"升级通知推送失败: {e}")

    return result


@router.post("/restart")
async def restart_service():
    """重启 systemd 服务"""
    result = updater.restart_service()
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
        result["hint"] = "回滚完成，请调用 /api/update/restart 重启服务"
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