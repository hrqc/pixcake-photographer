# -*- coding: utf-8 -*-
"""摄影师端客户端端到端: 客户端激活 -> 设置工作区 -> 扫描 -> 上传相册
-> 服务器建站/去水印 -> 数据归属隔离.
客户端以子进程方式运行 (与打包/真实使用一致, 避免 scanner 模块命名冲突).
依赖: D:/xsdg 像素蛋糕工作区里有带原图的相册 (与 gallery e2e 同源)."""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

GALLERY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'pixcake-gallery'))
CLIENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, GALLERY_DIR)

import db as gdb
import gallery as gmod


def pick_single_photo():
    for p in gdb.list_projects():
        for ph in gdb.list_photos(p['id']):
            if ph['src_3000'] and os.path.isfile(ph['src_3000']) \
                    and ph['src_o_3000'] and os.path.isfile(ph['src_o_3000']):
                return p, ph
    raise SystemExit('没有可用源照片')


def build_client_ws(dest, album_path, photo):
    """把一张照片的精修/原图 3000/375 复制成客户端工作区结构."""
    rel_user = 'u_%s' % photo['photo_id'][:6]
    rel_album = 'a_%s' % photo['photo_id']
    td = photo['thumb_dir']
    root = os.path.join(dest, rel_user, rel_album, 'thumbnail_cache', td)
    for sub, src in (('c_p_f_e', photo['src_3000']), ('c_p_f_o', photo['src_o_3000'])):
        d = os.path.join(root, sub)
        os.makedirs(d, exist_ok=True)
        base = os.path.basename(src)          # <ID>_3000
        shutil.copyfile(src, os.path.join(d, base))
        alt = base[:-5] + '_375'              # <ID>_375
        src_alt = os.path.join(os.path.dirname(src), alt)
        if os.path.isfile(src_alt):
            shutil.copyfile(src_alt, os.path.join(d, alt))
    return os.path.join(dest, rel_user, rel_album)


