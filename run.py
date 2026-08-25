# -*- coding: utf-8 -*-
"""摄影师端客户端入口: 启动本地服务 + 自动打开浏览器."""
import argparse
import threading
import time
import webbrowser

import app


def main():
    ap = argparse.ArgumentParser(description='像素蛋糕 · 摄影师端 (卡密激活上传工具)')
    ap.add_argument('--port', type=int, default=9699)
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()

    srv = app.start(args.port)

    if not args.no_browser:
        def open_browser():
            time.sleep(1.2)
            webbrowser.open('http://127.0.0.1:%d/' % args.port)
        threading.Thread(target=open_browser, daemon=True).start()

    print('=' * 56)
    print('  像素蛋糕 · 摄影师端已启动')
    print('  本地界面 : http://127.0.0.1:%d/' % args.port)
    print('  关闭本窗口即退出客户端')
    print('=' * 56)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == '__main__':
    main()
