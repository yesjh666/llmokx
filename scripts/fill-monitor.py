#!/usr/bin/env python3
"""
挂单成交监控 — 价格+仓位双触发版（支持所有币种）
监控指定币种价格和仓位变化，任一条件满足即自动修改止盈，然后退出

两种模式：
1. 开单成交监控：开仓挂单成交后，修改止盈到实际平均成本
2. 条件止盈监控：第二单成交后，修改止盈到指定目标价（如2392）

触发条件（满足任一即可）：
A. 价格 ≥ 触发价
B. 仓位数量增加（说明挂单成交了，即使价格已回落）

用法：
python3 fill-monitor.py <币种-SWAP> <触发价> <止盈目标价> [cost|fixed]
- cost模式(默认)：止盈=实际平均成本
- fixed模式：止盈=传入的目标价（条件止盈）
例：python3 fill-monitor.py ETH-USDT-SWAP 2333 2303
    python3 fill-monitor.py ETH-USDT-SWAP 2418 2392 fixed
"""

import requests
import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ========== 配置（通过命令行参数传入） ==========
if len(sys.argv) < 4:
    print("用法: python3 fill-monitor.py <币种-SWAP> <触发价> <止盈目标价> [cost|fixed]")
    print("例: python3 fill-monitor.py ETH-USDT-SWAP 2333 2303")
    sys.exit(1)

SYMBOL = sys.argv[1]          # 例如 ETH-USDT-SWAP
TRIGGER_PRICE = float(sys.argv[2])  # 触发价
TARGET_TP = float(sys.argv[3])      # 目标止盈价
MODE = sys.argv[4] if len(sys.argv) > 4 else 'cost'  # cost=实际成本, fixed=固定目标价
TARGET_SYMBOL = SYMBOL.replace('-SWAP', '')

# OKX API
from scripts.okx_close_position import API_BASE, get_headers as okx_headers
from scripts.okx_modify_tp_sl import modify_take_profit

# 微信通知
WECHAT_TARGET = 'o9cq80zZk50Q33Snd8zOZ5vlAEQ4@im.wechat'
ACCOUNT_ID = 'ea3465f35dfb-im-bot'

def get_price():
    """获取当前价格"""
    try:
        rp = f'/api/v5/market/ticker?instId={TARGET_SYMBOL}'
        resp = requests.get(API_BASE + rp, headers=okx_headers('GET', rp), timeout=10)
        data = resp.json()
        if data.get('code') == '0' and data.get('data'):
            return float(data['data'][0].get('last', '0'))
    except Exception as e:
        print(f"⚠️ 获取价格失败: {e}")
    return None

def get_position():
    """获取当前持仓"""
    try:
        rp = '/api/v5/account/positions'
        resp = requests.get(API_BASE + rp, headers=okx_headers('GET', rp), timeout=10)
        for p in resp.json().get('data', []):
            inst = p.get('instId', '')
            if inst == SYMBOL or inst == SYMBOL.replace('-SWAP', ''):
                pos = float(p.get('pos', '0') or '0')
                avg_px = float(p.get('avgPx', '0') or '0')
                if pos != 0:
                    return abs(pos), avg_px
    except Exception as e:
        print(f"⚠️ 获取仓位失败: {e}")
    return None, None

def get_algo_tp():
    """获取当前条件单的止盈价列表"""
    tp_prices = []
    try:
        for otype in ['conditional', 'oco']:
            rp = f'/api/v5/trade/orders-algo-pending?instId={SYMBOL}&ordType={otype}'
            resp = requests.get(API_BASE + rp, headers=okx_headers('GET', rp), timeout=10)
            for o in resp.json().get('data', []):
                tp = o.get('tpTriggerPx', '')
                if tp:
                    tp_prices.append(float(tp))
    except Exception as e:
        print(f"⚠️ 获取条件单止盈失败: {e}")
    return tp_prices

def send_wechat(msg):
    """发送微信通知"""
    import shlex
    safe_msg = shlex.quote(msg)
    cmd = f'openclaw message send --channel openclaw-weixin --account {ACCOUNT_ID} --target {WECHAT_TARGET} -m {safe_msg}'
    import subprocess
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    if proc.returncode == 0 and 'Sent via' in proc.stdout:
        print(f"✅ 微信推送成功")
        return True
    else:
        print(f"⚠️ 微信推送未确认: {proc.stdout[:200]} {proc.stderr[:200]}")
        return False

