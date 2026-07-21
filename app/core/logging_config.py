#!/usr/bin/env python3
"""
LLMOKX 交易工具 - 日志配置模块

日志架构:
  logs/
  ├── app.log              # 应用主日志（启动/关闭/系统级错误）
  ├── llm_analysis.log     # LLM分析日志（每次分析的输入/输出/耗时/模型）
  ├── forward.log          # 转发日志（每次转发的目标/信号内容/结果）
  ├── notification.log     # 通知日志（每次通知的消息/重试/结果）
  ├── records/             # 结构化JSON操作记录（便于程序分析）
  │   ├── llm_analysis.jsonl
  │   ├── forward.jsonl
  │   └── notification.jsonl
  └── archive/             # 轮转的历史日志（按天）
      ├── app.log.2024-01-15
      ├── llm_analysis.log.2024-01-15
      └── ...

特性:
  - 每个流程模块独立日志文件
  - 按天轮转，默认保留30天
  - 同时输出文本日志（人类可读）和JSONL记录（程序可分析）
  - 控制台同步输出（便于systemd journal查看）
"""
import os
import sys
import json
import time
import logging
import traceback
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
from typing import Any, Dict, Optional


# ==================== 路径定义 ====================
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_DIR = os.path.join(_BASE_DIR, "logs")
RECORDS_DIR = os.path.join(LOG_DIR, "records")
ARCHIVE_DIR = os.path.join(LOG_DIR, "archive")

# 日志保留天数
LOG_RETENTION_DAYS = 30

# 模块定义
LOG_MODULES = {
    "app": {
        "file": "app.log",
        "logger": "app",
        "level": logging.INFO,
        "description": "应用主日志",
    },
    "llm_analysis": {
        "file": "llm_analysis.log",
        "logger": "app.services.llm_analyzer",
        "level": logging.INFO,
        "description": "LLM分析日志",
        "record_file": "llm_analysis.jsonl",
    },
    "forward": {
        "file": "forward.log",
        "logger": "app.services.intent_forwarder",
        "level": logging.INFO,
        "description": "转发日志",
        "record_file": "forward.jsonl",
    },
    "notification": {
        "file": "notification.log",
        "logger": "app.services.notifier",
        "level": logging.INFO,
        "description": "通知日志",
        "record_file": "notification.jsonl",
    },
    "monitor": {
        "file": "monitor.log",
        "logger": "app.services.telegram_monitor",
        "level": logging.INFO,
        "description": "Telegram监听日志",
    },
}

_initialized = False


