#!/usr/bin/env python3
"""转发管理相关API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from app import config
from app.services import intent_forwarder
from app.services.llm_analyzer import analyzer as llm

router = APIRouter()


class ForwardTarget(BaseModel):
    channel: str = "openclaw-telegram"
    target: str = ""
    description: str = ""


class UpdateForwardConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    skip_intents: Optional[List[str]] = None
    telegram_bot_token: Optional[str] = None
    userbot_enabled: Optional[bool] = None


class ForwardTestRequest(BaseModel):
    target: ForwardTarget


class ForwardAndAnalyzeRequest(BaseModel):
    text: str
    context: str = "无持仓无挂单"
    source_chat: str = "API测试"


@router.get("/config")
async def get_config():
    """获取转发配置"""
    cfg = config.get_section("forward")
    cfg = dict(cfg)
    if cfg.get("telegram_bot_token"):
        cfg["bot_token_configured"] = True
        cfg["telegram_bot_token_masked"] = cfg["telegram_bot_token"][:8] + "****"
    else:
        cfg["bot_token_configured"] = False
    return cfg


@router.put("/config")
async def update_config(req: UpdateForwardConfigRequest):
    """更新转发配置"""
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="没有要更新的字段")
    success = config.update_section("forward", data)
    return {"success": success, "message": "配置已更新" if success else "更新失败"}


@router.get("/targets")
async def get_targets():
    """获取转发目标列表"""
    cfg = config.get_section("forward")
    targets = cfg.get("targets", [])
    return {"targets": targets}


@router.post("/targets")
async def add_target(req: ForwardTarget):
    """添加转发目标"""
    cfg = config.get_section("forward")
    targets = cfg.get("targets", [])
    targets.append(req.model_dump())
    success = config.update_section("forward", {"targets": targets})
    return {"success": success, "message": "目标已添加" if success else "添加失败"}


@router.put("/targets/{index}")
async def update_target(index: int, req: ForwardTarget):
    """更新转发目标"""
    cfg = config.get_section("forward")
    targets = cfg.get("targets", [])
    if 0 <= index < len(targets):
        targets[index] = req.model_dump()
        success = config.update_section("forward", {"targets": targets})
        return {"success": success}
    raise HTTPException(status_code=404, detail="目标不存在")


@router.delete("/targets/{index}")
async def delete_target(index: int):
    """删除转发目标"""
    cfg = config.get_section("forward")
    targets = cfg.get("targets", [])
    if 0 <= index < len(targets):
        targets.pop(index)
        success = config.update_section("forward", {"targets": targets})
        return {"success": success}
    raise HTTPException(status_code=404, detail="目标不存在")


@router.post("/test")
async def test_forward(req: ForwardTestRequest):
    """测试转发目标"""
    result = intent_forwarder.forwarder.test_forward(req.model_dump())
    return result


@router.post("/analyze-and-forward")
async def analyze_and_forward(req: ForwardAndAnalyzeRequest):
    """分析消息并转发意图"""
    # 1. 分析意图
    analysis = await llm.analyze_intent(req.text, req.context)
    if not analysis.get("success"):
        return {
            "success": False,
            "stage": "analysis",
            "error": analysis.get("error"),
        }

    # 2. 转发每个意图
    forward_results = []
    for intent in analysis.get("intents", []):
        result = await intent_forwarder.forwarder.forward_intent(
            intent, req.text, req.source_chat
        )
        forward_results.append({
            "intent": intent.get("intent"),
            "result": result,
        })

    return {
        "success": True,
        "analysis": analysis,
        "forward_results": forward_results,
    }
