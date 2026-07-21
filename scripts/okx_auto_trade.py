#!/usr/bin/env python3
"""
OKX 自动挂单模块（使用 requests 直接调用 API）
- 根据交易参数自动挂单
- 设置止损止盈
"""

import os
import hmac
import base64
import time
import hashlib
import requests
import json

# OKX API 配置
API_KEY = os.getenv('OKX_API_KEY', 'ecd201e1-1d4b-40b1-8f29-3ab786037a9e')
API_SECRET = os.getenv('OKX_API_SECRET', 'D619624E2846DC0CD5A2F2D6B1D48A74')
PASSPHRASE = os.getenv('OKX_PASSPHRASE', 'a12397255A!')
API_BASE = 'https://www.okx.com'

# 默认交易对（可被调用方覆盖）- 使用 SWAP 永续合约
DEFAULT_SYMBOL = 'BTC-USDT-SWAP'

# SWAP 合约面值配置（1 张 = 多少币）
CONTRACT_VALUES = {
    'BTC-USDT-SWAP': 0.01,   # 1 张 = 0.01 BTC
    'ETH-USDT-SWAP': 0.1,    # 1 张 = 0.1 ETH
}

def get_contract_value(symbol):
    """获取合约面值，非 SWAP 返回 None"""
    return CONTRACT_VALUES.get(symbol)

def is_swap(symbol):
    """判断是否是 SWAP 合约"""
    return symbol.endswith('-SWAP')

def coin_to_contracts(coin_amount, symbol):
    """币数量转换为张数（SWAP 合约）"""
    cv = get_contract_value(symbol)
    if cv:
        contracts = coin_amount / cv
        return max(round(contracts, 2), 0.01)  # 精度 0.01，最小 0.01 张
    return coin_amount  # 非 SWAP 返回原值

def contracts_to_coin(contracts, symbol):
    """张数转换为币数量（SWAP 合约）"""
    cv = get_contract_value(symbol)
    if cv:
        return contracts * cv
    return contracts

def ensure_swap_symbol(symbol):
    """确保使用 SWAP 交易对"""
    if not symbol:
        return DEFAULT_SYMBOL
    # 如果传入的是现货杠杆格式，自动转为 SWAP
    if not symbol.endswith('-SWAP'):
        symbol = symbol + '-SWAP'
    return symbol

def generate_signature(timestamp, method, request_path, body=''):
    """生成 OKX API 签名"""
    prehash = timestamp + method + request_path + body
    mac = hmac.new(
        bytes(API_SECRET, encoding='utf8'),
        bytes(prehash, encoding='utf8'),
        digestmod=hashlib.sha256
    )
    return base64.b64encode(mac.digest()).decode()

def get_headers(method, request_path, body=''):
    """获取请求头"""
    timestamp = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
    signature = generate_signature(timestamp, method, request_path, body)
    
    return {
        'OK-ACCESS-KEY': API_KEY,
        'OK-ACCESS-SIGN': signature,
        'OK-ACCESS-TIMESTAMP': timestamp,
        'OK-ACCESS-PASSPHRASE': PASSPHRASE,
        'Content-Type': 'application/json'
    }

def check_order_status(order_id, symbol=None):
    """
    检查订单状态
    
    Args:
        order_id: 订单 ID
    
    Returns:
        str: 订单状态 (filled/cancelled/pending)
    """
    symbol = symbol or DEFAULT_SYMBOL
    request_path = f'/api/v5/trade/order?instId={symbol}&ordId={order_id}'
    url = API_BASE + request_path
    headers = get_headers('GET', request_path)
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        result = response.json()
        
        if result.get('code') == '0' and result.get('data'):
            state = result['data'][0].get('state', '')
            # OKX 订单状态：live(未成交), partially_filled(部分成交), filled(已成交), cancelled(已取消)
            if state == 'filled':
                return 'filled'
            elif state == 'cancelled':
                return 'cancelled'
            else:
                return state
        else:
            return 'unknown'
    except Exception as e:
        return f'error: {e}'

