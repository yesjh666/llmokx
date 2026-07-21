#!/usr/bin/env python3
"""
Telegram 消息意图分析器 v3 - 优化版
使用精简 Prompt + Gateway HTTP API 直接调用
"""

import json
import re
import requests
import time

# Gateway API 配置
GATEWAY_URL = "http://127.0.0.1:18789"
GATEWAY_TOKEN = "d75d89b84f23165590ec2508361f1c660c5c1b3f75ee1292"

# 缓存配置
INTENT_CACHE = {}
CACHE_MAX_SIZE = 100
CACHE_SIMILARITY_THRESHOLD = 0.85  # 相似度阈值

def get_gateway_token():
    """获取 Gateway token"""
    global GATEWAY_TOKEN
    if GATEWAY_TOKEN:
        return GATEWAY_TOKEN
    
    # 尝试从环境变量获取
    import os
    token = os.environ.get('OPENCLAW_GATEWAY_TOKEN')
    if token:
        GATEWAY_TOKEN = token
        return token
    
    return None

def calculate_similarity(text1, text2):
    """计算文本相似度（简单版：关键词匹配）"""
    # 提取关键词（数字和中文词）
    keywords1 = set(re.findall(r'[\d]+|[止盈止损平仓撤单开多做空]', text1))
    keywords2 = set(re.findall(r'[\d]+|[止盈止损平仓撤单开多做空]', text2))
    
    if not keywords1 or not keywords2:
        return 0.0
    
    intersection = keywords1 & keywords2
    union = keywords1 | keywords2
    return len(intersection) / len(union) if union else 0.0

def check_cache(text):
    """检查缓存中是否有相似消息"""
    global INTENT_CACHE
    
    for cached_text, cached_result in INTENT_CACHE.items():
        similarity = calculate_similarity(text, cached_text)
        if similarity >= CACHE_SIMILARITY_THRESHOLD:
            print(f"  📦 缓存命中 (相似度: {similarity:.2f})")
            return cached_result
    
    return None

def add_to_cache(text, result):
    """添加到缓存"""
    global INTENT_CACHE
    
    # 清理旧缓存
    if len(INTENT_CACHE) >= CACHE_MAX_SIZE:
        # 删除最早的 20 个
        keys_to_remove = list(INTENT_CACHE.keys())[:20]
        for k in keys_to_remove:
            INTENT_CACHE.pop(k, None)
    
    INTENT_CACHE[text] = result

# 精简版 Prompt（核心规则 + 关键示例）
COMPACT_PROMPT_TEMPLATE = """分析交易消息，返回JSON。

当前仓位: {position_context}

消息: {text}

意图类型:
1. open_position: 开单（必须返回 take_profit 和 stop_loss）
2. close_position: 平仓（支持 close_ratio 部分平仓 + move_breakeven 移动保本）
3. conditional_close_position: 条件平仓（价格达到触发价才平仓，需要 trigger_price）
4. modify_tp: 修改止盈（需要具体价格）
5. modify_sl: 修改止损（需要具体价格）
6. cancel_orders: 撤单
7. query: 查询
8. chat: 闲聊

开单字段说明:
- orders: 入场价数组，格式 [{{"price": 价格, "type": "market/limit", "leverage": 杠杆倍数, "margin_pct": 保证金比例}}]
- ⚠️ 重要：leverage 和 margin_pct 必须放在每个 order 对象内部，不要放在 params 顶层
- ⚠️ margin_pct 必须用小数格式：2%=0.02, 3%=0.03, 5%=0.05。禁止写整数如2或3，必须写0.02或0.03
- take_profit: 止盈价数组，格式 [价格1, 价格2]
- stop_loss: 止损价（数字）

平仓字段说明:
- close_ratio: 平仓比例（0.7 = 平70%，省略=全平）
- move_breakeven: 是否移动保本止损（true/false）

条件平仓字段说明:
- trigger_price: 触发价格（价格达到此价位才执行平仓）
- close_ratio: 平仓比例（0.3 = 平30%，省略=全平）
- 示例："拉升到64000以上全出掉" = trigger_price: 64000

返回JSON格式:
{{"intent":"open_position","symbol":"BTC-USDT-SWAP","direction":"long/short","params":{{"orders":[{{"price":60000}}],"take_profit":[65000],"stop_loss":58000}},"confidence":0.9}} """

