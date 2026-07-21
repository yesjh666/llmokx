#!/bin/bash
# 重启交易监控脚本 - 安全流程: stop -> kill残留 -> start
# 用法: bash restart-trade-monitor.sh

SERVICE="trade-monitor"
SCRIPT="telegram-monitor-trade"

echo "=== 重启 $SERVICE ==="

# 1. 停止systemd服务
echo "[1/3] systemctl stop $SERVICE ..."
systemctl stop "$SERVICE"
sleep 2

# 2. 检查并清理残留进程
PIDS=$(pgrep -f "$SCRIPT")
if [ -n "$PIDS" ]; then
    echo "[2/3] 发现残留进程: $PIDS，正在清理..."
    for pid in $PIDS; do
        kill "$pid" 2>/dev/null
    done
    sleep 1
    # 强制清理仍在的
    PIDS=$(pgrep -f "$SCRIPT")
    if [ -n "$PIDS" ]; then
        echo "  强制kill: $PIDS"
        for pid in $PIDS; do
            kill -9 "$pid" 2>/dev/null
        done
        sleep 1
    fi
    echo "  残留进程已清理"
else
    echo "[2/3] 无残留进程"
fi

# 3. 启动服务
echo "[3/3] systemctl start $SERVICE ..."
systemctl start "$SERVICE"
sleep 2

# 确认状态
NEW_PID=$(pgrep -f "$SCRIPT")
if [ -n "$NEW_PID" ]; then
    echo "✅ 重启成功 PID: $NEW_PID"
    tail -3 /root/.openclaw/workspace/logs/trade-monitor.log
else
    echo "❌ 启动失败，检查日志:"
    tail -10 /root/.openclaw/workspace/logs/trade-monitor.log
fi
