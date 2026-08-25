# -*- coding: utf-8 -*-
"""验证 v2: 工作区 探测/深搜定位/日志/兜底. 起临时实例(9799, 临时数据目录)."""
import json
import os
import sys
import tempfile
import threading
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = tempfile.mkdtemp(prefix='pixphoto-test-')
os.environ['PIXCAKE_PHOTOGRAPHER_DATA'] = DATA

import app  # noqa: E402
import config  # noqa: E402

PASS = []


def check(name, cond, detail=''):
    PASS.append((name, cond))
    print(('  PASS  ' if cond else '  FAIL  ') + name + (('  | ' + detail) if detail else ''))


def call(method, path, body=None):
    url = 'http://127.0.0.1:9799%s' % path
    data = None
    headers = {'Content-Type': 'application/json'}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8'))


def main():
    srv = app.start(9799)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    print('\n== 1. 自动探测 ==')
    st, j = call('GET', '/api/workspace')
    cands = j.get('candidates', [])
    check('探测到 >=1', len(cands) >= 1, str(len(cands)))
    for c in cands[:3]:
        print('   -', c)

    print('\n== 2. 手填 project 路径 ==')
    ws = 'D:/xsdg/像素蛋糕/.PixCake-qt_pro Workspace/project'
    st, j = call('POST', '/api/config', {'workspace': ws})
    check('保存 200 + 相册根', st == 200 and j.get('workspace') == ws, str(j))
    st, j = call('GET', '/api/albums')
    check('相册 32', len(j.get('albums', [])) == 32)

    print('\n== 3. 填"安装文件夹"上层 (D:/xsdg/像素蛋糕) → 深搜定位 ==')
    install = 'D:/xsdg/像素蛋糕'
    st, j = call('POST', '/api/config', {'workspace': install})
    check('自动定位到 project', st == 200 and j.get('workspace', '').endswith('project'),
          str(j.get('workspace')))
    check('返回 note', bool(j.get('note')))
    st, j = call('GET', '/api/albums')
    check('相册 32 (定位后)', len(j.get('albums', [])) == 32)

    print('\n== 4. 填更高层 D:/xsdg → 仍深搜定位 ==')
    st, j = call('POST', '/api/config', {'workspace': 'D:/xsdg'})
    check('定位到 project', st == 200 and j.get('workspace', '').endswith('project'),
          str(j.get('workspace')))

    print('\n== 5. 不存在目录 → 400 ==')
    st, j = call('POST', '/api/config', {'workspace': 'Z:/not/exist'})
    check('400 + 提示不存在', st == 400 and '不存在' in (j.get('error') or ''), str(j))

    print('\n== 6. 客户端日志已写 ==')
    logp = os.path.join(DATA, 'client.log')
    check('client.log 存在且有内容', os.path.isfile(logp) and os.path.getsize(logp) > 0)
    with open(logp, encoding='utf-8') as f:
        lines = f.readlines()
    check('日志含 探测/设置 记录', any('探测工作区' in l for l in lines)
          and any('设置工作区' in l for l in lines), '共 %d 行' % len(lines))

    print('\n== 7. tkinter 探测 ==')
    check('_HAS_TK 已检测', app._HAS_TK is not None)

    srv.shutdown()
    print('\n==== 结果: %d/%d PASS ====' % (sum(1 for _, c in PASS if c), len(PASS)))
    if not all(c for _, c in PASS):
        sys.exit(1)


if __name__ == '__main__':
    main()
