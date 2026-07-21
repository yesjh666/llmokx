#!/usr/bin/env python3
"""
Telegram 群消息监控 v7 - 全量 LLM 意图分析版
🦞 每条群消息都过大模型理解意图,不依赖关键词过滤
支持:开单/平仓/撤单/修改止盈/修改止损/查询/闲聊
"""

import requests
import re
import time
import json
import os
import sys
import subprocess
import hashlib

# 确保能 import scripts 目录下的模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 📤 意图转发配置文件路径
INTENT_FORWARD_CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'intent-forward.json')

def load_intent_forward_config():
    """加载意图转发配置"""
    try:
        if os.path.exists(INTENT_FORWARD_CONFIG_FILE):
            with open(INTENT_FORWARD_CONFIG_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"  ⚠️ 加载转发配置失败: {e}")
    return {'enabled': False, 'targets': []}

def forward_intent_to_groups(intent_result, text, source_chat, original_message=None):
    """将提取的意图转发到配置的目标群 - Bot可执行格式"""
    config = load_intent_forward_config()
    if not config.get('enabled', False):
        return

    targets = config.get('targets', [])
    if not targets:
        return

    intent = intent_result.get('intent', 'chat')
    if intent in ['chat', 'query']:
        return  # 闲聊和查询不转发

    params = intent_result.get('params', {})
    symbol = intent_result.get('symbol', 'BTC-USDT')

    # 确保symbol带-SWAP后缀
    if symbol and '-SWAP' not in symbol:
        symbol = symbol + '-SWAP'

    direction = intent_result.get('direction')

    # ============ 参数完整性验证 ============
    can_execute = True
    missing_params = []
    auto_filled = []  # 记录自动补齐的参数

    # 调试:打印实际接收到的参数
    print(f"  🔍 验证参数: intent={intent}, params={params}")

    if intent == 'open_position':
        # 🔧 先从顶层提取杠杆和保证金,分配到每个订单
        orders = params.get('orders', [])
        top_leverage = params.get('leverage')  # 顶层杠杆
        margin_ratios = params.get('margin_ratios', [])  # 顶层保证金比例数组

        # ===== 固定开仓参数开关 =====
        try:
            with open('/root/.openclaw/workspace/config/trade-config.json', 'r') as f:
                trade_config = json.load(f)
            force_fixed_position = trade_config.get('force_fixed_position', False)
            fixed_leverage = trade_config.get('fixed_leverage', 100)
            fixed_margin_ratios = trade_config.get('fixed_margin_ratios', [0.02, 0.03])
        except Exception as e:
            force_fixed_position = False
            fixed_leverage = 100
            fixed_margin_ratios = [0.02, 0.03]
            print(f'  ⚠️ 读取trade-config失败,使用默认值: {e}')

        if force_fixed_position and orders:
            for i, o in enumerate(orders):
                o['leverage'] = fixed_leverage
                if i < len(fixed_margin_ratios):
                    o['margin_pct'] = fixed_margin_ratios[i]
            print(f"  🔧 固定开仓参数已覆盖: leverage={fixed_leverage}, margin_ratios={fixed_margin_ratios}")
        elif orders and top_leverage:
            for i, o in enumerate(orders):
                if not o.get('leverage'):
                    o['leverage'] = top_leverage
                if not o.get('margin_pct') and i < len(margin_ratios):
                    o['margin_pct'] = margin_ratios[i]
            print(f"  🔧 已分配杠杆和保证金到订单: {orders}")

        # 归一化 margin_pct:整数格式(2/3/5) → 小数格式(0.02/0.03/0.05)
        if orders:
            for o in orders:
                mp = o.get('margin_pct')
                if mp is not None and isinstance(mp, (int, float)) and mp > 1:
                    o['margin_pct'] = round(mp / 100, 4)
                    print(f"  🔧 margin_pct 归一化: {mp} → {o['margin_pct']}")

        # 开仓必须有:订单信息、杠杆、保证金
        print(f"  🔍 orders={orders}")
        if not orders:
            can_execute = False
            missing_params.append('订单信息')
        else:
            # 检查每个订单是否有杠杆和保证金
            for i, o in enumerate(orders):
                leverage = o.get('leverage')
                margin_pct = o.get('margin_pct')
                print(f"  🔍 订单{i+1}: leverage={leverage}, margin_pct={margin_pct}")
                if not leverage:
                    missing_params.append(f'订单{i+1}杠杆')
                if not margin_pct:
                    missing_params.append(f'订单{i+1}保证金')

        print(f"  🔍 missing_params={missing_params}, can_execute={can_execute}")

        if missing_params:
            can_execute = False

        # 止盈止损自动补齐逻辑
        if can_execute and direction:
            # 获取当前价格
            try:
                from scripts.okx_close_position import API_BASE, get_headers as okx_headers
                inst_id = symbol if '-' in symbol else f"{symbol}-USDT-SWAP"
                rp = f"/api/v5/market/ticker?instId={inst_id}"
                resp = requests.get(API_BASE + rp, headers=okx_headers('GET', rp), timeout=5)
                if resp.status_code == 200:
                    ticker_data = resp.json().get('data', [])
                    if ticker_data:
                        current_price = float(ticker_data[0].get('last', 0))

                        if current_price > 0:
                            # 多单:止盈+5%,止损-5%
                            # 空单:止盈-5%,止损+5%
                            if direction == 'long':
                                default_tp = current_price * 1.05
                                default_sl = current_price * 0.95
                            else:  # short
                                default_tp = current_price * 0.95
                                default_sl = current_price * 1.05

                            # 自动补齐缺失的止盈
                            if not params.get('take_profit'):
                                params['take_profit'] = [round(default_tp, 2)]
                                auto_filled.append(f'止盈={round(default_tp, 2)}')

                            # 自动补齐缺失的止损
                            if not params.get('stop_loss'):
                                params['stop_loss'] = round(default_sl, 2)
                                auto_filled.append(f'止损={round(default_sl, 2)}')
            except Exception as e:
                print(f"  ⚠️ 获取价格失败: {e}")

    elif intent == 'modify_tp':
        # 修改止盈必须有:新的止盈价格
        if not params.get('take_profit'):
            can_execute = False
            missing_params.append('新止盈价格')

    elif intent == 'modify_sl':
        # 修改止损必须有:新的止损价格
        if not params.get('stop_loss'):
            can_execute = False
            missing_params.append('新止损价格')

    elif intent == 'close_position':
        # 平仓参数相对灵活,close_ratio 是可选的
        pass

    elif intent == 'cancel_orders':
        # 撤单参数灵活,cancel_type 有默认值 'all'
        pass

    # 不完整信号不转发
    if not can_execute:
        print(f"  ⏭️ 参数不完整,不转发: 缺少 {', '.join(missing_params)}")
        return

    # ============ 构建标准JSON格式(其他Bot直接解析执行) ============
    from datetime import datetime

    signal_data = {
        "version": "1.0",
        "type": "TRADE_SIGNAL",
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "source": source_chat,
        "intent": intent,
        "symbol": symbol,
        "direction": direction,
        "params": {},
        "auto_filled": auto_filled
    }

    # 开仓
    if intent == 'open_position':
        orders = params.get('orders', [])
        if orders:
            formatted_orders = []
            for o in orders:
                order_dict = {
                    "type": o.get('type', 'market'),
                    "leverage": o.get('leverage'),
                    "margin_pct": o.get('margin_pct')
                }
                if o.get('price'):
                    order_dict["price"] = o.get('price')
                formatted_orders.append(order_dict)
            signal_data["params"]["orders"] = formatted_orders

        sl = params.get('stop_loss')
        if sl:
            signal_data["params"]["stop_loss"] = sl

        tp_list = params.get('take_profit', [])
        if tp_list:
            signal_data["params"]["take_profit"] = tp_list

        close_ratio = params.get('close_ratio')
        if close_ratio:
            signal_data["params"]["close_ratio"] = close_ratio

        if params.get('move_breakeven', False):
            signal_data["params"]["move_breakeven"] = True

    # 平仓
    elif intent == 'close_position':
        # 🦞 检查强制全平开关
        try:
            with open('/root/.openclaw/workspace/config/trade-config.json', 'r') as f:
                trade_config = json.load(f)
            force_full_close = trade_config.get('force_full_close', False)
            if force_full_close:
                signal_data['params']['close_ratio'] = 1.0
                print(f'  🔄 强制全平开关已启用,close_ratio=1.0')
        except Exception as e:
            print(f'  ⚠️ 读取trade-config失败: {e}')

        # 如果开关未启用,使用信号原始比例
        close_ratio = params.get('close_ratio')
        if close_ratio and 'close_ratio' not in signal_data['params']:
            signal_data['params']['close_ratio'] = close_ratio
        if params.get('move_breakeven', False):
            signal_data['params']['move_breakeven'] = True

    # 撤单
    elif intent == 'cancel_orders':
        cancel_type = params.get('cancel_type', 'all')
        signal_data["params"]["cancel_type"] = cancel_type

    # 修改止盈
    elif intent == 'modify_tp':
        tp_list = params.get('take_profit', [])
        if tp_list:
            signal_data["params"]["new_tp"] = tp_list

    # 修改止损
    elif intent == 'modify_sl':
        sl = params.get('stop_loss')
        if sl:
            signal_data["params"]["new_sl"] = sl

    # 条件修改止盈
    elif intent == 'conditional_modify_tp':
        trigger = params.get('trigger_price')
        tp_list = params.get('take_profit', [])
        if trigger:
            signal_data["params"]["trigger_price"] = trigger
        if tp_list:
            signal_data["params"]["target_tp"] = tp_list

    # 转为JSON字符串(带中文注释)
    msg = json.dumps(signal_data, ensure_ascii=False, indent=2)

    # 字段说明注释(供其他Bot解析)
    # version: "JSON格式版本号"
    # type: "消息类型,固定TRADE_SIGNAL"
    # timestamp: "ISO8601时间戳"
    # source: "消息来源群/频道"
    # intent: "意图类型: open_position/close_position/cancel_orders/modify_tp/modify_sl/conditional_modify_tp"
    # symbol: "交易对(如BTC-USDT-SWAP)"
    # direction: "方向: long(做多)/short(做空)/all(全部)/null"
    # params: "参数对象,不同意图有不同字段"
    #   open_position: orders[{type,price?,leverage,margin_pct}], stop_loss, take_profit[], close_ratio?, move_breakeven?
    #   close_position: close_ratio?, move_breakeven?
    #   cancel_orders: cancel_type("all"/"symbol")
    #   modify_tp: new_tp[]
    #   modify_sl: new_sl
    #   conditional_modify_tp: trigger_price, target_tp[]
    # auto_filled: "自动补齐的字段列表(如止损、止盈)"

    # 发送到目标群
    for target_config in targets:
        try:
            channel = target_config.get('channel', 'openclaw-telegram')
            target = target_config.get('target')

            if channel == 'openclaw-telegram':
                # 尝试使用 Userbot 发送(以用户身份)
                try:
                    from scripts.telegram_userbot_sender import send_message as userbot_send

                    # 转换 target 为整数(Telethon 需要)
                    target_id = int(target) if isinstance(target, str) else target

                    success, result_msg = userbot_send(target_id, msg)
                    if success:
                        print(f"  ✅ 意图已转发到 {target}(Userbot)")
                        continue
                    else:
                        print(f"  ⚠️ Userbot 发送失败: {result_msg},尝试 Bot API")
                except Exception as e:
                    print(f"  ⚠️ Userbot 异常: {e},尝试 Bot API")

                # 降级:Bot API 发送
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                data = {
                    'chat_id': target,
                    'text': msg,
                    'parse_mode': 'HTML'
                }
                resp = requests.post(url, json=data, timeout=10)
                if resp.status_code == 200:
                    print(f"  ✅ 意图已转发到 {target}(Bot)")
                else:
                    print(f"  ⚠️ 转发失败: {resp.status_code} {resp.text[:100]}")
            else:
                # 其他通道(如微信)使用 openclaw message 命令
                import shlex
                safe_msg = shlex.quote(msg)
                cmd = f'openclaw message send --channel {channel} --target {target} -m {safe_msg}'
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
                if result.returncode == 0:
                    print(f"  ✅ 意图已转发到 {target}")
                else:
                    print(f"  ⚠️ 转发失败: {result.stderr[:100]}")
        except Exception as e:
            print(f"  ⚠️ 转发异常: {e}")

# 🦞 最近指令上下文(内存 + 持久化)
RECENT_INTENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'recent_intents.json')
MAX_RECENT_INTENTS = 1

