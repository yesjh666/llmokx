#!/usr/bin/env python3
"""
获取 Telegram 群 ID 工具
用法：python3 get-group-id.py
"""

import requests
import json

BOT_TOKEN = "8666044834:AAFwV_Ss5Vi-pj7e_w1uESKwZisJDHja0iM"

def get_updates():
    """获取最近的更新"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    response = requests.get(url)
    return response.json()

def main():
    print("🔍 查询 Bot 最近的更新...\n")
    
    updates = get_updates()
    
    if not updates.get('ok'):
        print("❌ API 请求失败")
        return
    
    results = updates.get('result', [])
    
    if not results:
        print("📭 暂无更新记录")
        print("\n💡 使用方法:")
        print("1. 在目标群里发送一条消息")
        print("2. 重新运行此脚本")
        return
    
    print(f"📊 找到 {len(results)} 条更新:\n")
    
    for i, update in enumerate(results[-10:], 1):  # 显示最近 10 条
        print(f"--- 更新 #{i} ---")
        
        # 消息更新
        if 'message' in update:
            msg = update['message']
            chat = msg.get('chat', {})
            print(f"类型：{'群聊' if chat.get('type') in ['group', 'supergroup'] else '私聊'}")
            print(f"聊天 ID: {chat.get('id')}")
            print(f"名称：{chat.get('title', chat.get('first_name', '未知'))}")
            print(f"消息：{msg.get('text', '无文本')}")
        
        # 我的聊天成员更新
        if 'my_chat_member' in update:
            chat = update['my_chat_member'].get('chat', {})
            print(f"类型：群聊成员变更")
            print(f"聊天 ID: {chat.get('id')}")
            print(f"名称：{chat.get('title', '未知')}")
        
        print()
    
    print("\n" + "="*50)
    print("💡 配置方法:")
    print("1. 复制群 ID (负数，如 -1001234567890)")
    print("2. 编辑 /root/.openclaw/openclaw.json")
    print("3. 在 telegram.groupAllowFrom 数组中添加")
    print("="*50)

if __name__ == "__main__":
    main()
