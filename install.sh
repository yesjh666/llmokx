#!/bin/bash
# ================================================================
#  LLMOKX 交易工具 - Ubuntu 一键安装脚本（带组件自检）
#  自动检测系统环境，缺失组件自动安装
# ================================================================
set -uo pipefail

# ==================== 全局变量 ====================
APP_NAME="LLMOKX"
APP_DISPLAY="🦞 LLMOKX 交易工具"
INSTALL_DIR="/opt/llmokx"
SERVICE_NAME="llmokx"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
VENV_DIR="${INSTALL_DIR}/venv"
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=8
PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"
REPO_URL="https://github.com/yesjh666/llmokx.git"
LOG_FILE="/tmp/llmokx_install.log"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 统计
WARN_COUNT=0
ERROR_COUNT=0
INSTALL_COUNT=0

# ==================== 工具函数 ====================

log_info()    { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[!]${NC} $1"; ((WARN_COUNT++)); }
log_error()   { echo -e "${RED}[✗]${NC} $1"; ((ERROR_COUNT++)); }
log_step()    { echo -e "\n${BLUE}▶ $1${NC}"; }
log_install() { echo -e "${CYAN}[安装]${NC} $1"; ((INSTALL_COUNT++)); }
log_skip()    { echo -e "${GREEN}[跳过]${NC} $1 已安装"; }

print_banner() {
    echo -e "${CYAN}"
    echo "  ╔═══════════════════════════════════════════════╗"
    echo "  ║                                               ║"
    echo "  ║         ${APP_DISPLAY}                 ║"
    echo "  ║           一键安装脚本 (带自检)                ║"
    echo "  ║                                               ║"
    echo "  ╚═══════════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_summary() {
    echo -e "\n${BLUE}═══════════════════════════════════════════════${NC}"
    echo -e "  ${GREEN}安装完成汇总${NC}"
    echo -e "  ${BLUE}═══════════════════════════════════════════════${NC}"
    echo -e "  自动安装组件:  ${CYAN}${INSTALL_COUNT}${NC} 个"
    echo -e "  警告:          ${YELLOW}${WARN_COUNT}${NC} 个"
    echo -e "  错误:          ${RED}${ERROR_COUNT}${NC} 个"
    echo -e "  ${BLUE}═══════════════════════════════════════════════${NC}"
}

# 检查命令是否存在
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# 检查root权限
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_warn "非root用户运行，尝试使用sudo..."
        if ! command_exists sudo; then
            log_error "需要root权限或sudo，请使用 root 用户或配置 sudo 后重试"
            exit 1
        fi
        SUDO="sudo"
    else
        SUDO=""
    fi
}

# 获取脚本所在目录
get_script_dir() {
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
}

# ==================== 系统环境自检 ====================

check_system() {
    log_step "步骤 1/8: 系统环境自检"

    # 检测操作系统
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        OS_ID="${ID:-unknown}"
        OS_VERSION="${VERSION_ID:-unknown}"
        OS_NAME="${PRETTY_NAME:-unknown}"
        log_info "操作系统: ${OS_NAME}"
    else
        OS_ID="unknown"
        OS_VERSION="unknown"
        log_warn "无法检测操作系统版本（非标准Linux发行版）"
    fi

    # 判断包管理器
    PKG_MGR=""
    if command_exists apt-get; then
        PKG_MGR="apt"
    elif command_exists yum; then
        PKG_MGR="yum"
    elif command_exists dnf; then
        PKG_MGR="dnf"
    elif command_exists pacman; then
        PKG_MGR="pacman"
    else
        log_error "不支持的包管理器（需要 apt/yum/dnf/pacman）"
        exit 1
    fi
    log_info "包管理器: ${PKG_MGR}"

    # 检测架构
    ARCH="$(uname -m)"
    log_info "系统架构: ${ARCH}"

    # 检测内存
    if command_exists free; then
        MEM_MB="$(free -m | awk '/^Mem:/{print $2}')"
        if [[ "$MEM_MB" -lt 256 ]]; then
            log_warn "内存较小 (${MEM_MB}MB)，可能影响运行性能"
        else
            log_info "内存: ${MEM_MB}MB"
        fi
    fi

    # 检测磁盘空间
    DISK_AVAIL="$(df -m "${INSTALL_DIR:-/opt}" 2>/dev/null | awk 'NR==2{print $4}')"
    if [[ -n "${DISK_AVAIL:-}" ]]; then
        if [[ "$DISK_AVAIL" -lt 500 ]]; then
            log_warn "可用磁盘空间较小 (${DISK_AVAIL}MB)，建议至少500MB"
        else
            log_info "可用磁盘: ${DISK_AVAIL}MB"
        fi
    fi
}