def main():
    tmp = tempfile.mkdtemp(prefix='pixcake-client-e2e-')
    client_data = os.path.join(tmp, 'client-data')
    gallery_data = os.path.join(tmp, 'gallery-data')
    gws = os.path.join(tmp, 'gallery-ws')
    os.makedirs(client_data); os.makedirs(gallery_data); os.makedirs(gws)
    print('[1/7] 准备: 源照片 -> 客户端工作区')
    src_album, photo = pick_single_photo()
    client_ws = os.path.join(tmp, 'client-ws')
    album_path = build_client_ws(client_ws, src_album['path'], photo)
    print('      photo=%s -> %s' % (photo['photo_id'], album_path))

    # 切换 gallery 到临时数据
    old_db = gdb.DB_FILE
    gdb.DB_FILE = os.path.join(gallery_data, 'gallery.db')
    gmod.DATA_DIR = gallery_data
    gmod.TENANT_ROOT = os.path.join(gallery_data, 'tenants')
    gmod.CLEAN_CACHE_DIR = os.path.join(gallery_data, 'cache')
    gdb.init_db()

    # 建卡 (不预激活, 让客户端走激活流程)
    key = gdb.create_license_keys('张数卡', 0, 5, 1, 1, 'cle2e')[0]
    print('[2/7] 启动中央服务器 (卡 %s)' % key)
    gsrv = gmod.GalleryServer(('127.0.0.1', 0), gws, 'tok', wm_workers=1)
    gt = threading.Thread(target=gsrv.serve_forever, daemon=True); gt.start()
    gport = gsrv.server_port

    print('[3/7] 启动摄影师端客户端 (指向 http://127.0.0.1:%d)' % gport)
    env = dict(os.environ)
    env['PIXCAKE_PHOTOGRAPHER_DATA'] = client_data
    env['PYTHONIOENCODING'] = 'utf-8'
    cproc = subprocess.Popen(
        [sys.executable, os.path.join(CLIENT_DIR, 'run.py'), '--port', '9700', '--no-browser'],
        cwd=CLIENT_DIR, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.2)

    def creq(method, path, body=None):
        c = http.client.HTTPConnection('127.0.0.1', 9700, timeout=120)
        hdrs = {'Content-Type': 'application/json'} if body is not None else {}
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                  headers=hdrs)
        r = c.getresponse(); data = r.read().decode('utf-8'); c.close()
        return r.status, json.loads(data) if data else {}

    try:
        print('[4/7] 客户端激活')
        status, j = creq('POST', '/api/activate', {'key': key,
                                                   'server_url': 'http://127.0.0.1:%d' % gport})
        assert status == 200, (status, j)
        lic = j['license']
        assert lic['tenant'].startswith('p_'), lic
        assert 'site_url' in lic and lic['site_url'], lic
        print('      激活 ok: tenant=%s site=%s' % (lic['tenant'], lic['site_url']))
        slug = lic['tenant']

        status, j = creq('GET', '/api/status')
        assert j['license']['state'] == 'ok', j
        assert j['remote'] and j['remote']['card'], j
        print('      状态 ok: plan=%s remaining=%s' % (j['remote']['card'].get('plan_name'),
                                                       j['remote']['card'].get('remaining')))

        print('[5/7] 设置工作区 + 扫描相册')
        status, j = creq('POST', '/api/config', {'workspace': client_ws})
        assert status == 200, (status, j)
        status, j = creq('GET', '/api/albums')
        assert status == 200, (status, j)
        assert len(j['albums']) == 1, j
        alb = j['albums'][0]
        assert alb['photo_count'] == 1 and not alb['complete'], alb
        print('      发现相册: %s, %d 张, %s' % (alb['album_id'], alb['photo_count'],
                                                alb['total_bytes']))

        print('[6/7] 上传相册')
        status, j = creq('POST', '/api/upload', {'album_paths': [album_path]})
        assert status == 200, (status, j)
        deadline = time.time() + 120
        while time.time() < deadline:
            time.sleep(1)
            status, j = creq('GET', '/api/upload/status')
            if not j.get('running'):
                break
        assert j['phase'] == 'done', j
        assert j['done_files'] == j['total_files'], j
        print('      上传 ok: %d/%d 文件, %.0f KB, 服务器已扫描' % (
            j['done_files'], j['total_files'], j['done_bytes'] / 1024))

        # 等服务器去水印 (直接查 GalleryServer.tenant_warmer 状态)
        deadline = time.time() + 120
        while time.time() < deadline:
            pw = gsrv.tenant_warmer.status(slug)
            if not pw.get('running') and pw.get('error') is None \
                    and pw.get('photos', 0) >= 1:
                break
            time.sleep(1)
        assert pw and pw.get('built', 0) + pw.get('cached', 0) >= 1, pw
        print('      去水印 ok: built=%d cached=%d' % (pw.get('built'), pw.get('cached')))

        print('[7/7] 数据归属隔离验证')
        proj = gdb.list_projects_for(slug)
        assert len(proj) == 1 and gdb.project_owner(proj[0]['id']) == slug, proj
        # 别的摄影师看不到
        other = gdb.create_photographer('路人', 'OTHER-FP')
        assert len(gdb.list_projects_for(other['id'])) == 0
        # 照片归属
        photos = gdb.list_photos(proj[0]['id'])
        assert len(photos) == 1 and photos[0]['photo_id'] == photo['photo_id'], photos
        print('      项目 %s 属于 %s, 其他摄影师不可见' % (proj[0]['id'], slug))

        print('\n===== 摄影师端客户端端到端全通过 =====')
    finally:
        cproc.terminate()
        try:
            cproc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cproc.kill()
        gsrv.shutdown(); gsrv.server_close(); gt.join(timeout=2)
        gsrv.export_manager.shutdown(); gsrv.image_service.shutdown()
        gdb.DB_FILE = old_db
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
