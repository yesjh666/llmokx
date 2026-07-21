#!/usr/bin/env python3
"""
LLMOKX 交易工具 - 启动脚本
- 自动检测端口冲突，如果默认端口被占用则自动寻找可用端口
- 支持通过环境变量 HOST/PORT 覆盖配置
- 支持通过 --port/--host 命令行参数指定
"""
import os
import sys
import socket
import json
import uvicorn


def is_port_available(host: str, port: int) -> bool:
    """检查指定端口是否可用"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return True
    except (OSError, socket.error):
        return False


def find_available_port(host: str, start_port: int, max_attempts: int = 20) -> int:
    """
    从 start_port 开始向上寻找可用端口

    Args:
        host: 绑定地址
        start_port: 起始端口
        max_attempts: 最多尝试次数

    Returns:
        int: 可用的端口号，如果都不可用返回 -1
    """
    for offset in range(max_attempts):
        port = start_port + offset
        if is_port_available(host, port):
            return port
    return -1


def get_port_occupant(port: int) -> str:
    """获取占用指定端口的进程信息（Linux/macOS）"""
    import subprocess

    # 尝试 lsof (Linux/macOS 通用)
    try:
        result = subprocess.run(
            ["lsof", "-i", f":{port}", "-sTCP:LISTEN", "-n", "-P"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().split("\n")
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 2:
                    return f"PID={parts[1]} CMD={parts[0]}"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 尝试 ss (Linux)
    try:
        result = subprocess.run(
            ["ss", "-tlnp", f"sport = :{port}"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().split("\n")
            if len(lines) > 1:
                return lines[1].strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 尝试 netstat (Windows/Linux)
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=3,
        )
        for line in result.stdout.split("\n"):
            if f":{port}" in line and "LISTEN" in line.upper():
                return line.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return "未知"


def print_banner(host: str, port: int, auto_switched: bool = False):
    """打印启动横幅"""
    print("=" * 55)
    print("  🦞 LLMOKX 交易工具 启动中...")
    print(f"  访问地址: http://{host}:{port}")
    print(f"  API文档:  http://{host}:{port}/docs")

    if auto_switched:
        print(f"  ⚠️  默认端口被占用，已自动切换到 {port}")

    print("=" * 55)


def parse_args():
    """解析命令行参数"""
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    reload_flag = False
    no_auto_switch = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--host", "-H"):
            i += 1
            if i < len(args):
                host = args[i]
        elif arg in ("--port", "-p"):
            i += 1
            if i < len(args):
                port = int(args[i])
        elif arg == "--reload":
            reload_flag = True
        elif arg == "--no-auto-switch":
            no_auto_switch = True
        elif arg == "--help":
            print("用法: python run.py [选项]")
            print("")
            print("选项:")
            print("  --host, -H <addr>     指定绑定地址 (默认: 0.0.0.0)")
            print("  --port, -p <port>     指定端口 (默认: 8080)")
            print("  --reload              开启热重载 (开发模式)")
            print("  --no-auto-switch      端口被占用时不自动切换，直接报错退出")
            print("  --help                显示帮助")
            sys.exit(0)
        i += 1

    return host, port, reload_flag, no_auto_switch


def main():
    host, port, reload_flag, no_auto_switch = parse_args()

    # 端口冲突检测
    auto_switched = False
    if not is_port_available(host, port):
        occupant = get_port_occupant(port)
        print(f"")
        print(f"  ⚠️  端口 {port} 已被占用!")
        print(f"      占用进程: {occupant}")
        print(f"")

        if no_auto_switch:
            print(f"  ❌ 已指定 --no-auto-switch，不自动切换端口，退出")
            print(f"      请使用 --port 指定其他端口，或停止占用端口的进程")
            sys.exit(1)

        # 自动寻找可用端口
        print(f"  正在自动寻找可用端口 (从 {port} 开始)...")
        new_port = find_available_port(host, port)

        if new_port == -1:
            print(f"  ❌ 从 {port} 开始连续 20 个端口都被占用，请手动指定端口:")
            print(f"      python run.py --port <可用端口>")
            sys.exit(1)

        port = new_port
        auto_switched = True
        print(f"  ✅ 已找到可用端口: {port}")

    print_banner(host, port, auto_switched)

    # 将实际端口写回环境变量，供应用内部读取
    os.environ["PORT"] = str(port)
    os.environ["HOST"] = host

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload_flag,
        log_level="info",
    )


if __name__ == "__main__":
    main()