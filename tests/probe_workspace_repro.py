# -*- coding: utf-8 -*-
"""验证: 摄影师端工作区 探测/自动定位/锁 修复后行为. 起临时实例(9799, 临时数据目录)."""
import json
import os
import sys
import tempfile
import threading
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['PIXCAKE_PHOTOGRAPHER_DATA'] = tempfile.mkdtemp(prefix='pixphoto-test-')

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
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8'))


def main():
    srv = app.start(9799)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    print('\n== 1. 自动探测 (有界搜索) ==')
    st, j = call('GET', '/api/workspace')
    cands = j.get('candidates', [])
    print('  候选数 =', len(cands))
    for c in cands:
        print('   -', c)
    check('探测到 >=1 工作区', len(cands) >= 1)
    check('候选是相册根 (含 user_2911404)', any('project' in c and 'user_2911404' in os.listdir(c) if os.path.isdir(c) else False for c in cands))

    print('\n== 2. 手动设置: 正确路径 ==')
    ws = 'D:/xsdg/像素蛋糕/.PixCake-qt_pro Workspace/project'
    st, j = call('POST', '/api/config', {'workspace': ws})
    check('保存 200', st == 200, str(j))
    check('workspace 原样保存', j.get('workspace') == ws)
    st, j = call('GET', '/api/albums')
    check('相册 32 个', len(j.get('albums', [])) == 32, str(len(j.get('albums', []))))

    print('\n== 3. 手动设置: 上层目录自动向下定位 ==')
    ws2 = 'D:/xsdg/像素蛋糕/.PixCake-qt_pro Workspace'
    st, j = call('POST', '/api/config', {'workspace': ws2})
    check('保存 200', st == 200, str(j))
    check('自动定位到 project', j.get('workspace', '').endswith('project'), str(j.get('workspace')))
    check('返回 note', bool(j.get('note')), str(j.get('note')))
    st, j = call('GET', '/api/albums')
    check('相册 32 个 (定位后)', len(j.get('albums', [])) == 32, str(len(j.get('albums', []))))

    print('\n== 4. 手动设置: 不存在的目录 ==')
    st, j = call('POST', '/api/config', {'workspace': 'Z:/not/exist/path'})
    check('400 报错', st == 400, str(j))
    check('错误信息含"不存在"', '不存在' in (j.get('error') or ''))

    print('\n== 5. 带引号/空白路径 ==')
    st, j = call('POST', '/api/config', {'workspace': '  "D:/xsdg/像素蛋糕/.PixCake-qt_pro Workspace"  '})
    check('剥引号+自动定位', st == 200 and j.get('workspace', '').endswith('project'), str(j.get('workspace')))

    print('\n== 6. config 并发锁 ==')
    results = []
    def worker(n):
        for _ in range(50):
            config.set_many(license={'key': 'K%d' % n}, workspace='ws-%d' % n)
            results.append(config.get('workspace'))
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    # 锁下每次 set_many 都是原子读写, 最终值应是最后一次写入之一; 验证过程中无异常
    cfg = config.load()
    check('并发后 config 仍有效 (有 license+workspace)', 'license' in cfg and 'workspace' in cfg)
    check('无异常崩溃', len(results) == 8 * 50)

    srv.shutdown()
    print('\n==== 结果: %d/%d PASS ====' % (sum(1 for _, c in PASS if c), len(PASS)))
    if not all(c for _, c in PASS):
        sys.exit(1)


if __name__ == '__main__':
    main()
