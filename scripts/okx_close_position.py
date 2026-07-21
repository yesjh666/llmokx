#!/usr/bin/env python3
"""
OKX 平仓模块
- 识别平仓指令 (多单平仓/空单平仓)
- 平掉指定方向的持仓
- 取消该方向的条件单
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

# 交易对配置 - 使用 SWAP 永续合约
SYMBOL = 'BTC-USDT-SWAP'

def ensure_swap_symbol(symbol):
    """确保使用 SWAP 交易对"""
    if not symbol:
        return SYMBOL
    if not symbol.endswith('-SWAP'):
        symbol = symbol + '-SWAP'
    return symbol

def is_swap(symbol):
    """判断是否是 SWAP 合约"""
    return symbol.endswith('-SWAP')

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

def get_position(symbol=None):
    """
    获取当前持仓
    
    Args:
        symbol: 交易对，默认用全局 SYMBOL
    
    Returns:
        dict: 持仓信息 {long: {size, avg_price}, short: {size, avg_price}}
    """
    use_symbol = ensure_swap_symbol(symbol)
    request_path = f'/api/v5/account/positions?instId={use_symbol}'
    url = API_BASE + request_path
    headers = get_headers('GET', request_path)
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        result = response.json()
        
        position = {'long': None, 'short': None}
        
        if result.get('code') == '0' and result.get('data'):
            for pos in result['data']:
                pos_side = pos.get('posSide', '')
                pos_str = pos.get('pos', '0') or '0'
                avg_px_str = pos.get('avgPx', '0') or '0'
                liq_px_str = pos.get('liqPx', '0') or '0'
                size = float(pos_str)
                avg_price = float(avg_px_str)
                liq_price = float(liq_px_str)
                
                if size != 0:
                    if pos_side == 'long':
                        position['long'] = {'size': abs(size), 'avg_price': avg_price}
                    elif pos_side == 'short':
                        position['short'] = {'size': abs(size), 'avg_price': avg_price}
                    elif pos_side == 'net':
                        # net 模式：用 liqPx 与 avgPx 关系判断方向
                        # liqPx > avgPx → 做空（爆仓价在上方）
                        # liqPx < avgPx → 做多（爆仓价在下方）
                        if liq_price > avg_price:
                            position['short'] = {'size': abs(size), 'avg_price': avg_price}
                        else:
                            position['long'] = {'size': abs(size), 'avg_price': avg_price}
        
        return position
    except Exception as e:
        print(f"获取持仓失败：{e}")
        return {'long': None, 'short': None}

def cancel_all_algo_orders(direction=None, symbol=None):
    """
    取消所有条件单 (止损止盈)，包括 conditional 和 oco 类型
    
    Args:
        direction: 指定方向 ('long'/'short'/None 表示全部)
        symbol: 指定币种 ('ETH-USDT-SWAP'/None 表示所有币种)
    
    Returns:
        list: 取消的订单 ID 列表
    """
    cancelled = []
    
    # 🦞 symbol=None = 撤销所有币种；symbol='BTC-USDT' = 只撤销 BTC
    target_symbol = ensure_swap_symbol(symbol) if symbol else None
    
    # 查询所有类型的 algo 单：conditional, oco, trigger 等
    algo_types = ['conditional', 'oco', 'trigger']
    
    for algo_type in algo_types:
        request_path = f'/api/v5/trade/orders-algo-pending?ordType={algo_type}'
        url = API_BASE + request_path
        headers = get_headers('GET', request_path)
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            result = response.json()
            
            if result.get('code') == '0' and result.get('data'):
                for order in result['data']:
                    order_inst = order.get('instId', '')
                    
                    # 🦞 按币种过滤（有指定则只撤销该币种；无指定则撤销所有币种）
                    if target_symbol:
                        if order_inst != target_symbol:
                            print(f"    ⏭️ 跳过其他币种: {order_inst}")
                            continue
                    # symbol=None = 撤销所有币种，不做过滤
                    
                    # 根据方向过滤
                    if direction:
                        order_side = order.get('side', '')
                        if direction == 'long' and order_side != 'sell':
                            continue
                        if direction == 'short' and order_side != 'buy':
                            continue
                    
                    algo_id = order.get('algoId')
                    if algo_id:
                        # 取消订单
                        cancel_path = '/api/v5/trade/cancel-algos'
                        cancel_body = json.dumps([{
                            'instId': order_inst,
                            'algoId': algo_id
                        }])
                        cancel_url = API_BASE + cancel_path
                        cancel_headers = get_headers('POST', cancel_path, cancel_body)
                        
                        cancel_resp = requests.post(cancel_url, headers=cancel_headers, data=cancel_body, timeout=10)
                        cancel_result = cancel_resp.json()
                        
                        if cancel_result.get('code') == '0':
                            cancelled.append(algo_id)
                            print(f"  ✅ 取消{algo_type}单：{algo_id} ({order_inst})")
                        else:
                            print(f"  ❌ 取消{algo_type}单失败：{cancel_result.get('msg', '未知')}")
        except Exception as e:
            print(f"查询{algo_type}单失败：{e}")
    
    return cancelled

def cancel_pending_orders(direction=None, symbol=None):
    """
    取消所有普通挂单 (限价单)
    
    Args:
        direction: 指定方向 ('long'/'short'/None 表示全部)
        symbol: 指定币种 ('ETH-USDT-SWAP'/None 表示所有币种)
    
    Returns:
        list: 取消的订单 ID 列表
    """
    cancelled = []
    
    # 🦞 symbol=None = 撤销所有币种；需要遍历所有持仓币种
    if symbol:
        # 指定币种，只查询该币种
        target_symbol = ensure_swap_symbol(symbol)
        symbols_to_check = [target_symbol]
    else:
        # 未指定币种，查询所有挂单（不指定 instId）
        symbols_to_check = [None]  # None = 查询所有
    
    for target_symbol in symbols_to_check:
        if target_symbol:
            request_path = f'/api/v5/trade/orders-pending?instId={target_symbol}'
        else:
            request_path = f'/api/v5/trade/orders-pending'  # 查询所有币种
        
        url = API_BASE + request_path
        headers = get_headers('GET', request_path)
        
        try:
            # 获取所有挂单
            response = requests.get(url, headers=headers, timeout=10)
            result = response.json()
            
            coin_str = target_symbol if target_symbol else "所有币种"
            print(f"  📊 获取到 {len(result.get('data', []))} 个挂单 ({coin_str})")
            
            if result.get('code') == '0' and result.get('data'):
                for order in result['data']:
                    order_inst = order.get('instId', '')
                    order_side = order.get('side', '')
                    order_id = order.get('ordId')
                    order_px = order.get('px', '')
                    
                    print(f"  📋 挂单：{order_inst} side={order_side}, ordId={order_id}, px={order_px}")
                    
                    # 根据方向过滤
                    if direction:
                        if direction == 'long' and order_side != 'buy':
                            print(f"    ⏭️  跳过 (需要 long，这是{order_side})")
                            continue
                        if direction == 'short' and order_side != 'sell':
                            print(f"    ⏭️  跳过 (需要 short，这是{order_side})")
                            continue
                    
                    if order_id:
                        cancel_path = '/api/v5/trade/cancel-order'
                        body = json.dumps({
                            'instId': order_inst,
                            'ordId': order_id
                        })
                        cancel_url = API_BASE + cancel_path
                        cancel_headers = get_headers('POST', cancel_path, body)
                        
                        cancel_resp = requests.post(cancel_url, headers=cancel_headers, data=body, timeout=10)
                        cancel_result = cancel_resp.json()
                        
                        if cancel_result.get('code') == '0':
                            cancelled.append(order_id)
                            print(f"  ✅ 取消限价挂单：{order_id} ({order_inst})")
        except Exception as e:
            print(f"取消限价挂单失败：{e}")
    
    return cancelled

def close_position_partial(direction, ratio=1.0, symbol=None):
    """
    按比例平仓指定方向
    
    Args:
        direction: 'long' 或 'short' 或 'all'
        ratio: 平仓比例 0.0-1.0, 默认1.0全平
        symbol: 交易对，默认用全局 SYMBOL
    
    Returns:
        dict: 平仓结果
    """
    import time as _time
    use_symbol = ensure_swap_symbol(symbol)
    
    _t0 = _time.time()
    position = get_position(use_symbol)
    _t1 = _time.time()
    print(f"  ⏱️ get_position耗时: {_t1 - _t0:.3f}s")
    
    # direction='all' 时，根据实际持仓判断方向
    if direction == 'all':
        long_pos = position.get('long')
        short_pos = position.get('short')
        if long_pos and short_pos:
            # 双向持仓，优先处理主力仓位
            long_size = long_pos.get('size', 0)
            short_size = short_pos.get('size', 0)
            if short_size > long_size:
                direction = 'short'
                pos_info = short_pos
                side = 'buy'
            elif long_size > 0:
                direction = 'long'
                pos_info = long_pos
                side = 'sell'
            else:
                return {'success': False, 'message': '无持仓'}
        elif short_pos and short_pos.get('size', 0) > 0:
            direction = 'short'
            pos_info = short_pos
            side = 'buy'
        elif long_pos and long_pos.get('size', 0) > 0:
            direction = 'long'
            pos_info = long_pos
            side = 'sell'
        else:
            return {'success': False, 'message': '无持仓'}
        print(f"  🔄 direction='all' 自动识别为: {direction}")
    
    if direction == 'long':
        pos_info = position.get('long')
        if not pos_info:
            return {'success': False, 'message': '无做多持仓'}
        side = 'sell'
    elif direction == 'short':
        pos_info = position.get('short')
        if not pos_info:
            return {'success': False, 'message': '无做空持仓'}
        side = 'buy'
    else:
        return {'success': False, 'message': '无效方向'}
    
    total_size = pos_info['size']
    avg_px = pos_info.get('avg_price', 0)
    
    # 计算平仓数量
    close_size = total_size * ratio
    if close_size <= 0:
        return {'success': False, 'message': '计算平仓数量为0'}
    
    # 精度：SWAP 合约张数 0.01，现货按币种
    if is_swap(use_symbol):
        close_size_str = str(round(close_size, 2))
    else:
        close_size_str = str(round(close_size, 7))
    
    # 获取当前价（用 ticker 快速响应）
    # 用限价单代替市价单：避免 OKX 市价单等待撮合的时间
    ticker_path = f'/api/v5/market/ticker?instId={use_symbol}'
    try:
        ticker_resp = requests.get(API_BASE + ticker_path, headers=get_headers('GET', ticker_path), timeout=5)
        ticker_data = ticker_resp.json().get('data', [{}])[0]
        last_px = float(ticker_data.get('last', 0))
        print(f"  💰 当前价: {last_px}")
    except Exception as e:
        print(f"  ⚠️ 获取当前价失败: {e}")
        last_px = 0
    
    # 限价单价格：偏离当前价 0.3% 确保快速成交且不超出价格限制
    # 做空平仓（buy）：价格设高 0.3%，确保立即成交
    # 做多平仓（sell）：价格设低 0.3%，确保立即成交
    if last_px > 0:
        if side == 'buy':
            px = str(round(last_px * 1.003, 2))
        else:
            px = str(round(last_px * 0.997, 2))
        ord_type = 'limit'
        print(f"  📝 限价单平仓: {side} {close_size_str} @ {px} (市价 {last_px})")
    else:
        ord_type = 'market'
        px = None
        print(f"  📝 市价单平仓: {side} {close_size_str}")
    
    request_path = '/api/v5/trade/order'
    order_body = {
        'instId': use_symbol,
        'tdMode': 'cross',
        'side': side,
        'posSide': 'net',
        'ordType': ord_type,
        'sz': close_size_str,
        'reduceOnly': True
    }
    if ord_type == 'limit' and px:
        order_body['px'] = px
    body = json.dumps(order_body)
    
    url = API_BASE + request_path
    headers = get_headers('POST', request_path, body)
    
    try:
        import time as _time
        _t0 = _time.time()
        print(f"  ⏱️ 开始请求平仓API...")
        response = requests.post(url, headers=headers, data=body, timeout=(3, 10))
        _t1 = _time.time()
        print(f"  ⏱️ HTTP请求耗时: {_t1 - _t0:.2f}s")
        result = response.json()
        
        print(f"  📡 平仓API响应: code={result.get('code')} data={result.get('data', [])}")
        
        # 处理 51126 错误：有冲突的 reduce-only 挂单
        sCode = result.get('data', [{}])[0].get('sCode', '') if result.get('data') else ''
        if result.get('code') != '0' and sCode == '51126':
            print(f"  ⚠️ 51126: 有冲突挂单，尝试先取消再平仓...")
            # 保存当前挂单
            pending_rp = f'/api/v5/trade/orders-pending?instId={use_symbol}'
            pending_resp = requests.get(API_BASE + pending_rp, headers=get_headers('GET', pending_rp), timeout=10)
            saved_orders = pending_resp.json().get('data', [])
            
            # 取消所有挂单
            for o in saved_orders:
                cancel_body = json.dumps({'instId': use_symbol, 'ordId': o['ordId']})
                cancel_rp = '/api/v5/trade/cancel-order'
                requests.post(API_BASE + cancel_rp, headers=get_headers('POST', cancel_rp, cancel_body), data=cancel_body, timeout=10)
                print(f"    取消挂单: {o['ordId']} ({o.get('side')}@{o.get('px')})")
            
            import time; time.sleep(0.5)
            
            # 重试平仓
            headers2 = get_headers('POST', request_path, body)
            response2 = requests.post(url, headers=headers2, data=body, timeout=10)
            result = response2.json()
            print(f"  📡 重试平仓: code={result.get('code')} data={result.get('data', [])}")
            
            # 平仓后恢复挂单
            if result.get('code') == '0':
                import time; time.sleep(0.5)
                for o in saved_orders:
                    restore_body = json.dumps({
                        'instId': use_symbol,
                        'tdMode': 'cross',
                        'side': o.get('side'),
                        'posSide': 'net',
                        'ordType': 'limit',
                        'sz': o.get('sz'),
                        'px': o.get('px'),
                    })
                    restore_rp = '/api/v5/trade/order'
                    requests.post(API_BASE + restore_rp, headers=get_headers('POST', restore_rp, restore_body), data=restore_body, timeout=10)
                    print(f"    恢复挂单: {o.get('side')}@{o.get('px')} sz={o.get('sz')}")
        
        if result.get('code') == '0':
            order_id = result.get('data', [{}])[0].get('ordId')
            return {
                'success': True,
                'order_id': order_id,
                'message': f"部分平仓成功：{direction} {close_size_str}/{total_size} ({ratio*100:.0f}%)",
                'closed_size': close_size_str,
                'total_size': str(total_size),
                'avg_px': avg_px,
                'ratio': ratio
            }
        else:
            error_msg = result.get('data', [{}])[0].get('sMsg', '') if result.get('data') else result.get('msg', '')
            return {'success': False, 'error': error_msg}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def close_position(direction):
    """
    平仓指定方向
    
    Args:
        direction: 'long' (做多) 或 'short' (做空)
    
    Returns:
        dict: 平仓结果
    """
    position = get_position()
    
    if direction == 'long':
        pos_info = position.get('long')
        if not pos_info:
            return {'success': False, 'message': '无做多持仓'}
        
        # 平多：卖出
        side = 'sell'
        size = str(pos_info['size'])
        
    elif direction == 'short':
        pos_info = position.get('short')
        if not pos_info:
            return {'success': False, 'message': '无做空持仓'}
        
        # 平空：买入
        side = 'buy'
        size = str(pos_info['size'])
    else:
        return {'success': False, 'message': '无效方向'}
    
    # 市价平仓
    request_path = '/api/v5/trade/order'
    body = json.dumps({
        'instId': SYMBOL,
        'tdMode': 'cross',
        'side': side,
        'posSide': 'net',
        'ordType': 'market',
        'sz': size,
    })
    
    url = API_BASE + request_path
    headers = get_headers('POST', request_path, body)
    
    try:
        response = requests.post(url, headers=headers, data=body, timeout=10)
        result = response.json()
        
        if result.get('code') == '0':
            order_id = result.get('data', [{}])[0].get('ordId')
            
            # 取消该方向的条件单
            cancelled_algo = cancel_all_algo_orders(direction)
            
            # 取消该方向的限价挂单
            cancelled_pending = cancel_pending_orders(direction)
            
            return {
                'success': True,
                'order_id': order_id,
                'message': f"平仓成功：{direction} {size} BTC",
                'cancelled_algo_orders': cancelled_algo,
                'cancelled_pending_orders': cancelled_pending
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

def execute_close(text):
    """
    从文本中识别平仓指令并执行
    
    Args:
        text: 群消息文本
    
    Returns:
        dict: 执行结果
    """
    text_lower = text.lower()
    
    # 识别平仓方向
    direction = None
    direction_name = None
    
    if any(kw in text_lower for kw in ['多单平仓', '平多', '多单平了', '多单止盈', '多单止损', '平掉多单', '多单全部平仓']):
        direction = 'long'
        direction_name = '做多'
    elif any(kw in text_lower for kw in ['空单平仓', '平空', '空单平了', '空单止盈', '空单止损', '平掉空单', '空单全部平仓']):
        direction = 'short'
        direction_name = '做空'
    elif any(kw in text_lower for kw in ['全部平仓', '全平', '所有平仓', '清空仓位']):
        direction = 'all'
        direction_name = '所有'
    
    if not direction:
        return {'success': False, 'message': '未识别到平仓指令'}
    
    print(f"🔍 识别到平仓指令：{direction_name}")
    
    if direction == 'all':
        # 平所有方向：先取消所有挂单和条件单，再平仓
        cancelled_algo_all = cancel_all_algo_orders(None)
        cancelled_pending_all = cancel_pending_orders(None)
        
        result_long = close_position('long')
        result_short = close_position('short')
        
        return {
            'success': True,
            'message': '全部平仓指令执行完成',
            'cancelled_algo_orders': cancelled_algo_all,
            'cancelled_pending_orders': cancelled_pending_all,
            'results': {
                'long': result_long,
                'short': result_short
            }
        }
    else:
        # 平指定方向：即使没有持仓，也要取消挂单
        position = get_position()
        has_position = False
        
        if direction == 'long' and position.get('long'):
            has_position = True
        elif direction == 'short' and position.get('short'):
            has_position = True
        
        # 先取消该方向的条件单
        cancelled_algo = cancel_all_algo_orders(direction)
        
        # 取消该方向的限价挂单
        cancelled_pending = cancel_pending_orders(direction)
        
        # 如果有持仓，再平仓
        if has_position:
            result = close_position(direction)
            # 无论平仓成功与否，都返回取消的订单
            result['cancelled_algo_orders'] = cancelled_algo
            result['cancelled_pending_orders'] = cancelled_pending
            # 如果有取消的订单，标记为成功
            if cancelled_algo or cancelled_pending:
                result['success'] = True
                if not result.get('message'):
                    result['message'] = f'已取消挂单 (平仓{result.get("error", "失败")})'
            return result
        else:
            # 无持仓，只返回取消挂单的结果
            return {
                'success': True,
                'message': f'无{direction_name}持仓',
                'cancelled_algo_orders': cancelled_algo,
                'cancelled_pending_orders': cancelled_pending
            }

if __name__ == "__main__":
    # 测试
    test_messages = [
        "多单平仓了",
        "空单平了",
        "全部平仓",
        "多单止盈"
    ]
    
    for msg in test_messages:
        print(f"\n测试：{msg}")
        result = execute_close(msg)
        print(f"结果：{result}")
