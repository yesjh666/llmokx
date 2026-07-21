#!/usr/bin/env python3
"""
Telegram 消息意图分析器 v4
基于三马哥历史策略模型优化
支持：开单/平仓/撤单/修改止盈/修改止损/查询/闲聊
"""

import json
import re
import subprocess
import shlex
import sys

LLM_SESSION_ID = "trade-intent-session"

# 意图分类体系
INTENTS = {
    'open_position_long': '开仓做多',
    'open_position_short': '开仓做空',
    'close_position_all': '全部平仓',
    'close_position_partial': '比例平仓',
    'modify_stop_loss': '修改止损',
    'modify_take_profit': '修改止盈',
    'cancel_orders': '撤单',
    'live_stream': '直播通知',
    'chat': '闲聊/分析'
}

# 关键词规则库 (v4 优化版)
RULES = {
    'close_position_partial': {
        'keywords': ['止盈 50%', '止盈 70%', '止盈 30%', '平仓 70%', '落袋 50%', '落袋 70%', 
                    '减仓', '走 50%', '走 70%', '平仓 50%', '总仓位落袋', '跑掉一半',
                    '一半', '分批', '部分止盈', '先跑'],
        'priority': 1
    },
    'close_position_all': {
        'keywords': ['全部止盈', '全平', '全部平仓', '跑了', '出局', '结束战斗', 
                    '完美收官', '全部结束', '全走了', '全部跑完', '止盈睡觉',
                    '止盈了', '平仓了', '止盈全部', '都止盈', '全跑了', '全部出',
                    '清仓', '空仓', '结束'],
        'priority': 2
    },
    'modify_stop_loss': {
        'keywords': ['移动止损', '移动保本', '保本损', '止损改', '止损移', '止损统一改',
                    '成本价格', '无风险', '硬止损', '保本', '成本跑', '回调到成本',
                    '拉均价', '止损设置', '挂.*止损'],
        'priority': 3
    },
    'modify_take_profit': {
        'keywords': ['挂.*止盈', '止盈目标', '全部止盈', '第一止盈', '第二止盈',
                    '止盈挂', '预计.*止盈', '提前挂', '挂上', '挂单止盈'],
        'priority': 4
    },
    'cancel_orders': {
        'keywords': ['撤掉', '取消挂单', '作废', '不挂了', '撤单', '清空挂单',
                    '点位全撤', '所有单子都撤掉', '撤', '取消', '不再'],
        'priority': 5
    },
    'live_stream': {
        'keywords': ['binance.com/uni-qr', '收听语音直播', '直播将于', '开播',
                    '直播了', '上人直播', '广场语音', '语音直播'],
        'priority': 6
    },
    'open_position_long': {
        'keywords': ['做多', '多单', '多 ', '上车', '进场做多', '挂单多', 
                    '限价做多', '睡觉做多', '市价多', '现在.*市价', '保证金',
                    '补仓', '头仓', '仓位思路'],
        'negative': ['做空', '空单', '平仓', '止盈', '跑了'],
        'priority': 7
    },
    'open_position_short': {
        'keywords': ['做空', '空单', '空 ', '挂单空', '限价做空', '睡觉挂单空',
                    '市价空', '进场做空', '保证金', '补仓', '头仓', '仓位思路'],
        'negative': ['做多', '多单', '平仓', '止盈', '跑了'],
        'priority': 8
    },
    'chat': {
        'keywords': ['周末愉快', '晚安', '睡醒', '汇报', '恭喜', '感谢',
                    '心态', '人性', '纪律', '返佣', '邀请码', '打赏', '粉丝'],
        'priority': 9
    }
}

def classify_intent(text: str) -> tuple:
    """分类消息意图"""
    if not text or len(text.strip()) < 5:
        return ('chat', 0.5)
    
    # 特殊规则：检查是否是策略消息 vs 分析/总结消息
    has_strategy_pattern = bool(re.search(r'\d{5,}.*(?:保证金 | 止损 | 止盈)', text))
    has_position_pattern = bool(re.search(r'(?:做多 | 做空 | 多单 | 空单).*\d+ 倍', text))
    has_tp_sl_pattern = bool(re.search(r'(?:止损 | 止盈)[：:]\s*\d{5,}', text))
    
    # 分析/总结消息特征
    is_summary = any(x in text for x in ['昨日', '昨天', '总结', '共交易', '获利', '利润',
                                          '这样的', '有一种人', '注意', '记住', '提醒'])
    is_educational = any(x in text for x in ['心态', '人性', '纪律', '严格执行', '策略仅供参考',
                                              '不要', '应该', '必须', '建议'])
    
    # 如果是总结/教育内容，优先分类为 chat
    if is_summary or is_educational:
        if not has_strategy_pattern and not has_position_pattern:
            return ('chat', 0.7)
    
    scores = {}
    
    for intent, rule in RULES.items():
        score = 0
        keywords = rule.get('keywords', [])
        negative = rule.get('negative', [])
        priority = rule.get('priority', 10)
        
        # 检查排除词
        has_negative = any(neg in text for neg in negative)
        if has_negative and intent in ['open_position_long', 'open_position_short']:
            continue
        
        # 计算匹配分数
        for kw in keywords:
            if kw in text:
                score += (10 - priority) * 0.1
                pos = text.find(kw)
                if pos < 50:
                    score += 0.2
        
        # 策略消息加分
        if intent in ['open_position_long', 'open_position_short']:
            if has_strategy_pattern:
                score += 0.3
            if has_position_pattern:
                score += 0.3
            if has_tp_sl_pattern:
                score += 0.2
        
        if score > 0:
            scores[intent] = min(score, 1.0)
    
    if not scores:
        return ('chat', 0.5)
    
    best_intent = max(scores, key=scores.get)
    return (best_intent, scores[best_intent])


