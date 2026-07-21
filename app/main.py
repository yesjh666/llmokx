#!/usr/bin/env python3
"""
LLMOKX 交易工具 - FastAPI主应用
"""
import os
import time
import logging

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.api import llm, forward, notification, settings, logs, update, monitor
from app.core.logging_config import setup_logging, get_logger
from app.services import updater

# 初始化日志系统（分模块 + 按天轮转 + JSON操作记录）
setup_logging()
logger = get_logger("app")

# 更新 FastAPI 版本号（从 version.txt 读取）
_APP_VERSION = updater.get_current_version()

# 启动时间
_START_TIME = time.time()

# 创建FastAPI应用
app = FastAPI(
    title="LLMOKX 交易工具",
    description="LLM分析 + 意图转发 + 通知推送 + 自动升级 一体化管理平台",
    version=_APP_VERSION,
)

# CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 注册API路由
app.include_router(llm.router, prefix="/api/llm", tags=["LLM分析"])
app.include_router(forward.router, prefix="/api/forward", tags=["转发管理"])
app.include_router(notification.router, prefix="/api/notification", tags=["通知管理"])
app.include_router(settings.router, prefix="/api/settings", tags=["系统设置"])
app.include_router(logs.router, prefix="/api/logs", tags=["日志查看"])
app.include_router(update.router, prefix="/api/update", tags=["升级管理"])
app.include_router(monitor.router, prefix="/api/monitor", tags=["监听管理"])


@app.on_event("startup")
async def startup_event():
    """启动事件"""
    logger.info("=" * 50)
    logger.info(f"LLMOKX 交易工具 v{_APP_VERSION} 启动中...")
    logger.info("=" * 50)
    # 加载配置
    config.load_config()
    logger.info("配置已加载")

    # 启动时自动检查更新（按配置）
    import asyncio
    asyncio.create_task(updater.startup_check())

    # 启动 Telegram 监听（如果已启用）
    from app.services.telegram_monitor import monitor
    from app.services.message_pipeline import pipeline
    monitor_cfg = config.get_section("monitor")
    if monitor_cfg.get("enabled", False) and monitor_cfg.get("chat_ids"):
        monitor.set_message_handler(pipeline.process_message)
        asyncio.create_task(monitor.start())
        logger.info("Telegram 监听已启动")
    else:
        logger.info("Telegram 监听未启用（可在 Web 界面开启）")


@app.on_event("shutdown")
async def shutdown_event():
    """关闭事件"""
    logger.info("LLMOKX 交易工具 已关闭")


@app.get("/", response_class=HTMLResponse)
async def root():
    """返回前端页面"""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h1>LLMOKX 交易工具</h1><p>前端页面未找到</p>")


@app.get("/api/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "version": _APP_VERSION,
        "uptime": int(time.time() - _START_TIME),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
