#!/usr/bin/env python3
"""
Prompt管理服务
负责管理所有prompt配置，支持动态添加规则和示例
"""
import os
import json
import threading
from typing import List, Dict, Any, Optional


PROMPTS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
    "prompts.json",
)

_lock = threading.Lock()
_prompts_cache: Optional[dict] = None


def load_prompts() -> dict:
    """加载prompt配置文件"""
    global _prompts_cache

    with _lock:
        if _prompts_cache is not None:
            return _prompts_cache

        if os.path.exists(PROMPTS_FILE):
            try:
                with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
                    _prompts_cache = json.load(f)
            except Exception as e:
                print(f"加载prompt配置失败: {e}")
                _prompts_cache = {}
        else:
            _prompts_cache = {}

        return _prompts_cache


def save_prompts(prompts: dict) -> bool:
    """保存prompt配置到文件"""
    global _prompts_cache

    with _lock:
        os.makedirs(os.path.dirname(PROMPTS_FILE), exist_ok=True)
        try:
            with open(PROMPTS_FILE, "w", encoding="utf-8") as f:
                json.dump(prompts, f, ensure_ascii=False, indent=2)
            _prompts_cache = prompts
            return True
        except Exception as e:
            print(f"保存prompt配置失败: {e}")
            return False


def reload_prompts() -> dict:
    """强制重新加载prompt配置"""
    global _prompts_cache
    with _lock:
        _prompts_cache = None
    return load_prompts()


def build_prompt(text: str, context_str: str = "无持仓无挂单") -> str:
    """
    根据配置构建完整的prompt

    Args:
        text: 待分析的消息文本
        context_str: 上下文信息（持仓、挂单等）

    Returns:
        str: 完整的prompt字符串
    """
    prompts = load_prompts()

    system_prompt = prompts.get("system_prompt", "")
    base_prompt = prompts.get("base_prompt", "分析交易消息意图:")
    intent_types = prompts.get("intent_types", {})
    field_descriptions = prompts.get("field_descriptions", {})
    return_format = prompts.get("return_format", {})
    rules = prompts.get("rules", [])
    examples = prompts.get("examples", [])
    custom_rules = prompts.get("custom_rules", [])
    custom_examples = prompts.get("custom_examples", [])

    # 构建意图类型说明
    intent_lines = []
    for k, v in intent_types.items():
        if not k.startswith("_"):
            intent_lines.append(f"  {k}: {v}")
    intent_section = "\n".join(intent_lines) if intent_lines else ""

    # 构建字段说明
    field_lines = []
    for intent_name, fields in field_descriptions.items():
        if intent_name.startswith("_"):
            continue
        field_lines.append(f"\n  [{intent_name}]")
        for fname, fdesc in fields.items():
            if not fname.startswith("_"):
                field_lines.append(f"    - {fname}: {fdesc}")
    field_section = "\n".join(field_lines) if field_lines else ""

    # 构建返回格式说明
    format_lines = []
    if isinstance(return_format, dict):
        structure = return_format.get("_structure", {})
        if structure:
            format_lines.append(json.dumps(structure, ensure_ascii=False, indent=2))
        note = return_format.get("_note", "")
        if note:
            format_lines.append(note)
    format_section = "\n".join(format_lines) if format_lines else ""

    # 合并规则（内置规则 + 自定义规则，自定义规则按优先级排序）
    all_rules = list(rules)
    # 按 priority 排序自定义规则
    valid_custom = []
    for r in custom_rules:
        if isinstance(r, dict):
            if r.get("enabled", True):
                valid_custom.append(r)
        elif isinstance(r, str):
            valid_custom.append({"rule": r, "priority": 0, "enabled": True})

    valid_custom.sort(key=lambda x: x.get("priority", 0))
    for r in valid_custom:
        all_rules.append(r.get("rule", "") if isinstance(r, dict) else str(r))

    rules_section = "\n".join(f"- {r}" for r in all_rules) if all_rules else ""

    # 合并示例（内置 + 自定义）
    all_examples = list(examples)
    all_examples.extend(custom_examples)

    examples_lines = []
    for i, ex in enumerate(all_examples, 1):
        if isinstance(ex, dict):
            desc = ex.get("description", "")
            inp = ex.get("input", "")
            out = ex.get("output", "")
            if desc:
                examples_lines.append(f"\n示例{i} ({desc}):")
            else:
                examples_lines.append(f"\n示例{i}:")
            examples_lines.append(f"输入: \"{inp}\"")
            examples_lines.append(f"输出: {out}")
    examples_section = "\n".join(examples_lines) if examples_lines else ""

    # 组装完整prompt
    parts = []

    parts.append(f"{base_prompt}\n")

    parts.append(f"当前上下文:\n{context_str}\n")

    parts.append(f"待分析消息: {text}\n")

    if intent_section:
        parts.append(f"意图类型:\n{intent_section}\n")

    if field_section:
        parts.append(f"字段说明:{field_section}\n")

    if rules_section:
        parts.append(f"规则:\n{rules_section}\n")

    if format_section:
        parts.append(f"返回JSON格式(只返回JSON,无其他文字):\n{{\n  \"intents\": [\n{format_section}\n  ]\n}}\n")

    if examples_section:
        parts.append(f"示例参考:{examples_section}\n")

    full_prompt = "\n".join(parts)
    return system_prompt + "\n\n" + full_prompt if system_prompt else full_prompt


