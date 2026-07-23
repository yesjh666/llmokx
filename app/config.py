#!/usr/bin/env python3
"""
LLMOKX 交易工具 - 配置管理模块
管理三个功能模块的配置，支持配置文件读写和运行时更新
"""
import os
import json
import threading
from typing import Any, Dict, Optional


CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
UNIFIED_CONFIG_FILE = os.path.join(CONFIG_DIR, "unified-config.json")

_config_lock = threading.Lock()
_config_cache: Optional[dict] = None


DEFAULT_CONFIG = {
    "llm_analysis": {
        "enabled": True,
        "provider": "openai",
        "api_key": "",
        "api_base": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "fallback_model": "gpt-3.5-turbo",
        "max_retries": 2,
        "temperature": 0.3,
        "max_tokens": 2000,
        "timeout": 90,
        "thinking": False,
        "backup_models": []
    },
    "forward": {
        "enabled": True,
        "targets": [],
        "skip_intents": ["chat", "query"],
        "telegram_bot_token": "",
        "userbot_enabled": True,
        "userbot_config_file": "config/telegram_userbot.json",
        "force_full_close": False,
        "force_close_threshold": 0.5
    },
    "notification": {
        "enabled": True,
        "max_retries": 3,
        "retry_interval": 5,
        "parallel": True,
        "wechat": {
            "enabled": True,
            "target": "o9cq80zZk50Q33Snd8zOZ5vlAEQ4@im.wechat",
            "account": "ea3465f35dfb-im-bot",
            "channel": "openclaw-weixin",
            "use_openclaw": True,
            "webhook_url": ""
        },
        "telegram": {
            "enabled": True,
            "bot_token": "",
            "chat_id": "",
            "parse_mode": "HTML",
            "disable_notification": False
        }
    },
    "monitor": {
        "enabled": False,
        "chat_ids": [],
        "chat_names": {},
        "min_message_length": 5,
        "keywords": [],
        "default_context": "无持仓无挂单",
        "notify_on_signal": True,
        "userbot_config_file": "config/telegram_userbot.json",
        "message_dedup_seconds": 300,
        "intent_dedup_seconds": 300
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8080,
        "auth_enabled": False,
        "username": "admin",
        "password": "admin123"
    },
    "update": {
        "enabled": True,
        "check_on_startup": True,
        "auto_install": False,
        "check_interval_hours": 24,
        "github_repo": "yesjh666/llmokx",
        "method": "release",
        "asset_pattern": "llmokx-*.tar.gz",
        "preserve_dirs": ["config", "data", "logs", "venv"],
        "restart_command": "systemctl restart llmokx",
        "notify_on_update": True
    }
}


def load_config() -> dict:
    """加载统一配置文件，如果不存在则创建默认配置"""
    global _config_cache

    with _config_lock:
        if _config_cache is not None:
            return _config_cache

        if os.path.exists(UNIFIED_CONFIG_FILE):
            try:
                with open(UNIFIED_CONFIG_FILE, "r", encoding="utf-8") as f:
                    _config_cache = json.load(f)
            except Exception as e:
                print(f"加载配置失败，使用默认配置: {e}")
                _config_cache = json.loads(json.dumps(DEFAULT_CONFIG))
        else:
            _config_cache = json.loads(json.dumps(DEFAULT_CONFIG))
            save_config(_config_cache)

        return _config_cache


def save_config(config: dict) -> bool:
    """保存配置到文件"""
    global _config_cache

    with _config_lock:
        os.makedirs(os.path.dirname(UNIFIED_CONFIG_FILE), exist_ok=True)
        try:
            with open(UNIFIED_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            _config_cache = config
            return True
        except Exception as e:
            print(f"保存配置失败: {e}")
            return False


def _deep_merge(base: dict, update: dict) -> dict:
    """深度合并字典：嵌套字典递归合并，而非整体替换"""
    for key, value in update.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def update_section(section: str, data: dict) -> bool:
    """更新指定配置节（支持嵌套深度合并）"""
    config = load_config()
    if section not in config:
        config[section] = {}
    _deep_merge(config[section], data)
    result = save_config(config)
    return result


def get_section(section: str) -> dict:
    """获取指定配置节"""
    config = load_config()
    return config.get(section, {})


def get_value(section: str, key: str, default: Any = None) -> Any:
    """获取指定配置值"""
    section_data = get_section(section)
    return section_data.get(key, default)


def set_value(section: str, key: str, value: Any) -> bool:
    """设置指定配置值"""
    config = load_config()
    if section not in config:
        config[section] = {}
    config[section][key] = value
    return save_config(config)


def reload_config() -> dict:
    """强制重新加载配置"""
    global _config_cache
    with _config_lock:
        _config_cache = None
    return load_config()
