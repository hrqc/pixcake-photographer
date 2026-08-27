# -*- coding: utf-8 -*-
"""客户端自更新: 启动时检查服务器最新版本, 版本不一致则强制下载并自更新.
流程: check_version() -> 需要更新 -> download() 到 <安装目录>/_update/ ->
写 apply.bat -> os.startfile(bat) -> 本进程退出. bat 等 3 秒(主进程退出释放文件锁)
后解压 zip 覆盖安装目录, 再自动启动新版 exe.
首次发布: 旧客户端没有此模块无法自更新, 需手动覆盖一次; 之后所有客户端都能云端强制更新.
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

import config

# 客户端当前版本号. 每次发版打包前改(必须与服务器发布的 version 不同才会触发更新).
# 版本号: V.主版本.次版本. 小更新改小数点后 (V.001.1), 大更新改小数点前 (V.002.0).
# 客户端与服务器版本号不同即强制更新.
CLIENT_VERSION = 'V.001.0'

# 下载 zip 的文件名 (服务器白名单固定此名)
ZIP_NAME = 'hrqc-photographer-win.zip'

# apply.bat: 等待主进程退出 -> 解压覆盖安装目录 -> 启动新版 -> 删除自身.
# %~dp0 为 _update 目录(尾带 \); 中文路径由 PowerShell 处理, bat 自身保持 ASCII.
_APPLY_BAT = '''@echo off
timeout /t 3 /nobreak >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p='%~dp0'; Expand-Archive -Force -LiteralPath ($p+'update.zip') -DestinationPath ($p+'x'); Get-ChildItem -LiteralPath ($p+'x') | Copy-Item -Destination ($p+'..') -Recurse -Force; Remove-Item -Recurse -Force ($p+'x'); Start-Process -FilePath ($p+'..\\pixcake-photographer.exe')"
del "%~f0"
del "%~dp0update.zip"
'''


class UpdateError(Exception):
    pass


def app_dir():
    """安装目录 (exe 所在目录); 源码运行时为项目目录."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _server_base():
    return (config.get('server_url') or '').strip().rstrip('/') or config.DEFAULT_SERVER


def check_version():
    """查询服务器最新版本. 返回 (server_version, release dict) 或 (None, {}).
    网络/解析失败抛 UpdateError(调用方决定严格处理)."""
    url = _server_base() + '/api/client/version'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'hrqc-updater/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            j = json.loads(resp.read().decode('utf-8'))
    except Exception as exc:
        raise UpdateError('无法连接服务器检查更新: %s' % (exc or exc))
    return j.get('version'), (j.get('release') or {})


def needs_update(server_version):
    """server_version 为空(服务器未发布)不更新; 不同则强制更新."""
    return bool(server_version) and server_version != CLIENT_VERSION


def download(release, dest_path):
    """下载发布包 zip 到 dest_path, 校验 SHA256. 返回字节数."""
    url = _server_base() + '/client/' + ZIP_NAME
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'hrqc-updater/1.0'})
        resp = urllib.request.urlopen(req, timeout=180)
    except Exception as exc:
        raise UpdateError('下载失败: %s' % (exc or exc))
    sha = hashlib.sha256()
    total = 0
    try:
        with resp, open(dest_path, 'wb') as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
                sha.update(chunk)
    except Exception as exc:
        raise UpdateError('下载中断: %s' % exc)
    expect = release.get('sha256') or ''
    if expect and sha.hexdigest() != expect:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        raise UpdateError('下载校验失败 (SHA256 不匹配), 请重试')
    return total


def apply_and_restart():
    """下载新版并安排自更新. 返回 True 表示本进程应退出 (bat 将负责重启)."""
    root = app_dir()
    upd = os.path.join(root, '_update')
    os.makedirs(upd, exist_ok=True)
    zip_path = os.path.join(upd, 'update.zip')

    print('[更新] 检查到服务器有新版本, 正在下载…')
    server_version, release = check_version()
    size = download(release, zip_path)
    print('[更新] 下载完成 %.1f MB, 校验通过, 准备自动更新并重启…' % (size / 1048576.0))

    bat = os.path.join(upd, 'apply.bat')
    with open(bat, 'w', encoding='utf-8', newline='\n') as f:
        f.write(_APPLY_BAT)

    # 启动 apply.bat 后本进程退出; bat 等待 3 秒后替换安装目录并启动新版.
    try:
        os.startfile(bat)
    except Exception:
        # 退回 subprocess 直接拉起 bat
        import subprocess
        subprocess.Popen(['cmd', '/c', bat], cwd=upd, close_fds=True)
    return True
