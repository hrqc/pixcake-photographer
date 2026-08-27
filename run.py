# -*- coding: utf-8 -*-
"""摄影师端客户端入口: 强制更新检查 -> 启动本地服务 -> 内嵌浏览器窗口.
界面为软件窗口 (pywebview 内嵌浏览器), 不再自动弹出系统浏览器."""
import argparse
import multiprocessing
import threading
import time

import app
import updater


def main():
    multiprocessing.freeze_support()
    ap = argparse.ArgumentParser(description='贺染导出选片 · 摄影师端 (卡密激活上传工具)')
    ap.add_argument('--port', type=int, default=9699)
    ap.add_argument('--no-window', action='store_true',
                    help='不打开软件窗口 (仅后台服务, 调试用)')
    args = ap.parse_args()

    # ---- 强制更新检查 (严格: 需更新则先完成更新, 更新成功前不进主界面) ----
    try:
        server_version, _release = updater.check_version()
        if updater.needs_update(server_version):
            updater.apply_and_restart()
            print('[更新] 新版已就绪, 将自动替换并重启…')
            time.sleep(2)
            return
    except updater.UpdateError as exc:
        print('[更新] %s' % exc)
        print('[更新] 客户端需要联网确认版本, 请检查网络后重新打开。')
        time.sleep(4)
        return

    # ---- 启动本地服务 (后台线程) ----
    srv = app.start(args.port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    print('=' * 56)
    print('  贺染导出选片 · 摄影师端已启动')
    print('  本地界面 : http://127.0.0.1:%d/' % args.port)
    print('  关闭本窗口即退出客户端')
    print('=' * 56)

    if args.no_window:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            srv.server_close()
        return

    # ---- 软件窗口 (内嵌浏览器, 不弹系统浏览器) ----
    import webview
    webview.create_window(
        '贺染导出选片 · 摄影师端',
        'http://127.0.0.1:%d/' % args.port,
        width=1280, height=860, min_size=(1024, 700),
    )
    webview.start()
    srv.server_close()


if __name__ == '__main__':
    main()
