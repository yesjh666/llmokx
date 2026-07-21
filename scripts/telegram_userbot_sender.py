#!/usr/bin/env python3
"""
Telegram Userbot 发送消息模块
使用 Telethon 以用户身份发送消息（优化版）
"""

import asyncio
import json
import os
import atexit
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

# 配置文件路径
CONFIG_FILE = '/root/.openclaw/workspace/config/telegram_userbot.json'

_client = None
_loop = None

def load_config():
    """加载 Userbot 配置"""
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

async def get_client():
    """获取或创建 Telegram 客户端（保持连接）"""
    global _client, _loop
    
    if _client is None:
        config = load_config()
        api_id = config['api_id']
        api_hash = config['api_hash']
        session_file = config['session_file']
        
        # 使用当前事件循环
        _loop = asyncio.get_event_loop()
        _client = TelegramClient(session_file, api_id, api_hash, loop=_loop)
        await _client.connect()
        
        if not await _client.is_user_authorized():
            raise Exception("❌ Userbot 未授权！请先运行 init_telegram_userbot.py 进行初始化")
        
        # 注册退出清理
        atexit.register(cleanup_client)
    
    return _client

def cleanup_client():
    """清理客户端连接"""
    global _client, _loop
    if _client and _loop and _loop.is_running():
        try:
            _loop.run_until_complete(_client.disconnect())
        except:
            pass

async def send_message_async(chat_id, text):
    """异步发送消息"""
    client = await get_client()
    
    try:
        # 发送消息
        await client.send_message(chat_id, text, parse_mode='html')
        return True, "发送成功"
    except Exception as e:
        return False, f"发送失败: {str(e)}"

def send_message(chat_id, text):
    """同步接口：发送消息到指定群/用户"""
    try:
        config = load_config()
        if not config.get('enabled', False):
            return False, "Userbot 未启用"
        
        # 在新的事件循环中运行异步函数
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # 使用原始 session 文件（共享授权）
        client = TelegramClient(
            config['session_file'],  # 使用原始 session
            config['api_id'],
            config['api_hash'],
            loop=loop
        )
        
        async def send():
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    return False, "Userbot 未授权（请检查 session 文件）"
                
                await client.send_message(chat_id, text, parse_mode='html')
                return True, "发送成功"
            except Exception as e:
                return False, f"发送异常: {str(e)}"
            finally:
                # 确保断开连接，释放 session 文件锁
                try:
                    await client.disconnect()
                except:
                    pass
        
        success, msg = loop.run_until_complete(send())
        loop.close()
        
        return success, msg
    except Exception as e:
        return False, f"异常: {str(e)}"

async def test_async():
    """测试 Userbot 连接"""
    try:
        config = load_config()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        client = TelegramClient(
            config['session_file'],
            config['api_id'],
            config['api_hash'],
            loop=loop
        )
        
        await client.connect()
        me = await client.get_me()
        await client.disconnect()
        loop.close()
        
        return True, f"✅ Userbot 已就绪: {me.first_name} (@{me.username or 'N/A'})"
    except Exception as e:
        return False, f"❌ Userbot 测试失败: {str(e)}"

def test():
    """同步接口：测试 Userbot 连接"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success, msg = loop.run_until_complete(test_async())
        loop.close()
        return success, msg
    except Exception as e:
        return False, f"❌ 测试异常: {str(e)}"

if __name__ == '__main__':
    # 测试 Userbot
    success, msg = test()
    print(msg)