def load_recent_intents():
    """加载最近执行的指令列表"""
    try:
        if os.path.exists(RECENT_INTENTS_FILE):
            with open(RECENT_INTENTS_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return []

def save_recent_intent(intent_result, text, wechat_msg=None):
    """保存一条已执行指令到最近指令列表"""
    intents = load_recent_intents()
    intent = intent_result.get('intent', 'unknown')
    symbol = intent_result.get('symbol', 'BTC-USDT')
    direction = intent_result.get('direction', '?')
    params = intent_result.get('params', {})

    entry = {
        'time': time.strftime('%H:%M:%S'),
        'intent': intent,
        'symbol': symbol,
        'direction': direction,
        'params': {k: v for k, v in params.items() if v is not None},  # 去掉 null
        'text': text[:500],
    }
    if wechat_msg:
        entry['result'] = wechat_msg[:100]

    intents.append(entry)
    # 保留最近 MAX_RECENT_INTENTS 条
    if len(intents) > MAX_RECENT_INTENTS:
        intents = intents[-MAX_RECENT_INTENTS:]

    try:
        with open(RECENT_INTENTS_FILE, 'w') as f:
            json.dump(intents, f, ensure_ascii=False)
    except Exception as e:
        print(f"  ⚠️ 保存最近指令失败: {e}")

def check_duplicate_process(script_name="telegram-monitor-trade.py"):
    """检测是否有重复进程在运行"""
    import time
    time.sleep(2)  # 等待进程完全启动

    try:
        # 获取所有匹配进程
        result = subprocess.run(
            ["pgrep", "-f", script_name],
            capture_output=True, text=True, timeout=5
        )
        current_pid = os.getpid()
        pids = [int(p) for p in result.stdout.strip().split('\n') if p]

        # 过滤掉当前进程和父进程
        other_pids = [p for p in pids if p != current_pid and p != os.getppid()]

        if len(other_pids) > 0:
            print(f"⚠️  检测到 {len(other_pids)} 个其他进程: {other_pids}")
            # 只清理其他进程,不退出当前进程
            for p in other_pids:
                try:
                    os.kill(p, 9)
                    print(f"  ✅ 清理残留进程: {p}")
                except Exception as e:
                    print(f"  ❌ 清理失败: {p} ({e})")
            # 等待清理完成
            time.sleep(2)
            print(f"✅ 当前进程继续运行 (pid: {current_pid})")
        else:
            print(f"✅ 单进程运行 (pid: {current_pid})")
    except Exception as e:
        print(f"⚠️  重复检测失败:{e}")

BOT_TOKEN = "8666044834:AAFwV_Ss5Vi-pj7e_w1uESKwZisJDHja0iM"
GROUP_IDS = [-1003719261162, -1003672411075]  # 主群 + 备用群
MAIN_GROUP_ID = -1003719261162  # 主群ID,用于跨群去重优先级判断
WECHAT_TARGET = "o9cq80zZk50Q33Snd8zOZ5vlAEQ4@im.wechat"

# 已处理的消息 ID 集合 (防止重复)
processed_messages = set()

# 指令指纹去重(同群相同指令不重复执行)
FINGERPRINT_EXPIRE = 900  # 同群去重过期时间(秒), 默认15分钟
FINGERPRINT_FILE = '/tmp/trade_fingerprints.json'
fingerprints = {}

# 🦞 原文哈希去重(跨群相同信号不重复执行)
TEXT_HASH_EXPIRE = 3600  # 跨群去重过期时间(秒), 默认1小时
TEXT_HASH_FILE = '/tmp/trade_text_hashes.json'
text_hashes = {}  # {hash: timestamp}

# 🦞 信号核心价格去重(同群不同用户转发同一信号)
SIGNAL_PRICES_EXPIRE = 900  # 信号价格去重过期(秒), 默认15分钟
SIGNAL_PRICES_FILE = '/tmp/trade_signal_prices.json'
signal_prices_cache = {}  # {prices_key: timestamp}

def extract_signal_prices(text):
    """提取信号中的核心价格数字(>=1000的整数价格), 返回排序后的tuple作为指纹"""
    import re as _re
    prices = _re.findall(r'\b(1\d{4,}|[2-9]\d{3,})\b', text)
    if not prices:
        return None
    # 去重并排序
    unique_prices = sorted(set(int(p) for p in prices))
    return tuple(unique_prices)

def load_signal_prices():
    global signal_prices_cache
    try:
        if os.path.exists(SIGNAL_PRICES_FILE):
            with open(SIGNAL_PRICES_FILE, 'r') as f:
                signal_prices_cache = json.load(f)
            current_time = time.time()
            signal_prices_cache = {k: v for k, v in signal_prices_cache.items() if current_time - v < SIGNAL_PRICES_EXPIRE}
            save_signal_prices()
    except:
        signal_prices_cache = {}

def save_signal_prices():
    try:
        with open(SIGNAL_PRICES_FILE, 'w') as f:
            json.dump(signal_prices_cache, f)
    except Exception as e:
        print(f"⚠️ 保存信号价格缓存失败:{e}")

def is_duplicate_signal_prices(text):
    """检查信号核心价格是否在短时间内已处理过"""
    global signal_prices_cache
    prices = extract_signal_prices(text)
    if not prices:
        return False
    prices_key = str(prices)
    current_time = time.time()
    # 清理过期
    stale = [k for k, v in signal_prices_cache.items() if current_time - v > SIGNAL_PRICES_EXPIRE]
    for k in stale:
        del signal_prices_cache[k]
    if prices_key in signal_prices_cache:
        last_seen = signal_prices_cache[prices_key]
        if current_time - last_seen < SIGNAL_PRICES_EXPIRE:
            print(f"  ⏭️  15分钟内相同信号价格组合已处理,跳过 (价格:{prices_key})")
            return True
    signal_prices_cache[prices_key] = current_time
    save_signal_prices()
    return False

def normalize_signal_text(t):
    """标准化信号文本,去除群名/转发来源等噪音,用于跨群哈希对比"""
    import re as _re
    # 去除转发来源标记
    t = _re.sub(r'Forwarded from .+', '', t, flags=_re.IGNORECASE)
    # 去除 @用户名/@群名
    t = _re.sub(r'@[\w]+', '', t)
    # 去除群名前缀
    t = _re.sub(r'[【\[][\s\S]*?[】\]]', '', t)
    # 去除多余空白
    t = _re.sub(r'\s+', '', t)
    return t.strip()

def load_text_hashes():
    """加载已处理信号原文哈希"""
    global text_hashes
    try:
        if os.path.exists(TEXT_HASH_FILE):
            with open(TEXT_HASH_FILE, 'r') as f:
                text_hashes = json.load(f)
            current_time = time.time()
            text_hashes = {k: v for k, v in text_hashes.items() if current_time - v < TEXT_HASH_EXPIRE}
    except Exception as e:
        print(f"⚠️ 加载原文哈希失败:{e}")
        text_hashes = {}

def save_text_hashes():
    """保存已处理信号原文哈希"""
    try:
        with open(TEXT_HASH_FILE, 'w') as f:
            json.dump(text_hashes, f)
    except Exception as e:
        print(f"⚠️ 保存原文哈希失败:{e}")

offset = 0
_last_analysis_notify_ts = 0  # 上次"正在分析"通知时间戳, 防止同一批消息重复通知

def load_fingerprints():
    """加载已执行指令指纹"""
    global fingerprints
    try:
        if os.path.exists(FINGERPRINT_FILE):
            with open(FINGERPRINT_FILE, 'r') as f:
                fingerprints = json.load(f)
            # 清理过期记录(超过 5 分钟)
            current_time = time.time()
            fingerprints = {k: v for k, v in fingerprints.items() if current_time - v < FINGERPRINT_EXPIRE}
            save_fingerprints()
    except Exception as e:
        print(f"⚠️ 加载指纹失败:{e}")
        fingerprints = {}

def save_fingerprints():
    """保存指令指纹"""
    try:
        with open(FINGERPRINT_FILE, 'w') as f:
            json.dump(fingerprints, f)
    except Exception as e:
        print(f"⚠️ 保存指纹失败:{e}")

def build_fingerprint(intent, params, direction=None):
    """生成操作指纹

    设计原则:
    - 开单指纹:只比较核心开仓参数(方向+价格),止盈止损变化不触发重复
    - 平仓/修改指纹:保留完整参数
    """
    if intent == 'close_position':
        # 平仓指纹:意图+方向+比例
        ratio = params.get('ratio', 1.0)
        return f"{intent}:{direction}:{ratio}"

    # 开单指纹:只比较核心开仓参数(方向+价格),不包含止盈止损
    if intent == 'open_position':
        orders = params.get('orders', [])
        if orders:
            prices = ','.join(str(o.get('price', '')) for o in orders)
        else:
            prices = str(params.get('price', 'None'))
        # 核心开仓参数:意图+方向+价格(止盈止损变化不触发重复)
        return f"{intent}:{direction}:{prices}"

    # 其他操作(修改止盈/止损/价格):保留完整参数
    tp = params.get('take_profit', [])
    tp_str = ','.join(map(str, tp)) if tp else 'None'
    orders = params.get('orders', [])
    if orders:
        prices = ','.join(str(o.get('price', '')) for o in orders)
    else:
        prices = str(params.get('price', 'None'))
    return f"{intent}:{direction}:{prices}:{params.get('stop_loss', 'None')}:{tp_str}"

def is_duplicate_fingerprint(intent, params, direction=None):
    """检查是否是指纹重复的指令"""
    # 撤单/查询/闲聊不过滤(平仓也要去重,避免置顶消息重复平仓)
    if intent in ['cancel_orders', 'query', 'chat']:
        return False

    fingerprint = build_fingerprint(intent, params, direction)
    current_time = time.time()

    # 🦞 清理过期指纹(每次判断时清理)
    global fingerprints
    fingerprints = {k: v for k, v in fingerprints.items() if current_time - v < FINGERPRINT_EXPIRE}

    if fingerprint in fingerprints:
        last_exec = fingerprints[fingerprint]
        if current_time - last_exec < FINGERPRINT_EXPIRE:
            print(f"  ⏭️  15分钟内相同指令已执行,跳过 (指纹:{fingerprint})")
            return True

    # 记录新指纹
    fingerprints[fingerprint] = current_time
    save_fingerprints()
    return False

def ensure_swap_symbol(symbol):
    """确保使用 SWAP 交易对"""
    if not symbol:
        return 'BTC-USDT-SWAP'
    if not symbol.endswith('-SWAP'):
        symbol = symbol + '-SWAP'
    return symbol

def scan_all_positions():
    """扫描所有持仓,返回所有有仓位的币种信息"""
    positions = []
    try:
        sys.path.insert(0, '/root/.openclaw/workspace')
        from scripts.okx_close_position import API_BASE, get_headers as okx_headers
        rp = '/api/v5/account/positions'
        resp = requests.get(API_BASE + rp, headers=okx_headers('GET', rp), timeout=10)
        for p in resp.json().get('data', []):
            pos = float(p.get('pos', '0') or '0')
            if pos != 0:
                inst = p.get('instId', '')
                avg_px = float(p.get('avgPx', '0') or '0')
                liq_px = float(p.get('liqPx', '0') or '0')
                direction = 'short' if liq_px > avg_px else 'long'
                positions.append({'symbol': inst, 'direction': direction, 'size': abs(pos), 'avg_px': avg_px})
    except Exception as e:
        print(f"⚠️ 扫描仓位失败: {e}")
    return positions

def scan_pending_orders():
    """扫描所有待成交挂单"""
    orders = []
    try:
        sys.path.insert(0, '/root/.openclaw/workspace')
        from scripts.okx_close_position import API_BASE, get_headers as okx_headers
        rp = '/api/v5/trade/orders-pending'
        resp = requests.get(API_BASE + rp, headers=okx_headers('GET', rp), timeout=10)
        for o in resp.json().get('data', []):
            inst = o.get('instId', '')
            side = o.get('side', '')  # buy/sell
            price = float(o.get('px', '0') or '0')
            sz = float(o.get('sz', '0') or '0')
            if price > 0 and sz > 0:
                orders.append({'symbol': inst, 'side': side, 'price': price, 'size': sz})
    except Exception as e:
        print(f"⚠️ 扫描挂单失败: {e}")
    return orders

def get_position_info(symbol='BTC-USDT-SWAP'):
    """获取当前完整仓位信息(含挂单方向)"""
    info = {
        'has_long': False, 'has_short': False,
        'long_size': 0, 'short_size': 0,
        'long_avg_px': 0, 'short_avg_px': 0,
        'pending_count': 0, 'algo_count': 0,
        'algo_details': '',
        # 挂单方向
        'has_pending_long': False, 'has_pending_short': False,
        'pending_long_count': 0, 'pending_short_count': 0,
    }
    try:
        sys.path.insert(0, '/root/.openclaw/workspace')
        from scripts.okx_close_position import API_BASE, get_headers as okx_headers

        # 持仓(扫描所有品种,不限于指定symbol)
        rp = '/api/v5/account/positions'
        resp = requests.get(API_BASE + rp, headers=okx_headers('GET', rp), timeout=10)
        for p in resp.json().get('data', []):
            p_inst = p.get('instId', '')
            # 只统计指定symbol的持仓
            if p_inst != symbol and p_inst != symbol.replace('-SWAP', ''):
                continue
            pos = float(p.get('pos', '0') or '0')
            avg_px = float(p.get('avgPx', '0') or '0')
            if pos != 0:
                # 🦞 单向持仓模式:pos>0=做多, pos<0=做空
                # 双向持仓模式:posSide字段区分方向
                pos_side = p.get('posSide', 'net')
                if pos_side == 'net':
                    # 单向持仓模式,用pos正负判断
                    if pos > 0:
                        info['has_long'] = True
                        info['long_size'] = abs(pos)
                        info['long_avg_px'] = avg_px
                    else:
                        info['has_short'] = True
                        info['short_size'] = abs(pos)
                        info['short_avg_px'] = avg_px
                else:
                    # 双向持仓模式,用posSide判断
                    if pos_side == 'long':
                        info['has_long'] = True
                        info['long_size'] = abs(pos)
                        info['long_avg_px'] = avg_px
                    else:
                        info['has_short'] = True
                        info['short_size'] = abs(pos)
                        info['short_avg_px'] = avg_px

        # 挂单(含方向识别)
        rp2 = f'/api/v5/trade/orders-pending?instId={symbol}'
        resp2 = requests.get(API_BASE + rp2, headers=okx_headers('GET', rp2), timeout=10)
        pending_orders = resp2.json().get('data', [])
        info['pending_count'] = len(pending_orders)
        for o in pending_orders:
            side = o.get('side', '')       # buy / sell
            pos_side = o.get('posSide', '')  # long / short / net
            # 双向持仓模式
            if pos_side == 'long' or (pos_side == 'net' and side == 'buy'):
                info['has_pending_long'] = True
                info['pending_long_count'] += 1
            elif pos_side == 'short' or (pos_side == 'net' and side == 'sell'):
                info['has_pending_short'] = True
                info['pending_short_count'] += 1

        # 条件单
        algo_details = []
        for otype in ['conditional', 'oco']:
            rp3 = f'/api/v5/trade/orders-algo-pending?ordType={otype}'
            resp3 = requests.get(API_BASE + rp3, headers=okx_headers('GET', rp3), timeout=10)
            for o in resp3.json().get('data', []):
                o_inst = o.get('instId', '')
                if o_inst == symbol or o_inst == symbol.replace('-SWAP', ''):
                    info['algo_count'] += 1
                    tp = o.get('tpTriggerPx', '-')
                    sl = o.get('slTriggerPx', '-')
                    sz = o.get('sz', '0')
                    algo_details.append(f"{otype}:sz={sz},tp={tp},sl={sl}")
        info['algo_details'] = '; '.join(algo_details) if algo_details else '无'

    except Exception as e:
        print(f"    ⚠️ 获取仓位失败:{e}")
    return info

def send_wechat(msg, max_retries=3):
    """发送微信通知(带重试、间隔和送达验证)"""
    import shlex
    safe_msg = shlex.quote(msg)
    # 加 --account 确保走正确账号
    cmd = f'openclaw message send --channel openclaw-weixin --account ea3465f35dfb-im-bot --target {WECHAT_TARGET} -m {safe_msg}'

    for attempt in range(1, max_retries + 1):
        try:
            # 重试前等 2 秒,避免插件重载竞态
            if attempt > 1:
                wait = attempt * 5  # 10s, 15s
                print(f"      {wait}秒后重试...")
                time.sleep(wait)
            else:
                time.sleep(2)  # 首次也稍等,让插件初始化稳定

            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()

            # 必须同时满足:returncode=0 且 stdout 包含 "Sent via"
            if proc.returncode == 0 and 'Sent via' in stdout:
                print(f"  ✅ 微信推送成功 (第{attempt}次) | {stdout}")
                return True
            else:
                print(f"  ⚠️ 微信推送未确认 (第{attempt}/{max_retries}次)")
                print(f"      returncode={proc.returncode}")
                print(f"      stdout={stdout[:200]}")
                print(f"      stderr={stderr[:200]}")
        except subprocess.TimeoutExpired:
            print(f"  ⚠️ 微信推送超时 (第{attempt}/{max_retries}次)")
        except Exception as e:
            print(f"  ⚠️ 微信推送异常 (第{attempt}/{max_retries}次): {e}")

    print(f"  ❌ 微信推送最终失败!已重试{max_retries}次")
    return False

def execute_intent(intent_result, text, position_info, msg_start_time=None):
    """
    根据意图分析结果执行操作 + 微信通知(全自动模式)

    Args:
        intent_result: LLM 分析结果
        text: 原始消息文本
        position_info: 仓位信息
        msg_start_time: 消息接收时间(用于计算耗时)

    Returns:
        str or None: 微信通知消息 (不论成败都通知)
    """
    if msg_start_time is None:
        msg_start_time = time.time()

    exec_start = time.time()
    intent = intent_result.get('intent', 'chat')
    params = intent_result.get('params', {})
    direction = intent_result.get('direction')
    confidence = intent_result.get('confidence', 0)
    reason = intent_result.get('reason', '')
    symbol = ensure_swap_symbol(intent_result.get('symbol', 'BTC-USDT-SWAP'))

    # ===== 固定开仓参数配置 =====
    try:
        with open('/root/.openclaw/workspace/config/trade-config.json', 'r') as f:
            trade_config = json.load(f)
        force_fixed_position = trade_config.get('force_fixed_position', False)
        fixed_leverage = trade_config.get('fixed_leverage', 100)
        fixed_margin_ratios = trade_config.get('fixed_margin_ratios', [0.02, 0.03])
    except Exception as e:
        force_fixed_position = False
        fixed_leverage = 100
        fixed_margin_ratios = [0.02, 0.03]
        print(f'  ⚠️ 读取trade-config失败: {e}')

    print(f"  ⏱️ [T+{exec_start-msg_start_time:.1f}s] 开始执行意图: {intent}")

    # ===== 自动交易总开关 =====
    try:
        with open('/root/.openclaw/workspace/config/trade-config.json', 'r') as _f:
            _tc = json.load(_f)
        if not _tc.get('auto_trade_enabled', True):
            print(f"  ⏸️ 自动交易已暂停，仅分析+转发，不执行: {intent}")
            notify_msg = f"🦞 信号分析（未执行）\n\n⚠️ 自动交易已暂停\n意图: {intent}\n交易对: {symbol}\n方向: {direction or '未知'}\n"
            notify_msg += f"原文: {text[:150]}"
            send_wechat(notify_msg)
            return None
    except Exception:
        pass

    # 闲聊/查询/直播/条件止盈不执行(条件止盈由监控脚本处理)
    if intent in ['chat', 'query', 'live_stream', 'conditional_modify_tp']:
        print(f"  i️ {intent}消息,不直接执行")
        return None

    # 低置信度不执行
    if confidence < 0.7:
        print(f"  ⏭️ 置信度过低({confidence}),不执行")
        return None

    # 🦞 重复指令过滤(5 分钟内相同指令不重复执行,但仍推送微信通知)
    if is_duplicate_fingerprint(intent, params, direction):
        dup_msg = f"⏭️ 重复信号已跳过(5分钟内相同指令)\n"
        dup_msg += f"意图: {intent} | 方向: {direction or '未知'}\n"
        dup_msg += f"交易对: {symbol}\n"
        dup_msg += f"原文: {text[:100]}"
        send_wechat(dup_msg)
        return None

    # ============ 止盈止损价格合理性校验 ============
    def get_current_price(sym):
        """获取当前最新价格"""
        try:
            from scripts.okx_close_position import API_BASE, get_headers as okx_h
            rp = f'/api/v5/market/ticker?instId={sym}'
            resp = requests.get(API_BASE + rp, headers=okx_h('GET', rp), timeout=10)
            data = resp.json().get('data', [])
            if data:
                return float(data[0].get('last', 0))
        except:
            pass
        return None

    def get_position_direction(sym):
        """获取持仓方向: 'long'/'short'/None"""
        try:
            from scripts.okx_close_position import API_BASE, get_headers as okx_h
            rp = f'/api/v5/account/positions?instId={sym}'
            resp = requests.get(API_BASE + rp, headers=okx_h('GET', rp), timeout=10)
            data = resp.json().get('data', [])
            if data:
                pos = float(data[0].get('pos', '0') or '0')
                if pos == 0:
                    return None
                liq_px = float(data[0].get('liqPx', '0') or '0')
                avg_px = float(data[0].get('avgPx', '0') or '0')
                return 'short' if liq_px > avg_px else 'long'
        except:
            pass
        return None

    if intent in ('modify_tp', 'modify_sl'):
        pos_dir = get_position_direction(symbol)
        cur_price = get_current_price(symbol)
        if pos_dir and cur_price:
            tp_list = params.get('take_profit', [])
            sl_val = params.get('stop_loss')
            if intent == 'modify_tp' and tp_list:
                new_tp = tp_list[0]
                # 做空:止盈应在现价以下; 做多:止盈应在现价以上
                invalid = (pos_dir == 'short' and new_tp > cur_price) or (pos_dir == 'long' and new_tp < cur_price)
                if invalid:
                    err_msg = f"🛑 止盈价格异常: {new_tp} {'>' if pos_dir=='short' else '<'} 现价{cur_price:.0f} ({pos_dir}), 不执行"
                    print(f"  {err_msg}")
                    send_wechat(err_msg)
                    return None
            if intent == 'modify_sl' and sl_val:
                # 做空:止损应在现价以上; 做多:止损应在现价以下
                invalid = (pos_dir == 'short' and sl_val < cur_price) or (pos_dir == 'long' and sl_val > cur_price)
                if invalid:
                    err_msg = f"🛑 止损价格异常: {sl_val} {'<' if pos_dir=='short' else '>'} 现价{cur_price:.0f} ({pos_dir}), 不执行"
                    print(f"  {err_msg}")
                    send_wechat(err_msg)
                    return None

    # ============ 修改止盈 ============
    if intent == 'modify_tp':
        tp_list = params.get('take_profit', [])
        if not tp_list:
            print(f"  ⏭️ 无止盈价,跳过")
            return None
        new_tp = tp_list[0]
        print(f"  🔧 执行修改止盈 → {new_tp} ({symbol})")

        from scripts.okx_modify_tp_sl import modify_take_profit
        result = modify_take_profit(new_tp, symbol=symbol)

        msg = f"🦞 止盈修改\n\n"
        msg += f"交易对: {symbol}\n"
        msg += f"新止盈价: {new_tp}\n"
        msg += f"{result.get('message', '')}\n"
        msg += f"持仓: {result.get('position', '?')} | 条件单: {result.get('algo_total', '?')}\n"
        if result.get('pending_modified', 0) > 0:
            msg += f"限价单重挂: {result['pending_modified']}个\n"
        msg += f"平衡: {'✅' if result.get('balanced') else '❌ 不平衡!'}\n"
        if result.get('errors'):
            msg += f"错误: {', '.join(result['errors'])}\n"
        return msg

    # ============ 修改止损 ============
    elif intent == 'modify_sl':
        # 止损价格已在上方校验块中检查
        new_sl = params.get('stop_loss')
        if not new_sl:
            print(f"  ⏭️ 无止损价,跳过")
            return None
        print(f"  🔧 执行修改止损 → {new_sl} ({symbol})")

        from scripts.okx_modify_tp_sl import modify_stop_loss
        result = modify_stop_loss(new_sl, symbol=symbol)

        msg = f"🦞 止损修改\n\n"
        msg += f"交易对: {symbol}\n"
        msg += f"新止损价: {new_sl}\n"
        msg += f"{result.get('message', '')}\n"
        msg += f"持仓: {result.get('position', '?')} | 条件单: {result.get('algo_total', '?')}\n"
        if result.get('pending_modified', 0) > 0:
            msg += f"限价单重挂: {result['pending_modified']}个\n"
        msg += f"平衡: {'✅' if result.get('balanced') else '❌ 不平衡!'}\n"
        return msg

    # ============ 平仓 ============
    elif intent == 'close_position':
        # 🦞 检查强制全平开关（执行分支）
        try:
            with open('/root/.openclaw/workspace/config/trade-config.json', 'r') as f:
                close_trade_config = json.load(f)
        except Exception:
            close_trade_config = {}
        if close_trade_config.get('force_full_close', False):
            close_ratio = 1.0
        else:
            close_ratio = params.get('close_ratio')
        move_breakeven = params.get('move_breakeven', False)
        print(f"  🔍 执行平仓: direction={direction} ratio={close_ratio} breakeven={move_breakeven}")

        # 🦞 如果指定symbol无仓位:消息明确提到某币种时不自动切换,只警告
        if not position_info.get('has_long') and not position_info.get('has_short'):
            # 先确认是否完全无持仓
            from scripts.okx_close_position import API_BASE, get_headers as okx_headers
            rp = '/api/v5/account/positions'
            resp = requests.get(API_BASE + rp, headers=okx_headers('GET', rp), timeout=10)
            all_pos = resp.json().get('data', [])
            active_symbols = []
            for p in all_pos:
                if float(p.get('pos', '0') or '0') != 0:
                    active_symbols.append(p.get('instId'))
                    print(f"    发现持仓: {p.get('instId')} pos={p.get('pos')} avgPx={p.get('avgPx')}")

            if not active_symbols:
                # 完全空仓,跳过
                warn_msg = f"🦞 ⚠️ 止盈信号 - 但系统无任何仓位\n\n群消息: {text[:150]}\n交易对: {symbol}\n请手动确认是否需要操作"
                print(f"  ⏭️ 完全空仓,跳过执行,推送警告")
                send_wechat(warn_msg)
                return None

            # 有其他仓位,但消息指定了不同的币种 → 不自动切换,只警告
            print(f"  ⚠️ {symbol} 无仓位,但消息明确指定该币种,不自动切换到其他持仓")
            warn_msg = f"🦞 ⚠️ 币种不匹配\n\n群消息指定: {symbol}\n实际持仓: {'、'.join(active_symbols)}\n\n原文: {text[:150]}\n\n为避免误操作,未自动执行。请手动确认。"
            send_wechat(warn_msg)
            return None

        # 部分平仓逻辑
        if close_ratio and 0 < close_ratio < 1:
            from scripts.okx_close_position import close_position_partial

            # 自动识别方向
            actual_dir = direction
            if actual_dir in ['auto', None, '']:
                if position_info.get('has_long'):
                    actual_dir = 'long'
                elif position_info.get('has_short'):
                    actual_dir = 'short'
                else:
                    return f"🦞 ❌ 无持仓可平"

            _t0 = time.time()
            print(f"  ⏱️ 开始平仓...")
            result = close_position_partial(actual_dir, ratio=close_ratio, symbol=symbol)
            print(f"  ⏱️ 平仓完成耗时: {time.time() - _t0:.2f}s")
            print(f"  📊 平仓结果: {result}")  # 打印完整结果

            msg = f"🦞 部分平仓执行\n\n"
            if result.get('success'):
                msg += f"✅ {result.get('message', '完成')}\n"

                remaining_size = float(result.get('total_size', 0)) - float(result.get('closed_size', 0))
                avg_px = result.get('avg_px') or position_info.get(f'{actual_dir}_avg_px', 0)

                # 部分平仓后,始终按剩余仓位调整所有条件单数量
                # 如果同时 move_breakeven,额外把止损价改为开仓均价
                if remaining_size > 0:
                    print(f"  🔧 调整条件单数量 → 剩余仓位 {remaining_size}" + (f",保本止损 → {avg_px}" if move_breakeven and avg_px else ""))
                    try:
                        from scripts.okx_close_position import API_BASE, get_headers as okx_headers

                        # 查找所有条件单(conditional + oco)
                        algo_orders = []
                        for otype in ['conditional', 'oco']:
                            rp = f'/api/v5/trade/orders-algo-pending?instId={symbol}&ordType={otype}'
                            resp = requests.get(API_BASE + rp, headers=okx_headers('GET', rp), timeout=10)
                            algo_orders.extend(resp.json().get('data', []))

                        if algo_orders:
                            modified = 0
                            for ao in algo_orders:
                                algo_id = ao.get('algoId')
                                old_sl = ao.get('slTriggerPx', '')
                                old_tp = ao.get('tpTriggerPx', '')
                                old_sz = ao.get('sz', '')
                                print(f"    📋 条件单 {algo_id}: sl={old_sl} tp={old_tp} sz={old_sz}")

                                # 构建修改参数:始终调整数量
                                # SWAP 张数精度 0.01
                                sz_precision = 2 if symbol.endswith('-SWAP') else 7
                                amend_params = {
                                    'instId': symbol,
                                    'algoId': algo_id,
                                    'newSz': str(round(remaining_size, sz_precision))
                                }
                                # 保本时额外修改止损价
                                if move_breakeven and avg_px:
                                    amend_params['newSlTriggerPx'] = str(avg_px)

                                amend_body = json.dumps(amend_params, separators=(',', ':'))
                                amend_rp = '/api/v5/trade/amend-algos'
                                amend_resp = requests.post(API_BASE + amend_rp, headers=okx_headers('POST', amend_rp, amend_body), data=amend_body, timeout=10)
                                amend_data = amend_resp.json()
                                print(f"    🔧 amend-algos 响应: {amend_data}")

                                # 检查返回结果
                                if amend_data.get('code') == '0':
                                    data_list = amend_data.get('data', [])
                                    if data_list and len(data_list) > 0:
                                        sCode = data_list[0].get('sCode', '1')
                                        if sCode == '0':
                                            modified += 1
                                            log_msg = f"    ✅ 修改成功: sz {old_sz}→{round(remaining_size, sz_precision)}"
                                            if move_breakeven and avg_px:
                                                log_msg += f", sl {old_sl}→{avg_px}"
                                            print(log_msg)
                                        else:
                                            sMsg = data_list[0].get('sMsg', amend_data.get('msg', ''))
                                            print(f"    ❌ 修改失败: sCode={sCode} {sMsg}")
                                    else:
                                        # amend-algos 成功但 data 为空也算成功
                                        modified += 1
                                        print(f"    ✅ 修改成功: sz {old_sz}→{round(remaining_size, sz_precision)}")
                                else:
                                    print(f"    ❌ 修改失败: {amend_data.get('msg', '未知错误')}")

                            if modified > 0:
                                msg += f"✅ 条件单已调整: {modified}个 (数量→{round(remaining_size, 2)})"
                                if move_breakeven and avg_px:
                                    msg += f", 保本止损→{avg_px}"
                                msg += "\n"
                            else:
                                msg += f"❌ 条件单调整失败\n"
                        else:
                            msg += f"i️ 无条件单需调整\n"
                    except Exception as e:
                        msg += f"❌ 调整条件单失败: {e}\n"
                elif remaining_size == 0:
                    msg += f"i️ 仓位已全部平完\n"
            else:
                # 更详细的错误信息
                error_detail = result.get('error') or result.get('message') or '无返回信息'
                msg += f"❌ 平仓失败: {error_detail}\n"
                msg += f"📋 完整响应: {result}\n"

            return msg

        # 全量平仓逻辑(使用 close_position_partial 100%,支持多币种)
        from scripts.okx_close_position import close_position_partial, cancel_all_algo_orders, cancel_pending_orders

        # 确定平仓方向
        actual_dir = direction
        if actual_dir in ['auto', None, '']:
            if position_info.get('has_short'):
                actual_dir = 'short'
            elif position_info.get('has_long'):
                actual_dir = 'long'
            else:
                actual_dir = 'all'

        msg = f"🦞 平仓执行\n\n"

        if actual_dir == 'all':
            # 平所有方向
            for d in ['long', 'short']:
                if position_info.get(f'has_{d}'):
                    r = close_position_partial(d, ratio=1.0, symbol=symbol)
                    msg += f"{'✅' if r.get('success') else '❌'} {r.get('message', '?')}\n"
        else:
            if position_info.get(f'has_{actual_dir}'):
                r = close_position_partial(actual_dir, ratio=1.0, symbol=symbol)
                msg += f"{'✅' if r.get('success') else '❌'} {r.get('message', '?')}\n"
            else:
                msg += f"无{actual_dir}持仓\n"

        # 取消相关条件单和挂单
        try:
            from scripts.okx_close_position import API_BASE, get_headers as okx_h
            for otype in ['conditional', 'oco']:
                rp = f'/api/v5/trade/orders-algo-pending?instId={symbol}&ordType={otype}'
                resp = requests.get(API_BASE + rp, headers=okx_h('GET', rp), timeout=10)
                for a in resp.json().get('data', []):
                    cb = json.dumps([{'instId': symbol, 'algoId': a['algoId']}])
                    crp = '/api/v5/trade/cancel-algos'
                    requests.post(API_BASE + crp, headers=okx_h('POST', crp, cb), data=cb, timeout=10)
                    msg += f"✅ 取消条件单 {a['algoId'][:8]}...\n"
        except Exception as e:
            print(f"  ⚠️ 清理条件单失败: {e}")

        return msg

    # ============ 条件平仓 ============
    elif intent == 'conditional_close_position':
        trigger_price = params.get('trigger_price')
        close_ratio = params.get('close_ratio')

        print(f"  🔍 执行条件平仓: trigger={trigger_price} ratio={close_ratio}")

        if not trigger_price:
            print(f"  ⚠️ 缺少触发价格,跳过")
            return None

        # 检查是否有仓位
        if not position_info.get('has_long') and not position_info.get('has_short'):
            warn_msg = f"🦞 ⚠️ 条件平仓信号 - 但系统无仓位\n\n触发价: {trigger_price}\n平仓比例: {close_ratio or '全部'}\n交易对: {symbol}\n群消息: {text[:100]}"
            print(f"  ⏭️ 无仓位,跳过执行,推送警告")
            send_wechat(warn_msg)
            return None

        # 创建条件平仓单(使用 OKX 策略订单)
        from scripts.okx_close_position import API_BASE, get_headers as okx_h

        # 获取当前持仓
        pos_resp = requests.get(API_BASE + '/api/v5/account/positions?instId=' + symbol, headers=okx_h('GET', '/api/v5/account/positions?instId=' + symbol), timeout=10)
        pos_data = pos_resp.json().get('data', [])

        if not pos_data:
            print(f"  ⏭️ 无持仓数据,跳过")
            return None

        pos = pos_data[0]
        pos_size = float(pos.get('pos', '0'))
        pos_dir = 'long' if pos_size > 0 else 'short'

        # 计算平仓量
        close_size = abs(pos_size) * (close_ratio or 1.0)
        close_sz = int(close_size * 10) / 10  # 保留1位小数

        # 创建触发平仓订单
        # trigger: 价格达到 trigger_price 时,以市价平仓
        order_type = 'trigger'  # 触发单
        side = 'sell' if pos_dir == 'long' else 'buy'

        algo_body = {
            'instId': symbol,
            'tdMode': 'cross',
            'side': side,
            'posSide': 'long' if pos_dir == 'long' else 'short',
            'ordType': 'trigger',
            'sz': str(close_sz),
            'triggerPx': str(trigger_price),
            'triggerPxType': 'last',  # 最新价格触发
            'orderPx': '-1',  # 市价单
        }

        algo_path = '/api/v5/trade/order-algo'
        algo_resp = requests.post(API_BASE + algo_path, headers=okx_h('POST', algo_path, json.dumps([algo_body])), json=[algo_body], timeout=10)
        algo_result = algo_resp.json()

        if algo_result.get('code') == '0':
            msg = f"🦞 条件平仓已设置\n\n"
            msg += f"触发价: {trigger_price}\n"
            msg += f"平仓量: {close_sz} ({close_ratio or '全部'})\n"
            msg += f"交易对: {symbol}\n"
            msg += f"方向: {pos_dir}\n"
            print(f"  ✅ 条件平仓单已创建")
            return msg
        else:
            print(f"  ❌ 创建条件单失败: {algo_result.get('msg')}")
            return f"❌ 条件平仓失败: {algo_result.get('msg')}"

    # ============ 撤单 ============
    elif intent == 'cancel_orders':
        print(f"  🔍 执行撤单")
        from scripts.okx_close_position import cancel_all_algo_orders, cancel_pending_orders

        # 🦞 提取币种参数(null = 撤销所有币种)
        cancel_symbol = params.get('symbol')
        if cancel_symbol:
            print(f"    📍 币种过滤: {cancel_symbol}")
        else:
            print(f"    📍 撤销所有币种挂单")

        cancelled_algo = cancel_all_algo_orders(None, cancel_symbol)
        cancelled_pending = cancel_pending_orders(None, cancel_symbol)

        msg = f"🦞 撤单执行\n\n"
        if cancel_symbol:
            msg += f"币种: {cancel_symbol}\n"
        else:
            msg += f"币种: 所有\n"
        msg += f"取消条件单: {len(cancelled_algo)} 个\n"
        msg += f"取消限价挂单: {len(cancelled_pending)} 个"
        return msg

    # ============ 开单 ============
    elif intent == 'open_position':
        orders_list = params.get('orders', [])
        if not orders_list and params.get('price'):
            orders_list = [{"price": params['price'], "type": "market"}]

        sl = params.get('stop_loss')
        tp_list = params.get('take_profit', [])

        if not orders_list:
            print(f"  ⏭️ 无开单价,跳过")
            return None

        if not tp_list:
            print(f"  ⏭️ 无止盈价,跳过自动开单")
            return None

        # 🦞 方向感知多币种安全检测:反方向持仓可共存,同方向持仓才拦截
        from scripts.okx_close_position import API_BASE as _saapi, get_headers as _saokx_headers
        _resp = requests.get(_saapi + '/api/v5/account/positions', headers=_saokx_headers('GET', '/api/v5/account/positions'), timeout=10)
        _other_positions = []
        _same_dir_positions = []
        _opposite_dir_positions = []
        for _p in _resp.json().get('data', []):
            _pos = float(_p.get('pos', '0') or '0')
            if _pos != 0:
                _inst = _p.get('instId', '')
                _other_positions.append(_inst)
                _pos_dir = 'long' if _pos > 0 else 'short'
                # 判断是否同方向
                if _pos_dir == direction:
                    _same_dir_positions.append(f"{_inst}({_pos_dir})")
                else:
                    _opposite_dir_positions.append(f"{_inst}({_pos_dir})")

        _signal_coin = symbol.split('-')[0]
        _held_coins = [_o.split('-')[0] for _o in _other_positions if '-SWAP' in _o or '-USDT' in _o]

        if _same_dir_positions and _signal_coin not in _held_coins:
            # 同方向多币种持仓 → 拦截(风险高)
            warn_msg = f"🦞 ⚠️ 开单信号已跳过 - 同方向多币种持仓保护\n\n信号: {_signal_coin} ({symbol}) {direction}\n同方向持仓: {'、'.join(_same_dir_positions)}\n\n原文: {text[:150]}\n\n为避免同方向多币种爆仓风险,未执行开单。请先处理现有持仓。"
            print(f"  ⏭️ 同方向安全保护:持有 {'、'.join(_same_dir_positions)},跳过 {_signal_coin} {direction} 开单")
            send_wechat(warn_msg)
            return None

        if _opposite_dir_positions and _signal_coin not in _held_coins:
            # 反方向多币种持仓 → 允许(多空对冲)
            print(f"  ✅ 反方向对冲允许:持有 {'、'.join(_opposite_dir_positions)},允许 {_signal_coin} {direction} 开单")

        print(f"  📈 执行开单:orders={orders_list} sl={sl} tp={tp_list}")

        # ============ 🦞 更新模式：同品种同方向持仓 > 1% 时，只更新挂单+止盈止损，不加仓 ============
        _same_dir_holding = (direction == 'short' and position_info.get('has_short')) or \
                           (direction == 'long' and position_info.get('has_long'))

        if _same_dir_holding and direction:
            from scripts.okx_auto_trade import get_position_margin, get_account_equity, get_position_size
            _pm = get_position_margin(symbol)
            _eq = get_account_equity()
            print(f"  📊 更新模式检查: 持仓保证金={_pm:.2f} USDT, 账户权益={_eq:.2f} USDT, 占比={_pm/_eq*100:.2f}%")

            if _eq > 0 and _pm > _eq * 0.01:
                # 进入更新模式：不加仓，只更新挂单和止盈止损
                print(f"  🔄 [更新模式] 持仓>{_eq*0.01:.2f}USDT(1%),进入更新模式")
                
                # 获取当前持仓量
                _current_pos_size = get_position_size(symbol)
                _dir_str = "做空" if direction in ['short', '做空'] else "做多"
                _update_msg = f"🦞 信号更新（持仓>1%，加仓保护）\n\n"
                _update_msg += f"交易对: {symbol}\n"
                _update_msg += f"方向: {_dir_str}\n"
                _update_msg += f"操作: 更新挂单+止盈止损（跳过市价加仓）\n"
                _update_msg += f"当前持仓: {_current_pos_size} 张\n\n"

                # --- Step 1: 获取现有挂单信息(用于通知对比+风控检查) ---
                from scripts.okx_close_position import API_BASE, get_headers as okx_h
                _rp_old = f'/api/v5/trade/orders-pending?instId={symbol}'
                _resp_old = requests.get(API_BASE + _rp_old, headers=okx_h('GET', _rp_old), timeout=10)
                _old_pending = _resp_old.json().get('data', [])
                _old_pending_prices = [o.get('px', '?') for o in _old_pending]
                _old_pending_ids = [o.get('ordId', '') for o in _old_pending]
                _old_pending_sizes = [float(o.get('sz', '0')) for o in _old_pending]
                _old_total_sz = sum(_old_pending_sizes) if _old_pending_sizes else 0

                # 🛡️ 更新模式风控检查：挂单数量是否会导致总保证金超限
                try:
                    import json
                    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'trade-config.json')
                    if os.path.exists(config_path):
                        with open(config_path, 'r') as f:
                            trade_config = json.load(f)
                        risk_control = trade_config.get('risk_control', {})
                        disable_risk = risk_control.get('disable_risk_control', False)
                        
                        if not disable_risk and _eq > 0:
                            # 计算旧挂单总保证金（用当前持仓量估算）
                            _old_total_margin = 0.0
                            if _old_pending and _current_pos_size > 0:
                                for _o in _old_pending:
                                    _old_price = float(_o.get('px', '0'))
                                    if _old_price > 0:
                                        margin_ratio = risk_control.get('single_order_max_margin_ratio', 0.03)
                                        _old_order_margin = _current_pos_size * _old_price * margin_ratio
                                        _old_total_margin += _old_order_margin
                            
                            # 总保证金 = 持仓保证金 + 旧挂单保证金
                            total_estimated_margin = _pm + _old_total_margin
                            
                            # 检查是否超限
                            if total_estimated_margin > _eq * risk_control.get('total_max_margin_ratio', 0.05):
                                warn_msg = f"🦞 ⚠️ 更新模式风控拦截\n\n交易对: {symbol}\n方向: {_dir_str}\n当前持仓保证金: {_pm:.2f} USDT\n预估挂单保证金: {_old_total_margin:.2f} USDT\n总预估: {total_estimated_margin:.2f} USDT\n限制: {_eq * risk_control.get('total_max_margin_ratio', 0.05):.2f} USDT\n\n原文: {text[:150]}\n\n已占5%风险上限，不更新挂单。"
                                print(f"  🛑 更新模式风控: 总保证金 {total_estimated_margin:.2f} > 限制 {_eq * risk_control.get('total_max_margin_ratio', 0.05):.2f}")
                                send_wechat(warn_msg)
                                return None
                except Exception as e:
                    print(f"  ⚠️ 更新模式风控检查异常: {e}")

                # --- Step 2: 获取现有条件单信息(用于通知对比) ---
                _old_algo = []
                _old_algo_ids = []
                _old_tp_prices = []
                _old_sl_prices = []
                for _otype in ['conditional', 'oco']:
                    _rp_algo = f'/api/v5/trade/orders-algo-pending?instId={symbol}&ordType={_otype}'
                    _resp_algo = requests.get(API_BASE + _rp_algo, headers=okx_h('GET', _rp_algo), timeout=10)
                    for _ao in _resp_algo.json().get('data', []):
                        _old_algo.append(_ao)
                        _old_algo_ids.append(_ao.get('algoId', ''))
                        _tp_val = _ao.get('tpTriggerPx', '')
                        _sl_val = _ao.get('slTriggerPx', '')
                        if _tp_val:
                            _old_tp_prices.append(_tp_val)
                        if _sl_val:
                            _old_sl_prices.append(_sl_val)

                # --- Step 3: 跳过市价单，不执行加仓 ---
                _market_orders = [o for o in orders_list if o.get('type') == 'market']
                _limit_orders = [o for o in orders_list if o.get('type') == 'limit']

                if _market_orders:
                    print(f"  ⏭️ 跳过市价单加仓: {_market_orders}")
                _update_msg += f"市价单: 跳过(持仓>{1}%不加仓)\n"

                # --- Step 4: 撤旧挂单 + 用信号B价格挂新限价单 ---
                if _limit_orders:
                    _cancelled_old = 0
                    for _old_id in _old_pending_ids:
                        _cb = json.dumps({'instId': symbol, 'ordId': _old_id})
                        _crp = '/api/v5/trade/cancel-order'
                        _cresp = requests.post(API_BASE + _crp, headers=okx_h('POST', _crp, _cb), data=_cb, timeout=10)
                        _cancelled_old += 1
                        print(f"    🗑️ 撤掉旧挂单: {_old_id}")

                    _update_msg += f"\n挂单更新:\n"
                    for _lo in _limit_orders:
                        _new_price = _lo.get('price')
                        # 数量用旧挂单总数量(更新模式不加仓)
                        _new_sz = str(round(_old_total_sz, 2)) if _old_total_sz > 0 else (str(round(_current_pos_size, 2)) if _current_pos_size > 0 else '1')
                        _side = 'sell' if direction in ['short', '做空'] else 'buy'

                        # 挂新限价单(不带 attachAlgoOrds，止盈止损单独更新)
                        _order_body = {
                            'instId': symbol,
                            'tdMode': 'cross',
                            'side': _side,
                            'posSide': 'net',
                            'ordType': 'limit',
                            'px': str(_new_price),
                            'sz': _new_sz
                        }
                        _order_body_str = json.dumps(_order_body)
                        _order_rp = '/api/v5/trade/order'
                        _order_resp = requests.post(API_BASE + _order_rp, headers=okx_h('POST', _order_rp, _order_body_str), data=_order_body_str, timeout=10)
                        _order_result = _order_resp.json()

                        if _order_result.get('code') == '0':
                            _ord_id = _order_result.get('data', [{}])[0].get('ordId', '')
                            _update_msg += f"  旧{_old_pending_prices} → 新限价@{_new_price} ({_new_sz}张) ✅\n"
                            print(f"    ✅ 挂新限价单: {_new_price} sz={_new_sz} ordId={_ord_id}")
                        else:
                            _update_msg += f"  新限价@{_new_price} 挂单失败: {_order_result.get('msg', '?')} ❌\n"
                            print(f"    ❌ 挂新限价单失败: {_order_result.get('msg')}")
                else:
                    _update_msg += f"\n限价单: 无(信号无限价单)\n"

                # --- Step 5: 更新止盈止损(amend-algos) ---
                _tp_updated = 0
                _sl_updated = 0
                if _old_algo_ids and (tp_list or sl):
                    _sz_precision = 2 if symbol.endswith('-SWAP') else 7
                    for _aid in _old_algo_ids:
                        _amend_params = {
                            'instId': symbol,
                            'algoId': _aid,
                            'newSz': str(round(_current_pos_size, _sz_precision))
                        }
                        # 更新止盈价
                        if tp_list:
                            _amend_params['newTpTriggerPx'] = str(tp_list[0])
                            _amend_params['newTpOrdPx'] = '-1'
                        # 更新止损价
                        if sl:
                            _amend_params['newSlTriggerPx'] = str(sl)
                            _amend_params['newSlOrdPx'] = '-1'

                        _amend_body = json.dumps(_amend_params, separators=(',', ':'))
                        _amend_rp = '/api/v5/trade/amend-algos'
                        _amend_resp = requests.post(API_BASE + _amend_rp, headers=okx_h('POST', _amend_rp, _amend_body), data=_amend_body, timeout=10)
                        _amend_data = _amend_resp.json()

                        if _amend_data.get('code') == '0':
                            _data_list = _amend_data.get('data', [])
                            if _data_list and _data_list[0].get('sCode') == '0':
                                _tp_updated += 1
                                _sl_updated += 1
                            else:
                                # data为空也算成功
                                _tp_updated += 1
                                _sl_updated += 1
                            print(f"    ✅ 条件单 {_aid[:8]}: tp→{tp_list[0] if tp_list else '不变'} sl→{sl if sl else '不变'} sz→{_current_pos_size}")
                        else:
                            print(f"    ❌ 条件单修改失败: {_amend_data.get('msg')}")

                if tp_list:
                    _old_tp_str = ','.join(_old_tp_prices) if _old_tp_prices else '无'
                    _new_tp_str = ','.join(str(t) for t in tp_list)
                    _update_msg += f"\n止盈更新:\n"
                    _update_msg += f"  旧({_old_tp_str}) → 新({_new_tp_str}) {'✅' if _tp_updated > 0 else '❌'}\n"

                if sl:
                    _old_sl_str = ','.join(_old_sl_prices) if _old_sl_prices else '无'
                    _update_msg += f"\n止损更新:\n"
                    _update_msg += f"  旧({_old_sl_str}) → 新({sl}) {'✅' if _sl_updated > 0 else '❌'}\n"

                if not tp_list and not sl:
                    _update_msg += f"\n止盈止损: 无更新(信号未提供)\n"

                # --- Step 5b: 无旧条件单时，创建新条件单 ---
                if not _old_algo_ids and _current_pos_size > 0 and (tp_list or sl):
                    _side = 'sell' if direction in ['short', '做空'] else 'buy'
                    _algo_body = {
                        'instId': symbol,
                        'tdMode': 'cross',
                        'side': _side,
                        'posSide': 'net',
                        'ordType': 'conditional',
                        'sz': str(round(_current_pos_size, 2)),
                    }
                    if tp_list:
                        _algo_body['tpTriggerPx'] = str(tp_list[0])
                        _algo_body['tpOrdPx'] = '-1'
                    if sl:
                        _algo_body['slTriggerPx'] = str(sl)
                        _algo_body['slOrdPx'] = '-1'

                    _algo_path = '/api/v5/trade/order-algo'
                    _algo_resp = requests.post(API_BASE + _algo_path, headers=okx_h('POST', _algo_path, json.dumps([_algo_body])), json=[_algo_body], timeout=10)
                    _algo_result = _algo_resp.json()

                    if _algo_result.get('code') == '0':
                        _update_msg += f"\n条件单: 新建 TP={tp_list[0] if tp_list else '-'} SL={sl or '-'} sz={_current_pos_size} ✅\n"
                        print(f"    ✅ 新建条件单: tp={tp_list[0] if tp_list else '-'} sl={sl or '-'}")
                    else:
                        _update_msg += f"\n条件单: 新建失败 {_algo_result.get('msg')} ❌\n"
                        print(f"    ❌ 新建条件单失败: {_algo_result.get('msg')}")

                _update_msg += f"\n条件单数量同步: {_current_pos_size}张\n"

                # --- Step 6: 启动限价单成交监控(如果挂了新限价单) ---
                if _limit_orders:
                    try:
                        script_dir = os.path.dirname(os.path.abspath(__file__))
                        cleaner_script = os.path.join(script_dir, 'limit-fill-cleaner.py')
                        monitor_symbol = symbol if '-SWAP' in symbol else symbol + '-SWAP'
                        print(f"  ⏱️ 启动限价单成交监控: {monitor_symbol}")
                        subprocess.Popen([
                            'python3', '-u',
                            cleaner_script,
                            monitor_symbol
                        ], cwd=script_dir,
                        stdout=open('logs/limit-cleaner.log', 'a'),
                        stderr=subprocess.STDOUT)
                    except Exception as mon_e:
                        print(f"  ⚠️ 启动限价单监控失败: {mon_e}")

                _total_time = time.time() - msg_start_time
                print(f"  ⏱️ [T+{_total_time:.1f}s] 更新模式完成")
                send_wechat(_update_msg)
                return _update_msg

        # ============ 正常开仓流程 ============

        # 🦞 开单前撤销同品种同方向挂单
        cancel_start = time.time()
        from scripts.okx_close_position import API_BASE, get_headers as okx_h
        _new_dir_side = 'sell' if direction in ['short', '做空'] else 'buy'
        print(f"  ⏱️ [T+{cancel_start-msg_start_time:.1f}s] 撤销 {symbol} 同方向({direction})挂单...")
        cancelled_count = 0
        rp = f'/api/v5/trade/orders-pending?instId={symbol}'
        resp = requests.get(API_BASE + rp, headers=okx_h('GET', rp), timeout=10)
        for o in resp.json().get('data', []):
            ord_id = o.get('ordId', '')
            px = o.get('px', '?')
            side = o.get('side', '?')
            # 只撤同方向挂单
            if side != _new_dir_side:
                print(f"    ⏭️ 保留反方向挂单: {side}@{px}")
                continue
            cb = json.dumps({'instId': symbol, 'ordId': ord_id})
            crp = '/api/v5/trade/cancel-order'
            requests.post(API_BASE + crp, headers=okx_h('POST', crp, cb), data=cb, timeout=10)
            print(f"    🗑️ 撤掉挂单: {side}@{px}")
            cancelled_count += 1
        if cancelled_count > 0:
            print(f"  ⏱️ [T+{time.time()-msg_start_time:.1f}s] 共撤销同方向 {cancelled_count} 个挂单")
        else:
            print(f"  ⏱️ [T+{time.time()-msg_start_time:.1f}s] 无同方向挂单需要撤销")

        dir_str = "做空" if direction in ['short', '做空'] else "做多"
        new_direction = 'short' if direction in ['short', '做空'] else 'long'

        # ============ 反向开仓检测 ============
        # 只在反向有持仓时触发，反向只有挂单不触发（挂单已在上一步按方向撤销）
        has_opposite = False
        reverse_action = ""
        if new_direction == 'short' and position_info.get('has_long'):
            has_opposite = True
            reverse_action = "long"
        elif new_direction == 'long' and position_info.get('has_short'):
            has_opposite = True
            reverse_action = "short"

        reverse_action_msg = ""
        if has_opposite:
            from scripts.okx_close_position import execute_close, cancel_all_algo_orders, cancel_pending_orders

            close_msg = ""
            if (reverse_action == "long" and position_info.get('has_long')) or \
               (reverse_action == "short" and position_info.get('has_short')):
                close_text = "多单平仓" if reverse_action == "long" else "空单平仓"
                print(f"  🔍 步骤1: {close_text}")
                close_result = execute_close(close_text)
                close_msg = f"1. ✅ {close_result.get('message', '平仓完成')}\n"
            else:
                close_msg = f"1. ⏭️ 无反向持仓,跳过平仓\n"

            print(f"  🔍 步骤2: 取消该品种所有委托单")
            cancelled_algo = cancel_all_algo_orders(None, symbol)
            cancelled_pending = cancel_pending_orders(None, symbol)

            reverse_action_msg += f"🔄 反向开仓 - 自动清理\n"
            reverse_action_msg += f"原方向:{'做多' if reverse_action == 'long' else '做空'} → 新方向:{dir_str}\n"
            reverse_action_msg += close_msg
            reverse_action_msg += f"2. ✅ 撤单:条件单 {len(cancelled_algo)} 个,挂单 {len(cancelled_pending)} 个\n\n"

        trade_orders = []
        margin_ratios_from_orders = []
        for o in orders_list:
            # 🦞 尊重群消息指定的委托类型:市价→market,限价→limit
            trade_orders.append({
                "price": o.get("price"),
                "type": o.get("type", "limit"),
                "take_profit": tp_list,
                "margin_pct": o.get("margin_pct")
            })
            # 从每个 order 的 margin_pct 提取到 margin_ratios 数组
            mp = o.get("margin_pct")
            if mp is not None:
                margin_ratios_from_orders.append(mp)

        # 优先用 order 内的 margin_pct,其次用顶层的 margin_ratios
        final_margin_ratios = margin_ratios_from_orders if margin_ratios_from_orders else params.get("margin_ratios", [])

        # ===== 应用固定开仓参数 =====
        final_leverage = params.get("leverage")
        final_margin = final_margin_ratios

        if force_fixed_position and intent == 'open_position':
            final_leverage = fixed_leverage
            final_margin = fixed_margin_ratios
            # 覆盖每个订单的杠杆和保证金
            for i, o in enumerate(trade_orders):
                o['leverage'] = fixed_leverage
                if i < len(fixed_margin_ratios):
                    o['margin_pct'] = fixed_margin_ratios[i]
            print(f"  🔧 固定开仓参数已覆盖: leverage={fixed_leverage}, margins={fixed_margin_ratios}")

        trade_params = {
            "direction": dir_str,
            "stop_loss": sl,
            "orders": trade_orders,
            "leverage": final_leverage,
            "margin_ratios": final_margin
        }

        # ===== 自动交易开关检查 =====
        try:
            with open('/root/.openclaw/workspace/config/trade-config.json', 'r') as _f:
                _tc = json.load(_f)
            auto_trade_enabled = _tc.get('auto_trade_enabled', True)
        except Exception:
            auto_trade_enabled = True

        if not auto_trade_enabled:
            print(f"  ⏸️ [T+{time.time()-msg_start_time:.1f}s] 自动交易已暂停，仅分析+转发，不执行开单")
            msg = f"🦞 信号分析（未执行）\n\n"
            msg += f"⚠️ 自动交易已暂停，等待手动确认\n"
            msg += f"交易对:{symbol}\n"
            msg += f"方向:{dir_str}\n"
            msg += f"委托数:{len(orders_list)} 条\n"
            for i, o in enumerate(orders_list):
                o_type = "市价" if o.get('type') == "market" else "限价"
                msg += f"  第{i+1}单:{o.get('price')} ({o_type})\n"
            msg += f"止盈:{tp_list}\n"
            msg += f"止损:{sl}\n"
            # 转发通知但不执行
            send_wechat_notify(msg)
            return

        from scripts.okx_auto_trade import execute_trade
        trade_start = time.time()
        print(f"  ⏱️ [T+{trade_start-msg_start_time:.1f}s] 开始调用 execute_trade...")
        result = execute_trade(trade_params, symbol=symbol)
        trade_end = time.time()
        print(f"  ⏱️ [T+{trade_end-msg_start_time:.1f}s] execute_trade 完成 (耗时 {trade_end-trade_start:.1f}s)")

        msg = f"🦞 开单执行\n\n"
        if reverse_action_msg:
            msg += reverse_action_msg
        msg += f"交易对:{symbol}\n"
        msg += f"方向:{dir_str}\n"
        msg += f"委托数:{len(orders_list)} 条\n"
        for i, o in enumerate(orders_list):
            o_type = "市价" if o.get("type") == "market" else "限价"
            msg += f"  第{i+1}单:{o.get('price')} ({o_type})\n"
        msg += f"止盈:{tp_list}\n"
        msg += f"止损:{sl}\n"
        if result.get('block_reason') == 'margin_exceeded':
            msg += f"🛑 已拦截加仓\n{result.get('errors', ['保证金占比超限'])[0]}"
            print(f"  🛑 加仓拦截: {result.get('errors')}")
        elif result.get('success'):
            msg += f"✅ 成功挂单 {len(result.get('orders', []))} 条"

            # 🦞 判断是否有市价单成交(市价单才会立即清理其他币种挂单)
            has_market_fill = any(o.get('type') == 'market' for o in orders_list)
            has_limit_order = any(o.get('type') == 'limit' for o in orders_list)

            if has_market_fill:
                # 🦞 方向感知跨品种清理:同方向撤销,反方向保留(多空对冲)
                try:
                    from scripts.okx_close_position import API_BASE, get_headers as okx_h
                    rp_all = '/api/v5/trade/orders-pending'
                    resp_all = requests.get(API_BASE + rp_all, headers=okx_h('GET', rp_all), timeout=10)
                    all_pending = resp_all.json().get('data', [])

                    # 当前开单方向
                    current_direction = direction  # 'long' or 'short'
                    coin_to_check = symbol.split('-')[0]

                    cancelled_same_dir = []  # 同方向挂单 → 撤销
                    kept_opposite_dir = []    # 反方向挂单 → 保留

                    for o in all_pending:
                        o_inst = o.get('instId', '')
                        o_coin = o_inst.split('-')[0]
                        if o_coin == coin_to_check:
                            continue  # 同币种不处理

                        # 判断挂单方向:sell=做空, buy=做多
                        o_side = o.get('side', '')
                        o_direction = 'short' if o_side == 'sell' else 'long' if o_side == 'buy' else 'unknown'

                        if o_direction == current_direction:
                            # 同方向 → 撤销(避免同方向多币种持仓风险)
                            ord_id = o.get('ordId', '')
                            cb = json.dumps({'instId': o_inst, 'ordId': ord_id})
                            crp = '/api/v5/trade/cancel-order'
                            cancel_resp = requests.post(API_BASE + crp, headers=okx_h('POST', crp, cb), data=cb, timeout=10)
                            cancelled_same_dir.append(f"{o_inst} @{o.get('px', '?')} ({o_direction})")
                            print(f"    🗑️ 撤销同方向挂单: {o_inst} @{o.get('px', '?')} ({o_direction}) ← 与 {symbol}({current_direction}) 同向")
                        else:
                            # 反方向 → 保留(多空对冲)
                            kept_opposite_dir.append(f"{o_inst} @{o.get('px', '?')} ({o_direction})")
                            print(f"    ✅ 保留反方向挂单: {o_inst} @{o.get('px', '?')} ({o_direction}) ← 与 {symbol}({current_direction}) 反向对冲")

                    if cancelled_same_dir:
                        msg += f"\n🗑️ 同方向清理:撤销 {len(cancelled_same_dir)} 个同方向挂单\n"
                        for c in cancelled_same_dir:
                            msg += f"  ❌ {c}\n"
                    if kept_opposite_dir:
                        msg += f"\n🔒 反方向保留:{len(kept_opposite_dir)} 个反方向挂单(多空对冲)\n"
                        for k in kept_opposite_dir:
                            msg += f"  ✅ {k}\n"
                except Exception as cancel_e:
                    print(f"    ⚠️ 跨品种清理失败: {cancel_e}")
            else:
                print(f"  ⏭️ 无市价单成交,跳过跨品种清理")

            # 🦞 如果有限价单,启动限价单成交监控脚本
            if has_limit_order:
                try:
                    script_dir = os.path.dirname(os.path.abspath(__file__))
                    cleaner_script = os.path.join(script_dir, 'limit-fill-cleaner.py')
                    monitor_symbol = symbol if '-SWAP' in symbol else symbol + '-SWAP'
                    print(f"  ⏱️ [T+{time.time()-msg_start_time:.1f}s] 启动限价单成交监控: {monitor_symbol}")
                    subprocess.Popen([
                        'python3', '-u',
                        cleaner_script,
                        monitor_symbol
                    ], cwd=os.path.dirname(os.path.abspath(__file__)),
                    stdout=open('logs/limit-cleaner.log', 'a'),
                    stderr=subprocess.STDOUT)
                    print(f"  ⏱️ [T+{time.time()-msg_start_time:.1f}s] 限价单成交监控已启动")
                except Exception as mon_e:
                    print(f"  ⚠️ 启动限价单监控失败: {mon_e}")
        else:
            msg += f"❌ {result.get('errors', ['未知错误'])}"

        total_time = time.time() - msg_start_time
        print(f"  ⏱️ [T+{total_time:.1f}s] 执行完成,总耗时 {total_time:.1f}s")
        return msg

    return None


