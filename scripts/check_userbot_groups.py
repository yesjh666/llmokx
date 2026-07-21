#!/usr/bin/env python3
"""
检查 Userbot 加入的群列表
"""

import asyncio
import json
from telethon import TelegramClient

CONFIG_FILE = '/root/.openclaw/workspace/config/telegram_userbot.json'

async def main():
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
    
    client = TelegramClient(
        config['session_file'],
        config['api_id'],
        config['api_hash']
    )
    
    await client.connect()
    
    if not await client.is_user_authorized():
        print("❌ Userbot 未授权")
        await client.disconnect()
        return
    
    print("✅ Userbot 已授权，获取对话列表...\n")
    
    # 获取所有对话
    dialogs = await client.get_dialogs()
    
    print(f"📋 共 {len(dialogs)} 个对话:\n")
    
    for dialog in dialogs:
        chat = dialog.chat
        chat_id = chat.id
        chat_title = chat.title if hasattr(chat, 'title') else f"{chat.first_name} {chat.last_name or ''}"
        chat_type = "群组" if hasattr(chat, 'title') else "私聊"
        
        print(f"[{chat_type}] {chat_title}")
        print(f"  ID: {chat_id}")
        
        # 检查是否是目标群
        if str(chat_id) == "-1004208828815" or chat_id == -1004208828815:
            print(f"  ✅ 找到目标群！")
        
        print()
    
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())