def place_order_with_tp_sl(direction, price, stop_loss, take_profit_list, size="0.002", symbol=None, order_type="limit"):
    """    限价/市价单挂单 + 同时设置止损止盈（使用 attachAlgoOrds 单个对象）
    
    Args:
        direction: 方向
        price: 开单价
        stop_loss: 止损价
        take_profit_list: 止盈价列表
        size: 下单数量（SWAP 合约为张数，现货为币数量）
        symbol: 交易对
        order_type: 'limit' 或 'market'
    
    Returns:
        dict: 订单结果
    """
    symbol = ensure_swap_symbol(symbol)
    side = 'buy' if direction in ('long', '做多') else 'sell'
    request_path = '/api/v5/trade/order'
    
    # 构建 attachAlgoOrds（只在有止盈或止损时才添加）
    has_tp = take_profit_list and len(take_profit_list) > 0
    has_sl = bool(stop_loss)
    attach_algo_ords = []
    if has_tp or has_sl:
        attach_algo_ord = {
            'side': side,
            'posSide': 'net',
            'ordType': 'conditional',
            'algoOrdType': 'conditional',
            'sz': str(size)
        }
        if has_tp:
            attach_algo_ord['tpTriggerPx'] = str(take_profit_list[0])
            attach_algo_ord['tpOrdPx'] = '-1'
        if has_sl:
            attach_algo_ord['slTriggerPx'] = str(stop_loss)
            attach_algo_ord['slOrdPx'] = '-1'
        attach_algo_ords = [attach_algo_ord]
    
    # 构建请求体
    request_body = {
        'instId': symbol,
        'tdMode': 'cross',
        'side': side,
        'posSide': 'net',
        'ordType': order_type,
        'sz': str(size)
    }
    # 只在有止盈/止损时添加 attachAlgoOrds
    if attach_algo_ords:
        request_body['attachAlgoOrds'] = attach_algo_ords
    # 限价单需要价格，市价单不需要
    if order_type == 'limit':
        request_body['px'] = str(price)
    
    body = json.dumps(request_body)
    
    # 发送请求
    url = API_BASE + request_path
    headers = get_headers('POST', request_path, body)
    
    try:
        response = requests.post(url, headers=headers, data=body, timeout=10)
        result = response.json()
        
        if result.get('code') == '0':
            order_id = result.get('data', [{}])[0].get('ordId')
            return {
                'success': True,
                'order_id': order_id,
                'message': f"挂单成功：{direction} @ {price}",
                'tp_sl_set': True
            }
        else:
            return {
                'success': False,
                'error': result.get('msg', '未知错误')
            }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def set_stop_loss(direction, stop_loss, size="0.001", symbol=None):
    """    设置止损
    
    Args:
        direction: 方向
        stop_loss: 止损价
        size: 数量（SWAP 为张数）
    
    Returns:
        dict: 设置结果
    """
    symbol = ensure_swap_symbol(symbol)
    # API 路径
    request_path = '/api/v5/trade/order-algo'
    
    # 确定止损方向
    side = 'sell' if '做多' in direction else 'buy'
    
    # 🦞 用实际仓位量替代传入的 size
    actual_pos = get_position_size(symbol)
    if actual_pos > 0:
        if is_swap(symbol):
            size = str(round(actual_pos, 2))  # SWAP 张数精度 0.01
        elif 'BTC' in symbol:
            size = str(round(actual_pos, 6))
        elif 'ETH' in symbol:
            size = str(round(actual_pos, 4))
        else:
            size = str(round(actual_pos, 4))
        print(f"  📊 止损使用实际仓位量: {size}")
    
    # 请求体
    body = json.dumps({
        'instId': symbol,
        'tdMode': 'cross',
        'side': side,
        'posSide': 'net',
        'ordType': 'conditional',
        'algoOrdType': 'conditional',
        'sz': str(size),
        'slTriggerPx': str(stop_loss),
        'slOrdPx': '-1',
    })
    
    # 发送请求
    url = API_BASE + request_path
    headers = get_headers('POST', request_path, body)
    
    try:
        response = requests.post(url, headers=headers, data=body, timeout=10)
        result = response.json()
        
        if result.get('code') == '0':
            algo_id = result.get('data', [{}])[0].get('algoId')
            return {
                'success': True,
                'algo_id': algo_id,
                'message': f"止损设置成功：{stop_loss} (sz={size})"
            }
        else:
            return {
                'success': False,
                'error': result.get('msg', '未知错误')
            }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def set_stop_loss_take_profit(direction, stop_loss, take_profit_list, size="0.002", symbol=None):
    """    分别设置止损和止盈（需要有持仓）
    
    Args:
        direction: 方向
        stop_loss: 止损价
        take_profit_list: 止盈价列表
        size: 数量（SWAP 为张数）
    
    Returns:
        dict: 设置结果
    """
    symbol = ensure_swap_symbol(symbol)
    result = {
        'success': True,
        'stop_loss_set': False,
        'take_profit_set': False,
        'errors': []
    }
    
    # 确定平仓方向（与开仓方向相反）
    close_side = 'buy' if '做空' in direction else 'sell'
    
    # 🦞 查询实际仓位量，而非使用传入的 size（避免币价变化导致数量不一致）
    actual_pos = get_position_size(symbol)
    if actual_pos <= 0:
        print(f"  ⚠️ 无仓位，跳过止损止盈设置")
        return {'success': False, 'message': '无仓位', 'errors': ['无仓位']}
    
    # 精度处理：SWAP 合约用张数精度 0.01，现货用币精度
    if is_swap(symbol):
        pos_precision = 2
    elif 'BTC' in symbol:
        pos_precision = 6
    elif 'ETH' in symbol:
        pos_precision = 4
    else:
        pos_precision = 4
    
    # API 路径
    request_path = '/api/v5/trade/order-algo'
    url = API_BASE + request_path
    
    # 止损：用全仓位量
    if stop_loss:
        sl_size = round(actual_pos, pos_precision)
        body = json.dumps({
            'instId': symbol,
            'tdMode': 'cross',
            'side': close_side,
            'posSide': 'net',
            'ordType': 'conditional',
            'algoOrdType': 'conditional',
            'sz': str(sl_size),
            'slTriggerPx': str(stop_loss),
            'slOrdPx': '-1',
        })
        
        headers = get_headers('POST', request_path, body)
        response = requests.post(url, headers=headers, data=body, timeout=10)
        sl_result = response.json()
        
        if sl_result.get('code') == '0':
            result['stop_loss_set'] = True
            print(f"  ✅ 止损设置成功 (sz={sl_size}，全仓位)")
        else:
            result['errors'].append(f"止损：{sl_result.get('msg')}")
            print(f"  ❌ 止损设置失败：{sl_result.get('msg')}")
    
    # 止盈：按比例分配仓位量
    if take_profit_list:
        n_tp = len(take_profit_list)
        remaining = actual_pos
        
        for i, tp in enumerate(take_profit_list):
            if i < n_tp - 1:
                # 非最后一档：默认 70% / 均分
                if n_tp == 2:
                    tp_size = round(actual_pos * 0.7, pos_precision)
                else:
                    tp_size = round(actual_pos / n_tp, pos_precision)
                remaining -= tp_size
            else:
                # 最后一档：用剩余全部
                tp_size = round(remaining, pos_precision)
            
            if tp_size <= 0:
                continue
            
            body = json.dumps({
                'instId': symbol,
                'tdMode': 'cross',
                'side': close_side,
                'posSide': 'net',
                'ordType': 'conditional',
                'algoOrdType': 'conditional',
                'sz': str(tp_size),
                'tpTriggerPx': str(tp),
                'tpOrdPx': '-1',
            })
            
            headers = get_headers('POST', request_path, body)
            response = requests.post(url, headers=headers, data=body, timeout=10)
            tp_result = response.json()
            
            if tp_result.get('code') == '0':
                result['take_profit_set'] = True
                print(f"  ✅ 止盈{i+1}设置成功：{tp} (sz={tp_size})")
            else:
                result['errors'].append(f"止盈{i+1}: {tp_result.get('msg')}")
                print(f"  ❌ 止盈{i+1}设置失败：{tp_result.get('msg')}")
    
    if result['stop_loss_set'] and result['take_profit_set']:
        result['message'] = "止损止盈设置成功"
    elif result['stop_loss_set'] or result['take_profit_set']:
        result['message'] = "止损止盈部分设置成功"
    else:
        result['success'] = False
        result['message'] = "止损止盈设置失败"
    
    return result

