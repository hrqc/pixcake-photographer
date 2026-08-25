# -*- coding: utf-8 -*-
"""摄影师端本地 Web 程序.
本地后端跑在 127.0.0.1, 浏览器开工业风界面; 后端代理调中央服务器 API,
并负责: 工作区探测/扫描、卡密激活、批量上传、状态面板."""
import json
import os
import re
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import remote
import scanner
import uploader
from machine import machine_fp

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(getattr(sys, '_MEIPASS', HERE), 'static', 'index.html')

# 常见像素蛋糕工作区路径 (Win + Mac)
WS_CANDIDATES = [
    r'D:/xsdg/像素蛋糕/.PixCake-qt_pro Workspace/project',
    r'D:\xsdg\像素蛋糕\.PixCake-qt_pro Workspace\project',
    r'C:/xsdg/像素蛋糕/.PixCake-qt_pro Workspace/project',
    r'C:\xsdg\像素蛋糕\.PixCake-qt_pro Workspace\project',
    r'E:/xsdg/像素蛋糕/.PixCake-qt_pro Workspace/project',
    os.path.join(os.path.expanduser('~'), 'xsdg', '像素蛋糕', '.PixCake-qt_pro Workspace', 'project'),
    os.path.join(os.path.expanduser('~'), '像素蛋糕', '.PixCake-qt_pro Workspace', 'project'),
    os.path.join(os.path.expanduser('~'), 'PixelCake', 'Workspace', 'project'),
]

import threading

_uploader = uploader.Uploader()
_remote = None
_remote_lock = threading.Lock()


def _get_remote():
    global _remote
    base = (config.get('server_url') or '').strip() or config.DEFAULT_SERVER
    with _remote_lock:
        if _remote is None or _remote.base_url != base.rstrip('/'):
            _remote = remote.Remote(base)
        return _remote


def probe_workspace():
    """返回已存在的候选路径 + 当前配置的工作区."""
    found = []
    for p in WS_CANDIDATES:
        if os.path.isdir(p):
            found.append(p)
    cur = (config.get('workspace') or '').strip()
    return {'candidates': found, 'current': cur, 'default': scanner_default()}


def scanner_default():
    if os.path.isdir(WS_CANDIDATES[0]):
        return WS_CANDIDATES[0]
    return ''


def license_status(lic):
    """lic: config.license dict 或 None. 返回 state: none/ok/expired/quota/device/tampered."""
    if not lic or not isinstance(lic, dict):
        return {'state': 'none', 'info': None}
    if lic.get('machine') and lic['machine'] != machine_fp():
        return {'state': 'device', 'info': lic}
    exp = lic.get('expires_at') or 0
    quota = lic.get('quota') or 0
    used = lic.get('quota_used') or 0
    if exp and time.time() > exp:
        return {'state': 'expired', 'info': lic}
    if quota and used >= quota:
        return {'state': 'quota', 'info': lic}
    return {'state': 'ok', 'info': lic}


