# -*- coding: utf-8 -*-
"""批量上传: 扫描本地相册 -> 分批把精修/原图文件传到自己租户 -> 触发服务器扫描建站.
支持断点续传: 每文件按 4MB 分块, 已上传字节记入 upload_state.json, 下次续传."""
import base64
import json
import os
import threading
import time

import config
import remote
import scanner

CHUNK = 4 * 1024 * 1024  # 每块二进制 4MB (base64 后 <6MB, 远低于服务器 24MB 限制)
RETRY = 3


def _state_path():
    return os.path.join(config.data_dir(), 'upload_state.json')


def _tenant_key():
    """租户隔离键: 上传进度按摄影师站点分开, 换卡/换租户不沿用旧进度
    (旧租户已传的文件在另一站点不存在, 误判已传会丢文件)."""
    lic = config.get('license')
    if lic and isinstance(lic, dict):
        t = (lic.get('tenant') or '').strip()
        if t:
            return t
    return 'default'


def _load_state():
    """返回 {tenant: {rel: {...}}}. 兼容旧扁平格式 {rel: {...}} (归入 'default')."""
    try:
        with open(_state_path(), encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    for v in raw.values():
        if isinstance(v, dict) and 'size' in v:   # 旧扁平格式: key 是 rel_path
            return {'default': raw}
    return raw


def _files(state):
    return state.setdefault(_tenant_key(), {})


def _save_state(state):
    os.makedirs(config.data_dir(), exist_ok=True)
    tmp = _state_path() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, _state_path())


