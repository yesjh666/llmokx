#!/usr/bin/env python3
"""
Telegram 消息意图分析器 v2
使用大模型理解口语化消息，返回操作意图
支持：开单/平仓/撤单/修改止盈/修改止损/修改价格/查询/闲聊
"""

import json
import re
import subprocess
import shlex
import sys

LLM_SESSION_ID = "trade-intent-session"

def analyze_intent(text, position_info=None):
    """
    使用大模型分析消息意图
    
    Args:
        text: 群消息文本
        position_info: 当前仓位信息 (可选)
    
    Returns:
        dict: {
            "intent": str,
            "params": {...},
            "direction": str or null,
            "confidence": float,
            "reason": str
        }
    """
    
    position_context = ""
    recent_intents_context = ""
    if position_info:
        # 支持两种格式：新格式（全局扫描所有仓位）和旧格式（单币种）
        if 'positions' in position_info:
            positions = position_info.get('positions', [])
            if positions:
                pos_lines = []
                for p in positions:
                    dir_str = "做多" if p['direction'] == 'long' else "做空"
                    pos_lines.append(f"- {p['symbol']} {dir_str}{p['size']}@{p['avg_px']}")
                position_context = "当前仓位状态:\n" + "\n".join(pos_lines) + "\n"
            else:
                position_context = "当前仓位状态: 无持仓\n"
            
            # 🦞 最近指令上下文（关联历史操作）
            recent_intents = position_info.get('recent_intents', [])
            if recent_intents:
                intent_lines = []
                for entry in recent_intents:
                    line = f"[{entry.get('time', '?')}] {entry.get('intent')} {entry.get('symbol', '?')} {entry.get('direction', '?')}"
                    params = entry.get('params', {})
                    if params.get('take_profit'):
                        line += f" 止盈={params['take_profit']}"
                    if params.get('stop_loss'):
                        line += f" 止损={params['stop_loss']}"
                    if params.get('orders'):
                        line += f" 委托={params['orders']}"
                    if params.get('close_ratio'):
                        line += f" 平仓比例={params['close_ratio']*100:.0f}%"
                    result = entry.get('result', '')
                    if result:
                        line += f" 结果={result}"
                    intent_lines.append(line)
                recent_intents_context = "\n最近已执行指令（供参考关联）:\n" + "\n".join(intent_lines) + "\n"
        else:
            position_context = f"""
当前仓位状态:
- 做多持仓：{'有 (' + str(position_info.get('long_size', '')) + ' USDT @ ' + str(position_info.get('long_avg_px', '')) + ')' if position_info.get('has_long') else '无'}
- 做空持仓：{'有 (' + str(position_info.get('short_size', '')) + ' USDT @ ' + str(position_info.get('short_avg_px', '')) + ')' if position_info.get('has_short') else '无'}
- 限价挂单：{position_info.get('pending_count', 0)} 个
- 条件单(止损止盈)：{position_info.get('algo_count', 0)} 个
- 条件单详情：{position_info.get('algo_details', '无')}
"""
    
    prompt = f"""你是 BTC 交易助手，分析群消息的意图。注意区分：限价卖单可能是做空入场委托（不是止盈单）。

{position_context}{recent_intents_context}
消息内容：{text}

分析任务 — 判断消息意图类型:
1. "open_position": 开新单 (有明确开单价/入场价)
2. "close_position": 平仓 (平多/平空/全平/止盈走人/部分平仓/平掉XX%/移动保本)
3. "cancel_orders": 撤单 (撤掉/取消/清空挂单)
4. "modify_tp": 修改止盈价 (挂XX止盈/止盈改XX/全部止盈XX/睡觉挂XX全部止盈)
5. "modify_sl": 修改止损价 (止损改XX/止损移到XX)
6. "modify_price": 修改挂单价格 (挂单改价/委托改到XX)
7. "query": 查询 (询问价格/仓位/行情)
8. "chat": 闲聊 (无操作意图)
9. "conditional_modify_tp": 条件修改止盈 (如果XX成交才改止盈到YY / XX成交了成本变YY就改止盈到YY / 不成交不用跑)

返回 JSON:
{{
  "intent": "类型",
  "symbol": "交易对，如BTC-USDT、ETH-USDT、SOL-USDT等。未指定币种时 = 当前持仓币种，空仓默认BTC-USDT",
  "direction": "long/short/all 或 null",
  "params": {{
    "orders": [
      {{"price": 数字, "type": "market或limit"}}
    ],
    "stop_loss": 数字或null,
    "take_profit": [数字]或null,
    "close_ratio": 0.0-1.0或null (部分平仓比例，如50%=0.5),
    "move_breakeven": true/false (是否移动止损到开仓价保本),
    "leverage": 数字或null (杠杆倍数，如100),
    "margin_ratios": [数字]或null (每个委托对应的保证金占总本金比例，如[0.02, 0.03]表示2%和3%),
    "cancel_prices": [数字]或null (需要先撤掉的挂单价格，如消息里说"撤掉2455的挂单"则为[2455]),
    "trigger_price": 数字或null (conditional_modify_tp的触发价)
  }},
  "confidence": 0.0-1.0,
  "reason": "分析理由"
}}

⚠️ 重要规则:
- 当消息包含多个开仓价格时（如"66930市价上车，再挂68088"），必须在 orders 数组中列出每一条委托
- "市价上车"/"市价"/"附近" → type="market"
- "再挂"/"挂单" → type="limit"
- 止盈止损是所有委托共享的

⚠️ 币种识别规则（最高优先级）:
- 消息中明确提到币种（如 BTC/比特币/ETH/以太坊等） → 使用对应币种
- 消息中**未指定币种**，但当前有持仓 → symbol = 当前持仓的币种
- 消息中**未指定币种**，且当前无持仓 → 默认 BTC-USDT

⚠️ 止盈平仓识别规则（最高优先级）:
- 消息中出现 "止盈XX%仓位利润" / "止盈XX%仓位" / "做空单止盈XX%" / "做多单止盈XX%" → close_position, close_ratio=XX/100
- 不能把"止盈70%仓位利润"误判为 open_position！这是平仓指令，不是开单
- 示例："以太坊做空单止盈70%仓位利润！恭喜跟上的朋友" → close_position, direction="short", close_ratio=0.7

⚠️ 条件指令识别规则（最高优先级）:
- 消息中出现 "如果XX成交/才/才XX/不成交不用跑/成交了就" 等条件句式 → intent="conditional_modify_tp"
- params 中必须有: trigger_price (触发价), take_profit (成交后修改的止盈价)
- conditional_modify_tp 不立即执行止盈修改，而是启动价格监控等待条件满足后再修改
- 示例："如果2418点位成交，成本会变2392，回调到2392全部出局。不成交不用跑" → conditional_modify_tp, trigger_price=2418, take_profit=[2392]
- 示例："2418成交了就改止盈2392" → conditional_modify_tp, trigger_price=2418, take_profit=[2392]
- 示例："如果XX成交，成本变YY" → conditional_modify_tp, trigger_price=XX, take_profit=[YY]

⚠️ 建议 vs 指令识别规则（最高优先级）:
- 消息中出现 "如果/如果你/要是/假如" + "可以/建议" 等条件建议语气 → 这部分是建议，不是指令，不要执行 close_position
- 消息中明确的价格指令（如 "在2318全部止盈"/"短线在XX全部止盈"） → 这是核心指令，应该 modify_tp
- 示例："如果你心慌可以在成本附近跑一半，短线在2318全部止盈" → modify_tp 到 2318，不是 close_position

⚠️ 成本价保本规则:
- 不管做多还是做空，"回调到成本价出局"/"回到成本跑掉"/"成本价全部出"/"保本" = modify_tp（修改止盈到成本价保本出场）
- 做空持仓：价格涨回成本 = 保本止盈 → modify_tp
- 做多持仓：价格跌回成本 = 保本止盈 → modify_tp
- modify_sl 只用于明确说"止损改到XX"且目标价在亏损方向（比成本更差的位置）

关键示例:

输入："睡觉挂 67388 全部止盈"
输出：{{"intent":"modify_tp","direction":"all","params":{{"take_profit":[67388]}},"confidence":0.95,"reason":"修改所有单的止盈价为67388"}}

输入："止损移到 71000"
输出：{{"intent":"modify_sl","direction":"all","params":{{"stop_loss":71000}},"confidence":0.9,"reason":"修改止损价"}}

输入："BTC 在 67000 附近进，止损 65000，第一止盈看 7 万"
输出：{{"intent":"open_position","direction":"long","params":{{"orders":[{{"price":67000,"type":"market"}}],"stop_loss":65000,"take_profit":[70000]}},"confidence":0.95,"reason":"单条委托：67000市价入场"}}

输入："67400 附近市价，再挂 67788，第一止盈 65188，第二止盈 63888"
输出：{{"intent":"open_position","direction":"short","params":{{"orders":[{{"price":67400,"type":"market"}},{{"price":67788,"type":"limit"}}],"stop_loss":null,"take_profit":[65188,63888]}},"confidence":0.9,"reason":"两条委托：67400市价+67788限价挂单"}}

输入："66930附近市价上车（轻仓）100倍 2%保证金\n再挂68088（误差50U左右）100倍 3%保证金\n第一止盈65588 止盈50%\n止损69500"
输出：{{"intent":"open_position","direction":"short","params":{{"orders":[{{"price":66930,"type":"market"}},{{"price":68088,"type":"limit"}}],"stop_loss":69500,"take_profit":[65588,63888,60088],"leverage":100,"margin_ratios":[0.02,0.03]}},"confidence":0.95,"reason":"两条委托：66930市价+68088限价，100倍杠杆，分别2%和3%保证金"}}

输入："多单平仓了"
输出：{{"intent":"close_position","direction":"long","params":{{}},"confidence":0.9,"reason":"明确表达平多单"}}

输入："平掉50%利润，移动保本"
输出：{{"intent":"close_position","direction":"auto","params":{{"close_ratio":0.5,"move_breakeven":true}},"confidence":0.95,"reason":"部分平仓50%并将止损移到开仓价保本"}}

输入："止盈50%，保本损"
输出：{{"intent":"close_position","direction":"auto","params":{{"close_ratio":0.5,"move_breakeven":true}},"confidence":0.9,"reason":"平掉50%仓位，止损移到开仓价"}}

输入："先平一半利润"
输出：{{"intent":"close_position","direction":"auto","params":{{"close_ratio":0.5}},"confidence":0.85,"reason":"平掉50%仓位"}}

输入："2388挂单已成交，综合成本2351，回调到成本价2351全部出局"（当前做空持仓）
输出：{{"intent":"modify_tp","symbol":"ETH-USDT","direction":"all","params":{{"take_profit":[2351]}},"confidence":0.95,"reason":"做空持仓，价格涨回成本2351出局=止盈保本，修改止盈到2351"}}

输入："回到成本跑掉，成本67000"（当前做多持仓）
输出：{{"intent":"modify_tp","symbol":"BTC-USDT","direction":"all","params":{{"take_profit":[67000]}},"confidence":0.9,"reason":"做多持仓，回到成本出局=止盈保本，修改止盈到67000"}}

输入："撤掉没用的挂单 准备重新布局"
输出：{{"intent":"cancel_orders","direction":null,"params":{{}},"confidence":0.9,"reason":"明确表达撤单意图"}}

输入："现在 BTC 多少了"
输出：{{"intent":"query","direction":null,"params":{{}},"confidence":0.8,"reason":"询问价格"}}

⚠️ 建议 vs 指令 关键示例（必读）:

输入："按照策略成本2353左右，如果你很心慌或者仓位很大，可以在成本附近跑出一半仓位，短线在2318全部止盈"（当前做空持仓）
输出：{{"intent":"modify_tp","symbol":"ETH-USDT","direction":"all","params":{{"take_profit":[2318]}},"confidence":0.95,"reason":"前半段是条件建议（如果…可以…），核心指令是在2318全部止盈，应修改止盈价"}}

⚠️ conditional_modify_tp 关键示例:

输入："如果2418点位成交，成本会变2392，回调到2392全部出局。不成交不用跑，吃到了就要回调跑"（当前做空持仓）
输出：{{"intent":"conditional_modify_tp","symbol":"ETH-USDT","direction":"all","params":{{"trigger_price":2418,"take_profit":[2392]}},"confidence":0.95,"reason":"条件指令：2418成交后才改止盈到2392，不成交不动。trigger_price=2418，take_profit=2392"}}

输入："今天天气不错啊"
输出：{{"intent":"chat","direction":null,"params":{{}},"confidence":0.95,"reason":"闲聊，无交易意图"}}

只返回 JSON，不要其他文字。
"""
    
    try:
        safe_prompt = shlex.quote(prompt)
        cmd = f'openclaw agent --local --session-id {LLM_SESSION_ID} -m {safe_prompt}'
        
        for attempt in range(2):
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
            
            if proc.returncode == 0 and proc.stdout.strip():
                content = proc.stdout.strip()
                
                # 提取 JSON：先尝试 markdown 代码块，再用大括号匹配
                json_str = None
                
                # 方法1: markdown 代码块
                json_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', content)
                if json_match:
                    json_str = json_match.group(1).strip()
                
                # 方法2: 匹配最外层完整 {} (支持嵌套)
                if not json_str:
                    start = content.find('{')
                    if start >= 0:
                        brace_count = 0
                        for i in range(start, len(content)):
                            if content[i] == '{': brace_count += 1
                            elif content[i] == '}': brace_count -= 1
                            if brace_count == 0:
                                json_str = content[start:i+1]
                                break
                
                if not json_str:
                    json_str = content
                
                # 修复常见 JSON 问题
                json_str = re.sub(r',\s*}', '}', json_str)
                json_str = re.sub(r',\s*]', ']', json_str)
                
                try:
                    result = json.loads(json_str)
                except json.JSONDecodeError:
                    # 修复1: 缺少逗号 — "}\n  "reason" 或 数字\n  "key"
                    json_str = re.sub(r'(\d+\.?\d*)\s*\n(\s*")', r'\1,\n\2', json_str)
                    json_str = re.sub(r'(null|true|false)\s*\n(\s*")', r'\1,\n\2', json_str)
                    json_str = re.sub(r'"\s*\n(\s*")', r'",\n\1', json_str)
                    json_str = re.sub(r'}\s*\n(\s*")', r'},\n\1', json_str)
                    json_str = re.sub(r']\s*\n(\s*")', r'],\n\1', json_str)
                    try:
                        result = json.loads(json_str)
                    except json.JSONDecodeError:
                        # 修复2: reason 字段里的未转义引号 — 截掉 reason 字段重试
                        json_str_no_reason = re.sub(r'"reason"\s*:\s*".*?(?:"\s*})', '"reason":"(parsed)"}', json_str, flags=re.DOTALL)
                        result = json.loads(json_str_no_reason)
                
                print(f"  🤖 意图分析成功：{result.get('intent')}")
                return result
            
            if proc.returncode != 0:
                stderr_msg = proc.stderr[:200] if proc.stderr else "unknown error"
                if "session file locked" in stderr_msg and attempt == 0:
                    print(f"  ⚠️ 会话锁定，重试中...")
                    import time
                    time.sleep(2)
                    continue
                print(f"  ⚠️ LLM 调用失败：{stderr_msg}")
            break
            
        return None
    except Exception as e:
        print(f"  ⚠️ 意图分析异常：{e}")
        return None

if __name__ == "__main__":
    test_messages = [
        "睡觉挂 67388 全部止盈",
        "止损移到 71000",
        "BTC 在 67000 附近进，止损 65000，第一止盈看 7 万",
        "撤掉没用的挂单 准备重新布局",
        "多单平仓了",
        "今天行情怎么样"
    ]
    
    for msg in test_messages:
        print(f"\n测试：{msg}")
        result = analyze_intent(msg)
        print(f"结果：{json.dumps(result, ensure_ascii=False, indent=2) if result else 'None'}")
