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

# 原生目录选择框 (tkinter; 顶层 import 让 PyInstaller 打包进去, 无 tkinter 系统优雅降级)
try:
    import tkinter as _tk  # noqa: F401
    _HAS_TK = True
except Exception:
    _tk = None
    _HAS_TK = False

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

# 有界搜索时跳过的系统/无关目录
_SKIP_DIRS = frozenset({
    'windows', 'program files', 'program files (x86)', 'perflogs', 'recovery',
    '$recycle.bin', 'system volume information', 'msocache', 'temp', 'appdata',
    'node_modules', '__pycache__', '.git', '.pixcake-photographer', 'hiberfil.sys',
})


def _log(msg):
    """写客户端日志到 ~/.pixcake-photographer/client.log (远程诊断用)."""
    try:
        d = config.data_dir()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'client.log'), 'a', encoding='utf-8') as f:
            f.write('%s  %s\n' % (time.strftime('%m-%d %H:%M:%S'), msg))
    except Exception:
        pass


def _listdir_safe(d):
    try:
        return os.listdir(d)
    except OSError:
        return []


def _is_album_root(d):
    """d 是否为相册根: 存在 <d>/<user>/<album>/thumbnail_cache."""
    for u in _listdir_safe(d):
        up = os.path.join(d, u)
        if os.path.isdir(up):
            for a in _listdir_safe(up):
                if os.path.isdir(os.path.join(up, a, 'thumbnail_cache')):
                    return True
    return False


def _find_workspace_deep(p, depth=5):
    """在 p 内部有界递归寻找相册根 (用户手填/浏览时用, 允许进入 Program Files 等).
    命中即返回, 找不到返回 None."""
    if _is_album_root(p):
        return p
    if depth <= 0:
        return None
    for name in _listdir_safe(p):
        if name.lower() in _SKIP_DIRS:
            continue
        q = os.path.join(p, name)
        if os.path.isdir(q):
            hit = _find_workspace_deep(q, depth - 1)
            if hit:
                return hit
    return None


def _resolve_workspace(ws):
    """接受用户输入/浏览选中的目录, 尽力返回真正的相册根.
    - 目录不存在 → None
    - 目录存在 → 有界深搜(depth5)定位相册根; 找不到再查父目录 (应对填了安装文件夹)
    - 仍找不到 → 原样返回 (让前端提示, 不锁死用户)"""
    if not ws:
        return None
    ws = ws.strip().strip('"').strip("'")
    if not os.path.isdir(ws):
        return None
    hit = _find_workspace_deep(ws, 5)
    if hit:
        return hit
    parent = os.path.dirname(ws)
    if parent and os.path.isdir(parent):
        hit = _find_workspace_deep(parent, 2)
        if hit:
            return hit
    return ws


def _search(d, depth, max_depth, _add):
    """有界深度搜索工作区; 命中标记名目录才下钻检查, 避免全盘 listdir."""
    if depth >= max_depth:
        return
    for name in _listdir_safe(d):
        if name.lower() in _SKIP_DIRS:
            continue
        p = os.path.join(d, name)
        if not os.path.isdir(p):
            continue
        low = name.lower()
        if 'pixcake' in low or name == '像素蛋糕' or name == 'xsdg' or name == 'project':
            hit = _find_workspace_deep(p, 3)
            if hit:
                _add(hit)
                continue
        _search(p, depth + 1, max_depth, _add)


def _discover_workspaces():
    """返回所有探测到的工作区相册根 (绝对路径), 去重.
    顺序: 已知候选路径 → 所有盘符 + 用户目录 有界搜索."""
    found, seen = [], set()

    def _add(p):
        p = os.path.normpath(p)
        if p not in seen:
            found.append(p)
            seen.add(p)

    for p in WS_CANDIDATES:
        if os.path.isdir(p) and _is_album_root(p):
            _add(p)
    roots = [os.path.expanduser('~')]
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        root = '%s:/' % letter
        if os.path.isdir(root):
            roots.append(root)
    for root in roots:
        _search(root, 0, 4, _add)
    return found


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


def _ensure_tenant_session():
    """确保租户会话存在: 无 g cookie 且本地有卡密时自动重新登录.
    租户会话只存内存, 软件重启即丢, 此前只在激活时登录一次 → 重启后
    所有租户接口都被服务器 401「摄影师未登录」. 这里每次调用前自动补会话.
    返回 None(会话就绪) 或错误信息."""
    lic = config.get('license')
    if not lic or not isinstance(lic, dict) or not lic.get('key'):
        return '尚未激活卡密'
    r = _get_remote()
    if r._cookies.get('g'):
        return None
    try:
        r.tenant_login(lic['key'])
        return None
    except remote.RemoteError as exc:
        _log('租户自动登录失败: %s' % exc.message)
        return exc.message