def set_take_profit(direction, take_profit, size="0.001", symbol=None):
    """    设置止盈
    
    Args:
        direction: 方向
        take_profit: 止盈价
        size: 数量
    
    Returns:
        dict: 设置结果
    """
    symbol = ensure_swap_symbol(symbol)
    # API 路径
    request_path = '/api/v5/trade/order-algo'
    
    # 确定止盈方向
    side = 'sell' if '做多' in direction else 'buy'
    
    # 🦞 用实际仓位量替代传入的 size
    actual_pos = get_position_size(symbol)
    if actual_pos > 0:
        if is_swap(symbol):
            size = str(round(actual_pos, 2))
        elif 'BTC' in symbol:
            size = str(round(actual_pos, 6))
        elif 'ETH' in symbol:
            size = str(round(actual_pos, 4))
        else:
            size = str(round(actual_pos, 4))
        print(f"  📊 止盈使用实际仓位量: {size}")
    
    # 请求体
    body = json.dumps({
        'instId': symbol,
        'tdMode': 'cross',
        'side': side,
        'posSide': 'net',
        'ordType': 'conditional',
        'algoOrdType': 'conditional',
        'sz': str(size),
        'tpTriggerPx': str(take_profit),
        'tpOrdPx': '-1',
    })
    
    # 发送请求
    url = API_BASE + request_path
    headers = get_headers('POST', request_path, body)
    
    try:
        response = requests.post(url, headers=headers, data=body, timeout=10)
        result = response.json()
        
        if result.get('code') == '0':
            algo_id = result.get('data', [{}])[0].get('algoId')
            return {
                'success': True,
                'algo_id': algo_id,
                'message': f"止盈设置成功：{take_profit}"
            }
        else:
            return {
                'success': False,
                'error': result.get('msg', '未知错误')
            }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def get_position_size(symbol=None):
    """获取当前仓位数量（绝对值）。SWAP 返回张数，现货返回币数量"""
    symbol = ensure_swap_symbol(symbol)
    rp = f'/api/v5/account/positions?instId={symbol}'
    resp = requests.get(API_BASE + rp, headers=get_headers('GET', rp), timeout=10)
    data = resp.json().get('data', [])
    if data:
        pos_str = str(data[0].get('pos', '0') or '0').strip()
        if not pos_str:
            return 0.0
        pos = abs(float(pos_str))
        if is_swap(symbol):
            cv = get_contract_value(symbol)
            coin_equiv = pos * cv if cv else pos
            print(f"  📊 当前仓位: {pos} 张 ≈ {coin_equiv} 币 ({symbol})")
        else:
            print(f"  📊 当前仓位量: {pos} ({symbol})")
        return pos
    return 0.0


