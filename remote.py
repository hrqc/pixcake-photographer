# -*- coding: utf-8 -*-
"""中央服务器 API 客户端 (urllib, 自动维持 g 会话 cookie).
服务器地址默认 https://hrqc105.icu (可在客户端配置里改)."""
import json
import urllib.error
import urllib.request

from machine import machine_fp


class RemoteError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Remote:
    def __init__(self, base_url, timeout=60):
        self.base_url = (base_url or '').rstrip('/')
        self.timeout = timeout
        self.jar = None
        self._jar = []
        self._cookies = {}

    # ---- cookie 管理 (手写, 兼容 PyInstaller 无需 http.cookiejar) ----
    def _load_cookies(self):
        self._cookies = {}
        for name, value in self._jar:
            self._cookies[name] = value

    def _store_cookies(self, headers):
        for key, value in headers.items():
            if key.lower() != 'set-cookie':
                continue
            for part in value.split(','):
                part = part.strip()
                name, _, val = part.partition('=')
                if name == 'g':
                    self._jar.append(('g', val.split(';')[0]))
                    self._load_cookies()
                    break

    def _cookie_header(self):
        if self._cookies.get('g'):
            return 'g=%s' % self._cookies['g']
        return ''

    # ---- 基础请求 ----
    def _request(self, method, path, payload=None):
        headers = {'User-Agent': 'PixCake-Photographer/1.0',
                   'Content-Type': 'application/json'}
        cookie = self._cookie_header()
        if cookie:
            headers['Cookie'] = cookie
        body = json.dumps(payload).encode('utf-8') if payload is not None else None
        req = urllib.request.Request(self.base_url + path, data=body,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                self._store_cookies(resp.headers)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            self._store_cookies(exc.headers)
            try:
                j = json.loads(raw.decode('utf-8'))
            except Exception:
                raise RemoteError(exc.code, '服务器错误 (HTTP %d)' % exc.code)
            raise RemoteError(exc.code, j.get('error') or '服务器错误')
        except urllib.error.URLError as exc:
            raise RemoteError(0, '无法连接服务器: %s' % (exc.reason or exc))
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            raise RemoteError(0, '服务器响应异常')

    def post(self, path, payload=None):
        return self._request('POST', path, payload)

    def get(self, path):
        return self._request('GET', path)

    # ---- 业务接口 ----
    def activate(self, key):
        return self.post('/api/license/activate', {'key': key, 'machine': machine_fp()})

    def tenant_login(self, key):
        return self.post('/api/tenant/login', {'key': key, 'machine': machine_fp()})

    def tenant_me(self):
        return self.get('/api/tenant/me')

    def tenant_logout(self):
        return self.post('/api/tenant/logout', {})

    def upload(self, rel_path, data_b64, offset=0):
        return self.post('/api/tenant/upload', {
            'rel_path': rel_path, 'data_b64': data_b64, 'offset': offset,
            'machine': machine_fp(),
        })

    def tenant_scan(self):
        return self.post('/api/tenant/scan', {})

    def tenant_prewarm(self):
        return self.get('/api/tenant/prewarm')

    def verify(self, key):
        return self.post('/api/license/verify', {'key': key, 'machine': machine_fp(),
                                                 'quota_delta': 0})
