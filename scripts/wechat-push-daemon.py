#!/usr/bin/env python3
"""
微信推送守护进程 - 检查 flag 文件并发送微信
"""

import os
import time
import subprocess

ALERT_DIR = "/root/.openclaw/workspace/trade-alerts"
FLAG_FILE = f"{ALERT_DIR}/SEND_WECHAT_NOW.flag"
PENDING_FILE = f"{ALERT_DIR}/pending_message.txt"
SENT_FLAG_FILE = f"{ALERT_DIR}/sent_flags.txt"

def get_sent_flags():
    if os.path.exists(SENT_FLAG_FILE):
        with open(SENT_FLAG_FILE, 'r') as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_sent_flag(flag_content):
    sent_flags = get_sent_flags()
    sent_flags.add(flag_content)
    with open(SENT_FLAG_FILE, 'w') as f:
        f.write('\n'.join(sent_flags))

def send_wechat_via_message(text):
    """通过 message 工具发送微信"""
    # 使用 sessions_send 发送到当前会话
    # 或者写入一个特殊文件，由主助手检查
    trigger_file = f"{ALERT_DIR}/wechat_message_to_send.txt"
    with open(trigger_file, 'w', encoding='utf-8') as f:
        f.write(text)
    print(f"[{time.strftime('%H:%M:%S')}] 微信消息已写入：{trigger_file}")

def main():
    print("🦞 微信推送守护进程启动...")
    
    while True:
        try:
            if os.path.exists(FLAG_FILE):
                with open(FLAG_FILE, 'r') as f:
                    flag_content = f.read().strip()
                
                sent_flags = get_sent_flags()
                if flag_content and flag_content not in sent_flags:
                    # 有新 flag，检查 pending_message.txt 并发送
                    if os.path.exists(PENDING_FILE):
                        with open(PENDING_FILE, 'r', encoding='utf-8') as f:
                            message = f.read().strip()
                        
                        if message:
                            send_wechat_via_message(message)
                            save_sent_flag(flag_content)
                            # 清空 flag
                            os.remove(FLAG_FILE)
                            print(f"[{time.strftime('%H:%M:%S')}] ✅ 微信推送已触发")
            
            time.sleep(5)
            
        except Exception as e:
            print(f"❌ 错误：{e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
