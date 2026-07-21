#!/usr/bin/env python3
"""
微信推送脚本 - 直接通过 OpenClaw 消息系统发送微信
"""

import os
import sys
import json
import time

def send_wechat_message(text):
    """发送微信消息"""
    # 写入特殊触发文件，由主助手检查并发送
    trigger_file = "/root/.openclaw/workspace/trade-alerts/WECHAT_SEND_NOW.txt"
    
    payload = {
        "type": "wechat_message",
        "text": text,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    
    with open(trigger_file, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    
    print(f"[{time.strftime('%H:%M:%S')}] 微信发送请求已写入：{trigger_file}")
    return True

if __name__ == "__main__":
    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])
        send_wechat_message(message)
    else:
        print("用法：python3 wechat-send.py <消息内容>")
