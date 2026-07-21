#!/usr/bin/env python3
"""
OKX 交易执行脚本 - 使用 httpx
"""

import httpx
import hmac
import hashlib
import base64
import time
import json

API_KEY = "ecd201e1-1d4b-40b1-8f29-3ab786037a9e"
SECRET = "D619624E2846DC0CD5A2F2D6B1D48A74"
PASSPHRASE = "a12397255A!"
BASE_URL = "https://www.okx.com"

def sign_and_request(method, path, body=None):
    """签名并发送请求"""
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    body_str = json.dumps(body) if body else ""
    
    message = timestamp + method + path + body_str
    sign = base64.b64encode(
        hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).digest()
    ).decode()
    
    headers = {
        "OKX-ACCESS-KEY": API_KEY,
        "OKX-ACCESS-SIGN": sign,
        "OKX-ACCESS-TIMESTAMP": timestamp,
        "OKX-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json"
    }
    
    print(f"请求：{method} {path}")
    print(f"时间戳：{timestamp}")
    print(f"Headers: OKX-ACCESS-KEY={API_KEY[:20]}...")
    
    url = f"{BASE_URL}{path}"
    
    with httpx.Client(timeout=10) as client:
        if method == "GET":
            response = client.get(url, headers=headers)
        else:
            response = client.post(url, headers=headers, json=body)
    
    return response.json()

def main():
    print("=" * 60)
    print("🦞 BTC 合约交易执行")
    print("=" * 60)
    print()
    
    # 1. 测试账户
    print("1️⃣ 测试账户连接...")
    result = sign_and_request("GET", "/api/v5/account/balance")
    print(f"结果：{result}")
    
    if result.get('code') != '0':
        print(f"❌ 账户连接失败：{result.get('msg')}")
        print()
        print("可能原因:")
        print("1. API Key 无交易权限")
        print("2. API Key 被禁用")
        print("3. 需要完成实名认证")
        return
    
    print("✅ 账户连接成功！")
    print()
    
    # 2. 开空单
    print("2️⃣ 开空单...")
    order_body = {
        "instId": "BTC-USDT-SWAP",
        "tdMode": "cross",
        "side": "sell",
        "posSide": "short",
        "ordType": "market",
        "sz": "0.001"
    }
    
    result = sign_and_request("POST", "/api/v5/trade/order", order_body)
    print(f"结果：{result}")
    
    if result.get('code') == '0':
        order_id = result['data'][0]['ordId']
        fill_price = result['data'][0].get('avgPx', '市价')
        print(f"✅ 开单成功！订单 ID: {order_id}, 成交价：${fill_price}")
    else:
        print(f"❌ 开单失败：{result.get('msg')}")
        return
    
    print()
    print("=" * 60)
    print("✅ 交易执行完成！")
    print("=" * 60)

if __name__ == "__main__":
    try:
        import httpx
        main()
    except ImportError:
        print("❌ 需要安装 httpx: pip3 install httpx")
