#!/usr/bin/env python3
"""
OKX 合约交易 - 使用官方 SDK
"""

import okx.Trade as Trade
import okx.Account as Account
import time

# API 配置
api_key = "ecd201e1-1d4b-40b1-8f29-3ab786037a9e"
secret_key = "D619624E2846DC0CD5A2F2D6B1D48A74"
passphrase = "a12397255A!"
flag = "0"  # 正式环境

def test_account():
    """测试账户连接"""
    print("📊 测试账户连接...")
    accountAPI = Account.AccountAPI(api_key, secret_key, passphrase, False, flag)
    result = accountAPI.get_account_balance()
    print(f"结果：{result}")
    return result

def open_short_position():
    """开空单"""
    print("\n📊 开空单...")
    
    tradeAPI = Trade.TradeAPI(api_key, secret_key, passphrase, False, flag)
    
    order_data = {
        "instId": "BTC-USDT-SWAP",
        "tdMode": "cross",
        "side": "sell",
        "posSide": "short",
        "ordType": "market",
        "sz": "0.001"
    }
    
    print(f"订单数据：{order_data}")
    result = tradeAPI.place_order(order_data)
    
    print(f"结果：{result}")
    
    if result.get('code') == '0':
        order_id = result['data'][0].get('ordId')
        fill_price = result['data'][0].get('avgPx', '市价')
        print(f"✅ 开单成功！")
        print(f"   订单 ID: {order_id}")
        print(f"   成交价：${fill_price}")
        return order_id, fill_price
    else:
        print(f"❌ 开单失败：{result.get('msg')}")
        return None, None

def set_stop_loss_take_profit():
    """设置止损止盈"""
    print("\n📋 设置止损止盈...")
    
    tradeAPI = Trade.TradeAPI(api_key, secret_key, passphrase, False, flag)
    
    algo_orders = [
        {"tpTriggerPx": "65500", "tpOrdPx": "65500", "tpTriggerPxType": "last"},
        {"tpTriggerPx": "64500", "tpOrdPx": "64500", "tpTriggerPxType": "last"},
        {"slTriggerPx": "67200", "slOrdPx": "67200", "slTriggerPxType": "last"}
    ]
    
    result = tradeAPI.place_algo_order(
        instId="BTC-USDT-SWAP",
        tdMode="cross",
        side="sell",
        posSide="short",
        ordType="conditional",
        algoOrd=algo_orders
    )
    
    print(f"结果：{result}")
    
    if result.get('code') == '0':
        print(f"✅ 止损止盈设置成功！")
        return True
    else:
        print(f"❌ 设置失败：{result.get('msg')}")
        return False

def get_position():
    """查询持仓"""
    print("\n📊 查询持仓...")
    
    accountAPI = Account.AccountAPI(api_key, secret_key, passphrase, False, flag)
    result = accountAPI.get_positions(instId="BTC-USDT-SWAP")
    
    if result.get('code') == '0':
        positions = result.get('data', [])
        if positions:
            pos = positions[0]
            print(f"✅ 持仓信息:")
            print(f"   方向：{'做多' if pos['posSide']=='long' else '做空'}")
            print(f"   持仓：{pos['pos']} BTC")
            print(f"   开仓均价：${pos['avgPx']}")
            print(f"   未实现盈亏：${pos['upl']}")
        else:
            print("   无持仓")
    else:
        print(f"   查询失败：{result.get('msg')}")

def main():
    print("=" * 60)
    print("🦞 BTC 合约交易 - OKX SDK")
    print("=" * 60)
    
    # 1. 测试账户
    test_account()
    time.sleep(1)
    
    # 2. 开空单
    order_id, fill_price = open_short_position()
    
    if order_id:
        time.sleep(2)
        
        # 3. 设置止损止盈
        set_stop_loss_take_profit()
        
        time.sleep(2)
        
        # 4. 查询持仓
        get_position()
        
        print("\n" + "=" * 60)
        print("✅ 交易完成！")
        print("=" * 60)
    else:
        print("\n❌ 交易失败")

if __name__ == "__main__":
    main()
