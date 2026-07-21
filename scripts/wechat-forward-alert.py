#!/usr/bin/env python3
"""
微信转发脚本 - 监控 Telegram 监控脚本日志，提取交易参数并转发到微信
"""

import os
import time
import re

LOG_FILE = "/root/.openclaw/workspace/logs/telegram-monitor.log"
STATE_FILE = "/root/.openclaw/workspace/trade-alerts/last_position.txt"
ALERT_FILE = "/root/.openclaw/workspace/trade-alerts/latest.json"

def ensure_dirs():
    os.makedirs("/root/.openclaw/workspace/trade-alerts", exist_ok=True)

def get_last_position():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return int(f.read().strip())
    return 0

def save_last_position(pos):
    with open(STATE_FILE, 'w') as f:
        f.write(str(pos))

def parse_alert_from_log(line):
    """从日志行中提取已回复的交易参数"""
    # 匹配：✅ 已回复 (开单:66700.0 止盈:[65188.0], 开单:67788.0 止盈:[63888.0])
    match = re.search(r'✅ 已回复 \((.*?)\)', line)
    if not match:
        return None
    
    content = match.group(1)
    
    # 提取方向（从上下文获取，简化处理）
    direction = "BTC 做空"  # 默认，可从日志前文提取
    
    # 提取止损（从日志前文获取）
    # 简化：从 content 中提取
    
    orders = []
    # 匹配：开单:66700.0 止盈:[65188.0]
    order_matches = re.findall(r'开单:([\d.]+)\s*止盈:\[([\d.,\[\]]+)\]', content)
    for price_str, tp_str in order_matches:
        price = float(price_str)
        # 解析止盈列表
        tp_str = tp_str.strip('[]')
        tp_list = [float(x.strip()) for x in tp_str.split(',') if x.strip()]
        orders.append({"price": price, "take_profit": tp_list})
    
    if not orders:
        return None
    
    return {"direction": direction, "orders": orders}

def format_wechat_message(alert):
    """格式化为微信消息"""
    msg = "🦞 **交易参数提醒**\n\n"
    
    for i, order in enumerate(alert["orders"], 1):
        tp_str = ",".join([f"{int(tp)}" for tp in order["take_profit"]]) if order["take_profit"] else "未识别"
        price = int(order["price"])
        
        msg += f"**第{i}单**\n"
        msg += f"方向：{alert['direction']}\n"
        msg += f"开单价：{price}\n"
        msg += f"止盈：{tp_str}\n"
        msg += f"杠杆：20 倍\n"
        msg += f"保证金：10%\n"
        if i < len(alert["orders"]):
            msg += "\n"
    
    msg += "\n_🤖 自动提取，仅供参考_"
    return msg

def main():
    ensure_dirs()
    print("🦞 微信转发服务启动...")
    print(f"监控日志：{LOG_FILE}")
    
    last_pos = get_last_position()
    print(f"起始位置：{last_pos}")
    
    while True:
        try:
            if not os.path.exists(LOG_FILE):
                time.sleep(5)
                continue
            
            with open(LOG_FILE, 'r', encoding='utf-8', errors='ignore') as f:
                f.seek(last_pos)
                new_lines = f.readlines()
                new_pos = f.tell()
            
            if new_lines:
                for line in new_lines:
                    if '✅ 已回复' in line:
                        alert = parse_alert_from_log(line)
                        if alert:
                            # 保存到文件，供主助手读取
                            import json
                            alert["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
                            with open(ALERT_FILE, 'w', encoding='utf-8') as f:
                                json.dump(alert, f, ensure_ascii=False, indent=2)
                            print(f"[{time.strftime('%H:%M:%S')}] 发现新提醒：{len(alert['orders'])} 单")
                
                save_last_position(new_pos)
                last_pos = new_pos
            
            time.sleep(3)
            
        except Exception as e:
            print(f"❌ 错误：{e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
