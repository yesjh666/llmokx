#!/usr/bin/env python3
"""
测试使用 Telethon 登录（不重新发送验证码）
"""

import asyncio
import json
from telethon import TelegramClient

CONFIG_FILE = '/root/.openclaw/workspace/config/telegram_userbot.json'
HASH_FILE = '/root/.openclaw/workspace/config/userbot_phone_code_hash.txt'

async def main():
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
    
    api_id = config['api_id']
    api_hash = config['api_hash']
    session_file = config['session_file']
    phone = config['phone_number']
    
    client = TelegramClient(session_file, api_id, api_hash)
    await client.connect()
    
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"✅ 已授权！")
        print(f"👤 用户: {me.first_name} {me.last_name or ''}")
        print(f"📱 手机: {me.phone}")
        await client.disconnect()
        return
    
    # 读取保存的 hash
    with open(HASH_FILE, 'r') as f:
        phone_code_hash = f.read().strip()
    
    print(f"📱 手机号: {phone}")
    print(f"🔑 Hash: {phone_code_hash}")
    print(f"\n请输入验证码（尝试使用之前的验证码 542804）：")
    
    # 尝试使用旧验证码
    code = "542804"
    print(f"📝 尝试验证码: {code}")
    
    try:
        await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        print("✅ 登录成功！")
        me = await client.get_me()
        print(f"👤 用户: {me.first_name} {me.last_name or ''}")
    except Exception as e:
        print(f"❌ 登录失败: {str(e)}")
    
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())