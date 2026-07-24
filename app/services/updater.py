#!/usr/bin/env python3
"""
LLMOKX 自动升级服务（Linux 适配版）

源自 trade-app/app/updater.py 的设计，针对 Ubuntu + systemd + Python 项目重构：
  1. GitHub Releases 检查新版本（语义化版本比较）
  2. 下载新版本压缩包（tar.gz/zip）
  3. 备份当前程序目录到 backup/
  4. 自动解压替换（保留 config/ data/ logs/ venv/ 目录）
  5. 重启 systemd 服务（systemctl restart llmokx）

支持两种升级方式：
  - release: 从 GitHub Releases 下载压缩包（适合无Git环境）
  - git: 通过 git pull 拉取最新代码（适合装在 Git 仓库的情况）
"""
import os
import re
import sys
import json
import time
import shutil
import tarfile
import zipfile
import tempfile
import logging
import subprocess
from datetime import datetime
from typing import Dict, Any, Optional, Callable

import httpx

from app import config
from app.core.logging_config import get_logger

logger = get_logger("app")

# 项目根目录
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VERSION_FILE = os.path.join(_BASE_DIR, "version.txt")
BACKUP_DIR = os.path.join(_BASE_DIR, "backup")
GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"


def get_current_version() -> str:
    """获取当前版本号（从 version.txt 读取）"""
    try:
        if os.path.exists(VERSION_FILE):
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                return f.read().strip() or "0.0.0"
    except Exception as e:
        logger.warning(f"读取版本文件失败: {e}")
    return "0.0.0"


def set_current_version(version: str) -> bool:
    """写入当前版本号"""
    try:
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(version.strip() + "\n")
        return True
    except Exception as e:
        logger.error(f"写入版本文件失败: {e}")
        return False


def version_compare(v1: str, v2: str) -> int:
    """
    语义化版本比较
    返回: 1=v1>v2, 0=相等, -1=v1<v2
    """
    # 提取版本号数字部分（去掉 v 前缀和 -xxx 后缀）
    def parse(v):
        v = v.strip().lstrip("vV")
        # 只保留前3段数字（如 1.2.3-beta → 1.2.3）
        m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v)
        if m:
            return [int(x) for x in m.groups()]
        parts = re.findall(r"\d+", v)
        return [int(x) for x in parts] if parts else [0]

    p1 = parse(v1)
    p2 = parse(v2)
    for i in range(max(len(p1), len(p2))):
        a = p1[i] if i < len(p1) else 0
        b = p2[i] if i < len(p2) else 0
        if a > b:
            return 1
        if a < b:
            return -1
    return 0


def _load_update_config() -> dict:
    """加载升级配置"""
    return config.get_section("update") or {}


def is_update_enabled() -> bool:
    """检查自动升级功能是否启用"""
    return _load_update_config().get("enabled", True)


# ========================================
# GitHub Releases 方式
# ========================================

async def check_release_update() -> Dict[str, Any]:
    """
    检查 GitHub Releases 是否有新版本

    Returns:
        dict: {
            "has_update": bool,
            "current_version": str,
            "latest_version": str,
            "changelog": str,
            "download_url": str,
            "asset_name": str,
            "release_url": str,
            "published_at": str,
        }
        失败时返回 {"error": str}
    """
    update_cfg = _load_update_config()
    repo = (update_cfg.get("github_repo") or "").strip()

    if not repo:
        return {
            "has_update": False,
            "error": "未配置 github_repo（格式：owner/repo，如 octocat/Hello-World）",
            "current_version": get_current_version(),
        }

    current = get_current_version()
    asset_pattern = update_cfg.get("asset_pattern", "llmokx-*.tar.gz")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                GITHUB_API.format(repo=repo),
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "LLMOKX-Updater",
                },
            )

        if resp.status_code != 200:
            return {
                "has_update": False,
                "error": f"GitHub API 返回 {resp.status_code}",
                "current_version": current,
            }

        data = resp.json()
        latest = (data.get("tag_name") or "").lstrip("vV")
        has_update = version_compare(latest, current) > 0

        # 在 assets 中匹配需要的文件（按 asset_pattern 通配）
        import fnmatch
        download_url = None
        asset_name = None
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if fnmatch.fnmatch(name, asset_pattern) or name.endswith((".tar.gz", ".zip", ".tgz")):
                download_url = asset.get("browser_download_url")
                asset_name = name
                break

        return {
            "has_update": has_update,
            "current_version": current,
            "latest_version": latest,
            "changelog": data.get("body", "（无更新日志）"),
            "download_url": download_url,
            "asset_name": asset_name,
            "release_url": data.get("html_url", ""),
            "published_at": data.get("published_at", ""),
            "repo": repo,
        }
    except Exception as e:
        logger.error(f"检查更新失败: {e}")
        return {
            "has_update": False,
            "error": f"检查失败: {e}",
            "current_version": current,
        }