def get_position_margin(symbol=None):
    """获取当前持仓保证金，优先用imr字段，兜底用pos*ctVal*avgPx/leverage估算"""
    symbol = ensure_swap_symbol(symbol)
    rp = f'/api/v5/account/positions?instId={symbol}'
    resp = requests.get(API_BASE + rp, headers=get_headers('GET', rp), timeout=10)
    data = resp.json().get('data', [])
    if data:
        imr_str = str(data[0].get('imr', '') or '').strip()
        pos_str = str(data[0].get('pos', '0') or '0').strip()
        if pos_str == '0' or not pos_str:
            return 0.0
        pos = float(pos_str)
        pos_side = data[0].get('posSide', '')
        direction_label = "long" if (pos_side == 'long' or (not pos_side and pos > 0)) else "short"
        # 优先用imr(已用保证金)
        try:
            margin = abs(float(imr_str))
            if margin > 0:
                print(f"  📊 当前持仓保证金: {margin:.2f} USDT ({direction_label}, {abs(pos)} 张, {symbol}) [imr]")
                return margin
        except (ValueError, TypeError):
            pass
        # 兜底: 用pos*ctVal*avgPx/leverage估算
        if abs(pos) > 0:
            avg_px = abs(float(data[0].get('avgPx', '0') or '0'))
            leverage = abs(float(data[0].get('lever', '1') or '1')) or 1
            # 获取ctVal(合约面值)
            ct_val = 1.0
            try:
                rp2 = f'/api/v5/public/instruments?instType=SWAP&instId={symbol}'
                resp2 = requests.get(API_BASE + rp2, headers=get_headers('GET', rp2), timeout=10)
                inst_data = resp2.json().get('data', [])
                if inst_data:
                    ct_val = float(inst_data[0].get('ctVal', '1'))
            except Exception:
                pass
            margin = abs(pos) * ct_val * avg_px / leverage
            print(f"  📊 当前持仓保证金: {margin:.2f} USDT ({direction_label}, {abs(pos)} 张, {symbol}) [估算: ctVal={ct_val}]")
            return margin
        return 0.0
    return 0.0