_verify_state = {'state': None, 'reason': None}   # state: None/ok/expired/quota/locked
_verify_lock = threading.Lock()


def _verify_loop():
    """后台周期回连服务器校验卡密状态 (服务端权威).
    每 45 秒一次; 服务端禁用/不存在/设备不符 → locked (前端进激活闸门),
    过期/超额 → expired/quota (走警告条+换卡入口); valid 时回写本地额度/到期
    (管理员加张数/延期立即生效). 网络异常时保持上一次判定, 由本地判断兜底."""
    while True:
        try:
            lic = config.get('license')
            if not lic or not isinstance(lic, dict) or not lic.get('key'):
                with _verify_lock:
                    _verify_state['state'] = None
                    _verify_state['reason'] = None
            else:
                try:
                    j = _get_remote().verify(lic['key'])
                    with _verify_lock:
                        if j.get('valid'):
                            _verify_state['state'] = 'ok'
                            _verify_state['reason'] = None
                            p = j.get('payload') or {}
                            cur = dict(lic)
                            dirty = False
                            for fld in ('expires_at', 'quota', 'quota_used', 'plan_name'):
                                if p.get(fld) is not None and cur.get(fld) != p[fld]:
                                    cur[fld] = p[fld]
                                    dirty = True
                            if dirty:
                                config.set_many(license=cur)
                        else:
                            reason = j.get('reason') or 'disabled'
                            if reason in ('expired', 'quota'):
                                _verify_state['state'] = reason
                                _verify_state['reason'] = reason
                            else:
                                _verify_state['state'] = 'locked'
                                _verify_state['reason'] = reason
                except remote.RemoteError:
                    pass  # 网络异常: 保持上一次判定, 本地兜底
        except Exception:
            pass
        time.sleep(45)


def probe_workspace():
    """返回探测到的工作区(相册根)候选 + 当前配置的工作区."""
    try:
        cands = _discover_workspaces()
    except Exception as exc:
        _log('probe_workspace 异常: %r' % (exc,))
        cands = []
    cur = (config.get('workspace') or '').strip()
    out = {'candidates': cands, 'current': cur, 'default': scanner_default()}
    _log('探测工作区: candidates=%d current=%r' % (len(cands), cur))
    return out


def scanner_default():
    if os.path.isdir(WS_CANDIDATES[0]):
        return WS_CANDIDATES[0]
    return ''


