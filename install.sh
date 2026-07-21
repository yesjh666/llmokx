#!/bin/bash
# ================================================================
#  LLMOKX 一键安装引导脚本
#  用法: bash <(curl -fsSL https://raw.githubusercontent.com/yesjh666/llmokx/main/install.sh)
# ================================================================
set -uo pipefail

REPO_URL="https://github.com/yesjh666/llmokx.git"
CLONE_DIR="/tmp/llmokx_install_$$"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║         🦞 LLMOKX 一键安装引导              ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo -e "${NC}"

# 检查 git
if ! command -v git >/dev/null 2>&1; then
    echo -e "${YELLOW}[!] git 未安装，尝试自动安装...${NC}"
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -qq && sudo apt-get install -y -qq git
    elif command -v yum >/dev/null 2>&1; then
        sudo yum install -y -q git
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y -q git
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm git
    else
        echo -e "${RED}[✗] 无法自动安装 git，请先手动安装 git${NC}"
        exit 1
    fi
fi

# 克隆仓库
echo -e "${CYAN}[↓] 正在从 GitHub 克隆 LLMOKX...${NC}"
rm -rf "$CLONE_DIR"
if ! git clone --depth 1 "$REPO_URL" "$CLONE_DIR" 2>&1; then
    echo -e "${RED}[✗] 克隆失败，请检查网络连接${NC}"
    echo -e "${YELLOW}    如果在中国大陆，可尝试使用代理或镜像${NC}"
    exit 1
fi

echo -e "${GREEN}[✓] 克隆完成${NC}"

# 运行正式安装脚本
echo -e "${CYAN}[▶] 开始执行安装脚本...${NC}"
echo ""
cd "$CLONE_DIR"
bash install.sh "$@"

# 清理临时文件
rm -rf "$CLONE_DIR"
