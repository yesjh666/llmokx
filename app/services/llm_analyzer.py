#!/usr/bin/env python3
"""
LLM分析服务 - 直接调用大模型API
支持多种模型提供商：OpenAI兼容接口、Anthropic等
"""
import os
import re
import json
import time
import hmac
import base64
import asyncio
import hashlib
import logging
from typing import Dict, Any, List, Optional

import httpx

from app import config
from app.services import prompt_manager
from app.core.logging_config import get_logger, log_llm_analysis

logger = get_logger("llm_analysis")


# ==================== GLM (智谱AI) JWT Token 自动管理 ====================

_glm_token_cache: Dict[str, tuple] = {}  # {raw_key: (jwt_token, exp_timestamp_ms)}


def _is_glm_api(api_base: str) -> bool:
    """判断是否为 GLM (智谱AI) API"""
    b = (api_base or "").lower()
    return any(x in b for x in ("bigmodel", "zhipu", "chatglm"))


def clear_glm_token_cache():
    """清除 JWT token 缓存（配置变更时调用）"""
    _glm_token_cache.clear()


def _get_glm_token(api_key: str) -> str:
    """
    将 GLM 的 {id}.{secret} 格式密钥转为 JWT token。
    JWT 有效期 1 小时，提前 5 分钟刷新。
    如果不是 GLM 格式则原样返回。

    注意：exp 和 timestamp 必须用毫秒（与官方 zhipuai SDK 一致），
    否则 GLM 会判定 token 已过期返回 401。
    """
    if not api_key or "." not in api_key:
        return api_key

    now_ms = int(round(time.time() * 1000))
    exp_ms = now_ms + 3600 * 1000  # 1小时后（毫秒）

    cached = _glm_token_cache.get(api_key)
    if cached:
        token, cached_exp = cached
        if now_ms < cached_exp - 300 * 1000:  # 提前5分钟刷新
            return token

    try:
        key_id, secret = api_key.split(".", 1)
    except ValueError:
        return api_key

    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = _b64url(json.dumps({"alg": "HS256", "sign_type": "SIGN"}).encode())
    payload = _b64url(json.dumps({
        "api_key": key_id,
        "exp": exp_ms,
        "timestamp": now_ms,
    }).encode())
    msg = f"{header}.{payload}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    token = f"{msg}.{_b64url(sig)}"

    _glm_token_cache[api_key] = (token, exp_ms)
    logger.info(f"GLM JWT token 已刷新，有效期至 {time.strftime('%H:%M:%S', time.localtime(exp_ms / 1000))}")
    return token


def _get_auth_token(api_key: str, api_base: str) -> str:
    """根据 API 类型返回认证 token（GLM 自动转 JWT）"""
    if _is_glm_api(api_base):
        token = _get_glm_token(api_key)
        if token != api_key:
            logger.debug(f"GLM JWT 已生成: raw_key长度={len(api_key)}, token长度={len(token)}")
        else:
            logger.debug(f"GLM 原始key直传(无dot): key长度={len(api_key)}")
        return token
    return api_key


def _clean_key(key: str) -> str:
    """
    清理 API key：移除所有非可见字符（换行、制表符、零宽字符、BOM等）。
    API key 应只包含可打印 ASCII 字符。
    """
    if not key:
        return ""
    cleaned = "".join(c for c in key if c.isprintable() and ord(c) < 128).strip()
    if cleaned != key:
        import hashlib
        logger.warning(
            f"API key 含非可见字符! 原始长度={len(key)}, 清理后长度={len(cleaned)}, "
            f"原始指纹={hashlib.md5(key.encode()).hexdigest()[:8]}, "
            f"清理后指纹={hashlib.md5(cleaned.encode()).hexdigest()[:8]}"
        )
    return cleaned


# 配置文件路径
_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
)
_CONFIG_FILE = os.path.join(_CONFIG_DIR, "unified-config.json")
_API_KEY_FILE = os.path.join(_CONFIG_DIR, ".api_key")


def _save_api_key_to_private_file(key: str):
    """将 api_key 单独保存到 .api_key 文件（防止被 config.save_config 覆盖）"""
    try:
        with open(_API_KEY_FILE, "w", encoding="utf-8") as f:
            f.write(key)
    except Exception as e:
        logger.warning(f"保存 .api_key 失败: {e}")


def _read_api_key_from_disk() -> tuple:
    """读取 api_key 和 api_base（优先从 .api_key 独立文件读取）"""
    api_key = ""
    api_base = ""
    # 1. 优先从独立文件读取 api_key（不被任何其他操作覆盖）
    try:
        if os.path.exists(_API_KEY_FILE):
            with open(_API_KEY_FILE, "r", encoding="utf-8-sig") as f:
                api_key = f.read().strip()
    except Exception:
        pass
    # 2. 从统一配置读取（api_base + 兜底 api_key）
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
        llm = cfg.get("llm_analysis", {})
        if not api_key:
            api_key = llm.get("api_key", "")
        api_base = llm.get("api_base", "")
    except Exception as e:
        logger.warning(f"从磁盘读取配置失败: {e}")
    return api_key, api_base


