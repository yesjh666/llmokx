#!/usr/bin/env python3
"""
挂单成交监控 — 价格触发版（支持所有币种）
监控指定币种价格，达到开单价后自动修改止盈到成本价，然后退出
"""

import requests
import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ========== 配置（通过命令行参数传入） ==========
if len(sys.argv) < 4:
    print("用法: python3 fill-monitor.py <币种-SWAP> <触发价> <止盈成本价>")
    print("例: python3 fill-monitor.py ETH-USDT-SWAP 2333 2303")
    sys.exit(1)

SYMBOL = sys.argv[1]        # 例如 ETH-USDT-SWAP
ENTRY_PRICE = float(sys.argv[2])  # 挂单开单价（触发价）
COST_PRICE = float(sys.argv[3])   # 群消息指定的止盈成本价
TARGET_SYMBOL = SYMBOL.replace('-SWAP', '')  # ETH-USDT

# OKX API
from scripts.okx_close_position import API_BASE, get_headers as okx_headers
from scripts.okx_modify_tp_sl import modify_take_profit

# 微信通知
WECHAT_TARGET = 'o9cq80zZk50Q33Snd8zOZ5vlAEQ4@im.wechat'
ACCOUNT_ID = 'ea3465f35dfb-im-bot'

def get_eth_price():
    """获取 ETH 当前价格"""
    try:
        rp = f'/api/v5/market/ticker?instId={TARGET_SYMBOL}'
        resp = requests.get(API_BASE + rp, headers=okx_headers('GET', rp), timeout=10)
        data = resp.json()
        if data.get('code') == '0' and data.get('data'):
            return float(data['data'][0].get('last', '0'))
    except Exception as e:
        print(f"⚠️ 获取价格失败: {e}")
    return None

def get_eth_position():
    """获取 ETH 当前持仓"""
    try:
        rp = '/api/v5/account/positions'
        resp = requests.get(API_BASE + rp, headers=okx_headers('GET', rp), timeout=10)
        for p in resp.json().get('data', []):
            if p.get('instId') == SYMBOL or p.get('instId') == SYMBOL.replace('-SWAP', ''):
                pos = float(p.get('pos', '0') or '0')
                avg_px = float(p.get('avgPx', '0') or '0')
                if pos != 0:
                    return abs(pos), avg_px
    except Exception as e:
        print(f"⚠️ 获取仓位失败: {e}")
    return None, None

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

def main():
    print(f"🦞 ETH 挂单成交监控启动")
    print(f"触发价: {ENTRY_PRICE} | 目标止盈成本: {COST_PRICE}")
    print(f"检查间隔: 30 秒")
    print("┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅")
    
    check_count = 0
    
    while True:
        check_count += 1
        price = get_eth_price()
        
        if price and price > 0:
            print(f"[{time.strftime('%H:%M:%S')}] ETH 价格: {price} {'🔥 已触发!' if price >= ENTRY_PRICE else '(等待中...)'}")
            
            if price >= ENTRY_PRICE:
                # 触发！等待几秒确保挂单成交
                print(f"🔥 价格达到 {ENTRY_PRICE}，等待 10 秒确保成交...")
                time.sleep(10)
                
                # 重新获取仓位，计算新平均成本
                size, avg_px = get_eth_position()
                if size and avg_px:
                    new_cost = round(avg_px, 2)
                    print(f"📊 当前持仓: {size} ETH @ {new_cost}")
                    
                    # 修改止盈到成本价
                    print(f"🔧 修改止盈到 {new_cost}...")
                    result = modify_take_profit(new_cost, symbol=SYMBOL)
                    
                    msg = f"🦞 ETH 挂单成交监控\n\n"
                    msg += f"✅ 2333 挂单已成交\n"
                    msg += f"当前仓位: {size} ETH @ {new_cost}\n"
                    msg += f"止盈已修改到: {new_cost} (成本价保本)\n"
                    msg += f"{'✅ 修改成功' if result.get('message') else '❌ 修改失败'}\n"
                    msg += f"\n监控脚本自动退出"
                    
                    send_wechat(msg)
                else:
                    msg = f"🦞 ETH 挂单成交监控\n\n"
                    msg += f"⚠️ 价格达到 {ENTRY_PRICE}，但未检测到 ETH 仓位\n"
                    msg += f"请手动确认止盈设置\n\n监控脚本自动退出"
                    send_wechat(msg)
                
                print("✅ 完成，退出监控")
                sys.exit(0)
        else:
            print(f"[{time.strftime('%H:%M:%S')}] ⚠️ 获取价格失败")
        
        time.sleep(30)

if __name__ == "__main__":
    main()
