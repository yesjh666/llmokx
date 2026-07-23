#!/usr/bin/env python3
"""
Prompt助手服务 - 对话式生成规则/Prompt
用户用自然语言描述需求 → LLM生成可直接入库的规则或System Prompt
"""
import re
import json
from typing import Dict, Any, List

import httpx

from app import config
from app.core.logging_config import get_logger

logger = get_logger("prompt_assistant")


SYSTEM_PROMPT = """你是 LLMOKX 交易信号分析系统的「规则与提示词助手」。
用户会用自然语言描述：分析中遇到的问题、想要的识别行为、或新的交易信号模式。你帮用户把它转化为可入库的内容。

根据用户选择的 target 生成：
- target=rule：生成一条「规则」，风格仿照现有规则（简洁、可带⚠️强调、带短示例）。规则要针对用户描述的具体场景，可复用。
- target=prompt：生成/优化「System Prompt」片段或整体建议。

回复格式（严格遵守）：
1. 先用1-2句中文简要说明你生成的内容和理由
2. 然后输出一个 JSON 代码块，包含最终建议：
   - 规则: ```json {"rule": "规则正文", "reason": "简短理由"} ```
   - Prompt: ```json {"prompt": "建议的system_prompt文本"} ```
3. 如果用户的描述还不够生成（信息不足），直接追问，不要输出JSON块。

注意：规则正文里如需引用价格/币种示例，尽量用占位符或常见币种(BTC/ETH)。"""


def _build_messages(user_messages: List[dict], target: str) -> List[dict]:
    """组装发给LLM的messages"""
    sys = SYSTEM_PROMPT.replace(
        "根据用户选择的 target 生成：",
        f"当前用户选择的 target = {target}（rule=规则，prompt=System Prompt）。据此生成对应内容。\n根据用户选择的 target 生成：",
    )
    msgs = [{"role": "system", "content": sys}]
    for m in (user_messages or [])[-10:]:  # 最近10条上下文
        role = m.get("role", "user")
        content = m.get("content", "")
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    return msgs


def _parse_suggestion(content: str, target: str) -> Dict[str, Any]:
    """从LLM回复中解析JSON建议块"""
    match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", content)
    json_str = match.group(1).strip() if match else None
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
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return None
    if target == "rule" and data.get("rule"):
        return {"type": "rule", "rule": data["rule"], "reason": data.get("reason", "")}
    if target == "prompt" and data.get("prompt"):
        return {"type": "prompt", "prompt": data["prompt"]}
    return None


async def chat_generate(messages: List[dict], target: str = "rule") -> Dict[str, Any]:
    """
    对话式生成规则/Prompt

    Args:
        messages: 对话历史 [{role, content}]
        target: "rule" 或 "prompt"

    Returns:
        dict: {success, reply, suggestion?, error?}
    """
    llm_cfg = config.get_section("llm_analysis")
    api_key = llm_cfg.get("api_key", "")
    if not api_key:
        return {"success": False, "error": "LLM API Key未配置，请先在LLM分析页配置"}

    api_base = llm_cfg.get("api_base", "https://api.openai.com/v1").rstrip("/")
    model = llm_cfg.get("model", "gpt-4o-mini")
    timeout = llm_cfg.get("timeout", 90)

    url = f"{api_base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": _build_messages(messages, target),
        "temperature": 0.4,
        "max_tokens": 1000,
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

        content = choices[0].get("message", {}).get("content", "").strip()
        if not content:
            return {"success": False, "error": "LLM返回空回复"}

        suggestion = _parse_suggestion(content, target)
        return {
            "success": True,
            "reply": content,
            "suggestion": suggestion,
        }
    except Exception as e:
        logger.error(f"助手对话失败: {e}")
        return {"success": False, "error": f"调用失败: {e}"}
