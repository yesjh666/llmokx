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
from app.api import llm, forward, notification, settings, logs, update, monitor, history
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
app.include_router(history.router, prefix="/api/history", tags=["分析历史"])


@app.on_event("startup")
async def startup_event():
    """启动事件"""
    logger.info("=" * 50)
    logger.info(f"LLMOKX 交易工具 v{_APP_VERSION} 启动中...")
    logger.info("=" * 50)
    # 升级/回滚后强制重新读取磁盘配置（避免缓存陈旧）
    config.reload_config()
    logger.info("配置已加载(强制刷新)")

    # 启动后：测试所有模型连接 + 发送 Telegram 通知
    import asyncio
    async def _startup_model_check():
        try:
            await asyncio.sleep(5)
            from app.services import llm_analyzer, notifier as notifier_mod
            status = await llm_analyzer.analyzer.test_all_models()
            ok = status["ok_count"]
            total = status["total"]
            logger.info(f"模型连接测试完成: {ok}/{total} 成功")

            lines = []
            for r in status["results"]:
                icon = "\u2705" if r["success"] else "\u274c"
                lines.append(f"{icon} {r['label']}")
                if not r["success"] and r.get("error"):
                    lines.append(f"    {r['error'][:80]}")

            restart_msg = updater.check_restart_notify()
            if restart_msg:
                header = restart_msg
            else:
                header = f"\U0001F680 LLMOKX v{_APP_VERSION} \u5df2\u542f\u52a8"

            msg = f"{header}\n\n\U0001F4CA \u6a21\u578b\u8fde\u63a5\u72b6\u6001 ({ok}/{total}):\n" + "\n".join(lines)

            await notifier_mod.notifier.send(msg)
            logger.info("启动通知已发送")
        except Exception as e:
            logger.warning(f"启动模型测试/通知失败: {e}")

    asyncio.create_task(_startup_model_check())

    # 启动时自动检查更新（按配置）
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
    from app.services.llm_analyzer import analyzer
    from app.services.intent_forwarder import forwarder
    from app.services.notifier import notifier
    from app.services.telegram_monitor import monitor
    from app.services.telethon_manager import client_manager

    modules = {
        "llm_analysis": {
            "enabled": analyzer.is_enabled(),
            "api_configured": bool(analyzer.config.get("api_key")),
        },
        "forward": {
            "enabled": forwarder.is_enabled(),
            "targets_count": len(forwarder.config.get("targets", [])),
        },
        "notification": {
            "enabled": notifier.is_enabled(),
            "wechat_configured": bool(notifier._wechat_cfg.get("target")),
            "telegram_configured": bool(notifier._telegram_cfg.get("bot_token")),
        },
        "monitor": {
            "enabled": monitor.is_enabled(),
            "running": monitor.is_running(),
            "connected": client_manager._connected,
            "authorized": client_manager._authorized,
        },
    }

    return {
        "status": "ok",
        "version": _APP_VERSION,
        "uptime": int(time.time() - _START_TIME),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "modules": modules,
    }
