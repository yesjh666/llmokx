#!/usr/bin/env python3
"""
规则学习服务 - 根据用户纠错，调用LLM自动生成规则+示例
用于"人工确认学习"：用户提供正确结果 → LLM总结规则 → 入库
"""
import re
import json
import logging
from typing import Dict, Any, Optional

import httpx

from app import config
from app.core.logging_config import get_logger

logger = get_logger("rule_learner")


def _build_meta_prompt(
    text: str,
    wrong_intents: list,
    correct_intents: list,
) -> str:
    """构建元分析prompt：让LLM对比错误与正确结果，总结规则+示例"""
    wrong_str = json.dumps(wrong_intents, ensure_ascii=False, indent=2)
    correct_str = json.dumps(correct_intents, ensure_ascii=False, indent=2)

    return f"""你是Prompt规则优化助手。下面是一条交易信号消息，模型第一次分析的错误结果，以及人工纠正后的正确结果。
请对比错误与正确结果的差异，总结出一条【简短、可复用】的规则，帮助模型以后遇到类似消息时分析正确。

规则风格参考（简洁、带⚠️强调、可带示例）：
- "⚠️ 开仓计划完整性规则：消息同时有进场指令+止盈目标时，必须作为单个open_position返回，不要把止盈X%拆成close_position"
- "示例:\"...\" → open_position, take_profit=[...]"

要求：
1. 规则必须针对【本次错误的具体原因】，不要泛泛而谈
2. 规则里必须包含一个简短的示例（用本次消息的关键特征）
3. 只返回JSON，不要其他文字

=== 原始消息 ===
{text}

=== 模型错误结果 ===
{wrong_str}

=== 正确结果(人工纠正) ===
{correct_str}

=== 你的输出(只返回JSON) ===
{{
  "rule": "⚠️ ...(规则正文，含示例)",
  "reason": "简短说明这条规则解决什么问题",
  "example_input": "用于示例的消息文本(可直接用原始消息或精简版)",
  "example_output": "{{正确结果的JSON字符串，用作few-shot示例}}"
}}
"""


async def generate_learning(
    text: str,
    wrong_intents: list,
    correct_intents: list,
) -> Dict[str, Any]:
    """
    调用LLM生成规则+示例

    Returns:
        dict: {success, rule?, example_input?, example_output?, reason?, error?}
    """
    llm_cfg = config.get_section("llm_analysis")
    api_key = llm_cfg.get("api_key", "")
    if not api_key:
        return {"success": False, "error": "LLM API Key未配置"}

    api_base = llm_cfg.get("api_base", "https://api.openai.com/v1").rstrip("/")
    model = llm_cfg.get("model", "gpt-4o-mini")
    timeout = llm_cfg.get("timeout", 90)

    prompt = _build_meta_prompt(text, wrong_intents, correct_intents)

    url = f"{api_base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 800,
    }
    thinking = llm_cfg.get("thinking", False)
    if thinking is False:
        body["thinking"] = {"type": "disabled"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=body)

        if resp.status_code != 200:
            detail = ""
            try:
                detail = resp.json().get("error", {}).get("message", "")
            except Exception:
                detail = resp.text[:200]
            return {"success": False, "error": f"LLM API返回 {resp.status_code}: {detail}"}

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return {"success": False, "error": "LLM返回空choices"}

        content = choices[0].get("message", {}).get("content", "")
        parsed = _parse_meta_response(content)
        if parsed is None:
            return {"success": False, "error": "无法解析LLM返回的规则JSON", "raw": content[:500]}

        # 兜底：example_output 未提供时，直接用 correct_intents
        if not parsed.get("example_output"):
            parsed["example_output"] = json.dumps(correct_intents, ensure_ascii=False)
        if not parsed.get("example_input"):
            parsed["example_input"] = text[:200]

        parsed["success"] = True
        return parsed

    except Exception as e:
        logger.error(f"规则学习LLM调用失败: {e}")
        return {"success": False, "error": f"调用失败: {e}"}


def _parse_meta_response(content: str) -> Optional[dict]:
    """解析LLM返回的JSON（兼容markdown代码块）"""
    json_str = None

    match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", content)
    if match:
        json_str = match.group(1).strip()

    if not json_str:
        start = content.find("{")
        if start >= 0:
            brace = 0
            for i in range(start, len(content)):
                if content[i] == "{":
                    brace += 1
                elif content[i] == "}":
                    brace -= 1
                if brace == 0:
                    json_str = content[start:i + 1]
                    break

    if not json_str:
        return None

    json_str = re.sub(r",\s*}", "}", json_str)
    json_str = re.sub(r",\s*]", "]", json_str)

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None