# 包管理器安装封装
pkg_install() {
    local pkgs=("$@")
    case "$PKG_MGR" in
        apt)
            $SUDO apt-get update -qq 2>/dev/null
            $SUDO apt-get install -y -qq "${pkgs[@]}" 2>&1 | tail -3
            ;;
        yum)
            $SUDO yum install -y -q "${pkgs[@]}" 2>&1 | tail -3
            ;;
        dnf)
            $SUDO dnf install -y -q "${pkgs[@]}" 2>&1 | tail -3
            ;;
        pacman)
            $SUDO pacman -S --noconfirm --needed "${pkgs[@]}" 2>&1 | tail -3
            ;;
    esac
}

# ==================== Python 环境自检 ====================

check_python() {
    log_step "步骤 2/8: Python 环境自检"

    PYTHON_BIN=""

    # 按优先级检测可用的Python
    for candidate in python3.11 python3.10 python3.9 python3.8 python3; do
        if command_exists "$candidate"; then
            PYTHON_BIN="$candidate"
            break
        fi
    done

    if [[ -z "$PYTHON_BIN" ]]; then
        log_warn "未检测到 Python3，开始自动安装..."
        log_install "python3 python3-pip python3-venv python3-dev"
        case "$PKG_MGR" in
            apt)
                pkg_install software-properties-common
                # 尝试添加 deadsnakes PPA (Ubuntu) 获取较新Python
                if [[ "$OS_ID" == "ubuntu" ]]; then
                    $SUDO add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
                    $SUDO apt-get update -qq 2>/dev/null
                    $SUDO apt-get install -y -qq python3.11 python3.11-venv python3.11-dev 2>/dev/null && PYTHON_BIN="python3.11" || true
                fi
                # 退回默认python3
                if [[ -z "$PYTHON_BIN" ]]; then
                    pkg_install python3 python3-pip python3-venv python3-dev
                    PYTHON_BIN="python3"
                fi
                ;;
            yum|dnf)
                pkg_install python3 python3-pip python3-devel
                PYTHON_BIN="python3"
                ;;
            pacman)
                pkg_install python python-pip
                PYTHON_BIN="python3"
                ;;
        esac
    fi

    if ! command_exists "$PYTHON_BIN"; then
        log_error "Python 安装失败"
        exit 1
    fi

    # 检查Python版本
    PY_VERSION_FULL="$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")' 2>/dev/null)"
    PY_MAJOR="$($PYTHON_BIN -c 'import sys; print(sys.version_info.major)' 2>/dev/null)"
    PY_MINOR="$($PYTHON_BIN -c 'import sys; print(sys.version_info.minor)' 2>/dev/null)"

    log_info "Python 版本: ${PY_VERSION_FULL} ($PYTHON_BIN)"

    # 版本比较
    if [[ "$PY_MAJOR" -lt "$PYTHON_MIN_MAJOR" ]] || \
       [[ "$PY_MAJOR" -eq "$PYTHON_MIN_MAJOR" && "$PY_MINOR" -lt "$PYTHON_MIN_MINOR" ]]; then
        log_error "Python 版本过低，需要 ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+，当前 ${PY_MAJOR}.${PY_MINOR}"
        log_warn "尝试通过包管理器升级Python..."

        if [[ "$OS_ID" == "ubuntu" && "$PKG_MGR" == "apt" ]]; then
            $SUDO add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
            $SUDO apt-get update -qq
            $SUDO apt-get install -y -qq python3.11 python3.11-venv python3.11-dev 2>/dev/null
            if command_exists python3.11; then
                PYTHON_BIN="python3.11"
                log_info "已升级到 Python 3.11"
            else
                log_error "Python升级失败，请手动安装 Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+"
                exit 1
            fi
        else
            log_error "无法自动升级Python，请手动安装 Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+"
            exit 1
        fi
    else
        log_skip "Python ${PY_MAJOR}.${PY_MINOR}"
    fi

    # 检查 pip
    if ! $PYTHON_BIN -m pip --version >/dev/null 2>&1; then
        log_warn "pip 未安装，开始自动安装..."
        log_install "pip"
        if [[ "$PKG_MGR" == "apt" ]]; then
            pkg_install python3-pip
        else
            pkg_install python3-pip pip
        fi
        if $PYTHON_BIN -m pip --version >/dev/null 2>&1; then
            log_info "pip 安装成功"
        else
            # 尝试 ensurepip
            $SUDO $PYTHON_BIN -m ensurepip --upgrade 2>/dev/null || true
            if ! $PYTHON_BIN -m pip --version >/dev/null 2>&1; then
                log_error "pip 安装失败"
                exit 1
            fi
        fi
    else
        log_skip "pip"
    fi

    # 检查 venv 模块（含 ensurepip）
    local venv_ok=true
    if ! $PYTHON_BIN -c "import venv" >/dev/null 2>&1; then
        venv_ok=false
    elif ! $PYTHON_BIN -c "import ensurepip" >/dev/null 2>&1; then
        venv_ok=false
    elif ! $PYTHON_BIN -m venv --help >/dev/null 2>&1; then
        venv_ok=false
    fi

    if [[ "$venv_ok" == false ]]; then
        log_warn "venv/ensurepip 模块不完整，开始自动安装..."
        log_install "python${PY_MAJOR}.${PY_MINOR}-venv"
        if [[ "$PKG_MGR" == "apt" ]]; then
            pkg_install "python${PY_MAJOR}.${PY_MINOR}-venv" python3-venv
        else
            pkg_install python3-venv
        fi
        # 验证 ensurepip 可用
        if $PYTHON_BIN -c "import ensurepip" >/dev/null 2>&1; then
            log_info "venv 模块安装成功"
        else
            log_error "venv 模块安装失败，请手动运行: apt install python${PY_MAJOR}.${PY_MINOR}-venv"
            exit 1
        fi
    else
        log_skip "venv 模块"
    fi
}

