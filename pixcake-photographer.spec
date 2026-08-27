# -*- mode: python ; coding: utf-8 -*-
"""摄影师端客户端打包 spec (PyInstaller, Win/Mac 通用).
用法: pyinstaller pixcake-photographer.spec  (或 pyinstaller -y pixcake-photographer.spec)
"""
from PyInstaller.utils.hooks import collect_data_files, collect_all

block_cipher = None

# pywebview (内嵌浏览器窗口): 收集其资源与平台后端, 打包后 WebView 才能工作
_webview_datas, _webview_binaries, _webview_hidden = collect_all('webview')

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=_webview_binaries,
    datas=[('static', 'static')] + _webview_datas,
    hiddenimports=(_webview_hidden
                   + ['webview.platforms.winforms',
                      'webview.platforms.edgechromium',
                      'webview.platforms.mshtml']),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='pixcake-photographer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,          # 控制台窗口 = "关闭窗口即退出客户端"
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='pixcake-photographer',
)
