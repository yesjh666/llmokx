#!/bin/bash
# 微信推送守护进程 - 每 10 秒检查待发送消息

ALERT_DIR="/root/.openclaw/workspace/trade-alerts"
PENDING_FILE="$ALERT_DIR/pending_message.txt"
SENT_FILE="$ALERT_DIR/sent_messages.log"

while true; do
    if [ -f "$PENDING_FILE" ]; then
        CONTENT=$(cat "$PENDING_FILE" 2>/dev/null)
        if [ -n "$CONTENT" ]; then
            echo "[$(date '+%H:%M:%S')] 检测到新消息待推送" >> "$SENT_FILE"
            # 清空文件
            > "$PENDING_FILE"
        fi
    fi
    sleep 10
done