# ==================== 系统依赖自检 ====================

check_system_deps() {
    log_step "步骤 3/8: 系统依赖自检"

    # 构建依赖列表（按需检测）
    local missing_pkgs=()

    # gcc（编译C扩展）
    if ! command_exists gcc; then
        missing_pkgs+=("gcc" "build-essential")
        log_warn "gcc 未安装"
    else
        log_skip "gcc"
    fi

    # make
    if ! command_exists make; then
        missing_pkgs+=("make")
        log_warn "make 未安装"
    else
        log_skip "make"
    fi

    # ssl头文件（Python ssl模块编译需要）
    if [[ ! -f /usr/include/openssl/ssl.h ]] && [[ ! -f /usr/local/include/openssl/ssl.h ]]; then
        if [[ "$PKG_MGR" == "apt" ]]; then
            missing_pkgs+=("libssl-dev")
        elif [[ "$PKG_MGR" == "yum" || "$PKG_MGR" == "dnf" ]]; then
            missing_pkgs+=("openssl-devel")
        fi
        log_warn "OpenSSL 头文件未找到"
    else
        log_skip "OpenSSL 头文件"
    fi

    # ffi头文件（Python cffi编译需要）
    if [[ ! -f /usr/include/ffi.h ]] && [[ ! -f /usr/local/include/ffi.h ]]; then
        if [[ "$PKG_MGR" == "apt" ]]; then
            missing_pkgs+=("libffi-dev")
        elif [[ "$PKG_MGR" == "yum" || "$PKG_MGR" == "dnf" ]]; then
            missing_pkgs+=("libffi-devel")
        fi
        log_warn "libffi 头文件未找到"
    else
        log_skip "libffi 头文件"
    fi

    # zlib开发库
    if [[ "$PKG_MGR" == "apt" ]]; then
        if ! dpkg -s zlib1g-dev >/dev/null 2>&1; then
            missing_pkgs+=("zlib1g-dev")
            log_warn "zlib 开发库未找到"
        else
            log_skip "zlib 开发库"
        fi
    fi

    # curl（用于健康检查）
    if ! command_exists curl; then
        missing_pkgs+=("curl")
        log_warn "curl 未安装"
    else
        log_skip "curl"
    fi

    # 批量安装缺失的系统依赖
    if [[ ${#missing_pkgs[@]} -gt 0 ]]; then
        # 去重
        local unique_pkgs=()
        for pkg in "${missing_pkgs[@]}"; do
            if [[ ! " ${unique_pkgs[*]} " =~ " ${pkg} " ]]; then
                unique_pkgs+=("$pkg")
            fi
        done

        log_install "系统依赖: ${unique_pkgs[*]}"
        pkg_install "${unique_pkgs[@]}"

        # 验证安装
        local still_missing=()
        for pkg in "${unique_pkgs[@]}"; do
            case "$pkg" in
                gcc|make|curl) command_exists "$pkg" || still_missing+=("$pkg") ;;
            esac
        done

        if [[ ${#still_missing[@]} -gt 0 ]]; then
            log_warn "部分包可能未安装成功: ${still_missing[*]}"
        else
            log_info "系统依赖全部安装完成"
        fi
    else
        log_info "所有系统依赖已就绪"
    fi
}

# ==================== 网络连接自检 ====================

check_network() {
    log_step "步骤 4/8: 网络连接自检"

    # 检测 PyPI 连接
    local pypi_ok=false
    local pypi_urls=("https://pypi.org/simple/" "https://pypi.tuna.tsinghua.edu.cn/simple/" "https://mirrors.aliyun.com/pypi/simple/")

    for url in "${pypi_urls[@]}"; do
        if curl -s --connect-timeout 5 --max-time 10 -o /dev/null -w "%{http_code}" "$url" 2>/dev/null | grep -q "200\|301\|302"; then
            PIP_INDEX_URL="$url"
            pypi_ok=true
            log_info "PyPI 连接正常: $url"
            break
        fi
    done

    if [[ "$pypi_ok" == false ]]; then
        log_warn "无法连接到 PyPI，尝试使用默认源..."
        PIP_INDEX_URL=""
        # 网络不通但已有依赖也能继续
    fi

    # 检测 GitHub 连接（用于克隆，如果有需要）
    if curl -s --connect-timeout 5 --max-time 10 -o /dev/null "https://github.com" 2>/dev/null; then
        log_skip "GitHub 连接"
    else
        log_warn "GitHub 连接超时（不影响本地安装）"
    fi
}

# ==================== 文件部署 ====================

deploy_files() {
    log_step "步骤 5/8: 部署文件"

    get_script_dir

    # 如果本地没有 app/ 目录，自动从 GitHub 克隆
    if [[ ! -d "$SCRIPT_DIR/app" ]]; then
        if [[ -n "$REPO_URL" ]]; then
            log_warn "本地未找到 app/ 目录，尝试从 GitHub 克隆..."
            CLONE_DIR="/tmp/llmokx_clone_$$"
            rm -rf "$CLONE_DIR"

            # 确保 git 可用
            if ! command_exists git; then
                log_install "git"
                pkg_install git
            fi

            log_info "正在克隆 $REPO_URL ..."
            if ! git clone --depth 1 "$REPO_URL" "$CLONE_DIR" 2>&1; then
                log_error "克隆失败，请检查网络连接"
                log_info "可手动克隆后在项目目录运行: bash install.sh"
                exit 1
            fi
            log_info "克隆完成"
            SCRIPT_DIR="$CLONE_DIR"
        else
            log_error "未找到应用目录 app/，请确保在项目根目录运行此脚本"
            log_info "当前目录: $SCRIPT_DIR"
            log_info "应该包含: app/ config/ run.py requirements.txt"
            exit 1
        fi
    fi

    # 创建安装目录
    $SUDO mkdir -p "$INSTALL_DIR"

    # 停止旧服务（如果在运行）
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        log_info "停止正在运行的旧服务..."
        $SUDO systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    fi

    # 复制文件
    log_info "复制应用文件到 $INSTALL_DIR ..."
    $SUDO cp -r "$SCRIPT_DIR/app" "$INSTALL_DIR/"
    $SUDO cp -r "$SCRIPT_DIR/config" "$INSTALL_DIR/"

    # 配置文件：如已存在不覆盖（保留用户配置），不存在则从 example 创建
    for cfg_pair in "unified-config.example.json:unified-config.json" "telegram_userbot.example.json:telegram_userbot.json"; do
        src_file="${cfg_pair%%:*}"
        dst_file="${cfg_pair##*:}"
        if [[ ! -f "$INSTALL_DIR/config/$dst_file" ]] && [[ -f "$INSTALL_DIR/config/$src_file" ]]; then
            $SUDO cp "$INSTALL_DIR/config/$src_file" "$INSTALL_DIR/config/$dst_file"
        fi
    done

    # prompts.json 直接复制（不包含敏感信息）
    if [[ ! -f "$INSTALL_DIR/config/prompts.json" ]] && [[ -f "$SCRIPT_DIR/config/prompts.json" ]]; then
        $SUDO cp "$SCRIPT_DIR/config/prompts.json" "$INSTALL_DIR/config/"
    fi

    $SUDO cp "$SCRIPT_DIR/run.py" "$INSTALL_DIR/"
    $SUDO cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
    $SUDO cp "$SCRIPT_DIR/version.txt" "$INSTALL_DIR/" 2>/dev/null || true

    # 创建数据/日志目录
    $SUDO mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/logs"

    # 设置权限
    $SUDO chmod -R 755 "$INSTALL_DIR"

    # 初始化 git 仓库（用于后续升级）
    if [[ ! -d "$INSTALL_DIR/.git" ]]; then
        log_info "初始化 git 仓库..."
        cd "$INSTALL_DIR"
        $SUDO git init -b main -q 2>/dev/null || $SUDO git init -q
        $SUDO git remote add origin "$REPO_URL" 2>/dev/null || true
        $SUDO git fetch origin -q 2>/dev/null || true
        $SUDO git checkout -b main origin/main 2>/dev/null || $SUDO git checkout main 2>/dev/null || true
        $SUDO git branch --set-upstream-to=origin/main main 2>/dev/null || true
        cd - >/dev/null
    fi

    log_info "文件部署完成"
}

# ==================== Python虚拟环境 + 依赖自检 ====================

setup_python_env() {
    log_step "步骤 6/8: Python 虚拟环境与依赖自检"

    PYTHON_BIN_PATH="$(command -v $PYTHON_BIN)"

    # 创建虚拟环境
    if [[ ! -d "$VENV_DIR" ]]; then
        log_install "创建虚拟环境: $VENV_DIR"
        if ! $SUDO $PYTHON_BIN -m venv "$VENV_DIR" 2>&1; then
            log_error "venv 创建失败，尝试安装 python${PY_MAJOR}.${PY_MINOR}-venv 后重试..."
            $SUDO apt-get install -y -qq "python${PY_MAJOR}.${PY_MINOR}-venv" 2>&1 | tail -3
            if ! $SUDO $PYTHON_BIN -m venv "$VENV_DIR" 2>&1; then
                log_error "venv 创建失败，请手动运行: apt install python${PY_MAJOR}.${PY_MINOR}-venv"
                exit 1
            fi
        fi
    else
        log_skip "虚拟环境已存在"
    fi

    VENV_PYTHON="$VENV_DIR/bin/python"
    VENV_PIP="$VENV_DIR/bin/pip"

    # 确保 venv 中有 pip
    if [[ ! -f "$VENV_PIP" ]]; then
        log_warn "venv 中 pip 不存在，尝试通过 ensurepip 安装..."
        $SUDO $VENV_PYTHON -m ensurepip --upgrade 2>/dev/null || true
    fi
    if [[ ! -f "$VENV_PIP" ]]; then
        log_warn "ensurepip 失败，尝试通过 get-pip.py 安装..."
        curl -sS https://bootstrap.pypa.io/get-pip.py | $SUDO $VENV_PYTHON 2>/dev/null || true
    fi
    if [[ ! -f "$VENV_PIP" ]]; then
        log_error "pip 安装失败，请手动安装: sudo apt install python3-pip"
        exit 1
    fi

    # 激活虚拟环境
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"

    # 升级 pip
    log_info "升级 pip..."
    if [[ -n "$PIP_INDEX_URL" ]]; then
        $VENV_PIP install --upgrade pip -i "$PIP_INDEX_URL" --quiet 2>&1 | tail -2
    else
        $VENV_PIP install --upgrade pip --quiet 2>&1 | tail -2
    fi

    # 读取 requirements.txt 并逐个检查
    local req_file="$INSTALL_DIR/requirements.txt"
    local missing_pkgs=()

    if [[ -f "$req_file" ]]; then
        log_info "检查 Python 依赖..."

        while IFS= read -r line || [[ -n "$line" ]]; do
            # 跳过空行和注释
            line="$(echo "$line" | sed 's/#.*//' | sed 's/[[:space:]]*$//' | sed 's/^[[:space:]]*//')"
            [[ -z "$line" ]] && continue

            # 提取包名（去掉版本约束）
            pkg_name="$(echo "$line" | sed 's/[>=<~!].*//' | sed 's/\[.*\]//' | sed 's/[[:space:]]*$//')"

            # 检查是否已安装
            if $VENV_PYTHON -c "import ${pkg_name//-/_}" >/dev/null 2>&1; then
                : # 已安装
            elif $VENV_PYTHON -c "import pkg_resources; pkg_resources.require('${pkg_name}')" >/dev/null 2>&1; then
                : # 已安装
            else
                missing_pkgs+=("$line")
            fi
        done < "$req_file"

        if [[ ${#missing_pkgs[@]} -gt 0 ]]; then
            log_install "Python 依赖: ${#missing_pkgs[@]} 个包需要安装"
            for pkg in "${missing_pkgs[@]}"; do
                echo -e "  ${CYAN}→${NC} $pkg"
            done

            if [[ -n "$PIP_INDEX_URL" ]]; then
                $VENV_PIP install -r "$req_file" -i "$PIP_INDEX_URL" --quiet 2>&1 | tail -5
            else
                $VENV_PIP install -r "$req_file" --quiet 2>&1 | tail -5
            fi
        else
            log_skip "所有 Python 依赖"
        fi
    fi

    # 验证关键包
    log_info "验证关键依赖..."
    local critical_pkgs=("fastapi" "uvicorn" "httpx" "pydantic")
    for pkg in "${critical_pkgs[@]}"; do
        if $VENV_PYTHON -c "import ${pkg}" >/dev/null 2>&1; then
            log_skip "$pkg"
        else
            log_error "$pkg 导入失败，尝试重新安装..."
            if [[ -n "$PIP_INDEX_URL" ]]; then
                $VENV_PIP install "$pkg" -i "$PIP_INDEX_URL" --quiet 2>&1 | tail -2
            else
                $VENV_PIP install "$pkg" --quiet 2>&1 | tail -2
            fi
            if $VENV_PYTHON -c "import ${pkg}" >/dev/null 2>&1; then
                log_info "$pkg 安装成功"
            else
                log_error "$pkg 安装失败"
                exit 1
            fi
        fi
    done

    # 退出虚拟环境
    deactivate 2>/dev/null || true
}

# ==================== 配置文件自检 ====================

check_config_files() {
    log_step "步骤 7/8: 配置文件自检"

    local configs=(
        "$INSTALL_DIR/config/unified-config.json"
        "$INSTALL_DIR/config/prompts.json"
    )

    for cfg_file in "${configs[@]}"; do
        if [[ ! -f "$cfg_file" ]]; then
            log_error "配置文件缺失: $cfg_file"
            exit 1
        fi

        # 验证JSON格式
        if ! $VENV_PYTHON -c "import json; json.load(open('$cfg_file'))" >/dev/null 2>&1; then
            log_error "配置文件JSON格式错误: $cfg_file"
            exit 1
        fi
        log_skip "$(basename $cfg_file)"
    done

    # 检查配置目录权限
    $SUDO chmod -R 755 "$INSTALL_DIR/config"

    log_info "配置文件验证通过"
}

# ==================== 端口冲突预检 ====================

check_port_conflict() {
    log_step "检查端口冲突..."

    local check_port="${PORT:-8080}"

    # 检测端口是否被占用
    if command_exists ss; then
        if ss -tlnp 2>/dev/null | grep -q ":${check_port} " ; then
            local occupant
            occupant="$(ss -tlnp 2>/dev/null | grep ":${check_port} " | head -1)"
            log_warn "端口 ${check_port} 已被占用:"
            echo -e "    ${YELLOW}${occupant}${NC}"

            # 询问是否继续使用其他端口
            echo ""
            echo -e "  ${YELLOW}选择操作:${NC}"
            echo "    1. 自动寻找可用端口 (从 ${check_port} 开始向上查找)"
            echo "    2. 手动输入端口号"
            echo "    3. 退出安装，稍后处理"
            read -r -p "请选择 [1/2/3] (默认1): " choice

            case "${choice:-1}" in
                1)
                    # 自动寻找可用端口
                    local new_port="$check_port"
                    for offset in $(seq 1 20); do
                        local test_port=$((check_port + offset))
                        if ! ss -tlnp 2>/dev/null | grep -q ":${test_port} " ; then
                            new_port="$test_port"
                            break
                        fi
                    done

                    if [[ "$new_port" == "$check_port" ]]; then
                        log_error "从 ${check_port} 开始连续 20 个端口都被占用"
                        log_info "请手动指定端口: PORT=端口号 ./install.sh"
                        exit 1
                    fi

                    PORT="$new_port"
                    log_info "已自动选择可用端口: ${new_port}"
                    ;;
                2)
                    read -r -p "请输入端口号: " custom_port
                    if [[ -z "$custom_port" ]] || ! [[ "$custom_port" =~ ^[0-9]+$ ]]; then
                        log_error "无效的端口号"
                        exit 1
                    fi
                    if ss -tlnp 2>/dev/null | grep -q ":${custom_port} " ; then
                        log_error "端口 ${custom_port} 也被占用"
                        exit 1
                    fi
                    PORT="$custom_port"
                    log_info "将使用端口: ${PORT}"
                    ;;
                3)
                    log_info "安装已取消"
                    log_info "解决后可重新运行: PORT=可用端口 ./install.sh"
                    exit 0
                    ;;
            esac
        else
            log_skip "端口 ${check_port} 可用"
        fi
    elif command_exists netstat; then
        if netstat -tlnp 2>/dev/null | grep -q ":${check_port} " ; then
            log_warn "端口 ${check_port} 已被占用"
            log_info "可通过 PORT=其他端口 ./install.sh 指定其他端口"
            read -r -p "是否自动寻找可用端口？[Y/n]: " auto_find
            if [[ "${auto_find:-Y}" =~ ^[Yy]$ ]]; then
                for offset in $(seq 1 20); do
                    local test_port=$((check_port + offset))
                    if ! netstat -tlnp 2>/dev/null | grep -q ":${test_port} " ; then
                        PORT="$test_port"
                        log_info "已自动选择可用端口: ${PORT}"
                        return
                    fi
                done
                log_error "未找到可用端口，请手动指定: PORT=端口号 ./install.sh"
                exit 1
            else
                exit 1
            fi
        else
            log_skip "端口 ${check_port} 可用"
        fi
    else
        log_warn "无法检测端口占用（ss 和 netstat 都不可用）"
        log_info "如果启动时报端口占用错误，请用 PORT=其他端口 ./install.sh 重新安装"
    fi

    # 将端口写入环境变量，供 service 文件使用
    export PORT
}

# ==================== 系统服务配置 ====================

setup_service() {
    log_step "步骤 8/8: 配置系统服务"

    # 写入 systemd 服务文件
    log_info "创建 systemd 服务..."
    $SUDO tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=${APP_NAME} Trading Tool
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=${VENV_DIR}/bin/python ${INSTALL_DIR}/run.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1
Environment=HOST=${HOST}
Environment=PORT=${PORT}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    # 重载 systemd
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable "$SERVICE_NAME"
    log_info "系统服务已注册并设为开机自启"

    # 启动服务
    log_info "启动服务..."
    $SUDO systemctl start "$SERVICE_NAME"

    # 等待启动
    sleep 3

    # 验证服务状态
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_info "服务已成功启动"
    else
        log_error "服务启动失败"
        log_warn "查看详细日志: sudo journalctl -u $SERVICE_NAME -n 50 --no-pager"
        exit 1
    fi

    # 健康检查
    log_info "健康检查..."
    local health_ok=false
    for i in $(seq 1 10); do
        if curl -s --connect-timeout 3 "http://127.0.0.1:${PORT}/api/health" 2>/dev/null | grep -q "ok"; then
            health_ok=true
            break
        fi
        sleep 1
    done

    if [[ "$health_ok" == true ]]; then
        log_info "健康检查通过 ✓"
    else
        log_warn "健康检查超时（服务可能仍在启动中，请稍后访问）"
        log_warn "手动检查: curl http://127.0.0.1:${PORT}/api/health"
    fi
}

# ==================== 主流程 ====================

main() {
    print_banner

    echo -e "${CYAN}开始安装...${NC}\n"

    check_root
    check_system
    check_python
    check_system_deps
    check_network
    deploy_files
    setup_python_env
    check_config_files
    check_port_conflict
    setup_service

    print_summary

    # 清理临时克隆目录
    if [[ -d "${CLONE_DIR:-}" ]]; then
        rm -rf "$CLONE_DIR"
    fi

    # 获取服务器IP
    SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    [[ -z "$SERVER_IP" ]] && SERVER_IP="localhost"

    echo -e "\n${GREEN}╔═══════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║          🎉 安装成功！欢迎使用！               ║${NC}"
    echo -e "${GREEN}╠═══════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║                                               ║${NC}"
    echo -e "${GREEN}║  访问地址:  ${CYAN}http://${SERVER_IP}:${PORT}${NC}            ${GREEN}║${NC}"
    echo -e "${GREEN}║  API文档:   ${CYAN}http://${SERVER_IP}:${PORT}/docs${NC}       ${GREEN}║${NC}"
    echo -e "${GREEN}║                                               ║${NC}"
    echo -e "${GREEN}╠═══════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║  常用命令:                                     ║${NC}"
    echo -e "${GREEN}║    启动:  ${CYAN}systemctl start ${SERVICE_NAME}${NC}      ${GREEN}║${NC}"
    echo -e "${GREEN}║    停止:  ${CYAN}systemctl stop ${SERVICE_NAME}${NC}       ${GREEN}║${NC}"
    echo -e "${GREEN}║    重启:  ${CYAN}systemctl restart ${SERVICE_NAME}${NC}    ${GREEN}║${NC}"
    echo -e "${GREEN}║    状态:  ${CYAN}systemctl status ${SERVICE_NAME}${NC}     ${GREEN}║${NC}"
    echo -e "${GREEN}║    日志:  ${CYAN}journalctl -u ${SERVICE_NAME} -f${NC}     ${GREEN}║${NC}"
    echo -e "${GREEN}║    卸载:  ${CYAN}systemctl stop ${SERVICE_NAME} && \\${NC}  ${GREEN}║${NC}"
    echo -e "${GREEN}║           ${CYAN}systemctl disable ${SERVICE_NAME} && \\${NC}${GREEN}║${NC}"
    echo -e "${GREEN}║           ${CYAN}rm -rf ${INSTALL_DIR} ${SERVICE_FILE}${NC}   ${GREEN}║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════╝${NC}"

    echo -e "\n${YELLOW}提示: 首次使用请访问Web界面配置 LLM API Key${NC}"
    echo ""
}

# 执行主函数
main "$@"
