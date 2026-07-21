#!/usr/bin/env python3
"""
微信推送检查脚本 - 使用 sessions_send 直接发送微信
"""

import os
import time
import json

ALERT_DIR = "/root/.openclaw/workspace/trade-alerts"
SENT_FILE = f"{ALERT_DIR}/sent_count.txt"

def get_sent_count():
    if os.path.exists(SENT_FILE):
        with open(SENT_FILE, 'r') as f:
            return int(f.read().strip() or 0)
    return 0

def save_sent_count(count):
    with open(SENT_FILE, 'w') as f:
        f.write(str(count))

def find_trigger_file():
    """查找最新的 trigger_*.txt 文件（包括 sent 目录）"""
    import glob
    files = glob.glob(f"{ALERT_DIR}/trigger_*.txt")
    sent_files = glob.glob(f"{ALERT_DIR}/sent/trigger_*.txt")
    all_files = files + sent_files
    if all_files:
        return max(all_files, key=os.path.getmtime)
    return None

def main():
    print("🦞 微信推送检查服务启动...")
    print(f"监控目录：{ALERT_DIR}")
    
    last_count = get_sent_count()
    
    while True:
        try:
            trigger_file = find_trigger_file()
            
            if trigger_file and os.path.exists(trigger_file):
                with open(trigger_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                
                if content:
                    # 写入 pending_message.txt 供助手检查
                    with open(f"{ALERT_DIR}/pending_message.txt", 'w', encoding='utf-8') as f:
                        f.write(content)
                    
                    # 写入触发标记
                    with open(f"{ALERT_DIR}/SEND_WECHAT_NOW.flag", 'w') as f:
                        f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
                    
                    last_count += 1
                    save_sent_count(last_count)
                    print(f"[{time.strftime('%H:%M:%S')}] ✅ 检测到延时触发消息，已准备发送 (计数:{last_count})")
            
            time.sleep(3)
            
        except Exception as e:
            print(f"❌ 错误：{e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