def get_account_equity():
    """获取账户总权益(USDT)"""
    rp = '/api/v5/account/balance'
    resp = requests.get(API_BASE + rp, headers=get_headers('GET', rp), timeout=10)
    data = resp.json().get('data', [{}])[0]
    return float(data.get('totalEq', '0'))


def set_leverage(symbol, leverage):
    """设置杠杆倍数，如果超限自动降级，返回实际设置的杠杆"""
    rp = '/api/v5/account/set-leverage'
    
    # 先尝试目标杠杆
    body = json.dumps({'instId': symbol, 'lever': str(leverage), 'mgnMode': 'cross'})
    resp = requests.post(API_BASE + rp, headers=get_headers('POST', rp, body), data=body, timeout=10)
    result = resp.json()
    
    if result.get('code') == '0':
        print(f"  ✅ 杠杆已设为 {leverage}x")
        return int(leverage)
    
    # 超限，自动降级找最大可用
    print(f"  ⚠️ {leverage}x 超限，自动降级...")
    for lev in [75, 50, 30, 20, 10, 5, 3]:
        if lev >= leverage:
            continue
        body = json.dumps({'instId': symbol, 'lever': str(lev), 'mgnMode': 'cross'})
        resp = requests.post(API_BASE + rp, headers=get_headers('POST', rp, body), data=body, timeout=10)
        if resp.json().get('code') == '0':
            print(f"  ✅ 杠杆降级为 {lev}x (最大可用)")
            return lev
    
    # 全部失败，读取当前杠杆
    rp2 = f'/api/v5/account/leverage-info?instId={symbol}&mgnMode=cross'
    resp2 = requests.get(API_BASE + rp2, headers=get_headers('GET', rp2), timeout=10)
    data = resp2.json().get('data', [{}])
    current = int(data[0].get('lever', '5')) if data else 5
    print(f"  ⚠️ 使用当前杠杆 {current}x")
    return current


def calculate_order_size(price, leverage, margin_ratio, equity=None, symbol=None):
    """
    根据杠杆和保证金比例计算下单数量
    
    公式: size = (equity × margin_ratio × leverage) / price
    SWAP 合约：再除以面值得到张数
    
    Args:
        price: 开单价格
        leverage: 杠杆倍数
        margin_ratio: 保证金占总本金比例 (0.02 = 2%)
        equity: 账户权益，不传则自动获取
        symbol: 交易对
    
    Returns:
        float: 下单数量（SWAP 为张数，现货为币数量）
    """
    if equity is None:
        equity = get_account_equity()
    
    margin = equity * margin_ratio  # 投入保证金
    notional = margin * leverage     # 名义价值
    coin_size = notional / price     # 币的数量
    
    symbol = ensure_swap_symbol(symbol)
    if is_swap(symbol):
        contracts = coin_to_contracts(coin_size, symbol)
        cv = get_contract_value(symbol)
        print(f"  📊 计算下单量: 权益={equity:.2f} × 比例={margin_ratio} × 杠杆={leverage}x / 价格={price} = {coin_size:.6f} 币 = {contracts} 张 (面值={cv})")
        return contracts
    else:
        print(f"  📊 计算下单量: 权益={equity:.2f} × 比例={margin_ratio} × 杠杆={leverage}x / 价格={price} = {coin_size:.6f}")
        return coin_size


