#!/usr/bin/env python3
"""
Telegram Userbot 初始化脚本
首次运行需要输入手机号和验证码进行授权
"""

import asyncio
import json
import os
import sys
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

# 配置文件路径
CONFIG_FILE = '/root/.openclaw/workspace/config/telegram_userbot.json'
HASH_FILE = '/root/.openclaw/workspace/config/userbot_phone_code_hash.txt'

def load_config():
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def save_hash(hash_value):
    with open(HASH_FILE, 'w') as f:
        f.write(hash_value)

def load_hash():
    if os.path.exists(HASH_FILE):
        with open(HASH_FILE, 'r') as f:
            return f.read().strip()
    return None

async def main():
    config = load_config()
    
    api_id = config['api_id']
    api_hash = config['api_hash']
    session_file = config['session_file']
    
    print("🤖 Telegram Userbot 初始化")
    print(f"📝 API ID: {api_id}")
    print(f"📁 Session 文件: {session_file}")
    print()
    
    # 创建客户端
    client = TelegramClient(session_file, api_id, api_hash)
    
    await client.connect()
    
    # 检查是否已授权
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"✅ 已授权！")
        print(f"👤 用户: {me.first_name} {me.last_name or ''}")
        print(f"📱 手机: {me.phone}")
        print(f"🆔 User ID: {me.id}")
        await client.disconnect()
        return
    
    print("⚠️  未授权，开始授权流程...")
    
    # 获取手机号
    phone = config.get('phone_number', '')
    if not phone:
        print("❌ 配置文件中未设置 phone_number")
        await client.disconnect()
        return
    
    # 检查是否通过命令行参数传入验证码
    if len(sys.argv) > 1:
        code = sys.argv[1]
        print(f"📝 使用验证码: {code}")
        
        # 加载之前保存的 hash
        phone_code_hash = load_hash()
        if not phone_code_hash:
            print("❌ 未找到 phone_code_hash，请先运行不带参数的脚本发送验证码")
            await client.disconnect()
            return
        
        # 使用验证码登录
        try:
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            print("✅ 登录成功！")
            
            me = await client.get_me()
            print(f"👤 用户: {me.first_name} {me.last_name or ''}")
            print(f"📱 手机: {me.phone}")
            print(f"🆔 User ID: {me.id}")
            print(f"👤 Username: @{me.username or 'N/A'}")
            
            # 清理 hash 文件
            if os.path.exists(HASH_FILE):
                os.remove(HASH_FILE)
            
        except SessionPasswordNeededError:
            # 需要两步验证密码
            if len(sys.argv) > 2:
                password = sys.argv[2]
            else:
                print("⚠️  需要两步验证密码，请通过命令行参数传入：")
                print(f"python3 {sys.argv[0]} {code} <两步验证密码>")
                await client.disconnect()
                return
            
            await client.sign_in(password=password)
            print("✅ 两步验证成功！")
            
            me = await client.get_me()
            print(f"👤 用户: {me.first_name} {me.last_name or ''}")
            
        except Exception as e:
            print(f"❌ 登录失败: {str(e)}")
            await client.disconnect()
            return
            
    else:
        # 发送验证码并保存 hash
        result = await client.send_code_request(phone)
        phone_code_hash = result.phone_code_hash
        save_hash(phone_code_hash)
        print(f"✅ 验证码已发送到 {phone}")
        print(f"💾 phone_code_hash 已保存")
        print("\n请使用以下命令完成验证：")
        print(f"python3 {sys.argv[0]} <验证码>")
        print("如果需要两步验证密码：")
        print(f"python3 {sys.argv[0]} <验证码> <两步验证密码>")
    
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())