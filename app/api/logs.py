#!/usr/bin/env python3
"""日志查看相关API"""
from fastapi import APIRouter, Query
from typing import Optional

from app.core.logging_config import (
    LOG_MODULES,
    read_log_file,
    read_records,
    list_log_files,
    get_logger,
)

router = APIRouter()
logger = get_logger("app")


@router.get("/modules")
async def get_modules():
    """获取所有日志模块列表"""
    modules = []
    for key, cfg in LOG_MODULES.items():
        modules.append({
            "key": key,
            "description": cfg["description"],
            "file": cfg["file"],
            "has_records": "record_file" in cfg,
        })
    return {"modules": modules}


@router.get("/files")
async def get_files():
    """获取所有日志文件信息（大小/行数/错误数等）"""
    return list_log_files()


@router.get("/{module}/tail")
async def get_log_tail(
    module: str,
    lines: int = Query(200, ge=1, le=5000, description="读取行数"),
    level: str = Query("", description="日志级别过滤: INFO/WARNING/ERROR"),
):
    """
    获取指定模块的最近日志（文本格式）

    - module: app / llm_analysis / forward / notification
    - lines: 返回最后N行
    - level: 按级别过滤
    """
    return read_log_file(module, lines=lines, level=level)


@router.get("/{module}/records")
async def get_records(
    module: str,
    limit: int = Query(50, ge=1, le=500, description="返回记录条数"),
):
    """
    获取指定模块的结构化操作记录（JSON格式，便于分析）

    - module: llm_analysis / forward / notification
    - 返回最近的N条操作记录（含输入输出/耗时/错误等）
    """
    return read_records(module, limit=limit)


@router.get("/{module}/stats")
async def get_log_stats(module: str):
    """获取指定模块的日志统计（成功/失败次数等）"""
    records_data = read_records(module, limit=500)

    records = records_data.get("records", [])
    if not records:
        return {
            "module": module,
            "total": 0,
            "success": 0,
            "failed": 0,
            "success_rate": 0,
        }

    total = len(records)
    success = sum(1 for r in records if r.get("success"))
    failed = total - success

    # 计算平均耗时（如果有elapsed字段）
    elapsed_list = [r.get("elapsed", 0) for r in records if r.get("elapsed") is not None]
    avg_elapsed = sum(elapsed_list) / len(elapsed_list) if elapsed_list else 0
    max_elapsed = max(elapsed_list) if elapsed_list else 0
    min_elapsed = min(elapsed_list) if elapsed_list else 0

    # 按意图/操作类型统计（LLM分析/转发模块）
    by_type = {}
    for r in records:
        rtype = r.get("intent") or r.get("action") or r.get("type") or "unknown"
        if rtype not in by_type:
            by_type[rtype] = {"total": 0, "success": 0, "failed": 0}
        by_type[rtype]["total"] += 1
        if r.get("success"):
            by_type[rtype]["success"] += 1
        else:
            by_type[rtype]["failed"] += 1

    return {
        "module": module,
        "total": total,
        "success": success,
        "failed": failed,
        "success_rate": round(success / total * 100, 1) if total > 0 else 0,
        "avg_elapsed": round(avg_elapsed, 2),
        "max_elapsed": round(max_elapsed, 2),
        "min_elapsed": round(min_elapsed, 2),
        "by_type": by_type,
    }
