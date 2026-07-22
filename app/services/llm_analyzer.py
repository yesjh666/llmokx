#!/usr/bin/env python3
"""
LLM分析服务 - 直接调用大模型API
支持多种模型提供商：OpenAI兼容接口、Anthropic等
"""
import os
import re
import json
import time
import logging
from typing import Dict, Any, List, Optional

import httpx

from app import config
from app.services import prompt_manager
from app.core.logging_config import get_logger, log_llm_analysis

logger = get_logger("llm_analysis")


class LLMAnalyzer:
    """LLM意图分析器，直接调用大模型API"""

    def __init__(self):
        self.config = config.get_section("llm_analysis")

    def _refresh_config(self):
        """刷新配置"""
        self.config = config.get_section("llm_analysis")

    def is_enabled(self) -> bool:
        """检查LLM分析功能是否启用"""
        self._refresh_config()
        return self.config.get("enabled", True)

    async def analyze_intent(
        self,
        text: str,
        context_str: str = "无持仓无挂单",
    ) -> Dict[str, Any]:
        """
        分析消息意图

        Args:
            text: 待分析的消息文本
            context_str: 上下文信息

        Returns:
            dict: {
                "success": bool,
                "intents": list,
                "raw_response": str,
                "elapsed": float,
                "error": str (失败时)
            }
        """
        self._refresh_config()

        if not self.is_enabled():
            return {
                "success": False,
                "intents": [],
                "error": "LLM分析功能已禁用",
                "elapsed": 0,
            }

        if not text or not text.strip():
            return {
                "success": False,
                "intents": [],
                "error": "消息文本为空",
                "elapsed": 0,
            }

        # 构建prompt
        prompt = prompt_manager.build_prompt(text, context_str)

        # 调用LLM（带重试）
        max_retries = self.config.get("max_retries", 2)
        model = self.config.get("model", "gpt-4o-mini")
        fallback_model = self.config.get("fallback_model", "gpt-3.5-turbo")

        start_time = time.time()
        last_error = None
        llm_content = None
        actual_retries = 0
        used_model = model

        for attempt in range(max_retries):
            current_model = model
            if attempt > 0:
                actual_retries = attempt
                logger.info(f"LLM第{attempt + 1}次重试,切换到备用模型: {fallback_model}")
                current_model = fallback_model
                used_model = fallback_model

            try:
                content = await self._call_llm_api(current_model, prompt)
                if content:
                    llm_content = content
                    break
            except Exception as e:
                last_error = str(e)
                logger.warning(f"LLM调用失败(第{attempt + 1}次): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)

        elapsed = time.time() - start_time

        if not llm_content:
            error_msg = f"LLM调用失败(重试{max_retries}次): {last_error}"
            log_llm_analysis(
                text=text, context=context_str, success=False,
                model=used_model, elapsed=elapsed, error=error_msg,
                retries=actual_retries,
            )
            return {
                "success": False,
                "intents": [],
                "raw_response": "",
                "elapsed": elapsed,
                "error": error_msg,
            }

        # 解析响应
        intents_list, parse_error = self._parse_response(llm_content)

        if intents_list is None:
            error_msg = parse_error or "JSON解析失败"
            log_llm_analysis(
                text=text, context=context_str, success=False,
                model=used_model, elapsed=elapsed, error=error_msg,
                raw_response=llm_content, retries=actual_retries,
            )
            return {
                "success": False,
                "intents": [],
                "raw_response": llm_content,
                "elapsed": elapsed,
                "error": error_msg,
            }

        # 成功
        log_llm_analysis(
            text=text, context=context_str, success=True,
            intents=intents_list, model=used_model, elapsed=elapsed,
            raw_response=llm_content, retries=actual_retries,
        )
        return {
            "success": True,
            "intents": intents_list,
            "raw_response": llm_content,
            "elapsed": round(elapsed, 2),
            "error": None,
        }

    async def _call_llm_api(self, model: str, prompt: str) -> Optional[str]:
        """调用大模型API（OpenAI兼容接口）"""
        api_base = self.config.get("api_base", "https://api.openai.com/v1").rstrip("/")
        api_key = self.config.get("api_key", "")
        timeout = self.config.get("timeout", 90)
        temperature = self.config.get("temperature", 0.3)
        max_tokens = self.config.get("max_tokens", 800)
        system_prompt = prompt_manager.load_prompts().get("system_prompt", "")

        if not api_key:
            raise ValueError("API Key未配置，请在设置中填写")

        url = f"{api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt} if system_prompt else None,
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        body["messages"] = [m for m in body["messages"] if m is not None]

        # 思考模式控制（GLM-5.x 等推理模型）
        thinking = self.config.get("thinking", False)
        if thinking is False:
            body["thinking"] = {"type": "disabled"}

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=body)

        if resp.status_code != 200:
            error_detail = ""
            try:
                error_data = resp.json()
                error_detail = error_data.get("error", {}).get("message", "")
            except Exception:
                error_detail = resp.text[:200]
            raise ValueError(f"API返回 {resp.status_code}: {error_detail}")

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise ValueError(f"API返回空choices: {json.dumps(data, ensure_ascii=False)[:500]}")

        message = choices[0].get("message", {})
        content = message.get("content", "")
        finish_reason = choices[0].get("finish_reason", "")

        # GLM 等模型有 reasoning_content 字段（思考过程），先消耗 token 再输出 content
        # 如果 content 为空但 finish_reason=length，说明 token 被思考过程用完了
        if not content and finish_reason == "length":
            reasoning = message.get("reasoning_content", "")
            if reasoning:
                raise ValueError(
                    f"max_tokens 不足：模型思考过程消耗了全部 token（reasoning_content 长度={len(reasoning)}），"
                    f"请增大 max_tokens 设置（当前={self.config.get('max_tokens', 800)}，建议 2000+）"
                )

        if not content:
            raise ValueError(
                f"API返回空content (finish_reason={finish_reason}), "
                f"完整响应: {json.dumps(data, ensure_ascii=False)[:500]}"
            )

        logger.info(f"LLM返回成功: content长度={len(content)}, finish_reason={finish_reason}")
        return content.strip()

    def _parse_response(self, content: str) -> tuple:
        """
        解析LLM返回的JSON

        Returns:
            tuple: (intents_list, error_message)
        """
        json_str = None

        # 方法1: markdown代码块
        match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", content)
        if match:
            json_str = match.group(1).strip()

        # 方法2: 匹配最外层完整 {}
        if not json_str:
            start = content.find("{")
            if start >= 0:
                brace_count = 0
                for i in range(start, len(content)):
                    if content[i] == "{":
                        brace_count += 1
                    elif content[i] == "}":
                        brace_count -= 1
                    if brace_count == 0:
                        json_str = content[start:i + 1]
                        break

        if not json_str:
            return None, "未找到JSON内容"

        # 修复常见JSON问题
        json_str = re.sub(r",\s*}", "}", json_str)
        json_str = re.sub(r",\s*]", "]", json_str)

        try:
            result = json.loads(json_str)
        except json.JSONDecodeError as e:
            return None, f"JSON解析失败: {e}"

        # 处理多意图格式
        intents_list = result.get("intents", [])
        if not intents_list:
            # 兼容单意图格式
            intents_list = [result]

        return intents_list, None

    def test_connection(self) -> Dict[str, Any]:
        """测试LLM连接（同步版本，用于API检查）"""
        self._refresh_config()
        api_key = self.config.get("api_key", "")
        api_base = self.config.get("api_base", "")
        model = self.config.get("model", "")

        if not api_key:
            return {"success": False, "error": "API Key未配置"}
        if not api_base:
            return {"success": False, "error": "API Base未配置"}
        if not model:
            return {"success": False, "error": "模型未配置"}

        return {
            "success": True,
            "info": {
                "provider": self.config.get("provider", "openai"),
                "api_base": api_base,
                "model": model,
                "fallback_model": self.config.get("fallback_model", ""),
            },
        }


# 全局单例
analyzer = LLMAnalyzer()
