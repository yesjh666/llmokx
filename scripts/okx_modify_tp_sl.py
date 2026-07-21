#!/usr/bin/env python3
"""
OKX 修改止盈止损模块
- 修改所有 OCO/conditional 条件单的止盈或止损价
- 修改前核算条件单总量 = 持仓量
- 不支持直接修改的单取消重建
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
DEFAULT_SYMBOL = 'BTC-USDT-SWAP'

def generate_signature(timestamp, method, request_path, body=''):
    prehash = timestamp + method + request_path + body
    mac = hmac.new(bytes(API_SECRET, encoding='utf8'), bytes(prehash, encoding='utf8'), digestmod=hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()

def get_headers(method, request_path, body=''):
    timestamp = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
    signature = generate_signature(timestamp, method, request_path, body)
    return {
        'OK-ACCESS-KEY': API_KEY,
        'OK-ACCESS-SIGN': signature,
        'OK-ACCESS-TIMESTAMP': timestamp,
        'OK-ACCESS-PASSPHRASE': PASSPHRASE,
        'Content-Type': 'application/json'
    }

def ensure_swap_symbol(symbol):
    """确保使用 SWAP 交易对"""
    if not symbol:
        return DEFAULT_SYMBOL
    if not symbol.endswith('-SWAP'):
        symbol = symbol + '-SWAP'
    return symbol

def get_all_algo_orders(symbol=None):
    """获取指定交易对的条件单（conditional + oco）"""
    symbol = ensure_swap_symbol(symbol or DEFAULT_SYMBOL)
    all_orders = []
    for ordType in ['conditional', 'oco']:
        rp = f'/api/v5/trade/orders-algo-pending?instId={symbol}&ordType={ordType}'
        url = API_BASE + rp
        headers = get_headers('GET', rp)
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json().get('data', [])
        for o in data:
            o['_ordType'] = ordType
            all_orders.append(o)
    return all_orders

def get_position_size(symbol=None):
    """获取当前持仓量（绝对值）"""
    symbol = ensure_swap_symbol(symbol or DEFAULT_SYMBOL)
    rp = '/api/v5/account/positions'
    url = API_BASE + rp
    headers = get_headers('GET', rp)
    resp = requests.get(url, headers=headers, timeout=10)
    for p in resp.json().get('data', []):
        if p.get('instId') == symbol:
            return abs(float(p.get('pos', '0')))
    return 0

def get_pending_limit_orders(symbol=None):
    """获取指定交易对的限价挂单（未成交）"""
    symbol = ensure_swap_symbol(symbol or DEFAULT_SYMBOL)
    rp = f'/api/v5/trade/orders-pending?instId={symbol}'
    url = API_BASE + rp
    headers = get_headers('GET', rp)
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        return resp.json().get('data', [])
    except Exception as e:
        print(f"  ⚠️ 获取限价挂单失败: {e}")
        return []

def cancel_pending_order(inst_id, ord_id):
    """撤销单个限价挂单"""
    rp = '/api/v5/trade/cancel-order'
    body = json.dumps({'instId': inst_id, 'ordId': ord_id})
    headers = get_headers('POST', rp, body)
    try:
        resp = requests.post(API_BASE + rp, headers=headers, data=body, timeout=10)
        result = resp.json()
        return result.get('code') == '0'
    except Exception as e:
        print(f"  ❌ 撤单失败: {e}")
        return False

def place_limit_with_tp_sl(side, price, sz, new_tp, sl_price=None, symbol=None):
    """重新挂限价单，附带新止盈止损（使用 attachAlgoOrds）"""
    symbol = ensure_swap_symbol(symbol or DEFAULT_SYMBOL)
    rp = '/api/v5/trade/order'
    
    attach_algo = {
        'side': side,
        'posSide': 'net',
        'ordType': 'conditional',
        'algoOrdType': 'conditional',
        'sz': str(sz)
    }
    
    if new_tp:
        attach_algo['tpTriggerPx'] = str(new_tp)
        attach_algo['tpOrdPx'] = '-1'
    
    if sl_price:
        attach_algo['slTriggerPx'] = str(sl_price)
        attach_algo['slOrdPx'] = '-1'
    
    body = json.dumps({
        'instId': symbol,
        'tdMode': 'cross',
        'side': side,
        'posSide': 'net',
        'ordType': 'limit',
        'sz': str(sz),
        'px': str(price),
        'attachAlgoOrds': [attach_algo]
    })
    
    headers = get_headers('POST', rp, body)
    try:
        resp = requests.post(API_BASE + rp, headers=headers, data=body, timeout=10)
        result = resp.json()
        if result.get('code') == '0':
            new_ord_id = result.get('data', [{}])[0].get('ordId', '?')
            return {'success': True, 'ordId': new_ord_id}
        else:
            return {'success': False, 'error': result.get('msg', '未知错误')}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def modify_take_profit(new_tp, symbol=None):
    """
    修改所有条件单的止盈价 + 限价挂单附带止盈
    
    Args:
        new_tp: 新止盈价
        symbol: 交易对（如 ETH-USDT-SWAP），默认 BTC-USDT-SWAP
    
    Returns:
        dict: 执行结果
    """
    symbol = ensure_swap_symbol(symbol or DEFAULT_SYMBOL)
    results = {'success': True, 'modified': 0, 'rebuilt': 0, 'pending_modified': 0, 'errors': []}
    
    # ===== 1. 修改已有条件单（OCO/conditional）的止盈 =====
    orders = get_all_algo_orders(symbol)
    has_algo = len(orders) > 0
    if not has_algo:
        print(f"  ⚠️ {symbol} 无条件单")
    else:
        for o in orders:
            algo_id = o.get('algoId')
            ord_type = o.get('_ordType')
            old_tp = o.get('tpTriggerPx', '')
            old_sl = o.get('slTriggerPx', '')
            sz = o.get('sz', '0')
            side = o.get('side', '')
            
            print(f"  📋 {ord_type} algoId={algo_id} sz={sz} tp={old_tp} sl={old_sl}")
            
            # 尝试直接修改
            rp = '/api/v5/trade/amend-algos'
            body = json.dumps({
                'instId': symbol,
                'algoId': algo_id,
                'newTpTriggerPx': str(new_tp)
            })
            headers = get_headers('POST', rp, body)
            resp = requests.post(API_BASE + rp, headers=headers, data=body, timeout=10)
            result = resp.json()
            
            sCode = '1'
            if result.get('data'):
                sCode = result['data'][0].get('sCode', '1')
            
            if result.get('code') == '0' and sCode == '0':
                results['modified'] += 1
                print(f"    ✅ 修改成功: tp → {new_tp}")
            else:
                # 修改失败，取消重建
                sMsg = result['data'][0].get('sMsg', '') if result.get('data') else result.get('msg', '')
                print(f"    ⚠️ 修改失败({sMsg})，取消重建...")
                
                # 取消
                cancel_body = json.dumps([{'instId': symbol, 'algoId': algo_id}])
                cancel_rp = '/api/v5/trade/cancel-algos'
                cancel_headers = get_headers('POST', cancel_rp, cancel_body)
                cancel_resp = requests.post(API_BASE + cancel_rp, headers=cancel_headers, data=cancel_body, timeout=10)
                
                if cancel_resp.json().get('code') != '0':
                    results['errors'].append(f'取消 {algo_id} 失败')
                    continue
                
                # 重建为 OCO
                rebuild_body = json.dumps({
                    'instId': symbol,
                    'tdMode': 'cross',
                    'side': side,
                    'posSide': 'net',
                    'ordType': 'oco',
                    'sz': sz,
                    'tpTriggerPx': str(new_tp),
                    'tpOrdPx': '-1',
                    'slTriggerPx': old_sl if old_sl else '',
                    'slOrdPx': '-1',
                    'reduceOnly': 'true',
                    'ccy': 'USDT'
                })
                rebuild_rp = '/api/v5/trade/order-algo'
                rebuild_headers = get_headers('POST', rebuild_rp, rebuild_body)
                rebuild_resp = requests.post(API_BASE + rebuild_rp, headers=rebuild_headers, data=rebuild_body, timeout=10)
                
                if rebuild_resp.json().get('code') == '0':
                    results['rebuilt'] += 1
                    print(f"    ✅ 重建成功: tp={new_tp} sl={old_sl}")
                else:
                    err = rebuild_resp.json().get('msg', '未知错误')
                    results['errors'].append(f'重建 {algo_id} 失败: {err}')
                    print(f"    ❌ 重建失败: {err}")
    
    # ===== 2. 修改限价挂单附带的止盈（撤单重挂）=====
    pending_orders = get_pending_limit_orders(symbol)
    if pending_orders:
        print(f"\n  📋 发现 {len(pending_orders)} 个限价挂单，检查是否需要修改止盈...")
        for po in pending_orders:
            ord_id = po.get('ordId', '')
            side = po.get('side', '')  # buy/sell
            px = po.get('px', '')
            sz = po.get('sz', '')
            # 获取挂单附带的 algo 信息
            attach_algo = po.get('attachAlgoOrds', [])
            old_tp = ''
            old_sl = ''
            if attach_algo and len(attach_algo) > 0:
                old_tp = attach_algo[0].get('tpTriggerPx', '')
                old_sl = attach_algo[0].get('slTriggerPx', '')
            
            if old_tp == str(new_tp):
                print(f"    ⏭️ 挂单 {ord_id} 止盈已是 {new_tp}，跳过")
                continue
            
            print(f"    📌 挂单 {ord_id}: {side} @ {px} sz={sz} tp={old_tp} sl={old_sl} → 撤单重挂")
            
            # 撤单
            if not cancel_pending_order(symbol, ord_id):
                results['errors'].append(f'撤销限价挂单 {ord_id} 失败')
                continue
            
            # 重新挂单，附带新止盈
            sl_to_use = old_sl if old_sl else None
            rebase_result = place_limit_with_tp_sl(side, px, sz, new_tp, sl_price=sl_to_use, symbol=symbol)
            
            if rebase_result.get('success'):
                results['pending_modified'] += 1
                print(f"    ✅ 重挂成功: {side} @ {px} sz={sz} tp={new_tp} sl={old_sl} new_ordId={rebase_result.get('ordId')}")
            else:
                results['errors'].append(f'重挂限价单失败({side}@{px}): {rebase_result.get("error")}')
                print(f"    ❌ 重挂失败: {rebase_result.get('error')}")
    else:
        print(f"\n  ⏭️ 无限价挂单")
    
    # ===== 3. 核算 =====
    pos_sz = get_position_size(symbol)
    new_orders = get_all_algo_orders(symbol)
    algo_total = sum(float(o.get('sz', '0')) for o in new_orders)
    diff = round(algo_total - pos_sz, 6)
    
    results['position'] = pos_sz
    results['algo_total'] = algo_total
    results['diff'] = diff
    results['balanced'] = abs(diff) < 0.01
    
    print(f"\n  📊 核算: 持仓={pos_sz}, 条件单={algo_total}, 差额={diff}")
    
    if not results['balanced']:
        results['errors'].append(f'条件单总量({algo_total})≠持仓({pos_sz})，差额{diff}')
        print(f"  ❌ 不平衡！需要手动调整")
    else:
        print(f"  ✅ 平衡")
    
    parts = []
    if results['modified'] > 0:
        parts.append(f"直接修改{results['modified']}个")
    if results['rebuilt'] > 0:
        parts.append(f"重建{results['rebuilt']}个")
    if results['pending_modified'] > 0:
        parts.append(f"限价单重挂{results['pending_modified']}个")
    results['message'] = f"止盈修改完成: {', '.join(parts)}" if parts else "止盈修改完成: 无操作"
    return results
    
    for o in orders:
        algo_id = o.get('algoId')
        ord_type = o.get('_ordType')
        old_tp = o.get('tpTriggerPx', '')
        old_sl = o.get('slTriggerPx', '')
        sz = o.get('sz', '0')
        side = o.get('side', '')
        
        print(f"  📋 {ord_type} algoId={algo_id} sz={sz} tp={old_tp} sl={old_sl}")
        
        # 尝试直接修改
        rp = '/api/v5/trade/amend-algos'
        body = json.dumps({
            'instId': symbol,
            'algoId': algo_id,
            'newTpTriggerPx': str(new_tp)
        })
        headers = get_headers('POST', rp, body)
        resp = requests.post(API_BASE + rp, headers=headers, data=body, timeout=10)
        result = resp.json()
        
        sCode = '1'
        if result.get('data'):
            sCode = result['data'][0].get('sCode', '1')
        
        if result.get('code') == '0' and sCode == '0':
            results['modified'] += 1
            print(f"    ✅ 修改成功: tp → {new_tp}")
        else:
            # 修改失败，取消重建
            sMsg = result['data'][0].get('sMsg', '') if result.get('data') else result.get('msg', '')
            print(f"    ⚠️ 修改失败({sMsg})，取消重建...")
            
            # 取消
            cancel_body = json.dumps([{'instId': symbol, 'algoId': algo_id}])
            cancel_rp = '/api/v5/trade/cancel-algos'
            cancel_headers = get_headers('POST', cancel_rp, cancel_body)
            cancel_resp = requests.post(API_BASE + cancel_rp, headers=cancel_headers, data=cancel_body, timeout=10)
            
            if cancel_resp.json().get('code') != '0':
                results['errors'].append(f'取消 {algo_id} 失败')
                continue
            
            # 重建为 OCO
            rebuild_body = json.dumps({
                'instId': symbol,
                'tdMode': 'cross',
                'side': side,
                'posSide': 'net',
                'ordType': 'oco',
                'sz': sz,
                'tpTriggerPx': str(new_tp),
                'tpOrdPx': '-1',
                'slTriggerPx': old_sl if old_sl else '',
                'slOrdPx': '-1',
                'reduceOnly': 'true',
                'ccy': 'USDT'
            })
            rebuild_rp = '/api/v5/trade/order-algo'
            rebuild_headers = get_headers('POST', rebuild_rp, rebuild_body)
            rebuild_resp = requests.post(API_BASE + rebuild_rp, headers=rebuild_headers, data=rebuild_body, timeout=10)
            
            if rebuild_resp.json().get('code') == '0':
                results['rebuilt'] += 1
                print(f"    ✅ 重建成功: tp={new_tp} sl={old_sl}")
            else:
                err = rebuild_resp.json().get('msg', '未知错误')
                results['errors'].append(f'重建 {algo_id} 失败: {err}')
                print(f"    ❌ 重建失败: {err}")
    
    # 核算
    pos_sz = get_position_size(symbol)
    new_orders = get_all_algo_orders(symbol)
    algo_total = sum(float(o.get('sz', '0')) for o in new_orders)
    diff = round(algo_total - pos_sz, 6)
    
    results['position'] = pos_sz
    results['algo_total'] = algo_total
    results['diff'] = diff
    results['balanced'] = abs(diff) < 0.01
    
    print(f"\n  📊 核算: 持仓={pos_sz}, 条件单={algo_total}, 差额={diff}")
    
    if not results['balanced']:
        results['errors'].append(f'条件单总量({algo_total})≠持仓({pos_sz})，差额{diff}')
        print(f"  ❌ 不平衡！需要手动调整")
    else:
        print(f"  ✅ 平衡")
    
    results['message'] = f"止盈修改完成: 直接修改{results['modified']}个, 重建{results['rebuilt']}个"
    return results

def modify_stop_loss(new_sl, symbol=None):
    """
    修改所有条件单的止损价 + 限价挂单附带止损
    
    Args:
        new_sl: 新止损价
        symbol: 交易对（如 ETH-USDT-SWAP），默认 BTC-USDT-SWAP
    
    Returns:
        dict: 执行结果
    """
    symbol = ensure_swap_symbol(symbol or DEFAULT_SYMBOL)
    results = {'success': True, 'modified': 0, 'rebuilt': 0, 'pending_modified': 0, 'errors': []}
    
    # ===== 1. 修改已有条件单（OCO/conditional）的止损 =====
    orders = get_all_algo_orders(symbol)
    has_algo = len(orders) > 0
    if not has_algo:
        print(f"  ⚠️ {symbol} 无条件单")
    else:
        for o in orders:
            algo_id = o.get('algoId')
            ord_type = o.get('_ordType')
            old_tp = o.get('tpTriggerPx', '')
            old_sl = o.get('slTriggerPx', '')
            sz = o.get('sz', '0')
            side = o.get('side', '')
            
            print(f"  📋 {ord_type} algoId={algo_id} sz={sz} tp={old_tp} sl={old_sl}")
            
            # 尝试直接修改
            rp = '/api/v5/trade/amend-algos'
            body = json.dumps({
                'instId': symbol,
                'algoId': algo_id,
                'newSlTriggerPx': str(new_sl)
            })
            headers = get_headers('POST', rp, body)
            resp = requests.post(API_BASE + rp, headers=headers, data=body, timeout=10)
            result = resp.json()
            
            sCode = '1'
            if result.get('data'):
                sCode = result['data'][0].get('sCode', '1')
            
            if result.get('code') == '0' and sCode == '0':
                results['modified'] += 1
                print(f"    ✅ 修改成功: sl → {new_sl}")
            else:
                sMsg = result['data'][0].get('sMsg', '') if result.get('data') else result.get('msg', '')
                print(f"    ⚠️ 修改失败({sMsg})，取消重建...")
                
                cancel_body = json.dumps([{'instId': symbol, 'algoId': algo_id}])
                cancel_rp = '/api/v5/trade/cancel-algos'
                cancel_headers = get_headers('POST', cancel_rp, cancel_body)
                requests.post(API_BASE + cancel_rp, headers=cancel_headers, data=cancel_body, timeout=10)
                
                rebuild_body = json.dumps({
                    'instId': symbol,
                    'tdMode': 'cross',
                    'side': side,
                    'posSide': 'net',
                    'ordType': 'oco',
                    'sz': sz,
                    'tpTriggerPx': old_tp if old_tp else '',
                    'tpOrdPx': '-1',
                    'slTriggerPx': str(new_sl),
                    'slOrdPx': '-1',
                    'reduceOnly': 'true',
                    'ccy': 'USDT'
                })
                rebuild_rp = '/api/v5/trade/order-algo'
                rebuild_headers = get_headers('POST', rebuild_rp, rebuild_body)
                rebuild_resp = requests.post(API_BASE + rebuild_rp, headers=rebuild_headers, data=rebuild_body, timeout=10)
                
                if rebuild_resp.json().get('code') == '0':
                    results['rebuilt'] += 1
                    print(f"    ✅ 重建成功: tp={old_tp} sl={new_sl}")
                else:
                    err = rebuild_resp.json().get('msg', '未知错误')
                    results['errors'].append(f'重建 {algo_id} 失败: {err}')
    
    # ===== 2. 修改限价挂单附带的止损（撤单重挂）=====
    pending_orders = get_pending_limit_orders(symbol)
    if pending_orders:
        print(f"\n  📋 发现 {len(pending_orders)} 个限价挂单，检查是否需要修改止损...")
        for po in pending_orders:
            ord_id = po.get('ordId', '')
            side = po.get('side', '')
            px = po.get('px', '')
            sz = po.get('sz', '')
            attach_algo = po.get('attachAlgoOrds', [])
            old_tp = ''
            old_sl = ''
            if attach_algo and len(attach_algo) > 0:
                old_tp = attach_algo[0].get('tpTriggerPx', '')
                old_sl = attach_algo[0].get('slTriggerPx', '')
            
            if old_sl == str(new_sl):
                print(f"    ⏭️ 挂单 {ord_id} 止损已是 {new_sl}，跳过")
                continue
            
            print(f"    📌 挂单 {ord_id}: {side} @ {px} sz={sz} tp={old_tp} sl={old_sl} → 撤单重挂")
            
            # 撤单
            if not cancel_pending_order(symbol, ord_id):
                results['errors'].append(f'撤销限价挂单 {ord_id} 失败')
                continue
            
            # 重新挂单，附带新止损
            tp_to_use = old_tp if old_tp else None
            rebase_result = place_limit_with_tp_sl(side, px, sz, tp_to_use, sl_price=new_sl, symbol=symbol)
            
            if rebase_result.get('success'):
                results['pending_modified'] += 1
                print(f"    ✅ 重挂成功: {side} @ {px} sz={sz} tp={old_tp} sl={new_sl} new_ordId={rebase_result.get('ordId')}")
            else:
                results['errors'].append(f'重挂限价单失败({side}@{px}): {rebase_result.get("error")}')
                print(f"    ❌ 重挂失败: {rebase_result.get('error')}")
    else:
        print(f"\n  ⏭️ 无限价挂单")
    
    # ===== 3. 核算 =====
    pos_sz = get_position_size(symbol)
    new_orders = get_all_algo_orders(symbol)
    algo_total = sum(float(o.get('sz', '0')) for o in new_orders)
    diff = round(algo_total - pos_sz, 6)
    results['position'] = pos_sz
    results['algo_total'] = algo_total
    results['diff'] = diff
    results['balanced'] = abs(diff) < 0.01
    
    print(f"\n  📊 核算: 持仓={pos_sz}, 条件单={algo_total}, 差额={diff}")
    
    parts = []
    if results['modified'] > 0:
        parts.append(f"直接修改{results['modified']}个")
    if results['rebuilt'] > 0:
        parts.append(f"重建{results['rebuilt']}个")
    if results['pending_modified'] > 0:
        parts.append(f"限价单重挂{results['pending_modified']}个")
    results['message'] = f"止损修改完成: {', '.join(parts)}" if parts else "止损修改完成: 无操作"
    return results

if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        action = sys.argv[1]
        price = sys.argv[2]
        if action == 'tp':
            print(f"=== 修改止盈 → {price} ===")
            r = modify_take_profit(price)
        elif action == 'sl':
            print(f"=== 修改止损 → {price} ===")
            r = modify_stop_loss(price)
        print(f"\n结果: {json.dumps(r, ensure_ascii=False)}")
    else:
        print("用法: python3 okx_modify_tp_sl.py tp|sl <price>")