class Uploader:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._state = {
            'running': False,
            'phase': 'idle',          # idle / uploading / scan / done / error
            'album_ids': [],
            'total_files': 0, 'done_files': 0, 'failed_files': 0,
            'total_bytes': 0, 'done_bytes': 0,
            'current': '',            # 当前文件 rel_path
            'skipped': 0,
            'log': [],
            'last_error': '',
            'last_finish': 0,
        }

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def _log(self, msg):
        with self._lock:
            self._state['log'] = (self._state['log'] + [msg])[-200:]

    def start(self, server, album_paths):
        """server: remote.Remote (已登录), album_paths: 相册绝对路径列表."""
        with self._lock:
            if self._state['running']:
                return False, '上传已在运行'
            self._stop.clear()
            self._state.update({
                'running': True, 'phase': 'uploading', 'album_ids': album_paths,
                'total_files': 0, 'done_files': 0, 'failed_files': 0,
                'total_bytes': 0, 'done_bytes': 0, 'current': '',
                'skipped': 0, 'log': [], 'last_error': '', 'last_finish': 0,
            })
        self._thread = threading.Thread(target=self._run, args=(server, list(album_paths)),
                                        daemon=True, name='uploader')
        self._thread.start()
        return True, ''

    def stop(self):
        self._stop.set()

    # ---------------- 内部 ----------------
    def _run(self, server, album_paths):
        try:
            state = _load_state()
            total_files = total_bytes = 0
            bad_rel = 0
            plan = []
            for ap in album_paths:
                for f in scanner.album_upload_files(ap):
                    if not scanner.rel_path_ok(f['rel_path']):
                        bad_rel += 1   # H4: 服务器不接受含 ( ) + # 等字符的路径, 预校验过滤
                        continue
                    plan.append(f)
                    total_files += 1
                    total_bytes += f['size']
            if bad_rel:
                self._log('跳过 %d 个非法路径文件 (文件名含特殊字符, 服务器不接受)' % bad_rel)
            with self._lock:
                self._state['total_files'] = total_files
                self._state['total_bytes'] = total_bytes
            self._log('共 %d 个文件, %.1f MB' % (total_files, total_bytes / 1048576))

            done_files = failed = skipped = done_bytes = 0
            for idx, f in enumerate(plan):
                if self._stop.is_set():
                    break
                rel = f['rel_path']
                # 已完整上传且未变更 -> 跳过 (state 按租户隔离)
                prev = _files(state).get(rel)
                if prev and prev.get('size') == f['size'] and prev.get('mtime') == f['mtime'] \
                        and prev.get('uploaded') == f['size']:
                    skipped += 1
                    done_bytes += f['size']
                    continue
                with self._lock:
                    self._state['current'] = rel
                ok, err = self._upload_file(server, f, prev)
                if ok:
                    done_files += 1
                    done_bytes += f['size']
                else:
                    failed += 1
                    self._log('失败 %s: %s' % (rel, err))
                with self._lock:
                    self._state.update(done_files=done_files, failed_files=failed,
                                       done_bytes=done_bytes, skipped=skipped)
            if self._stop.is_set():
                self._log('上传已暂停')
                self._finish('error', '已暂停')
                return
            self._log('上传完成: 成功 %d, 跳过 %d, 失败 %d' % (done_files, skipped, failed))
            # 触发服务器扫描建站 (带上相册显示名 = 创建时间)
            with self._lock:
                self._state['phase'] = 'scan'
            try:
                names = {}
                for a in scanner.find_albums(config.get('workspace') or ''):
                    if a.get('name'):
                        names[a['id']] = a['name']
                server.tenant_scan(names if names else None)
            except remote.RemoteError as exc:
                self._log('扫描失败: %s' % exc.message)
                self._finish('error', '扫描失败: %s' % exc.message)
                return
            self._log('服务器扫描完成, 正在去水印建站…')
            self._finish('done', '' if failed == 0 else '%d 个文件失败' % failed)
        except remote.RemoteError as exc:
            self._finish('error', exc.message)
        except Exception as exc:
            self._finish('error', str(exc))

    def _upload_file(self, server, f, prev):
        rel = f['rel_path']
        size = f['size']
        # H5: 源文件在上传前被编辑 (变小/改动) → 重 stat, 变了就丢弃旧进度全量重传
        try:
            st = os.stat(f['abs_path'])
            if st.st_size != size or int(st.st_mtime_ns) != f['mtime']:
                size = st.st_size
                f['size'] = size
                f['mtime'] = int(st.st_mtime_ns)
                prev = None
                self._log('文件已变更, 全量重传 %s' % rel)
        except OSError as exc:
            return False, '读取文件失败: %s' % exc
        start = 0
        if prev and prev.get('size') == size and prev.get('mtime') == f['mtime'] \
                and isinstance(prev.get('uploaded'), int):
            start = prev['uploaded']
        state = _load_state()
        cur = dict(_files(state).get(rel) or {})
        cur['size'] = size
        cur['mtime'] = f['mtime']
        for attempt in range(RETRY):
            try:
                with open(f['abs_path'], 'rb') as fh:
                    fh.seek(start)
                    while start < size:
                        if self._stop.is_set():
                            return False, '已暂停'
                        raw = fh.read(CHUNK)
                        if not raw:
                            break
                        server.upload(rel, base64.b64encode(raw).decode('ascii'), start)
                        start += len(raw)
                        cur['uploaded'] = start
                        all_st = _load_state()
                        _files(all_st)[rel] = cur
                        _save_state(all_st)
                return True, ''
            except remote.RemoteError as exc:
                # S2: 服务器权威 offset. 本地进度过期/换租户残留 → 以服务器实际 size
                # 续传; 服务器已完整 (>= 本地) → 视为上传完成, 不再传.
                if exc.status == 409 and isinstance(exc.data, dict) and 'size' in exc.data:
                    ssize = exc.data['size']
                    if ssize >= size:
                        cur['uploaded'] = size
                        all_st = _load_state()
                        _files(all_st)[rel] = cur
                        _save_state(all_st)
                        return True, ''
                    start = ssize
                    cur['uploaded'] = ssize
                    continue
                if attempt == RETRY - 1:
                    return False, exc.message
                time.sleep(1)
            except OSError as exc:
                return False, '读取文件失败: %s' % exc
        return False, '上传失败'

    def _finish(self, phase, err):
        with self._lock:
            self._state.update(running=False, phase=phase, last_error=err,
                               last_finish=time.time())
