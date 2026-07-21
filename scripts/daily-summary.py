#!/usr/bin/env python3
"""
用户个人模型 - 每日自动总结
每天 24:00 运行，提取用户偏好、话题、禁忌
"""

import json
import os
from datetime import datetime
from pathlib import Path

class UserProfileUpdater:
    def __init__(self):
        self.workspace = Path("/root/.openclaw/workspace")
        self.memory_dir = self.workspace / "memory"
        self.profile_path = self.memory_dir / "user-profile.md"
        self.preferences_path = self.memory_dir / "preferences.md"
        self.daily_dir = self.memory_dir / "daily"
        
        self.daily_dir.mkdir(exist_ok=True)
    
    def extract_session_history(self):
        """提取今日会话历史"""
        today = datetime.now().strftime("%Y-%m-%d")
        history_file = self.workspace / f"session-{today}.json"
        
        if history_file.exists():
            with open(history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    
    def analyze_topics(self, messages):
        """分析话题"""
        topics = {}
        for msg in messages:
            content = msg.get('content', '').lower()
            
            if '投资' in content or '交易' in content:
                topics['投资交易'] = topics.get('投资交易', 0) + 1
            if '知识' in content or '学习' in content:
                topics['知识管理'] = topics.get('知识管理', 0) + 1
            if '技能' in content or '功能' in content:
                topics['系统功能'] = topics.get('系统功能', 0) + 1
        
        return topics
    
    def extract_preferences(self, messages):
        """提取用户偏好"""
        preferences = {
            'style': [],
            'commands': [],
            'topics': []
        }
        
        for msg in messages:
            content = msg.get('content', '')
            
            # 检测风格偏好
            if '不啰嗦' in content or '简洁' in content:
                preferences['style'].append('简洁直接')
            if '高执行力' in content:
                preferences['style'].append('高执行力')
            if '主动' in content:
                preferences['style'].append('主动服务')
            
            # 检测常用命令
            if content.startswith('/'):
                cmd = content.split()[0]
                preferences['commands'].append(cmd)
        
        return preferences
    
    def generate_daily_summary(self):
        """生成每日总结"""
        today = datetime.now()
        date_str = today.strftime("%Y-%m-%d")
        
        messages = self.extract_session_history()
        topics = self.analyze_topics(messages)
        preferences = self.extract_preferences(messages)
        
        summary = {
            'date': date_str,
            'message_count': len(messages),
            'topics': topics,
            'preferences': preferences,
            'timestamp': today.isoformat()
        }
        
        # 保存每日总结
        summary_file = self.daily_dir / f"{date_str}.json"
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        
        # 更新偏好文件
        self.update_preferences(preferences, topics)
        
        return summary
    
    def update_preferences(self, preferences, topics):
        """更新偏好文件"""
        # 统计高频偏好
        style_counts = {}
        for style in preferences.get('style', []):
            style_counts[style] = style_counts.get(style, 0) + 1
        
        # 统计高频命令
        cmd_counts = {}
        for cmd in preferences.get('commands', []):
            cmd_counts[cmd] = cmd_counts.get(cmd, 0) + 1
        
        # 更新偏好文件
        with open(self.preferences_path, 'a', encoding='utf-8') as f:
            f.write(f"\n\n## {datetime.now().strftime('%Y-%m-%d')} 更新\n")
            f.write(f"- 消息数：{sum(style_counts.values())}\n")
            f.write(f"- 热门话题：{', '.join(topics.keys())}\n")
            f.write(f"- 风格偏好：{', '.join(style_counts.keys())}\n")
        
        print(f"✅ 偏好已更新：{datetime.now().isoformat()}")
    
    def run(self):
        """运行每日总结"""
        print(f"🔄 开始生成每日总结...")
        summary = self.generate_daily_summary()
        
        print(f"📊 今日消息数：{summary['message_count']}")
        print(f"📚 热门话题：{', '.join(summary['topics'].keys())}")
        print(f"✅ 总结已保存到：{self.daily_dir}")
        
        return summary


if __name__ == "__main__":
    updater = UserProfileUpdater()
    updater.run()
