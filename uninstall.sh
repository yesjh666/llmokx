#!/bin/bash
# ================================================================
#  LLMOKX 交易工具 - 卸载脚本
# ================================================================
set -uo pipefail

SERVICE_NAME="llmokx"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
INSTALL_DIR="/opt/llmokx"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
log_step()  { echo -e "${CYAN}▶ $1${NC}"; }

echo -e "${CYAN}"
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║         🦞 LLMOKX 卸载脚本                    ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo -e "${NC}\n"

# 检查root
if [[ $EUID -ne 0 ]]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        echo -e "${RED}需要root权限${NC}"
        exit 1
    fi
else
    SUDO=""
fi

# 确认
echo -e "${YELLOW}警告: 此操作将删除以下内容:${NC}"
echo "  - 系统服务: $SERVICE_NAME"
echo "  - 安装目录: $INSTALL_DIR"
echo "  - 服务配置: $SERVICE_FILE"
echo ""
read -r -p "确认卸载？(y/N): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "已取消"
    exit 0
fi

echo ""
log_step "停止服务..."
$SUDO systemctl stop "$SERVICE_NAME" 2>/dev/null && log_info "服务已停止" || log_info "服务未运行"

log_step "禁用服务..."
$SUDO systemctl disable "$SERVICE_NAME" 2>/dev/null && log_info "服务已禁用" || log_info "服务未注册"

log_step "删除服务文件..."
$SUDO rm -f "$SERVICE_FILE"
$SUDO systemctl daemon-reload
log_info "服务文件已删除"

log_step "删除安装目录..."
read -r -p "是否保留配置和数据？(y/N): " keep_config
if [[ "$keep_config" == "y" || "$keep_config" == "Y" ]]; then
    $SUDO rm -rf "$INSTALL_DIR/app" "$INSTALL_DIR/venv" "$INSTALL_DIR/run.py" "$INSTALL_DIR/requirements.txt"
    log_info "已保留 config/ data/ logs/ 目录"
else
    $SUDO rm -rf "$INSTALL_DIR"
    log_info "安装目录已完全删除"
fi

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          🗑️  卸载完成！                       ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════╝${NC}"
echo ""