class LLMAnalyzer:
    """LLM意图分析器，直接调用大模型API"""

    def __init__(self):
        self.config = config.get_section("llm_analysis")
        self._model_status: Optional[dict] = None
        self._http_client: Optional[httpx.AsyncClient] = None

    def _get_client(self, timeout: int = 90, fresh: bool = False) -> httpx.AsyncClient:
        """获取 HTTP 客户端。fresh=True 时创建一次性客户端（coding plan 用）"""
        if fresh:
            return httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=10),
                headers={"User-Agent": "LLMOKX/1.0"},
            )
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=10),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                headers={"User-Agent": "LLMOKX/1.0"},
            )
        return self._http_client

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

        # 构建故障转移链：主模型 → 备用模型(fallback) → backup_models 列表
        max_retries = self.config.get("max_retries", 2)
        model = self.config.get("model", "gpt-4o-mini")
        fallback_model = self.config.get("fallback_model", "gpt-3.5-turbo")
        # ⚠️ api_key 和 api_base 每次从磁盘直接读取（绕过缓存，保证与保存时一致）
        disk_key, disk_base = _read_api_key_from_disk()
        api_key = _clean_key(disk_key) if disk_key else _clean_key(self.config.get("api_key", ""))
        api_base = (disk_base or "").strip() if disk_base else self.config.get("api_base", "https://api.openai.com/v1")
        thinking = self.config.get("thinking", False)
        global_temp = self.config.get("temperature", 0.3)

        chain = [{
            "model": model, "api_base": api_base, "api_key": api_key,
            "thinking": thinking, "label": model, "temperature": global_temp,
        }]
        if fallback_model and fallback_model != model:
            chain.append({
                "model": fallback_model, "api_base": api_base, "api_key": api_key,
                "thinking": thinking, "label": fallback_model, "temperature": global_temp,
            })
        for bk in (self.config.get("backup_models") or []):
            if bk.get("model") and bk.get("api_base"):
                bk_model = (bk.get("model", "") or "").strip()
                bk_base = (bk.get("api_base", "") or "").strip()
                bk_key = _clean_key(bk.get("api_key", "")) or api_key
                bk_thinking = bk.get("thinking", thinking)
                bk_temp = bk.get("temperature")
                try:
                    bk_temp = float(bk_temp) if bk_temp is not None else global_temp
                except (TypeError, ValueError):
                    bk_temp = global_temp
                chain.append({
                    "model": bk["model"],
                    "api_base": bk["api_base"],
                    "api_key": bk.get("api_key") or api_key,
                    "thinking": bk.get("thinking", thinking),
                    "label": bk.get("name") or bk["model"],
                    "temperature": bk_temp,
                })

        # 故障转移：依次尝试 整条链 的每个模型（主→fallback→backup_models）
        # max_retries = 单个模型遇到瞬时错误时的重试次数；认证类错误(401/403)直接换下一个模型
        per_model_retries = max(1, max_retries)

        start_time = time.time()
        last_error = None
        llm_content = None
        actual_retries = 0
        used_model = chain[0]["label"] if chain else model
        chain_errors = []   # 每个模型的错误明细
        failed_auth_pairs = set()  # (api_key, api_base) 已 401 失败的凭证对

        for idx, mcfg in enumerate(chain):
            # 优化：如果此模型的凭证(key+base)已因401失败，直接跳过（同凭证必定同结果）
            pair = (mcfg["api_key"], mcfg["api_base"])
            if pair in failed_auth_pairs:
                logger.info(f"模型 {mcfg['label']} 凭证与前序401失败相同，跳过")
                chain_errors.append({"model": mcfg["label"], "error": "同凭证已401，跳过"})
                continue

            success_here = False
            for r in range(per_model_retries):
                try:
                    content = await self._call_llm_api(
                        prompt, mcfg["model"], mcfg["api_base"], mcfg["api_key"],
                        mcfg["thinking"], mcfg["temperature"],
                    )
                    llm_content = content
                    used_model = mcfg["label"]
                    success_here = True
                    if idx > 0:
                        actual_retries = idx
                        logger.info(f"LLM故障转移成功(第{idx + 1}个模型): {mcfg['label']}")
                    break
                except Exception as e:
                    last_error = str(e)
                    actual_retries = idx
                    err_lower = str(e).lower()
                    is_auth_err = any(x in err_lower for x in (
                        "401", "403", "unauthorized", "令牌", "认证", "鉴权", "forbidden",
                    ))
                    logger.warning(f"LLM调用失败(模型{idx + 1}/{len(chain)} {mcfg['label']}, 重试{r + 1}/{per_model_retries}): {e}")
                    if is_auth_err:
                        # 记录此凭证对已401失败，后续同凭证的模型直接跳过
                        failed_auth_pairs.add(pair)
                    # 认证类错误也重试（可能是瞬时token刷新/限流），重试耗尽才换下一个模型
                    if r < per_model_retries - 1:
                        await asyncio.sleep(1 if not is_auth_err else 2)
            if not success_here:
                chain_errors.append({"model": mcfg["label"], "error": last_error})
                if idx < len(chain) - 1:
                    logger.info(f"模型 {mcfg['label']} 重试耗尽，切换到下一个模型")
            else:
                break

        elapsed = time.time() - start_time

        if not llm_content:
            error_msg = f"LLM调用失败(已尝试全部{len(chain)}个模型): {last_error}"
            logger.error(f"故障转移全部失败: {chain_errors}")
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
                "chain_errors": chain_errors,
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
            "model": used_model,
            "error": None,
        }

    async def _call_llm_api(
        self, prompt: str, model: str, api_base: str, api_key: str, thinking: bool,
        temperature: float = 0.3,
    ) -> Optional[str]:
        """调用大模型API（OpenAI兼容接口）- 使用传入的连接参数"""
        model = (model or "").strip()
        api_base = (api_base or "").strip()
        api_key = _clean_key(api_key)

        timeout = self.config.get("timeout", 90)
        max_tokens = self.config.get("max_tokens", 800)
        system_prompt = prompt_manager.load_prompts().get("system_prompt", "")

        if not api_key:
            raise ValueError("API Key未配置，请在设置中填写")

        auth_token = _get_auth_token(api_key, api_base)
        url = f"{api_base.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {auth_token}",
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
        if thinking is False:
            body["thinking"] = {"type": "disabled"}

        # coding plan 端点：每次请求用全新连接（模拟初始状态，避免累积触发限制）
        is_coding = "/coding/" in api_base
        if is_coding:
            clear_glm_token_cache()

        client = self._get_client(timeout, fresh=is_coding)
        try:
            resp = await client.post(url, headers=headers, json=body)

            # GLM 401 自动恢复
            if resp.status_code in (401, 403) and _is_glm_api(api_base):
                jwt_was_used = auth_token != api_key

                if jwt_was_used:
                    logger.info("GLM 401: JWT失败，尝试原始key直传")
                    clear_glm_token_cache()
                    headers["Authorization"] = f"Bearer {api_key}"
                    await asyncio.sleep(1)
                    resp = await client.post(url, headers=headers, json=body)

                if resp.status_code in (401, 403) and "/coding/" in api_base:
                    fallback_url = url.replace("/coding/paas/v4", "/paas/v4")
                    logger.info(f"GLM 401: coding端点被拒，尝试标准端点")
                    resp = await client.post(fallback_url, headers=headers, json=body)

                if resp.status_code in (401, 403):
                    logger.info("GLM 401: 等待2秒后重试")
                    await asyncio.sleep(2)
                    resp = await client.post(url, headers=headers, json=body)
        finally:
            if is_coding:
                await client.aclose()

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

    async def _test_model(
        self, api_base: str, api_key: str, model: str, thinking: bool
    ) -> Dict[str, Any]:
        """测试单个模型配置的连通性（发送一条简短消息）"""
        api_base = (api_base or "").strip()
        api_key = _clean_key(api_key)
        model = (model or "").strip()

        if not api_key:
            return {"success": False, "error": "API Key未配置"}
        if not api_base:
            return {"success": False, "error": "API Base未配置"}
        if not model:
            return {"success": False, "error": "模型未配置"}

        auth_token = _get_auth_token(api_key, api_base)
        url = f"{api_base.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "回复OK"}],
            "max_tokens": 10,
        }
        if thinking is False:
            body["thinking"] = {"type": "disabled"}

        try:
            is_coding = "/coding/" in api_base
            if is_coding:
                clear_glm_token_cache()
            client = self._get_client(30, fresh=is_coding)
            try:
                resp = await client.post(url, headers=headers, json=body)

                # GLM 401 自动恢复
                if resp.status_code in (401, 403) and _is_glm_api(api_base):
                    jwt_was_used = auth_token != api_key

                    if jwt_was_used:
                        logger.info("GLM 测试401: JWT失败，尝试原始key直传")
                        clear_glm_token_cache()
                        headers["Authorization"] = f"Bearer {api_key}"
                        await asyncio.sleep(1)
                        resp = await client.post(url, headers=headers, json=body)

                    if resp.status_code in (401, 403) and "/coding/" in api_base:
                        fallback_url = url.replace("/coding/paas/v4", "/paas/v4")
                        logger.info(f"GLM 测试401: coding端点被拒，尝试标准端点")
                        resp = await client.post(fallback_url, headers=headers, json=body)

                    if resp.status_code in (401, 403):
                        logger.info("GLM 测试401: 等待2秒后重试")
                        await asyncio.sleep(2)
                        resp = await client.post(url, headers=headers, json=body)
            finally:
                if is_coding:
                    await client.aclose()

            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                content = ""
                if choices:
                    msg = choices[0].get("message", {})
                    content = msg.get("content", "") or msg.get("reasoning_content", "")[:20]

                return {
                    "success": True,
                    "info": {
                        "model": model,
                        "api_base": api_base,
                        "response": content[:50] if content else "(空回复)",
                    },
                }
            else:
                detail = ""
                try:
                    err_data = resp.json()
                    detail = err_data.get("error", {}).get("message", "") or str(err_data)[:200]
                except Exception:
                    detail = resp.text[:200]

                # 认证类错误：附带实际使用的key/api_base（脱敏），方便排查
                if resp.status_code in (401, 403):
                    masked = (api_key[:6] + "..." + api_key[-4:]) if len(api_key) > 12 else "***"
                    has_dot = "." in api_key
                    hint = (f" | key={masked}(长度{len(api_key)},{'含dot→已尝试JWT+原始key' if has_dot else '无dot→直传'}) "
                            f"base={api_base} model={model}")
                    detail = detail + hint
                return {
                    "success": False,
                    "error": f"HTTP {resp.status_code}: {detail}",
                }
        except Exception as e:
            return {"success": False, "error": f"请求异常: {e}"}

    async def test_connection(self) -> Dict[str, Any]:
        """测试主模型连接（key 从磁盘读取）"""
        self._refresh_config()
        disk_key, disk_base = _read_api_key_from_disk()
        return await self._test_model(
            api_base=disk_base or self.config.get("api_base", ""),
            api_key=disk_key or self.config.get("api_key", ""),
            model=self.config.get("model", ""),
            thinking=self.config.get("thinking", False),
        )

    async def test_model_config(
        self, api_base: str, api_key: str, model: str, thinking: bool = False
    ) -> Dict[str, Any]:
        """测试任意模型配置（用于测试备用模型）"""
        self._refresh_config()
        return await self._test_model(api_base, api_key, model, thinking)

    async def test_all_models(self) -> Dict[str, Any]:
        """测试所有模型连接状态（主模型 + fallback + backup_models）"""
        self._refresh_config()
        results = []

        model = (self.config.get("model", "") or "").strip()
        # key 和 base 从磁盘读取
        disk_key, disk_base = _read_api_key_from_disk()
        api_key = _clean_key(disk_key) if disk_key else _clean_key(self.config.get("api_key", ""))
        api_base = (disk_base or "").strip() if disk_base else (self.config.get("api_base", "") or "").strip()
        thinking = self.config.get("thinking", False)

        primary = await self._test_model(api_base, api_key, model, thinking)
        results.append({
            "role": "primary",
            "label": model or "主模型",
            "model": model,
            "api_base": api_base,
            "success": primary.get("success", False),
            "error": primary.get("error", ""),
            "info": primary.get("info", {}),
        })

        fallback_model = (self.config.get("fallback_model", "") or "").strip()
        if fallback_model and fallback_model != model:
            fb = await self._test_model(api_base, api_key, fallback_model, thinking)
            results.append({
                "role": "fallback",
                "label": fallback_model,
                "model": fallback_model,
                "api_base": api_base,
                "success": fb.get("success", False),
                "error": fb.get("error", ""),
                "info": fb.get("info", {}),
            })

        for bk in (self.config.get("backup_models") or []):
            bk_model = (bk.get("model", "") or "").strip()
            bk_base = (bk.get("api_base", "") or "").strip()
            bk_key = (bk.get("api_key", "") or "").strip() or api_key
            bk_thinking = bk.get("thinking", thinking)
            if not bk_model or not bk_base:
                continue
            bk_result = await self._test_model(bk_base, bk_key, bk_model, bk_thinking)
            results.append({
                "role": "backup",
                "label": bk.get("name") or bk_model,
                "model": bk_model,
                "api_base": bk_base,
                "success": bk_result.get("success", False),
                "error": bk_result.get("error", ""),
                "info": bk_result.get("info", {}),
            })

        self._model_status = {
            "results": results,
            "tested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(results),
            "ok_count": sum(1 for r in results if r["success"]),
        }
        return self._model_status


# 全局单例
analyzer = LLMAnalyzer()
