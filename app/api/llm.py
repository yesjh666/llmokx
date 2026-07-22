#!/usr/bin/env python3
"""LLM分析相关API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app import config
from app.services import llm_analyzer, prompt_manager, analysis_history

router = APIRouter()


class AnalyzeRequest(BaseModel):
    text: str
    context: str = "无持仓无挂单"


class AddRuleRequest(BaseModel):
    rule: str
    priority: int = 0
    description: str = ""
    enabled: bool = True


class AddExampleRequest(BaseModel):
    input_text: str
    output_json: str
    description: str = ""


class UpdateConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[str] = None
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    model: Optional[str] = None
    fallback_model: Optional[str] = None
    max_retries: Optional[int] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout: Optional[int] = None
    thinking: Optional[bool] = None


@router.post("/analyze")
async def analyze_message(req: AnalyzeRequest):
    """分析消息意图"""
    result = await llm_analyzer.analyzer.analyze_intent(req.text, req.context)
    # 记录分析历史（供学习中心纠错）
    if result.get("success"):
        try:
            hid = analysis_history.add_record(
                text=req.text,
                context=req.context,
                intents=result.get("intents", []),
                source="manual",
                elapsed=result.get("elapsed", 0),
            )
            result["history_id"] = hid
        except Exception:
            pass
    return result


@router.get("/config")
async def get_config():
    """获取LLM配置"""
    cfg = config.get_section("llm_analysis")
    # 隐藏API Key的完整值
    if cfg.get("api_key"):
        cfg = dict(cfg)
        cfg["api_key_masked"] = cfg["api_key"][:8] + "****" + cfg["api_key"][-4:]
        cfg["api_key_configured"] = True
    return cfg


@router.put("/config")
async def update_config(req: UpdateConfigRequest):
    """更新LLM配置"""
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="没有要更新的字段")
    success = config.update_section("llm_analysis", data)
    return {"success": success, "message": "配置已更新" if success else "更新失败"}


@router.post("/test-connection")
async def test_connection():
    """测试LLM连接配置"""
    result = await llm_analyzer.analyzer.test_connection()
    return result


@router.get("/prompts")
async def get_prompts():
    """获取prompt配置"""
    prompts = prompt_manager.load_prompts()
    stats = prompt_manager.get_stats()
    return {"prompts": prompts, "stats": stats}


@router.post("/prompts/reload")
async def reload_prompts():
    """重新加载prompt配置"""
    prompts = prompt_manager.reload_prompts()
    stats = prompt_manager.get_stats()
    return {"success": True, "stats": stats}


@router.post("/prompts/rules")
async def add_rule(req: AddRuleRequest):
    """添加自定义规则"""
    success = prompt_manager.add_rule(
        rule=req.rule,
        priority=req.priority,
        description=req.description,
        enabled=req.enabled,
    )
    return {"success": success, "message": "规则已添加" if success else "添加失败"}


@router.delete("/prompts/rules/{index}")
async def delete_rule(index: int):
    """删除自定义规则"""
    success = prompt_manager.remove_rule(index)
    return {"success": success, "message": "规则已删除" if success else "删除失败"}


@router.put("/prompts/rules/{index}/toggle")
async def toggle_rule(index: int, enabled: bool = True):
    """启用/禁用自定义规则"""
    success = prompt_manager.toggle_rule(index, enabled)
    return {"success": success}


@router.post("/prompts/examples")
async def add_example(req: AddExampleRequest):
    """添加自定义示例"""
    success = prompt_manager.add_example(
        input_text=req.input_text,
        output_json=req.output_json,
        description=req.description,
    )
    return {"success": success, "message": "示例已添加" if success else "添加失败"}


@router.delete("/prompts/examples/{index}")
async def delete_example(index: int):
    """删除自定义示例"""
    success = prompt_manager.remove_example(index)
    return {"success": success, "message": "示例已删除" if success else "删除失败"}
