#!/usr/bin/env python3
"""
限价单成交后跨品种清理监控（方向感知版）
- 同方向持仓：某币种成交后撤销其他币种所有同方向挂单
- 反方向持仓：不撤销，允许多空对冲共存
无超时限制，直到有成交或所有挂单被取消才退出
"""

import requests, json, sys, os, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if len(sys.argv) < 2:
    print("用法: python3 limit-fill-cleaner.py <币种-SWAP>")
    sys.exit(1)

SYMBOL = sys.argv[1]
COIN = SYMBOL.split('-')[0]
CHECK_INTERVAL = 30
STATE_FILE = f'/tmp/limit-cleaner-{COIN}.state'

from scripts.okx_close_position import API_BASE, get_headers

def get_pending():
    """获取所有挂单"""
    resp = requests.get(API_BASE + '/api/v5/trade/orders-pending', headers=get_headers('GET', '/api/v5/trade/orders-pending'), timeout=10)
    return resp.json().get('data', [])

def get_positions():
    """获取所有持仓，返回 {instId: direction}"""
    rp = '/api/v5/account/positions'
    resp = requests.get(API_BASE + rp, headers=get_headers('GET', rp), timeout=10)
    positions = {}
    for p in resp.json().get('data', []):
        pos = float(p.get('pos', '0') or '0')
        if pos != 0:
            inst = p.get('instId', '')
            # pos > 0 =做多, pos < 0 =做空
            positions[inst] = 'long' if pos > 0 else 'short'
    return positions

def get_order_side(order):
    """判断挂单方向：side=sell → 做空, side=buy → 做多"""
    side = order.get('side', '')
    if side == 'sell':
        return 'short'
    elif side == 'buy':
        return 'long'
    return 'unknown'

def get_order_status(ord_id):
    """查询单个订单状态"""
    params = {'ordId': ord_id, 'instId': SYMBOL}
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    path = f'/api/v5/trade/order?{qs}'
    resp = requests.get(API_BASE + path, headers=get_headers('GET', path), timeout=10)
    data = resp.json()
    if data.get('code') == '0' and data.get('data'):
        return data['data'][0]
    return None

def cancel_same_direction_other():
    """撤销其他币种中与当前持仓同方向的挂单（反方向保留）"""
    # 获取当前币种的持仓方向
    positions = get_positions()
    my_direction = positions.get(SYMBOL, None)
    
    # 如果本币种无持仓，可能是限价单刚成交但仓位数据还没更新
    # 用挂单方向作为 fallback
    if not my_direction:
        # 从tracked orders找成交订单的方向
        for ord_id in tracked_ids_global:
            order = get_order_status(ord_id)
            if order and order.get('state') == 'filled':
                my_direction = get_order_side(order)
                break
    
    if not my_direction:
        # 无法确定方向，为安全起见撤销所有其他币种挂单
        print(f"  ⚠️ 无法确定持仓方向，安全模式：撤销所有其他币种挂单")
        all_pending = get_pending()
        cancelled = []
        for o in all_pending:
            inst = o.get('instId', '')
            coin = inst.split('-')[0]
            if coin != COIN:
                ord_id = o.get('ordId', '')
                cb = json.dumps({'instId': inst, 'ordId': ord_id})
                resp = requests.post(API_BASE + '/api/v5/trade/cancel-order',
                                    headers=get_headers('POST', '/api/v5/trade/cancel-order', cb),
                                    data=cb, timeout=10)
                cancelled.append(f"{inst} @{o.get('px', '?')} ({get_order_side(o)})")
                print(f"  🗑️ 撤销: {inst} @{o.get('px', '?')} ({get_order_side(o)})")
        return cancelled, []
    
    print(f"  📊 当前持仓方向: {SYMBOL} → {my_direction}")
    
    # 分类其他币种挂单
    all_pending = get_pending()
    same_dir_cancelled = []   # 同方向 → 撤销
    opposite_dir_kept = []    # 反方向 → 保留
    
    for o in all_pending:
        inst = o.get('instId', '')
        coin = inst.split('-')[0]
        if coin == COIN:
            continue
        
        other_side = get_order_side(o)
        
        if other_side == my_direction:
            # 同方向 → 撤销（避免同方向多币种持仓风险）
            ord_id = o.get('ordId', '')
            cb = json.dumps({'instId': inst, 'ordId': ord_id})
            resp = requests.post(API_BASE + '/api/v5/trade/cancel-order',
                                headers=get_headers('POST', '/api/v5/trade/cancel-order', cb),
                                data=cb, timeout=10)
            same_dir_cancelled.append(f"{inst} @{o.get('px', '?')} ({other_side})")
            print(f"  🗑️ 撤销同方向: {inst} @{o.get('px', '?')} ({other_side}) ← 与 {SYMBOL}({my_direction}) 同方向")
        else:
            # 反方向 → 保留（多空对冲，风险可控）
            opposite_dir_kept.append(f"{inst} @{o.get('px', '?')} ({other_side})")
            print(f"  ✅ 保留反方向: {inst} @{o.get('px', '?')} ({other_side}) ← 与 {SYMBOL}({my_direction}) 反方向对冲")
    
    return same_dir_cancelled, opposite_dir_kept

def send_wechat(msg):
    import shlex, subprocess
    safe_msg = shlex.quote(msg)
    cmd = f'openclaw message send --channel openclaw-weixin --account ea3465f35dfb-im-bot --target o9cq80zZk50Q33Snd8zOZ5vlAEQ4@im.wechat -m {safe_msg}'
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    return proc.returncode == 0 and 'Sent via' in proc.stdout