# ========================================
# Git 方式
# ========================================

def check_git_update() -> Dict[str, Any]:
    """
    使用 git pull 检查是否有新提交

    Returns:
        dict: {
            "has_update": bool,
            "current_commit": str,
            "remote_commit": str,
            "behind_count": int,
            "new_commits": list,
        }
    """
    try:
        # 先 fetch
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=_BASE_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # 当前 HEAD
        current = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_BASE_DIR,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()

        # 远程 HEAD
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=_BASE_DIR,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()

        remote = subprocess.run(
            ["git", "rev-parse", f"origin/{branch}"],
            cwd=_BASE_DIR,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()

        # 落后提交数
        count_proc = subprocess.run(
            ["git", "rev-list", "--count", f"{current}..{remote}"],
            cwd=_BASE_DIR,
            capture_output=True,
            text=True,
            timeout=5,
        )
        behind = int(count_proc.stdout.strip()) if count_proc.returncode == 0 else 0

        # 落后的提交信息
        new_commits = []
        if behind > 0:
            log_proc = subprocess.run(
                ["git", "log", f"{current}..{remote}", "--pretty=format:%h %s"],
                cwd=_BASE_DIR,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if log_proc.returncode == 0:
                new_commits = log_proc.stdout.strip().split("\n")[:10]

        return {
            "has_update": behind > 0,
            "current_commit": current[:8],
            "remote_commit": remote[:8],
            "behind_count": behind,
            "new_commits": new_commits,
            "branch": branch,
            "current_version": get_current_version(),
        }
    except FileNotFoundError:
        return {"has_update": False, "error": "未安装 git", "current_version": get_current_version()}
    except Exception as e:
        return {"has_update": False, "error": f"git 检查失败: {e}", "current_version": get_current_version()}


# ========================================
# 统一检查接口
# ========================================

async def check_update() -> Dict[str, Any]:
    """
    按配置的 method 选择检查方式
    method = "release" → GitHub Releases
    method = "git" → git fetch + rev-list
    """
    method = _load_update_config().get("method", "release")
    if method == "git":
        return {"method": "git", **check_git_update()}
    return {"method": "release", **await check_release_update()}


# ========================================
# 执行升级 - Release 方式
# ========================================

async def perform_release_update(
    download_url: str,
    asset_name: str,
    latest_version: str,
    progress_cb: Optional[Callable[[int], None]] = None,
) -> Dict[str, Any]:
    """
    下载新版本压缩包 + 备份 + 解压替换 + 写入版本号

    Args:
        download_url: 下载URL
        asset_name: 资源文件名
        latest_version: 新版本号
        progress_cb: 进度回调 progress_cb(percent)

    Returns:
        dict: {"success": bool, "message": str, "backup_path": str}
    """
    if not download_url:
        return {"success": False, "message": "缺少下载URL（可能是 Releases 未上传对应资源文件）"}

    preserve_dirs = _load_update_config().get("preserve_dirs", ["config", "data", "logs", "venv"])

    try:
        # 1. 下载到临时目录
        logger.info(f"开始下载新版本: {asset_name}")
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = os.path.join(tmpdir, asset_name or "llmokx-update.tar.gz")

            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream("GET", download_url) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    downloaded = 0
                    with open(archive_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(64 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_cb and total > 0:
                                progress_cb(downloaded * 100 // total)

            if total > 0:
                actual = os.path.getsize(archive_path)
                if actual != total:
                    raise Exception(f"下载不完整: {actual}/{total} 字节")
            logger.info(f"下载完成: {os.path.getsize(archive_path) // 1048576}MB")

            # 2. 备份当前版本（保留的目录除外）
            backup_name = f"backup-{get_current_version()}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            backup_path = os.path.join(BACKUP_DIR, backup_name)
            os.makedirs(BACKUP_DIR, exist_ok=True)
            os.makedirs(backup_path, exist_ok=True)

            logger.info(f"备份当前版本到: {backup_path}")
            for item in os.listdir(_BASE_DIR):
                if item in preserve_dirs or item in ("backup", "__pycache__"):
                    continue
                src = os.path.join(_BASE_DIR, item)
                dst = os.path.join(backup_path, item)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                elif os.path.isdir(src):
                    shutil.copytree(src, dst)

            # 3. 解压到临时目录
            extract_dir = os.path.join(tmpdir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)
            if asset_name and asset_name.endswith((".tar.gz", ".tgz")):
                with tarfile.open(archive_path, "r:gz") as tar:
                    tar.extractall(extract_dir)
            elif asset_name and asset_name.endswith(".zip"):
                with zipfile.ZipFile(archive_path) as zf:
                    zf.extractall(extract_dir)
            else:
                # 默认按 tar.gz 处理
                with tarfile.open(archive_path, "r:gz") as tar:
                    tar.extractall(extract_dir)

            # 找解压后的根目录（可能是嵌套一层）
            entries = os.listdir(extract_dir)
            if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
                src_root = os.path.join(extract_dir, entries[0])
            else:
                src_root = extract_dir

            # 4. 替换文件（不覆盖 preserve_dirs 中的目录）
            logger.info("替换程序文件...")
            for item in os.listdir(src_root):
                dst = os.path.join(_BASE_DIR, item)
                # 跳过保留目录
                if item in preserve_dirs:
                    continue
                # 先删除旧的
                if os.path.isfile(dst) or os.path.islink(dst):
                    os.remove(dst)
                elif os.path.isdir(dst):
                    shutil.rmtree(dst)
                # 复制新的
                src = os.path.join(src_root, item)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                elif os.path.isdir(src):
                    shutil.copytree(src, dst)

            # 5. 升级依赖（如果有新的 requirements.txt）
            req_path = os.path.join(_BASE_DIR, "requirements.txt")
            venv_pip = os.path.join(_BASE_DIR, "venv", "bin", "pip")
            if os.path.exists(req_path) and os.path.exists(venv_pip):
                logger.info("更新 Python 依赖...")
                subprocess.run(
                    [venv_pip, "install", "-r", req_path, "--quiet"],
                    timeout=300,
                )

            # 6. 写入新版本号
            set_current_version(latest_version)
            logger.info(f"升级完成: {get_current_version()} → {latest_version}")

            return {
                "success": True,
                "message": f"已升级到 {latest_version}，请重启服务使配置生效",
                "backup_path": backup_path,
                "new_version": latest_version,
                "old_version": get_current_version(),
            }
    except Exception as e:
        logger.error(f"升级失败: {e}")
        return {"success": False, "message": f"升级失败: {e}"}


# ========================================
# 执行升级 - Git 方式
# ========================================

def perform_git_update(progress_cb: Optional[Callable[[int], None]] = None) -> Dict[str, Any]:
    """
    git pull + 更新依赖 + 重启
    """
    try:
        logger.info("执行 git pull 拉取最新代码...")
        if progress_cb:
            progress_cb(10)

        result = subprocess.run(
            ["git", "pull", "origin"],
            cwd=_BASE_DIR,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if progress_cb:
            progress_cb(60)

        output = result.stdout or ""
        if result.returncode != 0:
            return {"success": False, "message": f"git pull 失败: {result.stderr}"}

        if "Already up to date" in output or "Already up-to-date" in output:
            return {"success": True, "message": "已是最新版本", "no_change": True}

        logger.info(f"git pull 输出: {output[:200]}")

        # 更新依赖
        req_path = os.path.join(_BASE_DIR, "requirements.txt")
        venv_pip = os.path.join(_BASE_DIR, "venv", "bin", "pip")
        if os.path.exists(req_path) and os.path.exists(venv_pip):
            logger.info("更新 Python 依赖...")
            subprocess.run([venv_pip, "install", "-r", req_path, "--quiet"], timeout=300)

        if progress_cb:
            progress_cb(100)

        # 从新代码读取版本号
        new_version = get_current_version()
        return {
            "success": True,
            "message": f"Git 更新完成，新版本 {new_version}，请重启服务",
            "output": output[:500],
            "new_version": new_version,
        }
    except FileNotFoundError:
        return {"success": False, "message": "未安装 git"}
    except Exception as e:
        return {"success": False, "message": f"git 更新失败: {e}"}


# ========================================
# 执行升级 - 统一入口
# ========================================

async def perform_update(progress_cb: Optional[Callable[[int], None]] = None) -> Dict[str, Any]:
    """按配置的 method 执行升级"""
    method = _load_update_config().get("method", "release")

    if method == "git":
        return {"method": "git", **perform_git_update(progress_cb)}

    # release 方式：先检查，再下载
    check_result = await check_release_update()
    if not check_result.get("has_update"):
        return {"success": False, "message": "已是最新版本"}

    return {
        "method": "release",
        **await perform_release_update(
            download_url=check_result.get("download_url", ""),
            asset_name=check_result.get("asset_name", ""),
            latest_version=check_result.get("latest_version", ""),
            progress_cb=progress_cb,
        ),
    }


# ========================================
# 重启服务
# ========================================

def restart_service(notify_message: str = "") -> Dict[str, Any]:
    """
    重启 systemd 服务（使用配置的命令）
    默认命令："systemctl restart llmokx"
    如果传了 notify_message，重启后会自动发送通知
    """
    cmd = _load_update_config().get("restart_command", "systemctl restart llmokx")
    try:
        # 重启前写 flag 文件，启动时检测并发送通知
        if notify_message:
            flag_path = os.path.join(_BASE_DIR, "data", "_restart_notify.txt")
            os.makedirs(os.path.dirname(flag_path), exist_ok=True)
            with open(flag_path, "w", encoding="utf-8") as f:
                f.write(notify_message)

        restart_script = os.path.join(_BASE_DIR, "data", "_restart.sh")
        with open(restart_script, "w") as f:
            f.write("#!/bin/bash\nsleep 1\n" + cmd + "\n")

        subprocess.Popen(
            ["bash", restart_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"success": True, "message": f"重启命令已派出: {cmd}"}
    except Exception as e:
        return {"success": False, "message": f"重启失败: {e}"}


def check_restart_notify() -> Optional[str]:
    """
    启动时检查是否有待发送的重启通知，有则读取并删除 flag 文件
    """
    flag_path = os.path.join(_BASE_DIR, "data", "_restart_notify.txt")
    try:
        if os.path.exists(flag_path):
            with open(flag_path, "r", encoding="utf-8") as f:
                msg = f.read().strip()
            os.remove(flag_path)
            return msg if msg else None
    except Exception as e:
        logger.warning(f"读取重启通知flag失败: {e}")
    return None


# ========================================
# 回滚
# ========================================

def list_backups() -> Dict[str, Any]:
    """列出所有备份"""
    backups = []
    if not os.path.exists(BACKUP_DIR):
        return {"backups": [], "total": 0}

    for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
        path = os.path.join(BACKUP_DIR, name)
        if not os.path.isdir(path):
            continue
        # 试图从目录名解析版本号 backup-旧版本-时间戳
        m = re.match(r"backup-(.+)-(\d{8}-\d{6})", name)
        version = m.group(1) if m else ""
        ts = m.group(2) if m else ""
        backups.append({
            "name": name,
            "version": version,
            "timestamp": ts,
            "size": sum(
                os.path.getsize(os.path.join(p, f))
                for p, _, files in os.walk(path) for f in files
            ),
            "path": path,
        })

    return {"backups": backups, "total": len(backups)}


def rollback(backup_name: str) -> Dict[str, Any]:
    """回滚到指定备份"""
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    if not os.path.exists(backup_path):
        return {"success": False, "message": f"备份不存在: {backup_name}"}

    preserve_dirs = _load_update_config().get("preserve_dirs", ["config", "data", "logs", "venv"])

    try:
        # 删除当前非保留文件
        for item in os.listdir(_BASE_DIR):
            if item in preserve_dirs or item in ("backup", "__pycache__"):
                continue
            path = os.path.join(_BASE_DIR, item)
            if os.path.isfile(path):
                os.remove(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)

        # 从备份恢复
        for item in os.listdir(backup_path):
            if item in preserve_dirs:
                continue
            src = os.path.join(backup_path, item)
            dst = os.path.join(_BASE_DIR, item)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
            elif os.path.isdir(src):
                shutil.copytree(src, dst)

        # 提取版本号
        m = re.match(r"backup-(.+)-\d{8}-\d{6}", backup_name)
        if m:
            set_current_version(m.group(1))

        return {
            "success": True,
            "message": f"已回滚到 {backup_name}，请重启服务",
        }
    except Exception as e:
        return {"success": False, "message": f"回滚失败: {e}"}


# ========================================
# 启动时自动检查
# ========================================

async def startup_check():
    """启动时自动检查更新（按配置执行，只检查不自动安装）"""
    cfg = _load_update_config()
    if not cfg.get("enabled", True):
        return
    if not cfg.get("check_on_startup", True):
        return

    try:
        result = await check_update()
        if result.get("has_update"):
            logger.info(
                f"📢 检测到新版本！"
                f"当前: {result.get('current_version')} → 最新: {result.get('latest_version') or result.get('remote_commit')}"
            )
            logger.info(f"   更新日志: {(result.get('changelog') or '')[:200]}")
            logger.info(f"   访问 Web 界面 '升级管理' 页面进行升级")
    except Exception as e:
        logger.warning(f"启动时检查更新失败: {e}")