# 关键示例
KEY_EXAMPLES = """
⚠️ 重要规则：
1. "止盈X%仓位" = 部分平仓指令，返回 close_position + close_ratio
2. "拉升到X以上全出掉" = 条件平仓，返回 conditional_close_position + trigger_price
3. "涨到X就空市价" / "跌到X就多市价" = 挂限价单 @ X，返回 open_position + orders[{"price": X, "type": "limit"}]
4. leverage 和 margin_pct 必须在每个 order 对象内，不能在 params 顶层
5. margin_pct 必须是小数格式(0.02/0.03/0.05)，绝对不能写整数(2/3/5)

示例1:
输入: "BTC 做多 60000，100倍2%保证金，止盈65000，止损58000"
输出: {{"intent":"open_position","symbol":"BTC-USDT-SWAP","direction":"long","params":{{"orders":[{{"price":60000,"type":"limit","leverage":100,"margin_pct":0.02}}],"take_profit":[65000],"stop_loss":58000}},"confidence":0.9}}

示例2:
输入: "ETH 挂单做空 2100，50倍3%保证金，止盈2018，止损2330"
输出: {{"intent":"open_position","symbol":"ETH-USDT-SWAP","direction":"short","params":{{"orders":[{{"price":2100,"type":"limit","leverage":50,"margin_pct":0.03}}],"take_profit":[2018],"stop_loss":2330}},"confidence":0.9}}

示例3:
输入: "BTC挂单54000做多 100倍 2%保证金"
输出: {{"intent":"open_position","symbol":"BTC-USDT-SWAP","direction":"long","params":{{"orders":[{{"price":54000,"type":"limit","leverage":100,"margin_pct":0.02}}]}},"confidence":0.9}}

示例4:
输入: "66850 市价上车 100倍2%保证金，再挂68088 100倍3%保证金"
输出: {{"intent":"open_position","symbol":"BTC-USDT-SWAP","direction":"short","params":{{"orders":[{{"price":66850,"type":"market","leverage":100,"margin_pct":0.02}},{{"price":68088,"type":"limit","leverage":100,"margin_pct":0.03}}]}},"confidence":0.9}}

示例5:
输入: "止盈70%仓位"
输出: {{"intent":"close_position","direction":"short","params":{{"close_ratio":0.7}},"confidence":0.9}}

示例6:
输入: "到达短线目标1止盈70%仓位利润！移动保本！"
输出: {{"intent":"close_position","direction":"short","params":{{"close_ratio":0.7,"move_breakeven":true}},"confidence":0.9}}

示例7:
输入: "剩余的30%仓位，一会拉升到64000以上全出掉"
输出: {{"intent":"conditional_close_position","direction":"long","params":{{"trigger_price":64000,"close_ratio":0.3}},"confidence":0.9}}

示例8:
输入: "涨到65000就把剩下的全出了"
输出: {{"intent":"conditional_close_position","direction":"long","params":{{"trigger_price":65000}},"confidence":0.9}}

示例9:
输入: "市价63500附近（盯盘一会涨到64000就空市价）100倍2%保证金"
输出: {{"intent":"open_position","symbol":"BTC-USDT-SWAP","direction":"short","params":{{"orders":[{{"price":64000,"type":"limit","leverage":100,"margin_pct":0.02}}]}},"confidence":0.9}}

示例10:
输入: "跌到60000就多市价 50倍3%保证金"
输出: {{"intent":"open_position","symbol":"BTC-USDT-SWAP","direction":"long","params":{{"orders":[{{"price":60000,"type":"limit","leverage":50,"margin_pct":0.03}}]}},"confidence":0.9}}
"""