def extract_strategy(text: str) -> dict:
    """提取策略结构化数据"""
    result = {
        'direction': None,
        'action': None,
        'leverage': None,
        'margin_total': None,
        'entries': [],
        'take_profit': [],
        'stop_loss': None,
    }
    
    # 判断方向
    if any(x in text for x in ['做多', '多单', '多 ']) and '做空' not in text:
        result['direction'] = 'long'
    elif any(x in text for x in ['做空', '空单', '空 ']) and '做多' not in text:
        result['direction'] = 'short'
    
    # 判断动作
    intent, _ = classify_intent(text)
    result['action'] = intent
    
    # 提取杠杆
    leverage_match = re.search(r'(\d+) 倍', text)
    if leverage_match:
        result['leverage'] = int(leverage_match.group(1))
    
    # 提取保证金
    margin_match = re.search(r'总 (\d+(?:\.\d+)?)%保证金', text)
    if margin_match:
        result['margin_total'] = float(margin_match.group(1))
    
    # 提取进场点位
    price_patterns = [
        (r'(\d{5,}) 附近 (?:市价 | 直接)', 'market'),
        (r'市价 (\d{5,})', 'market'),
        (r'再挂单 [：:]\s*(\d{5,})', 'limit'),
        (r'挂单 [：:]\s*(\d{5,})', 'limit'),
    ]
    for pattern, order_type in price_patterns:
        matches = re.findall(pattern, text)
        for m in matches:
            try:
                price = int(m)
                if 10000 < price < 200000:
                    result['entries'].append({'price': price, 'type': order_type})
            except:
                pass
    
    # 去重
    seen = set()
    unique_entries = []
    for e in result['entries']:
        if e['price'] not in seen:
            seen.add(e['price'])
            unique_entries.append(e)
    result['entries'] = unique_entries[:3]
    
    # 提取止盈
    tp_patterns = [
        r'止盈目标 [：:]\s*1[）)]?\s*(\d{5,})',
        r'第一止盈 [：:]?\s*(\d{5,})',
        r'全部止盈 [：:]?\s*(\d{5,})',
    ]
    for pattern in tp_patterns:
        matches = re.findall(pattern, text)
        for m in matches:
            try:
                tp = int(m)
                if tp not in result['take_profit'] and 50000 < tp < 200000:
                    result['take_profit'].append(tp)
            except:
                pass
    
    # 提取止损
    sl_patterns = [
        r'止損 [：:]\s*(\d{5,})',
        r'止损 [：:]\s*(\d{5,})',
    ]
    for pattern in sl_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                result['stop_loss'] = int(match.group(1))
            except:
                pass
    
    return result


def analyze_intent(text, position_info=None):
    """
    使用规则分析消息意图（无需 LLM）
    
    Returns:
        dict: {
            "intent": str,
            "params": {...},
            "direction": str or null,
            "confidence": float,
            "reason": str
        }
    """
    intent, confidence = classify_intent(text)
    strategy = extract_strategy(text)
    
    # 构建返回结果
    result = {
        "intent": intent,
        "params": {
            "orders": strategy.get('entries', []),
            "stop_loss": strategy.get('stop_loss'),
            "take_profit": strategy.get('take_profit')
        },
        "direction": strategy.get('direction'),
        "confidence": confidence,
        "reason": f"基于关键词匹配 - {INTENTS.get(intent, intent)}"
    }
    
    return result


if __name__ == '__main__':
    # 测试
    test_messages = [
        "BTC 在 110900 附近市价直接多 100 倍 2%保证金 补仓一会出",
        "全部止盈吧，现在价格 71800",
        "止盈 50% 仓位，移动保本损",
        "撤掉所有挂单，等待新的策略",
        "移动止损至 110000 整数",
        "挂 113000 全部止盈，提前挂上",
    ]
    
    print("🦞 意图分析器 v4 测试\n")
    for msg in test_messages:
        result = analyze_intent(msg)
        print(f"消息：{msg}")
        print(f"意图：{INTENTS.get(result['intent'], result['intent'])}")
        print(f"置信度：{result['confidence']}")
        print(f"方向：{result['direction']}")
        print()
