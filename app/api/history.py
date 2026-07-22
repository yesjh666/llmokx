#!/usr/bin/env python3
"""分析历史 + 学习 API"""
import json
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services import analysis_history, prompt_manager, rule_learner

router = APIRouter()


class LearnRequest(BaseModel):
    """纠错学习请求"""
    correct_intents: List[dict]          # 人工纠正后的正确意图
    generate_rule: bool = True           # 是否调LLM生成规则（False=仅存示例）
    description: str = ""                # 规则/示例说明


@router.get("")
async def list_history(
    limit: int = Query(50, ge=1, le=500),
    unlearned: bool = Query(False, description="只看未学习的"),
):
    """获取分析历史列表"""
    records = analysis_history.list_records(limit=limit, only_unlearned=unlearned)
    return {
        "records": records,
        "stats": analysis_history.stats(),
    }


@router.get("/{record_id}")
async def get_history(record_id: str):
    """获取单条历史"""
    rec = analysis_history.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")
    return rec


@router.delete("/{record_id}")
async def delete_history(record_id: str):
    """删除单条历史"""
    ok = analysis_history.delete_record(record_id)
    return {"success": ok}


@router.delete("")
async def clear_history():
    """清空全部历史"""
    count = analysis_history.clear()
    return {"success": True, "deleted": count}


@router.post("/{record_id}/learn")
async def learn_from_history(record_id: str, req: LearnRequest):
    """
    从历史记录学习：
    1. 把「正确意图」存为 few-shot 示例
    2. (可选) 调LLM对比错误结果，自动生成规则
    3. 标记该记录为已学习
    """
    rec = analysis_history.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")

    if not req.correct_intents:
        raise HTTPException(status_code=400, detail="correct_intents 不能为空")

    text = rec.get("text", "")
    wrong_intents = rec.get("intents", [])
    desc = req.description or f"从历史记录学习({rec.get('time', '')})"

    result = {
        "success": True,
        "example_saved": False,
        "rule_generated": False,
        "rule": None,
        "rule_reason": None,
    }

    # 1. 存示例（用正确结果）
    example_output = json.dumps(req.correct_intents, ensure_ascii=False)
    if len(req.correct_intents) == 1:
        # 单意图：示例输出用对象形式（与现有示例风格一致）
        example_output = json.dumps(req.correct_intents[0], ensure_ascii=False)
    ex_ok = prompt_manager.add_example(
        input_text=text,
        output_json=example_output,
        description=desc,
    )
    result["example_saved"] = ex_ok

    # 2. 可选：调LLM生成规则
    if req.generate_rule:
        learn = await rule_learner.generate_learning(text, wrong_intents, req.correct_intents)
        if learn.get("success"):
            rule_text = learn.get("rule", "").strip()
            reason = learn.get("reason", "").strip()
            if rule_text:
                rl_ok = prompt_manager.add_rule(
                    rule=rule_text,
                    priority=0,
                    description=f"{desc} | {reason}" if reason else desc,
                    enabled=True,
                )
                result["rule_generated"] = rl_ok
                result["rule"] = rule_text
                result["rule_reason"] = reason
        else:
            result["rule_error"] = learn.get("error", "生成失败")

    # 3. 标记已学习
    analysis_history.mark_learned(record_id, True)

    return result
