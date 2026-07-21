#!/bin/bash
# 微信推送自动检查守护进程
# 每 5 秒检查 pending_message.txt，有内容时调用 openclaw CLI 发送

ALERT_DIR="/root/.openclaw/workspace/trade-alerts"
PENDING_FILE="$ALERT_DIR/pending_message.txt"
SENT_FILE="$ALERT_DIR/sent_wechat.log"
TARGET="o9cq80zZk50Q33Snd8zOZ5vlAEQ4@im.wechat"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 微信推送自动检查启动..." >> "$SENT_FILE"

while true; do
    if [ -f "$PENDING_FILE" ]; then
        CONTENT=$(cat "$PENDING_FILE" 2>/dev/null)
        if [ -n "$CONTENT" ] && [ "$CONTENT" != "" ]; then
            # 使用 openclaw CLI 发送微信
            openclaw message send --channel openclaw-weixin --target "$TARGET" -m "$CONTENT" >> "$SENT_FILE" 2>&1
            echo "[$(date '+%H:%M:%S')] ✅ 已发送微信" >> "$SENT_FILE"
            # 清空文件防止重复
            > "$PENDING_FILE"
        fi
    fi
    sleep 5
done
