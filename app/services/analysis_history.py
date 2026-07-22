#!/usr/bin/env python3
"""
分析历史服务 - 记录每次LLM分析结果，供人工纠错学习
存储：data/analysis_history.json（单JSON数组 + 内存缓存，限制条数）
"""
import os
import json
import time
import uuid
import threading
from typing import Dict, Any, List, Optional

from app.core.logging_config import get_logger

logger = get_logger("history")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HISTORY_DIR = os.path.join(_BASE_DIR, "data")
HISTORY_FILE = os.path.join(HISTORY_DIR, "analysis_history.json")

MAX_RECORDS = 1000  # 最多保留条数

_lock = threading.Lock()
_cache: Optional[List[dict]] = None


def _load() -> List[dict]:
    """加载历史记录到内存缓存"""
    global _cache
    if _cache is not None:
        return _cache
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                _cache = json.load(f)
                if not isinstance(_cache, list):
                    _cache = []
        except Exception as e:
            logger.warning(f"加载分析历史失败: {e}")
            _cache = []
    else:
        _cache = []
    return _cache


def _persist():
    """持久化到磁盘"""
    if _cache is None:
        return
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存分析历史失败: {e}")


def add_record(
    text: str,
    context: str,
    intents: List[dict],
    model: str = "",
    source: str = "",
    elapsed: float = 0,
) -> Optional[str]:
    """
    记录一条分析结果

    Returns:
        record_id 或 None
    """
    if not text or not text.strip():
        return None
    record = {
        "id": uuid.uuid4().hex[:12],
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": time.time(),
        "text": text[:500],
        "context": context or "",
        "intents": intents or [],
        "model": model or "",
        "source": source or "pipeline",
        "elapsed": round(elapsed, 2),
        "learned": False,
    }
    with _lock:
        records = _load()
        records.insert(0, record)  # 最新的在前
        # 限制条数
        if len(records) > MAX_RECORDS:
            del records[MAX_RECORDS:]
        _persist()
    logger.info(f"[历史] 已记录分析: id={record['id']} 意图数={len(intents or [])}")
    return record["id"]


def list_records(limit: int = 50, only_unlearned: bool = False) -> List[dict]:
    """获取历史记录列表（最新在前）"""
    with _lock:
        records = _load()
        if only_unlearned:
            records = [r for r in records if not r.get("learned")]
        return records[:limit]


def get_record(record_id: str) -> Optional[dict]:
    """获取单条记录"""
    with _lock:
        for r in _load():
            if r.get("id") == record_id:
                return r
    return None


def mark_learned(record_id: str, learned: bool = True) -> bool:
    """标记为已学习"""
    with _lock:
        records = _load()
        for r in records:
            if r.get("id") == record_id:
                r["learned"] = learned
                _persist()
                return True
    return False


def delete_record(record_id: str) -> bool:
    """删除单条记录"""
    with _lock:
        records = _load()
        before = len(records)
        records[:] = [r for r in records if r.get("id") != record_id]
        if len(records) < before:
            _persist()
            return True
    return False


def clear() -> int:
    """清空全部历史，返回删除条数"""
    with _lock:
        records = _load()
        count = len(records)
        records.clear()
        _persist()
    return count


def stats() -> Dict[str, Any]:
    """统计信息"""
    with _lock:
        records = _load()
    learned = sum(1 for r in records if r.get("learned"))
    return {
        "total": len(records),
        "learned": learned,
        "unlearned": len(records) - learned,
        "max": MAX_RECORDS,
    }
