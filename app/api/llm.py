#!/usr/bin/env python3
"""LLM分析相关API"""
import copy
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from app import config
from app.services import llm_analyzer, prompt_manager, analysis_history, prompt_assistant

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


class BackupModel(BaseModel):
    """备用模型配置"""
    name: str = ""
    api_base: str = ""
    api_key: Optional[str] = None   # None/空 = 不修改已有key（编辑时）
    model: str = ""
    thinking: bool = False
    temperature: Optional[float] = None   # None = 用全局temperature


class TestModelRequest(BaseModel):
    """测试任意模型配置"""
    api_base: str = ""
    api_key: str = ""
    model: str = ""
    thinking: bool = False


class AssistantChatRequest(BaseModel):
    """AI助手对话"""
    messages: List[dict] = []
    target: str = "rule"   # rule | prompt


class UpdateSystemPromptRequest(BaseModel):
    """更新System Prompt"""
    system_prompt: str


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


def _mask_key(key: str) -> str:
    """脱敏 API Key"""
    if not key:
        return ""
    if len(key) <= 12:
        return "****"
    return key[:8] + "****" + key[-4:]


@router.get("/config")
async def get_config():
    """获取LLM配置（API Key脱敏）"""
    cfg = copy.deepcopy(config.get_section("llm_analysis"))
    if cfg.get("api_key"):
        cfg["api_key_masked"] = _mask_key(cfg["api_key"])
        cfg["api_key_configured"] = True
    # 备用模型的 key 也脱敏
    for bk in cfg.get("backup_models", []) or []:
        if bk.get("api_key"):
            bk["api_key_masked"] = _mask_key(bk["api_key"])
            bk["api_key_configured"] = True
        else:
            bk["api_key_configured"] = False
    return cfg


@router.put("/config")
async def update_config(req: UpdateConfigRequest):
    """更新LLM配置"""
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="没有要更新的字段")
    # 清理关键字段的空白（粘贴的key/base带空格/换行会导致401）
    for k in ("api_key", "api_base", "model", "fallback_model"):
        if isinstance(data.get(k), str):
            data[k] = data[k].strip()
    success = config.update_section("llm_analysis", data)
    return {"success": success, "message": "配置已更新" if success else "更新失败"}


@router.post("/test-connection")
async def test_connection():
    """测试LLM连接配置"""
    result = await llm_analyzer.analyzer.test_connection()
    return result


@router.get("/model-status")
async def get_model_status():
    """获取模型连接状态（从缓存读取）"""
    status = getattr(llm_analyzer.analyzer, '_model_status', None)
    if not status:
        return {"results": [], "tested_at": None, "total": 0, "ok_count": 0}
    return status


@router.post("/model-status")
async def refresh_model_status():
    """重新测试所有模型连接"""
    result = await llm_analyzer.analyzer.test_all_models()
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


# ==================== 备用模型管理 ====================

def _get_backup_models() -> list:
    return list(config.get_section("llm_analysis").get("backup_models") or [])


def _save_backup_models(models: list):
    config.update_section("llm_analysis", {"backup_models": models})


@router.get("/models")
async def list_models():
    """获取备用模型列表（key脱敏）"""
    models = copy.deepcopy(_get_backup_models())
    for bk in models:
        if bk.get("api_key"):
            bk["api_key_masked"] = _mask_key(bk["api_key"])
            bk["api_key_configured"] = True
        else:
            bk["api_key_configured"] = False
        bk.pop("api_key", None)
    return {"models": models}


@router.post("/models")
async def add_model(req: BackupModel):
    """添加备用模型"""
    if not req.model or not req.api_base:
        raise HTTPException(status_code=400, detail="model 和 api_base 不能为空")
    models = _get_backup_models()
    models.append({
        "name": (req.name or req.model).strip(),
        "api_base": req.api_base.strip(),
        "api_key": (req.api_key or "").strip(),
        "model": req.model.strip(),
        "thinking": req.thinking,
        "temperature": req.temperature,
    })
    _save_backup_models(models)
    return {"success": True, "message": "备用模型已添加", "count": len(models)}


@router.put("/models/{index}")
async def update_model(index: int, req: BackupModel):
    """更新备用模型（api_key 为空时保留原值）"""
    models = _get_backup_models()
    if index < 0 or index >= len(models):
        raise HTTPException(status_code=404, detail="模型不存在")
    old = models[index]
    models[index] = {
        "name": (req.name or req.model or old.get("name", "")).strip(),
        "api_base": (req.api_base or old.get("api_base", "")).strip(),
        "api_key": (req.api_key if req.api_key else old.get("api_key", "")).strip(),
        "model": (req.model or old.get("model", "")).strip(),
        "thinking": req.thinking,
        "temperature": req.temperature if req.temperature is not None else old.get("temperature"),
    }
    _save_backup_models(models)
    return {"success": True, "message": "备用模型已更新"}


@router.delete("/models/{index}")
async def delete_model(index: int):
    """删除备用模型"""
    models = _get_backup_models()
    if index < 0 or index >= len(models):
        raise HTTPException(status_code=404, detail="模型不存在")
    models.pop(index)
    _save_backup_models(models)
    return {"success": True, "message": "备用模型已删除", "count": len(models)}


@router.post("/models/test")
async def test_model(req: TestModelRequest):
    """测试任意模型配置（不保存，仅测连通性）"""
    result = await llm_analyzer.analyzer.test_model_config(
        api_base=req.api_base,
        api_key=req.api_key,
        model=req.model,
        thinking=req.thinking,
    )
    return result


@router.post("/models/{index}/test")
async def test_stored_model(index: int):
    """测试已保存的备用模型（用存储的key，index 从0开始）"""
    models = _get_backup_models()
    if index < 0 or index >= len(models):
        raise HTTPException(status_code=404, detail="模型不存在")
    m = models[index]
    result = await llm_analyzer.analyzer.test_model_config(
        api_base=m.get("api_base", ""),
        api_key=m.get("api_key", ""),
        model=m.get("model", ""),
        thinking=m.get("thinking", False),
    )
    return result


# ==================== AI助手（对话生成规则/Prompt） ====================

@router.post("/assistant/chat")
async def assistant_chat(req: AssistantChatRequest):
    """对话式生成规则或System Prompt"""
    target = req.target if req.target in ("rule", "prompt") else "rule"
    result = await prompt_assistant.chat_generate(req.messages, target)
    return result


@router.put("/prompts/system")
async def update_system_prompt(req: UpdateSystemPromptRequest):
    """更新 System Prompt（AI助手应用建议时调用）"""
    prompts = prompt_manager.load_prompts()
    prompts["system_prompt"] = req.system_prompt
    ok = prompt_manager.save_prompts(prompts)
    return {"success": ok, "message": "System Prompt 已更新" if ok else "更新失败"}