def license_status(lic):
    """lic: config.license dict 或 None. 返回 state: none/ok/expired/quota/device/locked.
    服务端周期回连结果优先 (权威); 未回连/网络异常时用本地判断兜底."""
    if not lic or not isinstance(lic, dict):
        return {'state': 'none', 'info': None}
    with _verify_lock:
        vs, vr = _verify_state['state'], _verify_state['reason']
    if vs == 'expired':
        return {'state': 'expired', 'info': lic, 'reason': 'expired'}
    if vs == 'quota':
        return {'state': 'quota', 'info': lic, 'reason': 'quota'}
    if vs == 'locked':
        return {'state': 'locked', 'info': lic, 'reason': vr or 'disabled'}
    if vs == 'ok':
        return {'state': 'ok', 'info': lic}
    # 本地兜底 (尚未回连 / 网络异常)
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
    # 尝试在线刷新卡密状态 (已登录会话; 无会话先自动登录)
    if st['state'] in ('ok', 'quota', 'expired'):
        err = _ensure_tenant_session()
        if err:
            out['remote'] = {'error': err}
        else:
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
        try:
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
            if path.startswith('/img/'):
                self._img_proxy(self.path)
                return
            if path.startswith('/api/tenant/project'):
                self._tenant_api_get(self.path)
                return
            self._json({'error': 'not found'}, 404)
        except Exception as exc:
            _log('GET %s 异常: %r' % (path, exc))
            self._json({'error': '服务器错误: %s' % exc}, 500)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self._read_json()
        if body is None:
            return
        try:
            if path == '/api/config':
                self._config_post(body)
                return
            if path == '/api/pickdir':
                self._pickdir_post()
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
            if path in ('/api/tenant/select', '/api/tenant/rename'):
                self._tenant_api_post(path, body)
                return
            self._json({'error': 'not found'}, 404)
        except Exception as exc:
            _log('POST %s 异常: %r' % (path, exc))
            self._json({'error': '服务器错误: %s' % exc}, 500)

    # ---------------- 实现 ----------------
    def _config_post(self, body):
        cfg = {}
        note = None
        if body.get('server_url'):
            url = str(body['server_url']).strip().rstrip('/')
            if url and not re.match(r'^https?://', url):
                self._json({'error': '服务器地址需要以 http:// 或 https:// 开头'}, 400)
                return
            cfg['server_url'] = url
        if body.get('workspace'):
            ws = str(body['workspace']).strip()
            if not ws:
                self._json({'error': '工作区路径为空'}, 400)
                return
            resolved = _resolve_workspace(ws)
            if resolved is None:
                _log('设置工作区失败: %s 目录不存在' % ws)
                self._json({'error': '目录不存在: %s' % ws}, 400)
                return
            cfg['workspace'] = resolved
            _log('设置工作区: %s → %s' % (ws, resolved))
            if os.path.normpath(resolved) != os.path.normpath(ws):
                note = '已自动定位到相册目录: %s' % resolved
        if cfg:
            config.set_many(**cfg)
        resp = {'ok': True}
        if 'workspace' in cfg:
            resp['workspace'] = cfg['workspace']
        if note:
            resp['note'] = note
        self._json(resp)

    def _pickdir_post(self):
        """弹出系统原生目录选择框 (tkinter), 返回用户选中的绝对路径."""
        if not _HAS_TK:
            self._json({'error': '此系统不支持原生目录选择，请手动输入路径'}, 400)
            return
        try:
            from tkinter import filedialog
            root = _tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            root.update()
            chosen = filedialog.askdirectory(
                parent=root, title='选择像素蛋糕工作区目录（含 thumbnail_cache 的文件夹）')
            root.destroy()
        except Exception as exc:
            _log('目录选择框异常: %r' % (exc,))
            try:
                root.destroy()
            except Exception:
                pass
            self._json({'error': '打开目录选择框失败: %s' % exc}, 400)
            return
        if chosen:
            _log('浏览选中: %s' % chosen)
            self._json({'ok': True, 'path': chosen})
        else:
            self._json({'ok': False, 'error': '未选择目录'})

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
        err = _ensure_tenant_session()
        if err:
            self._json({'error': err}, 401)
            return
        ok, msg = _uploader.start(_get_remote(), real)
        if not ok:
            self._json({'error': msg}, 409)
            return
        self._json({'ok': True})

    def _scan_post(self, body):
        try:
            err = _ensure_tenant_session()
            if err:
                self._json({'error': err}, 401)
                return
            _get_remote().tenant_scan()
            self._json({'ok': True})
        except remote.RemoteError as exc:
            self._json({'error': exc.message}, exc.status or 400)

    def _me_get(self):
        try:
            err = _ensure_tenant_session()
            if err:
                self._json({'error': err}, 401)
                return
            self._json(_get_remote().tenant_me())
        except remote.RemoteError as exc:
            self._json({'error': exc.message}, exc.status or 400)

    # ---------------- 相册浏览 (代理中央服务器, 自动带 g cookie) ----------------
    def _tenant_api_get(self, path):
        """代理服务器租户相册 API (GET). path 为本地原始请求路径 (含 query),
        与服务器接口路径一一对应, 服务器会校验租户归属 (只能看自己的相册)."""
        try:
            err = _ensure_tenant_session()
            if err:
                self._json({'error': err}, 401)
                return
            self._json(_get_remote().get(path))
        except remote.RemoteError as exc:
            self._json({'error': exc.message}, exc.status or 400)

    def _tenant_api_post(self, path, body):
        try:
            err = _ensure_tenant_session()
            if err:
                self._json({'error': err}, 401)
                return
            self._json(_get_remote().post(path, body))
        except remote.RemoteError as exc:
            self._json({'error': exc.message}, exc.status or 400)

    def _img_proxy(self, path):
        """代理 /img/... 图片请求到服务器 (带 g cookie, 服务器校验归属 → 只能看自己相册的图).
        转发完整 self.path 保留签名 query; 图片始终 JPEG."""
        try:
            err = _ensure_tenant_session()
            if err:
                self._json({'error': err}, 401)
                return
            data = _get_remote().get_bytes(path)
            self._send(200, data, 'image/jpeg')
        except remote.RemoteError as exc:
            self._json({'error': exc.message}, exc.status or 502)


class PhotographerServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def start(port=9699):
    threading.Thread(target=_verify_loop, daemon=True).start()
    srv = PhotographerServer(('127.0.0.1', port), Handler)
    return srv