def analyze_intent_compact(text, position_info=None):
    """
    使用精简 Prompt 分析意图（HTTP API 版本）
    
    优化点:
    1. Prompt 精简到 ~500 字（原来 ~3000 字）
    2. 直接 HTTP 调用 Gateway（避免 CLI 启动开销）
    3. 超时时间缩短到 30 秒
    4. 缓存层：相似消息不重复分析
    5. 重试机制：HTTP 失败后重试
    """
    
    # 📦 检查缓存
    cached_result = check_cache(text)
    if cached_result:
        return cached_result
    
    # 构建仓位上下文
    position_context = "无持仓"
    if position_info and 'positions' in position_info:
        positions = position_info.get('positions', [])
        if positions:
            pos_lines = []
            for p in positions:
                dir_str = "做多" if p['direction'] == 'long' else "做空"
                pos_lines.append(f"{p['symbol']} {dir_str}{p['size']}@{p['avg_px']}")
            position_context = "\n".join(pos_lines)
    
    # 构建精简 Prompt
    prompt = COMPACT_PROMPT_TEMPLATE.format(
        position_context=position_context,
        text=text
    ) + KEY_EXAMPLES
    
    # 方案 A: 使用 HTTP API（带重试）
    token = get_gateway_token()
    if token:
        for attempt in range(2):  # 最多重试 1 次
            try:
                start = time.time()
                resp = requests.post(
                    f"{GATEWAY_URL}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "openclaw",
                        "messages": [{"role": "user", "content": prompt}]
                    },
                    timeout=30
                )
                elapsed = time.time() - start
                
                if resp.status_code == 200:
                    data = resp.json()
                    content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
                    result = parse_llm_response(content)
                    if result:
                        print(f"  ⚡ HTTP API 耗时: {elapsed:.2f}s" + (f" (重试 {attempt+1})" if attempt > 0 else ""))
                        add_to_cache(text, result)
                        return result
                elif resp.status_code >= 500 and attempt == 0:
                    print(f"  ⚠️ HTTP API 服务器错误 ({resp.status_code})，重试中...")
                    time.sleep(1)
                    continue
                else:
                    print(f"  ⚠️ HTTP API 失败: {resp.status_code}")
                    break
            except requests.Timeout:
                if attempt == 0:
                    print(f"  ⚠️ HTTP API 超时，重试中...")
                    continue
                print(f"  ⚠️ HTTP API 超时")
            except Exception as e:
                print(f"  ⚠️ HTTP API 异常: {e}")
                break
    
    # 方案 B: 降级使用 CLI（精简 prompt + 更短超时）
    import subprocess
    import shlex
    
    start = time.time()
    cmd = f"openclaw agent --local --session-id trade-intent-session -m {shlex.quote(prompt)}"
    
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        elapsed = time.time() - start
        print(f"  🐢 CLI 耗时: {elapsed:.2f}s")
        
        if proc.returncode == 0 and proc.stdout.strip():
            result = parse_llm_response(proc.stdout.strip())
            if result:
                add_to_cache(text, result)
                return result
    except subprocess.TimeoutExpired:
        print(f"  ❌ CLI 超时（30s）")
    except Exception as e:
        print(f"  ⚠️ CLI 失败: {e}")
    
    return None

def parse_llm_response(content):
    """解析 LLM 返回的 JSON"""
    # 提取 JSON
    json_str = None
    
    # 方法1: markdown 代码块
    json_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', content)
    if json_match:
        json_str = json_match.group(1).strip()
    
    # 方法2: 匹配最外层完整 {}
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
        return None
    
    # 修复常见 JSON 问题
    json_str = re.sub(r',\s*}', '}', json_str)
    json_str = re.sub(r',\s*]', ']', json_str)
    
    try:
        result = json.loads(json_str)
        print(f"  🤖 意图: {result.get('intent')} (置信度: {result.get('confidence')})")
        return result
    except json.JSONDecodeError as e:
        print(f"  ⚠️ JSON 解析失败: {e}")
        return None

# 测试
if __name__ == "__main__":
    test_messages = [
        "止盈70%仓位",
        "止损改到71000",
        "平掉50%，保本损",
        "今天天气不错"
    ]
    
    position_info = {
        'positions': [
            {'symbol': 'ETH-USDT-SWAP', 'direction': 'short', 'size': 1.06, 'avg_px': 2115.3}
        ]
    }
    
    for msg in test_messages:
        print(f"\n测试: {msg}")
        result = analyze_intent_compact(msg, position_info)
        if result:
            print(f"结果: {json.dumps(result, ensure_ascii=False)}")