def execute_trade(result, symbol=None):
    """
    执行完整交易流程
    
    Args:
        result: 提取的交易参数
        symbol: 交易对，如 'ETH-USDT'，自动转为 SWAP
    """
    symbol = ensure_swap_symbol(symbol)
    trade_result = {
        "success": True,
        "orders": [],
        "errors": []
    }
    
    direction = result.get("direction", "")
    stop_loss = result.get("stop_loss")
    orders = result.get("orders", [])
    leverage = result.get("leverage")
    margin_ratios = result.get("margin_ratios", [])
    
    # 设置杠杆（必须在下单前，且无挂单时才能改）
    actual_leverage = leverage
    if leverage:
        actual_leverage = set_leverage(symbol, leverage)
    
    # 获取账户权益（一次获取，多次使用）
    equity = get_account_equity()
    print(f"  💰 账户权益: {equity:.2f} USDT, 实际杠杆: {actual_leverage}x")

    # 🛡️ 持仓保证金检查：如果已有持仓保证金 > 余额1%，拒绝加仓开单
    position_margin = get_position_margin(symbol)
    if equity > 0 and position_margin > equity * 0.01:
        print(f"  🛑 拒绝开单加仓：当前持仓保证金 {position_margin:.2f} USDT > 余额1% ({equity * 0.01:.2f} USDT)")
        trade_result["success"] = False
        trade_result["errors"].append(f"持仓保证金 {position_margin:.2f} USDT 已超余额1%，拒绝加仓")
        trade_result["block_reason"] = "margin_exceeded"
        return trade_result

    # 🛡️ 风控检查：单笔保证金和总保证金比例限制
    try:
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'config', 'trade-config.json')
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                trade_config = json.load(f)
            risk_control = trade_config.get('risk_control', {})
            disable_risk = risk_control.get('disable_risk_control', False)
            
            if not disable_risk and equity > 0:
                # 计算当前总保证金（持仓+即将下单）
                new_orders_margin = 0.0
                for i, order in enumerate(orders):
                    price = order.get("price", 0)
                    otype = order.get("type", "limit")
                    take_profit_list = order.get("take_profit", [])
                    
                    if not price and otype == "market":
                        # 市价单：用当前价格估算保证金
                        try:
                            rp = f'/api/v5/market/ticker?instId={symbol}'
                            ticker_resp = requests.get(API_BASE + rp, headers=get_headers('GET', rp), timeout=10)
                            ticker_data = ticker_resp.json().get('data', [])
                            price = float(ticker_data[0].get('last', 0)) if ticker_data else 0
                        except:
                            price = 0
                    
                    if price:
                        ratio = None
                        if margin_ratios and i < len(margin_ratios):
                            ratio = margin_ratios[i]
                        elif order.get("margin_pct"):
                            ratio = order.get("margin_pct")
                        
                        if actual_leverage and ratio and ratio > 0:
                            # 计算单笔保证金
                            size = calculate_order_size(price, actual_leverage, ratio, equity, symbol)
                            if is_swap(symbol):
                                size = max(round(size, 2), 0.01)
                            elif 'BTC' in symbol:
                                size = round(size, 6)
                            else:
                                size = round(size, 4)
                            
                            # 单笔保证金 = size * price * margin_ratio
                            order_margin = size * price * ratio
                            new_orders_margin += order_margin
                            
                            # 单笔保证金检查
                            if order_margin > equity * risk_control.get('single_order_max_margin_ratio', 0.03):
                                print(f"  🛑 单笔保证金风控：单笔保证金 {order_margin:.2f} USDT > 限制 {equity * risk_control.get('single_order_max_margin_ratio', 0.03):.2f} USDT (订单{i+1})")
                                trade_result["success"] = False
                                trade_result["errors"].append(f"单笔保证金 {order_margin:.2f} USDT 超过限制 {equity * risk_control.get('single_order_max_margin_ratio', 0.03):.2f} USDT")
                                trade_result["block_reason"] = "single_order_margin_exceeded"
                                return trade_result
                
                # 总保证金检查
                total_margin = position_margin + new_orders_margin
                if total_margin > equity * risk_control.get('total_max_margin_ratio', 0.05):
                    print(f"  🛑 总保证金风控：总保证金 {total_margin:.2f} USDT > 限制 {equity * risk_control.get('total_max_margin_ratio', 0.05):.2f} USDT")
                    trade_result["success"] = False
                    trade_result["errors"].append(f"总保证金 {total_margin:.2f} USDT 超过限制 {equity * risk_control.get('total_max_margin_ratio', 0.05):.2f} USDT")
                    trade_result["block_reason"] = "total_margin_exceeded"
                    return trade_result
    except Exception as e:
        print(f"  ⚠️ 风控检查异常: {e}")
    
    for i, order in enumerate(orders):
        price = order.get("price")
        otype = order.get("type", "limit")
        take_profit_list = order.get("take_profit", [])
        
        # 市价单无price，自动获取当前市价用于计算size
        calc_price = price  # 用于计算下单量的价格
        if not price and otype == "market":
            try:
                rp = f'/api/v5/market/ticker?instId={symbol}'
                ticker_resp = requests.get(API_BASE + rp, headers=get_headers('GET', rp), timeout=10)
                ticker_data = ticker_resp.json().get('data', [])
                if ticker_data:
                    calc_price = float(ticker_data[0].get('last', 0))
                    price = '-1'  # OKX市价单价格标记
                    print(f"  💡 市价单使用当前价格: {calc_price}")
                else:
                    trade_result["errors"].append(f"市价单获取当前价格失败")
                    continue
            except Exception as e:
                trade_result["errors"].append(f"市价单获取价格异常: {e}")
                continue
        elif not price:
            trade_result["errors"].append(f"限价单开单价缺失")
            continue
        
        # 计算下单量（SWAP 自动转张数）
        # 优先从 margin_ratios 数组取，其次从 order 内的 margin_pct 取
        ratio = None
        if margin_ratios and i < len(margin_ratios):
            ratio = margin_ratios[i]
        elif order.get("margin_pct"):
            ratio = order.get("margin_pct")
        
        if actual_leverage and ratio:
            size = calculate_order_size(calc_price, actual_leverage, ratio, equity, symbol)
            # SWAP 合约张数精度 0.01，现货按币种精度
            if is_swap(symbol):
                size = max(round(size, 2), 0.01)
            elif 'BTC' in symbol:
                size = round(size, 6)
            elif 'ETH' in symbol:
                size = round(size, 4)
            else:
                size = round(size, 4)
            size = str(size)
        else:
            # 没有杠杆信息，用默认值（SWAP 张数）
            if is_swap(symbol):
                size = '1' if 'BTC' in symbol else '1'
            else:
                size = '0.002' if 'BTC' in symbol else '0.1'
            print(f"  ⚠️ 无杠杆/保证金信息，使用默认 size={size}")
        
        # 下单 + 同时设置止损止盈
        order_result = place_order_with_tp_sl(direction, price, stop_loss, take_profit_list, size, symbol=symbol, order_type=otype)
        
        if not order_result.get('success'):
            error_msg = order_result.get('error', '未知错误')
            trade_result["errors"].append(f"挂单失败：{error_msg}")
            print(f"  ❌ 挂单失败：{error_msg}")
            continue
        
        trade_result["orders"].append({
            "price": price,
            "order_id": order_result.get('order_id'),
            "status": "pending",
            "size": size,
            "stop_loss_set": order_result.get('tp_sl_set', False),
            "take_profit_set": order_result.get('tp_sl_set', False)
        })
        print(f"  ✅ {order_result.get('message')} (sz={size})")
        if order_result.get('tp_sl_set'):
            print(f"  ✅ 止损止盈已同时设置")
    
    return trade_result

if __name__ == "__main__":
    # 测试
    test_result = {
        "direction": "做空",
        "stop_loss": 70000,
        "orders": [
            {"price": 66200, "take_profit": [65188]},
            {"price": 67300, "take_profit": [63888]}
        ]
    }
    
    print("=== 测试 OKX 自动挂单 ===")
    print(f"API Key: {API_KEY[:10]}...")
    print(f"交易对：{symbol}")
    result = execute_trade(test_result)
    print(f"\n执行结果：{result}")