def get_updates():
    """获取消息更新"""
    global offset
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=10"
    r = requests.get(url, timeout=15)
    return r.json()

def main():
    global offset, processed_messages, fingerprints

    check_duplicate_process()
    load_fingerprints()
    load_text_hashes()
    load_signal_prices()

    print("🦞 开始监控群消息 (v7 全量 LLM 意图分析)")
    print(f"指纹去重:已加载 {len(fingerprints)} 条记录")
    print(f"原文哈希:已加载 {len(text_hashes)} 条记录")
    print(f"监听群: {GROUP_IDS}")
    print("规则: 每条消息都过大模型,不依赖关键词过滤")
    print("支持: 开单/平仓/撤单/修改止盈/修改止损/查询/闲聊")
    print("┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅")

    while True:
        try:
            results = get_updates().get('result', [])

            # 🦞 调试: 打印收到的所有消息
            if results:
                for update in results:
                    msg = update.get('message', {})
                    chat = msg.get('chat', {})
                    chat_id = chat.get('id')
                    text = msg.get('text', '')
                    msg_type = '置顶' if msg.get('pinned_message') else '普通'
                    if chat_id in GROUP_IDS:
                        print(f"[调试] 收到{msg_type}消息: 群ID={chat_id} 内容={text}...")

            for update in results:
                offset = max(offset, update.get('update_id', 0) + 1)

                msg = update.get('message', {})
                chat = msg.get('chat', {})

                if chat.get('id') not in GROUP_IDS:
                    continue

                # 🦞 跳过置顶消息通知(避免重复处理老消息)
                if msg.get('pinned_message'):
                    print(f"\n[{time.strftime('%H:%M:%S')}] [置顶通知] 跳过")
                    continue

                text = msg.get('text', '') or msg.get('caption', '')
                if not text.strip():
                    continue

                # 🦞 跳过以置顶通知开头的消息
                if text.startswith('📌') or '置顶消息通知' in text:
                    print(f"\n[{time.strftime('%H:%M:%S')}] [置顶通知] 跳过")
                    continue

                from_user = msg.get('from', {})
                username = from_user.get('username', from_user.get('first_name', '未知'))
                message_id = msg.get('message_id')
                chat_title = chat.get('title', '未知群')  # 获取群名称

                # 🦞 跨群去重: key包含群ID,实现同群独立去重
                # 同群相同消息由同群指纹去重控制(15分钟),跨群相同消息由跨群哈希控制(1小时)
                normalized = normalize_signal_text(text)
                text_hash_cross_group = hashlib.md5(normalized.encode('utf-8')).hexdigest()
                chat_id_str = str(chat_id)
                # 跨群key: 不含群ID,用于检测其他群是否发过
                # 同群key: 含群ID,用于同群去重
                current_ts = time.time()

                # 清理过期的哈希记录
                stale = [k for k, v in text_hashes.items() if current_ts - v > TEXT_HASH_EXPIRE]
                for k in stale:
                    del text_hashes[k]

                # 检查是否来自其他群的相同信号(跨群去重)
                # 🦞 主群优先: 主群信号总是执行,只有备用群重复时才跳过
                cross_group_key = f"cross:{text_hash_cross_group}"
                same_group_key = f"same:{chat_id_str}:{text_hash_cross_group}"
                main_group_key = f"main:{text_hash_cross_group}"  # 标记主群已发过此信号

                # 如果是主群,直接执行并标记
                if chat_id == MAIN_GROUP_ID:
                    text_hashes[main_group_key] = current_ts  # 标记主群已发
                else:
                    # 备用群: 如果主群已发过,则跳过
                    if main_group_key in text_hashes:
                        print(f"\n[{time.strftime('%H:%M:%S')}] [跨群去重] {chat_title} {username}: {text[:50]}... → 跳过(主群已发相同信号)")
                        continue
                    # 如果其他备用群已发过,也跳过
                    if cross_group_key in text_hashes and same_group_key not in text_hashes:
                        print(f"\n[{time.strftime('%H:%M:%S')}] [跨群去重] {chat_title} {username}: {text[:50]}... → 跳过(其他备用群已发相同信号)")
                        continue

                msg_recv_time = time.time()
                print(f"\n[{time.strftime('%H:%M:%S')}] {chat_title} {username}: {text[:200]}")
                print(f"  ⏱️ [T+0.0s] 收到消息")

                # 🦞 记录原文哈希(标记此群此信号已处理)
                text_hashes[cross_group_key] = current_ts  # 跨群标记
                text_hashes[same_group_key] = current_ts   # 同群标记
                save_text_hashes()

                if message_id in processed_messages:
                    print(f"  ⏭️ 已处理过,跳过")
                    continue

                processed_messages.add(message_id)
                # 清理旧记录
                if len(processed_messages) > 200:
                    processed_messages = set(list(processed_messages)[-200:])

                # 🦞 文本内容去重(5 分钟内相同/相似文本不重复处理)
                # 用途:置顶消息与原始消息文字相同,避免重复执行
                import re
                clean_text = re.sub(r'\[图片\]|📌 置顶消息通知.*?\n|#\w+', '', text).strip()
                # 取前100字作为指纹(置顶消息可能被截断)
                text_fingerprint = clean_text[:100].strip()
                text_fp_key = f"text:{text_fingerprint}"
                current_time_fp = time.time()

                if text_fp_key in fingerprints:
                    if current_time_fp - fingerprints[text_fp_key] < FINGERPRINT_EXPIRE:
                        print(f"  ⏭️ 5分钟内相同文本已处理,跳过(置顶/重复消息)")
                        continue

                fingerprints[text_fp_key] = current_time_fp
                save_fingerprints()

                # 🦞 信号核心价格去重(同群不同用户转发同一信号,如QQ515815和Ty529101发同一信号)
                if is_duplicate_signal_prices(text):
                    continue

                # 🦞 先扫描所有持仓和挂单,再 LLM 分析意图
                scan_start = time.time()
                all_positions = scan_all_positions()
                pos_summary = ""
                for p in all_positions:
                    dir_str = "做多" if p['direction'] == 'long' else "做空"
                    pos_summary += f"{p['symbol']} {dir_str}{p['size']}@{p['avg_px']} "
                if not pos_summary:
                    pos_summary = "无持仓"

                # 扫描所有挂单
                pending_orders = scan_pending_orders()
                orders_summary = ""
                for o in pending_orders:
                    dir_str = "做多" if o['side'] == 'buy' else "做空"
                    orders_summary += f"{o['symbol']} {dir_str}@{o['price']} "
                if not orders_summary:
                    orders_summary = "无挂单"

                print(f"  ⏱️ [T+{time.time()-msg_recv_time:.1f}s] 仓位: {pos_summary} | 挂单: {orders_summary}")

                # 构造上下文供LLM使用
                recent_intents = load_recent_intents()
                position_context = {'has_any': len(all_positions) > 0, 'positions': all_positions, 'pending_orders': pending_orders, 'recent_intents': recent_intents}

                llm_start = time.time()
                print(f"  ⏱️ [T+{llm_start-msg_recv_time:.1f}s] LLM 分析意图... (关联{len(recent_intents)}条最近指令)")

                # 🦞 通知用户: 正在分析(10秒内同一批消息只通知一次)
                global _last_analysis_notify_ts
                if time.time() - _last_analysis_notify_ts >= 10:
                    _last_analysis_notify_ts = time.time()
                    notify_text = text[:80].replace('\n', ' ')
                    send_wechat(f"🔍 收到信号,正在分析...\n\n来源: {chat_title} {username}\n内容: {notify_text}\n仓位: {pos_summary}")

                try:
                    # 🦞 HTTP API 方式调用 LLM(快速稳定,8-12秒)
                    import requests
                    gateway_token = 'd75d89b84f23165590ec2508361f1c660c5c1b3f75ee1292'

                    # 构造上下文信息
                    context_parts = []
                    if pos_summary != "无持仓":
                        context_parts.append(f"当前持仓: {pos_summary}")
                    if orders_summary != "无挂单":
                        context_parts.append(f"当前挂单: {orders_summary}")
                    if recent_intents:
                        last_intent = recent_intents[-1]
                        context_parts.append(f"最近指令: [{last_intent.get('time')}] {last_intent.get('intent')} {last_intent.get('symbol')}")

                    context_str = "\n".join(context_parts) if context_parts else "无持仓无挂单"

                    # 精简 Prompt(增强交易对识别 + 多意图支持)
                    llm_prompt = f"""分析交易消息意图:

{context_str}

消息:{text}

⚠️ 多意图识别规则(最高优先级):
如果消息包含多个操作指令,必须按优先级顺序处理:
1. **平仓 > 修改止盈止损** - 如果同时有平仓和修改止盈止损,先执行平仓
2. 返回 intents 数组,按执行顺序排列
3. ⚠️ "止盈平仓X%" + "移动保本损" = 单个 close_position 意图,move_breakeven=true,不要拆成两个意图!
4. "平仓/止盈X%" 语句中的"移动保本损/保本/成本出局" = close_position的附带参数(move_breakeven=true),不是独立的modify_sl
5. 只有"移动止损到具体价格"或"止损改到X"才是独立的modify_sl意图
6. 示例:"止盈70%仓位利润！移动保本损" → intents=[{{"intent":"close_position", "params":{{"close_ratio":0.7, "move_breakeven":true}}}}] (单意图!)
7. 示例:"第一个止盈目标改成61888,平70%" → intents=[{{"intent":"close_position", "params":{{"close_ratio":0.7}}}}, {{"intent":"modify_tp", "params":{{"take_profit":[61888]}}}}]

返回JSON(只返回JSON,无其他文字):
{{
  "intents": [
    {{
      "intent": "open_position/close_position/cancel_orders/modify_tp/modify_sl/query/chat",
      "symbol": "交易对(默认BTC-USDT)",
      "direction": "long/short/all/null",
      "params": {{
        "orders": [{{"price": 数字, "type": "market/limit"}}],
        "stop_loss": 数字,
        "take_profit": [数字],
        "leverage": 数字,
        "margin_ratios": [数字],
        "close_ratio": 0.0-1.0,
        "move_breakeven": true/false
      }},
      "confidence": 0.95,
      "reason": "理由"
    }}
  ]
}}

如果只有单个意图,返回 intents 数组含一个元素。

⚠️ 交易对识别规则:
- 消息提BTC/BTC的支撑压力 → symbol=BTC-USDT
- 消息提ETH/以太坊 → symbol=ETH-USDT
- 消息未明确提币种 → 默认BTC-USDT
- 撤销/取消 + 币种 → cancel_orders, symbol=币种-USDT
- 撤销所有挂单 + 无币种 → cancel_orders, symbol=null(撤销所有币种)

⚠️ 止盈价格提取规则:
- 单个止盈价格 → take_profit=[价格]
- 多个止盈价格 + 后面有“最稳”/“优先”/“稳”/“靠谱”等标注 → 取标注的那个价格
- 多个止盈价格 + 有“或者”/“备选”等关键词 → 取“或者”后面的价格（备选更优）
- 多个止盈价格 + 无标注 → take_profit=[所有价格]全部保留(按信号顺序排列)
- 示例:"第一止盈65388(短线66188最稳)" → take_profit=[66188]
- 示例:"第一止盈1712（或者靠嘴喊短线1718止盈靠谱）" → take_profit=[1718]
- 示例:"第一止盈58388（睡觉挂58388全部止盈）第二止盈57388" → take_profit=[58388,57388]
- 示例:"止盈58388/57388" → take_profit=[58388,57388]
- 消息无明确价格 → intent=chat,不执行

⚠️ 修改止盈 vs 平仓 区分规则(重要):
- "剩余仓位挂X止盈"/"剩余仓位止盈改到X" → modify_tp(修改止盈价)
- "挂X止盈" + 无平仓关键词 → modify_tp(修改止盈价)
- "止盈改到X"/"止盈改成X" → modify_tp
- "止盈X%仓位"/"平掉X%仓位" → close_position(平仓)
- "全部止盈" + 无平仓关键词 → modify_tp(修改止盈价为全部仓位的止盈价)
- "全部止盈" + "平仓"/"清仓"关键词 → close_position
- 示例:"剩余仓位挂61588全部止盈" → modify_tp, take_profit=[61588]
- 示例:"止盈70%仓位" → close_position, close_ratio=0.7
- 示例:"做空单止盈70%仓位利润" → close_position, close_ratio=0.7 (有"止盈XX%仓位"=止盈指令,不管后面跟"利润")
- ⚠️ "止盈" + 百分比仓位 = 操作指令(close_position), 不是描述利润成果。即使句子含"利润/恭喜/跟上的朋友"等庆祝词,只要出现"止盈XX%仓位"就应识别为止盈指令
- 示例:"平掉一半仓位" → close_position, close_ratio=0.5

⚠️ 「假设性/条件性未来描述」 vs 「当前操作指令」区分规则(重要):
- "如果XX成交,成本变成YY,然后ZZ跑掉" → 这是对未来情况的假设描述,不是要求立即执行modify_tp
- 关键判断: 句子是否在描述"如果挂单成交之后会发生什么",而不是要求"现在立刻修改止盈"
- 以下模式 = 描述假设,不是指令:
  - "如果成交后面成本会变成..." + "然后回调XX成本跑掉" → 开仓意图+未来平仓描述,不是modify_tp
  - "成交后均价XX,保本出局" → 未来计划描述,不是当前modify_tp指令
  - "挂XX,如果成交成本变成YY" → 只有"挂XX"是当前指令,后面的成交描述不是
- 但以下模式 = 当前指令:
  - "保本出局" / "成本止盈" (无"如果/成交后"等假设词) → modify_tp
  - "成本价止盈" → modify_tp
- 判断标准: 有"如果/成交后/后面"等假设性连词 + 描述成交后的操作 = 未来计划描述,不执行

⚠️ 其他规则:
- move_breakeven 只在消息明确说"保本"/"成本出局"时为true
- 描述性语句 = chat,不执行操作
  - "差了XX自动止盈" → 描述已有止盈单,不是修改指令
  - "已经止盈了" → 描述已完成操作,不是新指令
  - "已经止盈了70%仓位" → 描述已完成操作,不是新指令 (有"已经")
  - 但 "止盈70%仓位利润!" 没有"已经/刚才/已" → 这是止盈指令,不是描述
  - "刚才平仓了" → 描述历史操作,不是新指令
  - "挂单已成交" → 描述成交情况,不是新指令
  - "XX已成交,成本YYYY" → 描述成交情况,不是开仓指令
⚠️ 「建议他人」vs「操作指令」区分规则:
- 信号员说"朋友/大家"时,核心动词仍可能是操作指令,不要被主语误导
- 判断标准: 句子的核心动作是什么,而不是主语是谁
  - "务必在XX跑掉70%仓位" → modify_tp或conditional_close (有价格条件=不是立即平仓)
  - "挂XX止盈" → modify_tp (核心动作是挂止盈,不管主语是谁)
  - "XX的朋友,挂YY止盈" → modify_tp (虽然提到朋友,但核心动作是挂止盈)
- 真正的纯建议(无操作动作):
  - "大家注意风险" → chat
  - "建议轻仓" → chat
  - "注意控制仓位" → chat
⚠️ 「在XX价格跑掉/平掉YY%仓位」= 条件触发的止盈,不是立即平仓:
- "在62888跑掉70%仓位" → modify_tp (止盈价改为62888,仓位70%) 或 conditional_close_position
- "在XX止盈YY%" → modify_tp
- 关键区分: 有价格条件("在XX") = 止盈/条件平仓, 无价格条件 = 立即平仓
  - "在62888跑掉70%" → modify_tp (有价格条件)
  - "平掉70%仓位" → close_position (无价格条件,立即执行)
  - "62000全部止盈" → modify_tp (止盈价62000)
- 示例:"XX已成交,成本YYYY。赌狗重仓的朋友务必在YY跑掉70%仓位,62000全部止盈。强平高于Z的朋友,挂62000止盈"
  → intents=[{{"intent":"modify_tp","params":{{"take_profit":[62888]}},"reason":"在62888止盈70%仓位"}}] (62000全部止盈+挂62000止盈 合并为同一个modify_tp,取更近的止盈价)
  → 注意: "在YY跑掉70%"不是close_position,因为有价格条件=修改止盈
- 市价/附近 → type=market
- 挂单/再挂 → type=limit
- 止盈XX%仓位 → close_position, close_ratio=XX/100
- 平仓比例从消息提取:"平70%" → close_ratio=0.7"""

                    # 🦞 LLM 调用 + JSON 解析重试逻辑(最多2次)
                    MAX_LLM_RETRIES = 2
                    LLM_TEMPERATURE = 0.3  # 低随机性,意图分析更稳定
                    intents_list = []
                    llm_is_retry = False  # 标记是否经过重试
                    llm_total_time = 0

                    for llm_attempt in range(MAX_LLM_RETRIES):
                        try:
                            llm_start_inner = time.time()
                            llm_temp = LLM_TEMPERATURE
                            llm_model = 'openclaw'  # 默认模型
                            if llm_attempt > 0:
                                print(f"  🔁 LLM 第 {llm_attempt + 1} 次重试...")
                                llm_is_retry = True
                                # 重试时切换到astroncodingplan(更稳定)
                                llm_model = 'openclaw/astroncodingplan'
                                print(f"  🔄 切换模型: openclaw → astroncodingplan")

                            llm_resp = requests.post(
                                'http://127.0.0.1:18789/v1/chat/completions',
                                headers={
                                    'Authorization': f'Bearer {gateway_token}',
                                    'Content-Type': 'application/json'
                                },
                                json={
                                    'model': llm_model,
                                    'messages': [{'role': 'user', 'content': llm_prompt}],
                                    'temperature': llm_temp,
                                    'max_tokens': 800
                                },
                                timeout=90
                            )

                            if llm_resp.status_code != 200:
                                error_detail = llm_resp.json().get('error', {}).get('message', '')[:50]
                                print(f"  ❌ LLM API 失败 (第{llm_attempt + 1}次): {llm_resp.status_code} {error_detail}")
                                if llm_attempt < MAX_LLM_RETRIES - 1:
                                    continue
                                else:
                                    print(f"  ❌ LLM 重试 {MAX_LLM_RETRIES} 次仍失败,放弃")
                                    send_wechat(f"🦞 ⚠️ LLM API失败(重试{MAX_LLM_RETRIES}次)\n\n原文: {text[:150]}\n\n信号未执行,请手动处理。")
                                    intents_list = []
                                break

                            llm_content = llm_resp.json()['choices'][0]['message']['content']
                            llm_api_time = time.time() - llm_start_inner
                            llm_total_time += llm_api_time
                            print(f"  ⚡ HTTP API 耗时: {llm_api_time:.2f}s" + (f" (第{llm_attempt + 1}次)" if llm_attempt > 0 else ""))

                            # 提取JSON
                            import re
                            json_match = re.search(r'```json\s*({.*?})\s*```', llm_content, re.DOTALL)
                            if json_match:
                                json_str = json_match.group(1)
                            else:
                                json_match = re.search(r'({.*})', llm_content, re.DOTALL)
                                json_str = json_match.group(1) if json_match else llm_content

                            intent_result = json.loads(json_str)

                            # 🦞 处理多意图: intents 数组或单个 intent
                            intents_list = intent_result.get('intents', [])
                            if not intents_list:
                                # 兼容旧格式:单个 intent
                                intents_list = [intent_result]

                            # 解析成功,跳出重试循环
                            break

                        except json.JSONDecodeError as json_e:
                            llm_total_time += time.time() - llm_start_inner
                            print(f"  ❌ JSON 解析失败 (第{llm_attempt + 1}次): {json_e}")
                            if llm_attempt < MAX_LLM_RETRIES - 1:
                                print(f"  🔁 将以更低 temperature 重试...")
                                continue
                            else:
                                print(f"  ❌ LLM 重试 {MAX_LLM_RETRIES} 次仍失败,放弃")
                                # 通知用户重试全部失败
                                send_wechat(f"🦞 ⚠️ LLM 分析失败(重试{MAX_LLM_RETRIES}次)\n\n原文: {text[:150]}\n\n信号未执行,请手动处理。")
                                intents_list = []
                                break
                        except Exception as llm_e:
                            llm_total_time += time.time() - llm_start_inner
                            print(f"  ❌ LLM 分析失败 (第{llm_attempt + 1}次): {llm_e}")
                            if llm_attempt < MAX_LLM_RETRIES - 1:
                                continue
                            else:
                                print(f"  ❌ LLM 重试 {MAX_LLM_RETRIES} 次仍失败,放弃")
                                send_wechat(f"🦞 ⚠️ LLM 分析失败(重试{MAX_LLM_RETRIES}次)\n\n原文: {text[:150]}\n\n信号未执行,请手动处理。")
                                intents_list = []
                                break
                    llm_end = time.time()

                    if intents_list:
                        retry_tag = " (重试分析)" if llm_is_retry else ""
                        print(f"  ⏱️ [T+{llm_end-msg_recv_time:.1f}s] LLM 完成 (耗时 {llm_total_time:.1f}s){retry_tag} 共{len(intents_list)}个意图")

                        # 🦞 提取第一个意图的止盈止损(供后续意图继承)
                        first_tp = None
                        first_sl = None
                        if intents_list and intents_list[0].get('intent') == 'open_position':
                            first_params = intents_list[0].get('params', {})
                            first_tp = first_params.get('take_profit')
                            first_sl = first_params.get('stop_loss')

                        # 🦞 如果是重试分析的结果,给每个意图打标记
                        if llm_is_retry:
                            for si in intents_list:
                                si['_is_retry'] = True

                        # 🦞 多意图谨慎机制配置
                        MULTI_INTENT_LOW_CONF = 0.92  # 多意图中非chat的低置信度阈值

                        # 🦞 依次执行每个意图
                        for idx, single_intent in enumerate(intents_list):
                            intent = single_intent.get('intent', 'chat')
                            confidence = single_intent.get('confidence', 0)
                            reason = single_intent.get('reason', '')
                            symbol = ensure_swap_symbol(single_intent.get('symbol', 'BTC-USDT-SWAP'))
                            direction = single_intent.get('direction')
                            params = single_intent.get('params', {})

                            # 🦞 后续开仓意图继承第一个意图的止盈止损
                            if idx > 0 and intent == 'open_position' and first_tp and first_sl:
                                if not params.get('take_profit'):
                                    params['take_profit'] = first_tp
                                    print(f"    ↩️ 继承止盈: {first_tp}")
                                if not params.get('stop_loss'):
                                    params['stop_loss'] = first_sl
                                    print(f"    ↩️ 继承止损: {first_sl}")

                            print(f"    [{idx+1}/{len(intents_list)}] 意图: {intent} (置信度: {confidence}) 交易对: {symbol} 方向: {direction or 'null'}")
                            print(f"    理由: {reason}")

                            # 🛡️ 多意图谨慎机制: 低置信度或高风险意图 → 微信确认后才执行
                            is_multi = len(intents_list) > 1
                            need_confirm = False
                            if is_multi and intent not in ('chat', 'query'):
                                # 多意图中: 置信度低于阈值
                                if confidence < MULTI_INTENT_LOW_CONF:
                                    need_confirm = True
                                    print(f"    ⚠️ 多意图+低置信度({confidence}<{MULTI_INTENT_LOW_CONF}),需要确认")
                                # 多意图中: 高风险意图类型(撤单/平仓)额外检查


                            if need_confirm:
                                coin = symbol.split('-')[0] if symbol else '未知'
                                warn_msg = f"🛡️ 多意图谨慎拦截 - 需确认\n\n"
                                warn_msg += f"原文: {text[:150]}\n\n"
                                warn_msg += f"意图 {idx+1}/{len(intents_list)}: {intent}\n"
                                warn_msg += f"交易对: {symbol} | 方向: {direction or '?'}\n"
                                warn_msg += f"置信度: {confidence} (阈值{MULTI_INTENT_LOW_CONF})\n"
                                warn_msg += f"理由: {reason}\n\n"
                                warn_msg += f"⚠️ 已跳过执行,如需执行请手动操作"
                                send_wechat(warn_msg)
                                continue

                            # 📤 转发意图到配置的目标群
                            forward_intent_to_groups(single_intent, text, chat_title or '未知群')

                            # 🦞 LLM 驱动的挂单成交自动监控
                            orders_list = params.get('orders', [])
                            has_pending_entry = orders_list and any(o.get('type') == 'limit' for o in orders_list)
                            cost_exit_patterns = ['回调到成本', '成本价出局', '保本出局', '回调到成本价', '成本跑掉', '全部出局', '保本出场', '成本价出']
                            has_cost_exit = any(p in text for p in cost_exit_patterns)

                            if has_pending_entry and has_cost_exit:
                                entry_price = None
                                cost_price = None
                                for o in orders_list:
                                    if o.get('type') == 'limit':
                                        entry_price = o.get('price')
                                        break
                                tp_list = params.get('take_profit', [])
                                if tp_list:
                                    cost_price = tp_list[0]
                                if not cost_price:
                                    nums = re.findall(r'\b(\d{3,8}(?:\.\d+)?)\b', text)
                                    for n in nums:
                                        try:
                                            v = float(n)
                                            if v > 100:
                                                cost_price = v
                                        except:
                                            pass
                                if entry_price and cost_price:
                                    coin = symbol.split('-')[0] if symbol else '未知'
                                    monitor_symbol = f"{coin}-{symbol.split('-')[1] if '-' in symbol else 'USDT'}-SWAP"
                                    try:
                                        check = subprocess.run(['pgrep', '-f', 'fill-monitor.py'], capture_output=True, text=True, timeout=5)
                                        if not check.stdout.strip():
                                            print(f"  🦞 LLM 检测到挂单调整信号,自动启动成交监控")
                                            print(f"  币种: {coin} | 触发价: {entry_price} | 成本价: {cost_price}")
                                            subprocess.Popen([
                                                'python3', '-u',
                                                'scripts/fill-monitor.py',
                                                monitor_symbol, str(entry_price), str(cost_price)
                                            ], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                            stdout=open('logs/fill-monitor.log', 'a'),
                                            stderr=subprocess.STDOUT)
                                            time.sleep(2)
                                            check2 = subprocess.run(['pgrep', '-f', 'fill-monitor.py'], capture_output=True, text=True, timeout=5)
                                            if check2.stdout.strip():
                                                print(f"  ✅ 成交监控已启动 (PID: {check2.stdout.strip()})")
                                            else:
                                                print(f"  ❌ 监控启动失败")
                                        else:
                                            print(f"  ⏭️ 成交监控已在运行中")
                                    except Exception as mon_e:
                                        print(f"  ⚠️ 自动启动监控失败: {mon_e}")

                            # 🦞 条件修改止盈 - LLM 识别到 conditional_modify_tp → 启动条件止盈监控
                            if intent == 'conditional_modify_tp':
                                trigger_price = intent_result.get('params', {}).get('trigger_price')
                                target_tp = intent_result.get('params', {}).get('take_profit', [None])[0]
                                if trigger_price and target_tp:
                                    coin = symbol.split('-')[0] if symbol else '未知'
                                    monitor_symbol = f"{coin}-{symbol.split('-')[1] if '-' in symbol else 'USDT'}-SWAP"
                                    try:
                                        check = subprocess.run(['pgrep', '-f', 'fill-monitor.py'], capture_output=True, text=True, timeout=5)
                                        if not check.stdout.strip():
                                            print(f"  🦞 LLM 检测到条件止盈指令,自动启动监控")
                                            print(f"  币种: {coin} | 触发价: {trigger_price} | 目标止盈: {target_tp}")
                                            subprocess.Popen([
                                                'python3', '-u',
                                                'scripts/fill-monitor.py',
                                                monitor_symbol, str(trigger_price), str(target_tp), 'fixed'
                                            ], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                            stdout=open('logs/fill-monitor.log', 'a'),
                                            stderr=subprocess.STDOUT)
                                            time.sleep(2)
                                            check2 = subprocess.run(['pgrep', '-f', 'fill-monitor.py'], capture_output=True, text=True, timeout=5)
                                            if check2.stdout.strip():
                                                print(f"  ✅ 条件止盈监控已启动 (PID: {check2.stdout.strip()})")
                                            else:
                                                print(f"  ❌ 监控启动失败")
                                        else:
                                            print(f"  ⏭️ 监控已在运行中")
                                    except Exception as mon_e:
                                        print(f"  ⚠️ 自动启动监控失败: {mon_e}")

                            # 按 LLM 确定的 symbol 获取仓位,再执行操作
                            position_info = get_position_info(symbol)

                            # 执行操作
                            try:
                                wechat_msg = execute_intent(single_intent, text, position_info, msg_recv_time)
                                # 🦞 保存最近指令上下文
                                if wechat_msg:
                                    save_recent_intent(single_intent, text, wechat_msg)
                                else:
                                    save_recent_intent(single_intent, text)
                            except Exception as exec_e:
                                import traceback
                                traceback.print_exc()
                                wechat_msg = f"🦞 ❌ 执行异常\n\n交易对: {symbol}\n意图: {intent}\n方向: {single_intent.get('direction', '?')}\n原始消息: {text[:100]}\n错误: {exec_e}"

                        if wechat_msg:
                            # 🦞 如果是重试分析的结果,在通知里标记
                            if single_intent.get('_is_retry'):
                                wechat_msg = "⚠️ 此信号经LLM重试分析(首次JSON解析失败)\n\n" + wechat_msg
                            # 延时 3 秒再推送
                            time.sleep(3)
                            send_wechat(wechat_msg)
                        # 🦞 chat/query/conditional_modify_tp 返回 None 是正常的,不需要警告
                        elif intent in ['chat', 'query', 'live_stream', 'conditional_modify_tp']:
                            print(f"  i️ {intent} 消息已处理,无需推送")
                        else:
                            # 非 chat 意图返回 None = 真正的分析失败或执行跳过
                            print(f"  ⚠️ 意图执行跳过或失败")
                            # 🦞 低置信度/重复指令/无仓位 等情况已内部处理,不再重复通知

                except Exception as e:
                    print(f"  ❌ 处理异常:{e}")
                    import traceback
                    traceback.print_exc()
                    # 🦞 强制规则:任何异常都必须发微信
                    try:
                        send_wechat(f"🦞 ❌ 处理异常\n\n群消息: {text[:150]}\n错误: {e}\n\n请手动检查")
                    except:
                        print(f"  ❌❌ 微信通知也失败了!")

            if not results:
                print(f"[{time.strftime('%H:%M:%S')}] 等待消息...")

            time.sleep(1)

        except Exception as e:
            print(f"❌ 循环错误:{e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
