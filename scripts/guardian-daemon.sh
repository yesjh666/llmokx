#!/bin/bash
# 智能助手守护进程 - 24 小时常驻
# 开机自启，崩溃自动重启

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="/root/.openclaw/workspace"
LOG_DIR="$WORKSPACE/logs"
PID_FILE="$WORKSPACE/.guardian.pid"

# 创建日志目录
mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_DIR/guardian.log"
}

check_and_restart() {
    while true; do
        # 检查 OpenClaw 进程
        if ! pgrep -f "openclaw" > /dev/null; then
            log "⚠️  OpenClaw 进程未运行，正在重启..."
            cd "$WORKSPACE" && nohup openclaw start > "$LOG_DIR/openclaw.log" 2>&1 &
            log "✅ OpenClaw 已重启"
        fi
        
        # 检查 Gateway 进程
        if ! pgrep -f "gateway" > /dev/null; then
            log "⚠️  Gateway 进程未运行，正在重启..."
            cd "$WORKSPACE" && nohup openclaw gateway restart > "$LOG_DIR/gateway.log" 2>&1 &
            log "✅ Gateway 已重启"
        fi
        
        # 检查群消息监控进程 (交易参数提取版)
        if ! pgrep -f "telegram-monitor-trade.py" > /dev/null; then
            log "⚠️  群消息监控未运行，正在重启..."
            cd "$WORKSPACE" && nohup python3 scripts/telegram-monitor-trade.py > "$LOG_DIR/telegram-monitor.log" 2>&1 &
            log "✅ 群消息监控已重启 (交易参数提取版)"
        fi
        
        # 检查 69688 成交监控 (成交后止盈改 68888)
        if [ -f "$WORKSPACE/scripts/monitor_69688_fill.py" ] && ! pgrep -f "monitor_69688_fill.py" > /dev/null; then
            log "⚠️  69688 成交监控未运行，正在重启..."
            cd "$WORKSPACE" && nohup python3 -u scripts/monitor_69688_fill.py > "$LOG_DIR/monitor_69688.log" 2>&1 &
            log "✅ 69688 成交监控已重启"
        fi
        
        # 每 30 秒检查一次
        sleep 30
    done
}

# 启动守护进程
log "🚀 智能助手守护进程启动"
log "📍 工作目录：$WORKSPACE"
log "📝 日志目录：$LOG_DIR"

# 保存 PID
echo $$ > "$PID_FILE"

# 启动监控循环
check_and_restart