def setup_logging():
    """初始化所有日志配置（只执行一次）"""
    global _initialized
    if _initialized:
        return
    _initialized = True

    # 创建目录
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(RECORDS_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    # 统一日志格式
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台handler（systemd journal会捕获）
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.INFO)

    # 根logger配置
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 为每个模块配置独立文件handler
    for module_key, module_cfg in LOG_MODULES.items():
        log_file = os.path.join(LOG_DIR, module_cfg["file"])

        # 按天轮转: midnight=每天0点轮转, backupCount=保留天数
        file_handler = TimedRotatingFileHandler(
            filename=log_file,
            when="midnight",
            interval=1,
            backupCount=LOG_RETENTION_DAYS,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(module_cfg["level"])

        # 轮转后的文件加上日期后缀，并移动到archive目录
        file_handler.suffix = "%Y-%m-%d"

        # 获取模块logger
        module_logger = logging.getLogger(module_cfg["logger"])

        # 避免重复添加handler
        has_file = any(
            isinstance(h, TimedRotatingFileHandler)
            and getattr(h, "baseFilename", "") == os.path.abspath(log_file)
            for h in module_logger.handlers
        )
        if not has_file:
            module_logger.addHandler(file_handler)

        # 同时也加到控制台（如果根logger没加）
        has_console = any(
            isinstance(h, logging.StreamHandler) and not isinstance(h, TimedRotatingFileHandler)
            for h in module_logger.handlers
        )
        if not has_console:
            module_logger.addHandler(console_handler)

        module_logger.propagate = False  # 不向上传播，避免重复

    # app logger 额外加控制台（确保启动信息可见）
    app_logger = logging.getLogger("app")
    if not any(isinstance(h, logging.StreamHandler) for h in app_logger.handlers):
        app_logger.addHandler(console_handler)

    # 第三方库日志降噪
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(module_key: str = "app") -> logging.Logger:
    """获取指定模块的logger"""
    if module_key in LOG_MODULES:
        return logging.getLogger(LOG_MODULES[module_key]["logger"])
    return logging.getLogger("app")


# ==================== 结构化操作记录（JSONL） ====================

def _write_record(module_key: str, record: dict):
    """写入一条结构化JSONL记录"""
    if module_key not in LOG_MODULES:
        return
    record_file = LOG_MODULES[module_key].get("record_file")
    if not record_file:
        return

    record_path = os.path.join(RECORDS_DIR, record_file)
    record["timestamp"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")

    try:
        with open(record_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.getLogger("app").error(f"写入操作记录失败: {e}")


def log_llm_analysis(
    text: str,
    context: str,
    success: bool,
    intents: Optional[list] = None,
    model: str = "",
    elapsed: float = 0,
    error: Optional[str] = None,
    raw_response: str = "",
    retries: int = 0,
):
    """
    记录一次LLM分析操作

    Args:
        text: 输入消息
        context: 上下文
        success: 是否成功
        intents: 分析结果
        model: 使用的模型
        elapsed: 耗时(秒)
        error: 错误信息
        raw_response: 原始响应（截断存储）
        retries: 重试次数
    """
    logger = get_logger("llm_analysis")

    # 文本日志
    status = "成功" if success else "失败"
    intent_summary = ""
    if intents:
        intent_names = [i.get("intent", "?") for i in intents]
        intent_summary = f" | 意图: {','.join(intent_names)}"

    logger.info(
        f"[分析{status}] model={model} elapsed={elapsed:.2f}s retries={retries}{intent_summary}"
        + (f" | 输入: {text[:100]}" if text else "")
        + (f" | 错误: {error}" if error else "")
    )

    # 结构化记录
    _write_record("llm_analysis", {
        "type": "llm_analysis",
        "action": "analyze",
        "input": text[:500] if text else "",
        "context": context[:200] if context else "",
        "success": success,
        "model": model,
        "elapsed": elapsed,
        "retries": retries,
        "intents": intents or [],
        "error": error,
        "raw_response": raw_response[:2000] if raw_response else "",
    })


def log_forward(
    intent: str,
    symbol: str,
    direction: Optional[str],
    text: str,
    source_chat: str,
    success: bool,
    forwarded_targets: list,
    errors: Optional[list] = None,
    signal_data: Optional[dict] = None,
):
    """
    记录一次转发操作

    Args:
        intent: 意图类型
        symbol: 交易对
        direction: 方向
        text: 原始消息
        source_chat: 来源群
        success: 是否成功
        forwarded_targets: 成功转发的目标列表
        errors: 错误列表
        signal_data: 转发的信号数据
    """
    logger = get_logger("forward")

    status = "成功" if success else "失败"
    targets_str = ",".join(str(t) for t in forwarded_targets) if forwarded_targets else "无"

    logger.info(
        f"[转发{status}] intent={intent} symbol={symbol} dir={direction or 'null'} "
        f"| 目标: {targets_str}"
        + (f" | 来源: {source_chat}" if source_chat else "")
        + (f" | 错误: {'; '.join(errors)}" if errors else "")
    )

    _write_record("forward", {
        "type": "forward",
        "action": "forward_intent",
        "intent": intent,
        "symbol": symbol,
        "direction": direction,
        "source": source_chat,
        "original_text": text[:500] if text else "",
        "success": success,
        "forwarded_targets": forwarded_targets,
        "errors": errors or [],
        "signal_data": signal_data,
    })


def log_notification(
    message: str,
    success: bool,
    attempts: int,
    channel: str = "wechat",
    method: str = "openclaw",
    error: Optional[str] = None,
    elapsed: float = 0,
):
    """
    记录一次通知操作

    Args:
        message: 通知内容
        success: 是否成功
        attempts: 尝试次数
        channel: 通知通道(wechat/telegram/all)
        method: 发送方式(openclaw/webhook/bot_api)
        error: 错误信息
        elapsed: 耗时
    """
    logger = get_logger("notification")

    status = "成功" if success else "失败"

    logger.info(
        f"[通知{status}] channel={channel} method={method} attempts={attempts} elapsed={elapsed:.1f}s"
        + (f" | 内容: {message[:80]}" if message else "")
        + (f" | 错误: {error}" if error else "")
    )

    _write_record("notification", {
        "type": "notification",
        "action": f"send_{channel}",
        "channel": channel,
        "message": message[:1000] if message else "",
        "success": success,
        "attempts": attempts,
        "method": method,
        "error": error,
        "elapsed": elapsed,
    })


# ==================== 日志读取工具（供API使用） ====================

def read_log_file(module_key: str, lines: int = 200, level: str = "") -> dict:
    """
    读取指定模块的最近日志

    Args:
        module_key: 模块名 (app/llm_analysis/forward/notification)
        lines: 读取行数
        level: 日志级别过滤 (INFO/WARNING/ERROR)

    Returns:
        dict: {module, file, lines: [...], total}
    """
    if module_key not in LOG_MODULES:
        return {"error": f"未知模块: {module_key}", "available": list(LOG_MODULES.keys())}

    log_file = os.path.join(LOG_DIR, LOG_MODULES[module_key]["file"])

    if not os.path.exists(log_file):
        return {"module": module_key, "file": log_file, "lines": [], "total": 0}

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
    except Exception as e:
        return {"module": module_key, "error": str(e), "lines": [], "total": 0}

    # 级别过滤
    if level:
        level_upper = level.upper()
        all_lines = [l for l in all_lines if f"[{level_upper}]" in l]

    # 取最后N行
    total = len(all_lines)
    recent = all_lines[-lines:] if total > lines else all_lines

    return {
        "module": module_key,
        "description": LOG_MODULES[module_key]["description"],
        "file": log_file,
        "lines": [l.rstrip("\n") for l in recent],
        "total": total,
        "returned": len(recent),
    }


def read_records(module_key: str, limit: int = 50) -> dict:
    """
    读取结构化JSONL操作记录

    Args:
        module_key: 模块名
        limit: 返回条数

    Returns:
        dict: {module, records: [...], total}
    """
    if module_key not in LOG_MODULES:
        return {"error": f"未知模块: {module_key}"}

    record_file = LOG_MODULES[module_key].get("record_file")
    if not record_file:
        return {"module": module_key, "records": [], "total": 0, "message": "该模块无结构化记录"}

    record_path = os.path.join(RECORDS_DIR, record_file)

    if not os.path.exists(record_path):
        return {"module": module_key, "records": [], "total": 0}

    try:
        with open(record_path, "r", encoding="utf-8") as f:
            all_records = f.readlines()
    except Exception as e:
        return {"module": module_key, "error": str(e), "records": [], "total": 0}

    total = len(all_records)
    recent = all_records[-limit:] if total > limit else all_records

    records = []
    for line in recent:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append({"raw": line, "parse_error": True})

    records.reverse()  # 最新的在前

    return {
        "module": module_key,
        "description": LOG_MODULES[module_key]["description"],
        "file": record_path,
        "records": records,
        "total": total,
        "returned": len(records),
    }


def list_log_files() -> dict:
    """列出所有日志文件及大小"""
    result = {}
    for module_key, cfg in LOG_MODULES.items():
        log_file = os.path.join(LOG_DIR, cfg["file"])
        info = {
            "module": module_key,
            "description": cfg["description"],
            "file": cfg["file"],
            "exists": os.path.exists(log_file),
        }
        if os.path.exists(log_file):
            size = os.path.getsize(log_file)
            info["size"] = size
            info["size_human"] = _format_size(size)

            # 统计行数和各级别数量
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    content = f.read()
                info["lines"] = content.count("\n")
                info["errors"] = content.count("[ERROR]")
                info["warnings"] = content.count("[WARNING]")
            except Exception:
                info["lines"] = 0

        # 结构化记录文件
        record_file = cfg.get("record_file")
        if record_file:
            record_path = os.path.join(RECORDS_DIR, record_file)
            info["record_file"] = record_file
            info["record_exists"] = os.path.exists(record_path)
            if os.path.exists(record_path):
                info["record_size"] = os.path.getsize(record_path)
                info["record_size_human"] = _format_size(os.path.getsize(record_path))

        result[module_key] = info

    # 归档目录
    archive_files = []
    if os.path.exists(ARCHIVE_DIR):
        for f in sorted(os.listdir(ARCHIVE_DIR), reverse=True):
            fpath = os.path.join(ARCHIVE_DIR, f)
            if os.path.isfile(fpath):
                archive_files.append({
                    "name": f,
                    "size": os.path.getsize(fpath),
                    "size_human": _format_size(os.path.getsize(fpath)),
                    "date": os.path.getmtime(fpath),
                })
    result["_archive"] = {
        "dir": ARCHIVE_DIR,
        "files": archive_files[:20],  # 最近20个
        "total_files": len(archive_files),
    }

    return result


def _format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes == 0:
        return "0B"
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"