def build_status():
    lic = config.get('license')
    st = license_status(lic)
    out = {
        'server_url': config.get('server_url') or config.DEFAULT_SERVER,
        'workspace': config.get('workspace') or '',
        'machine': machine_fp(),
        'license': st,
        'upload': _uploader.snapshot(),
    }
    # 尝试在线刷新卡密状态 (已登录会话)
    if st['state'] in ('ok', 'quota', 'expired'):
        try:
            me = _get_remote().tenant_me()
            out['remote'] = {'tenant': me.get('tenant'), 'card': me.get('card'),
                             'stats': me.get('stats')}
        except remote.RemoteError as exc:
            out['remote'] = {'error': exc.message}
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = 'PixCakePhotographer/1.0'

    def _send(self, code, body=b'', ctype='application/json', headers=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'))

    def _read_json(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length > 64 * 1024 * 1024:
            self._json({'error': '请求过大'}, 413)
            return None
        raw = self.rfile.read(length) if length else b'{}'
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            self._json({'error': 'JSON 解析失败'}, 400)
            return None

    def _serve_index(self):
        try:
            with open(INDEX_HTML, 'rb') as f:
                body = f.read()
        except OSError:
            self._json({'error': '界面文件缺失'}, 500)
            return
        self._send(200, body, 'text/html; charset=utf-8')

    # ---------------- 路由 ----------------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ('/', '/index.html'):
            self._serve_index()
            return
        if path == '/api/status':
            self._json(build_status())
            return
        if path == '/api/workspace':
            self._json(probe_workspace())
            return
        if path == '/api/albums':
            self._albums_get()
            return
        if path == '/api/upload/status':
            self._json(_uploader.snapshot())
            return
        if path == '/api/me':
            self._me_get()
            return
        self._json({'error': 'not found'}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self._read_json()
        if body is None:
            return
        if path == '/api/config':
            self._config_post(body)
            return
        if path == '/api/activate':
            self._activate_post(body)
            return
        if path == '/api/upload':
            self._upload_post(body)
            return
        if path == '/api/scan':
            self._scan_post(body)
            return
        self._json({'error': 'not found'}, 404)

    # ---------------- 实现 ----------------
    def _config_post(self, body):
        cfg = {}
        if body.get('server_url'):
            url = str(body['server_url']).strip().rstrip('/')
            if url and not re.match(r'^https?://', url):
                self._json({'error': '服务器地址需要以 http:// 或 https:// 开头'}, 400)
                return
            cfg['server_url'] = url
        if body.get('workspace'):
            ws = str(body['workspace']).strip()
            if not os.path.isdir(ws):
                self._json({'error': '工作区目录不存在'}, 400)
                return
            cfg['workspace'] = ws
        if cfg:
            config.set_many(**cfg)
        self._json({'ok': True})

    def _activate_post(self, body):
        key = (body.get('key') or '').strip().upper()
        if not re.match(r'^[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}$', key):
            self._json({'error': '卡密格式不正确'}, 400)
            return
        url = (body.get('server_url') or config.get('server_url') or config.DEFAULT_SERVER).strip().rstrip('/')
        if not re.match(r'^https?://', url):
            self._json({'error': '服务器地址无效'}, 400)
            return
        cli = remote.Remote(url)
        try:
            j = cli.activate(key)
        except remote.RemoteError as exc:
            self._json({'error': exc.message}, exc.status or 400)
            return
        payload = j.get('payload') or {}
        lic = {
            'key': key,
            'machine': machine_fp(),
            'plan_name': payload.get('plan_name', ''),
            'expires_at': payload.get('expires_at') or 0,
            'quota': payload.get('quota') or 0,
            'quota_used': payload.get('quota_used') or 0,
            'tenant': payload.get('tenant') or '',
            'site_url': payload.get('site_url') or '',
            'activated_at': int(time.time()),
        }
        config.set_many(server_url=url, license=lic)
        # 用卡密登录租户站点, 建立会话 (后续上传/状态用)
        global _remote
        with _remote_lock:
            _remote = cli
        try:
            cli.tenant_login(key)
        except remote.RemoteError:
            pass  # 登录失败不阻断激活
        self._json({'ok': True, 'license': lic, 'site_url': lic['site_url']})

    def _albums_get(self):
        ws = (config.get('workspace') or '').strip()
        if not ws or not os.path.isdir(ws):
            self._json({'error': '尚未设置工作区'}, 400)
            return
        state = uploader._load_state()
        albums = []
        for a in scanner.find_albums(ws):
            photos = scanner.scan_project_photos(a['path'])
            total_bytes = sum(f['size'] for f in scanner.album_upload_files(a['path']))
            uploaded_bytes = 0
            for f in scanner.album_upload_files(a['path']):
                prev = state.get(f['rel_path'])
                if prev and prev.get('size') == f['size'] and prev.get('mtime') == f['mtime'] \
                        and prev.get('uploaded') == f['size']:
                    uploaded_bytes += f['size']
            albums.append({
                'id': a['id'], 'user': a['user'], 'album_id': a['album_id'],
                'path': a['path'], 'photo_count': len(photos),
                'total_bytes': total_bytes, 'uploaded_bytes': uploaded_bytes,
                'complete': total_bytes > 0 and uploaded_bytes >= total_bytes,
            })
        albums.sort(key=lambda x: (-x['photo_count']))
        self._json({'workspace': ws, 'albums': albums})

    def _upload_post(self, body):
        ws = (config.get('workspace') or '').strip()
        paths = body.get('album_paths') or []
        if not ws or not paths:
            self._json({'error': '未选择相册'}, 400)
            return
        real = []
        for p in paths:
            p = os.path.normpath(os.path.join(ws, p))
            if os.path.isdir(p) and os.path.isdir(os.path.join(p, 'thumbnail_cache')):
                real.append(p)
        if not real:
            self._json({'error': '没有有效的相册目录'}, 400)
            return
        ok, msg = _uploader.start(_get_remote(), real)
        if not ok:
            self._json({'error': msg}, 409)
            return
        self._json({'ok': True})

    def _scan_post(self, body):
        try:
            _get_remote().tenant_scan()
            self._json({'ok': True})
        except remote.RemoteError as exc:
            self._json({'error': exc.message}, exc.status or 400)

    def _me_get(self):
        try:
            self._json(_get_remote().tenant_me())
        except remote.RemoteError as exc:
            self._json({'error': exc.message}, exc.status or 400)


class PhotographerServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def start(port=9699):
    srv = PhotographerServer(('127.0.0.1', port), Handler)
    return srv
