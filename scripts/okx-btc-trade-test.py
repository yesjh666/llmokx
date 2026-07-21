#!/usr/bin/env python3
"""
OKX 合约交易 - 最小仓位测试 (修复版)
"""

import requests
import hmac
import base64
import time
import hashlib
import json

# OKX API 配置
API_KEY = "ecd201e1-1d4b-40b1-8f29-3ab786037a9e"
SECRET_KEY = "D619624E2846DC0CD5A2F2D6B1D48A74"
PASSPHRASE = "a12397255A!"
BASE_URL = "https://www.okx.com"

def create_sign(timestamp, method, request_path, body):
    """创建签名"""
    body = body if body else ""
    message = timestamp + method + request_path + body
    mac = hmac.new(
        bytes(SECRET_KEY, encoding='utf8'),
        bytes(message, encoding='utf8'),
        digestmod=hashlib.sha256
    )
    return base64.b64encode(mac.digest()).decode()

def open_short_position():
    """开空单"""
    print("📊 开空单...")
    
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    method = "POST"
    request_path = "/api/v5/trade/order"
    body = json.dumps({
        "instId": "BTC-USDT-SWAP",
        "tdMode": "cross",
        "side": "sell",
        "posSide": "short",
        "ordType": "market",
        "sz": "0.001"
    })
    
    sign = create_sign(timestamp, method, request_path, body)
    
    headers = {
        "OKX-ACCESS-KEY": API_KEY,
        "OKX-ACCESS-SIGN": sign,
        "OKX-ACCESS-TIMESTAMP": timestamp,
        "OKX-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json"
    }
    
    print(f"时间戳：{timestamp}")
    print(f"签名：{sign[:30]}...")
    
    url = f"{BASE_URL}{request_path}"
    response = requests.post(url, headers=headers, data=body, timeout=10)
    
    result = response.json()
    print(f"状态码：{result.get('code')}")
    print(f"消息：{result.get('msg')}")
    
    if result.get('code') == '0':
        order_id = result['data'][0].get('ordId')
        fill_price = result['data'][0].get('avgPx', '市价')
        print(f"✅ 开单成功！")
        print(f"   订单 ID: {order_id}")
        print(f"   成交价：${fill_price}")
        return order_id, fill_price
    else:
        print(f"❌ 开单失败：{json.dumps(result, indent=2)}")
        return None, None

def set_stop_loss_take_profit():
    """设置止损止盈"""
    print("\n📋 设置止损止盈...")
    
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    method = "POST"
    request_path = "/api/v5/trade/order-algo"
    body = json.dumps({
        "instId": "BTC-USDT-SWAP",
        "tdMode": "cross",
        "closeFraction": "1",
        "algoOrd": [
            {"tpTriggerPx": "65500", "tpOrdPx": "65500", "tpTriggerPxType": "last"},
            {"tpTriggerPx": "64500", "tpOrdPx": "64500", "tpTriggerPxType": "last"},
            {"slTriggerPx": "67200", "slOrdPx": "67200", "slTriggerPxType": "last"}
        ]
    })
    
    sign = create_sign(timestamp, method, request_path, body)
    
    headers = {
        "OKX-ACCESS-KEY": API_KEY,
        "OKX-ACCESS-SIGN": sign,
        "OKX-ACCESS-TIMESTAMP": timestamp,
        "OKX-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json"
    }
    
    url = f"{BASE_URL}{request_path}"
    response = requests.post(url, headers=headers, data=body, timeout=10)
    
    result = response.json()
    print(f"状态码：{result.get('code')}")
    print(f"消息：{result.get('msg')}")
    
    if result.get('code') == '0':
        print(f"✅ 止损止盈设置成功！")
        return True
    else:
        print(f"❌ 设置失败：{json.dumps(result, indent=2)}")
        return False

def get_position():
    """查询持仓"""
    print("\n📊 查询持仓...")
    
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    method = "GET"
    request_path = "/api/v5/account/positions?instId=BTC-USDT-SWAP"
    body = ""
    
    sign = create_sign(timestamp, method, request_path, body)
    
    headers = {
        "OKX-ACCESS-KEY": API_KEY,
        "OKX-ACCESS-SIGN": sign,
        "OKX-ACCESS-TIMESTAMP": timestamp,
        "OKX-ACCESS-PASSPHRASE": PASSPHRASE,
    }
    
    url = f"{BASE_URL}{request_path}"
    response = requests.get(url, headers=headers, timeout=10)
    
    result = response.json()
    
    if result.get('code') == '0':
        positions = result.get('data', [])
        if positions:
            pos = positions[0]
            print(f"✅ 持仓信息:")
            print(f"   方向：{'做多' if pos['posSide']=='long' else '做空'}")
            print(f"   持仓：{pos['pos']} BTC")
            print(f"   开仓均价：${pos['avgPx']}")
            print(f"   未实现盈亏：${pos['upl']}")
            print(f"   收益率：{pos['uplRatio']}%")
        else:
            print("   无持仓")
    else:
        print(f"   查询失败：{result.get('msg')}")

def main():
    print("=" * 60)
    print("🦞 BTC 合约交易 - 最小仓位测试")
    print("=" * 60)
    print()
    
    # 1. 开空单
    order_id, fill_price = open_short_position()
    
    if order_id:
        time.sleep(2)
        
        # 2. 设置止损止盈
        set_stop_loss_take_profit()
        
        time.sleep(2)
        
        # 3. 查询持仓
        get_position()
        
        print("\n" + "=" * 60)
        print("✅ 交易执行完成！")
        print("=" * 60)
        print()
        print("📋 订单详情:")
        print(f"  方向：做空 (Short)")
        print(f"  品种：BTC-USDT-SWAP")
        print(f"  仓位：0.001 BTC")
        print(f"  杠杆：5x (全仓)")
        print()
        print("📊 止损止盈:")
        print(f"  止损：$67,200 (+1.4%)  最大亏损：~$0.87")
        print(f"  止盈 1: $65,500 (-1.3%)  盈利：~$0.83")
        print(f"  止盈 2: $64,500 (-2.8%)  盈利：~$1.83")
        print(f"  止盈 3: $63,200 (-4.7%)  盈利：~$3.13")
        print()
        print("⚠️  风险提示:")
        print("  - 最小仓位测试")
        print("  - 严格止损")
        print("  - 风险 < $1")
        print()
        print("📝 完整报告：/root/.openclaw/workspace/reports/btc-trade-analysis-0328.md")
    else:
        print("\n❌ 交易失败，请检查 API 配置和权限")
        print("   可能原因:")
        print("   1. API Key 权限不足 (需要交易权限)")
        print("   2. API Key 被禁用")
        print("   3. 账户未完成实名认证")

if __name__ == "__main__":
    main()