def execute_fill(size, avg_px):
    """执行成交后的止盈修改"""
    coin = SYMBOL.split('-')[0]
    
    if MODE == 'cost':
        # 开单成交模式：止盈=实际平均成本
        new_tp = round(avg_px, 2)
        tp_desc = f"成本价保本 {new_tp}"
        trigger_reason = f"价格达到 {TRIGGER_PRICE}"
    else:
        # 条件止盈模式：止盈=固定目标价
        new_tp = TARGET_TP
        tp_desc = f"条件止盈 {TARGET_TP}（成本{round(avg_px, 2)}）"
        trigger_reason = f"第二单成交"
    
    print(f"📊 当前持仓: {size} {coin} @ {round(avg_px, 2)}")
    print(f"🔧 修改止盈到 {new_tp}...")
    result = modify_take_profit(new_tp, symbol=SYMBOL)
    
    msg = f"🦞 {coin} 挂单成交监控\n\n"
    msg += f"✅ {trigger_reason}，挂单已成交\n"
    msg += f"当前仓位: {size} {coin} @ {round(avg_px, 2)}\n"
    msg += f"止盈已修改到: {new_tp} ({tp_desc})\n"
    msg += f"{'✅ 修改成功' if result.get('message') else '❌ 修改失败'}\n"
    msg += f"\n监控脚本自动退出"
    
    send_wechat(msg)

def main():
    coin = SYMBOL.split('-')[0]
    mode_label = "实际成本" if MODE == 'cost' else f"固定价{TARGET_TP}"
    print(f"🦞 {coin} 挂单成交监控启动 [{MODE}模式]")
    print(f"交易对: {SYMBOL} | 触发价: {TRIGGER_PRICE} | 目标止盈: {TARGET_TP} ({mode_label})")
    print(f"检查间隔: 30 秒")
    print(f"触发条件: 价格≥{TRIGGER_PRICE} 或 仓位数量增加")
    print("┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅")
    
    # 记录初始仓位和初始止盈价
    initial_size, initial_avg = get_position()
    initial_tp_prices = get_algo_tp()
    print(f"📋 初始仓位: {initial_size or 0} {coin} @ {initial_avg or 0}")
    print(f"📋 初始止盈价: {initial_tp_prices}")
    print(f"📋 触发条件: 仓位 > {initial_size or 0} 或 价格 ≥ {TRIGGER_PRICE}")
    print(f"📋 退出保护: 仓位清空 或 止盈价被手动修改")
    
    while True:
        price = get_price()
        size, avg_px = get_position()
        
        # 🛡️ 保护1: 仓位已清空 → 静默退出
        if initial_size and (not size or size == 0):
            print(f"📋 检测到仓位已清空，静默退出")
            sys.exit(0)
        
        # 🛡️ 保护2: 止盈价被手动修改 → 静默退出
        current_tp_prices = get_algo_tp()
        if current_tp_prices and initial_tp_prices:
            # 去重后比较（多个条件单可能设置相同止盈价）
            initial_set = sorted(set(round(t, 1) for t in initial_tp_prices))
            current_set = sorted(set(round(t, 1) for t in current_tp_prices))
            # 如果止盈价集合完全变了（不是新增/减少数量，而是价格值变化）
            # 只有当所有现有止盈价都不同于初始值时才认为是手动修改
            # 简单判断：如果初始止盈价集合中有值被替换为完全不同值，才退出
            if len(initial_set) == len(current_set) and initial_set != current_set:
                # 数量相同但价格不同 → 手动修改
                print(f"📋 检测到止盈价已手动修改 ({initial_set} → {current_set})，静默退出")
                sys.exit(0)
        
        # 检查触发条件
        price_triggered = price and price >= TRIGGER_PRICE
        size_triggered = size and initial_size and size > initial_size
        
        if price and price > 0:
            status = "(等待中...)"
            if price_triggered:
                status = "🔥 价格触发!"
            if size_triggered:
                status = "🔥 仓位增加触发!"
            print(f"[{time.strftime('%H:%M:%S')}] {coin} 价格: {price} | 仓位: {size or 0} @ {avg_px or 0} {status}")
        
        # 执行成交逻辑
        if price_triggered or size_triggered:
            if size_triggered:
                print(f"🔥 检测到仓位增加 ({initial_size} → {size})，确认第二单已成交")
            if price_triggered:
                print(f"🔥 价格达到 {TRIGGER_PRICE}，等待 10 秒确保成交...")
                time.sleep(10)
                size, avg_px = get_position()
            
            if size and avg_px:
                execute_fill(size, avg_px)
            else:
                msg = f"🦞 {coin} 挂单成交监控\n\n"
                trigger = "仓位增加" if size_triggered else f"价格达到 {TRIGGER_PRICE}"
                msg += f"⚠️ 触发条件满足（{trigger}），但未检测到 {coin} 仓位\n"
                msg += f"请手动确认止盈设置\n\n监控脚本自动退出"
                send_wechat(msg)
            
            print("✅ 完成，退出监控")
            sys.exit(0)
        
        if not price and not size:
            print(f"[{time.strftime('%H:%M:%S')}] ⚠️ 获取数据失败")
        
        time.sleep(30)

if __name__ == "__main__":
    main()