def save_tracked_orders(ord_ids):
    with open(STATE_FILE, 'w') as f:
        json.dump(ord_ids, f)

def load_tracked_orders():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return None

# 全局变量，供 cancel 函数使用
tracked_ids_global = []

def main():
    global tracked_ids_global
    
    print(f"🦞 {COIN} 限价单成交监控启动（方向感知版）")
    print(f"币种: {SYMBOL} | 检查间隔: {CHECK_INTERVAL}秒")
    print(f"逻辑: 同方向成交→撤销其他同方向挂单 | 反方向→保留共存")
    print("┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅")
    
    # 获取当前本币种所有挂单
    all_pending = get_pending()
    coin_orders = [o for o in all_pending if o.get('instId', '').split('-')[0] == COIN]
    other_orders = [o for o in all_pending if o.get('instId', '').split('-')[0] != COIN]
    
    # 显示其他币种挂单方向
    for o in other_orders:
        inst = o.get('instId', '')
        side = get_order_side(o)
        print(f"  📋 其他币种挂单: {inst} @{o.get('px', '?')} 方向={side}")
    
    print(f"初始状态: {COIN} 挂单 {len(coin_orders)} 个, 其他币种挂单 {len(other_orders)} 个")
    
    initial_other_count = len(other_orders)
    
    if not coin_orders:
        print(f"⚠️ {COIN} 无挂单，退出监控")
        sys.exit(0)
    
    tracked_ids = [o.get('ordId') for o in coin_orders if o.get('ordId')]
    tracked_ids_global = tracked_ids
    save_tracked_orders(tracked_ids)
    print(f"📋 追踪订单: {tracked_ids}")
    
    while True:
        time.sleep(CHECK_INTERVAL)
        
        try:
            filled_ids = []
            live_ids = []
            canceled_ids = []
            filled_direction = None
            
            for ord_id in tracked_ids:
                order = get_order_status(ord_id)
                if order:
                    state = order.get('state', '')
                    px = order.get('px', '?')
                    if state == 'filled':
                        side = get_order_side(order)
                        filled_ids.append(f"{ord_id} @{px} ({side})")
                        if filled_direction is None:
                            filled_direction = side
                        print(f"  ✅ 订单已成交: {ord_id} @{px} 方向={side}")
                    elif state == 'canceled':
                        canceled_ids.append(ord_id)
                        print(f"  ❌ 订单已取消: {ord_id}")
                    elif state == 'live':
                        live_ids.append(ord_id)
            
            # 🦞 方向感知清理：同方向撤销，反方向保留
            if filled_ids:
                print(f"🔥 检测到 {COIN} 限价单成交！{len(filled_ids)} 个订单已成交，方向={filled_direction}")
                
                same_cancelled, opposite_kept = cancel_same_direction_other()
                
                msg = f"🦞 限价单成交清理（方向感知）\n\n"
                msg += f"{COIN} 限价单已成交 ({len(filled_ids)} 单, {filled_direction})\n"
                for f_item in filled_ids:
                    msg += f"  ✅ {f_item}\n"
                
                if same_cancelled:
                    msg += f"\n🗑️ 撤销同方向挂单 {len(same_cancelled)} 个（避免同向风险）\n"
                    for c in same_cancelled:
                        msg += f"  ❌ {c}\n"
                
                if opposite_kept:
                    msg += f"\n✅ 保留反方向挂单 {len(opposite_kept)} 个（多空对冲）\n"
                    for k in opposite_kept:
                        msg += f"  🔒 {k}\n"
                
                if not same_cancelled and not opposite_kept:
                    msg += f"\n无其他币种挂单\n"
                
                send_wechat(msg)
                
                if os.path.exists(STATE_FILE):
                    os.remove(STATE_FILE)
                print("✅ 清理完成，退出监控")
                sys.exit(0)
            
            # 所有订单都取消了 → 也退出
            if not live_ids and not filled_ids:
                print("所有订单已取消，退出监控")
                msg = f"🦞 限价单监控退出\n\n{COIN} 所有限价单已被取消\n监控脚本退出\n"
                send_wechat(msg)
                if os.path.exists(STATE_FILE):
                    os.remove(STATE_FILE)
                sys.exit(0)
            
            # 其他币种挂单已被手动清空
            current_pending = get_pending()
            current_other = [o for o in current_pending if o.get('instId', '').split('-')[0] != COIN]
            if len(current_other) == 0 and initial_other_count > 0:
                print("其他币种挂单已全部清空，退出监控")
                msg = f"🦞 限价单监控退出\n\n{COIN} 限价单待成交 ({len(live_ids)} 单)\n其他币种挂单已手动清空\n监控脚本退出\n"
                send_wechat(msg)
                if os.path.exists(STATE_FILE):
                    os.remove(STATE_FILE)
                sys.exit(0)
            
            # 显示方向信息
            current_other_dirs = [f"{o.get('instId','')}({get_order_side(o)})" for o in current_other]
            print(f"[{time.strftime('%H:%M:%S')}] {COIN} 待成交 {len(live_ids)} | 已成交 {len(filled_ids)} | 其他币种挂单 {len(current_other)}: {','.join(current_other_dirs)} (等待中...)")
            
        except Exception as e:
            print(f"  ⚠️ 检查异常: {e}")

if __name__ == "__main__":
    main()