def add_rule(rule: str, priority: int = 0, description: str = "", enabled: bool = True) -> bool:
    """
    添加自定义规则

    Args:
        rule: 规则内容
        priority: 优先级（数字越小越优先）
        description: 规则说明
        enabled: 是否启用
    """
    prompts = load_prompts()
    if "custom_rules" not in prompts:
        prompts["custom_rules"] = []

    entry = {
        "rule": rule,
        "priority": priority,
        "description": description,
        "enabled": enabled,
    }
    prompts["custom_rules"].append(entry)
    return save_prompts(prompts)


def add_example(input_text: str, output_json: str, description: str = "") -> bool:
    """
    添加自定义示例

    Args:
        input_text: 输入消息
        output_json: 期望输出JSON
        description: 示例说明
    """
    prompts = load_prompts()
    if "custom_examples" not in prompts:
        prompts["custom_examples"] = []

    entry = {
        "description": description,
        "input": input_text,
        "output": output_json,
    }
    prompts["custom_examples"].append(entry)
    return save_prompts(prompts)


def remove_rule(index: int) -> bool:
    """删除自定义规则（按索引）"""
    prompts = load_prompts()
    custom_rules = prompts.get("custom_rules", [])
    if 0 <= index < len(custom_rules):
        custom_rules.pop(index)
        prompts["custom_rules"] = custom_rules
        return save_prompts(prompts)
    return False


def remove_example(index: int) -> bool:
    """删除自定义示例（按索引）"""
    prompts = load_prompts()
    custom_examples = prompts.get("custom_examples", [])
    if 0 <= index < len(custom_examples):
        custom_examples.pop(index)
        prompts["custom_examples"] = custom_examples
        return save_prompts(prompts)
    return False


def toggle_rule(index: int, enabled: bool) -> bool:
    """启用/禁用自定义规则"""
    prompts = load_prompts()
    custom_rules = prompts.get("custom_rules", [])
    if 0 <= index < len(custom_rules):
        if isinstance(custom_rules[index], dict):
            custom_rules[index]["enabled"] = enabled
        prompts["custom_rules"] = custom_rules
        return save_prompts(prompts)
    return False


def get_stats() -> dict:
    """获取prompt统计信息"""
    prompts = load_prompts()
    return {
        "rules_count": len(prompts.get("rules", [])),
        "examples_count": len(prompts.get("examples", [])),
        "custom_rules_count": len(prompts.get("custom_rules", [])),
        "custom_examples_count": len(prompts.get("custom_examples", [])),
    }
