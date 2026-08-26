# -*- coding: utf-8 -*-
"""全尺寸原图来源解析: AppData project.db thumbnail 表(权威) + 像素蛋糕云空间(兜底).

- project.db thumbnail.originalImagePath 记录每张照片原图的精确路径
  (相机导入 = 云空间路径; 微信导入 = 微信文件路径), 是最权威来源.
- 云空间位置自动探测 (`*云空间` 目录, 不写死盘符), 作为 project.db 缺失/路径失效时的兜底.
"""
import os
import re
import sqlite3


def _drives():
    out = []
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        p = letter + ':\\'
        if os.path.isdir(p):
            out.append(p)
    return out


def discover_cloudspace():
    """探测各盘根下的云空间目录 (如 G:/像素蛋糕云空间), 返回绝对路径列表."""
    found = []
    for drive in _drives():
        try:
            for name in os.listdir(drive):
                if name.endswith('云空间'):
                    cand = os.path.join(drive, name)
                    if os.path.isdir(cand):
                        found.append(cand)
        except OSError:
            continue
    return found


def _appdata_db_dir():
    appdata = os.environ.get('APPDATA') or os.path.expanduser(r'~\AppData\Roaming')
    return os.path.join(appdata, 'PixCake-qt_pro', 'db')


def find_project_db(album_id):
    """AppData 下 project_{album_id}/project.db (跨 user_* 子目录)."""
    base = _appdata_db_dir()
    if not os.path.isdir(base):
        return None
    try:
        for user_dir in os.listdir(base):
            if not user_dir.startswith('user_'):
                continue
            cand = os.path.join(base, user_dir, 'project_%s' % album_id, 'project.db')
            if os.path.isfile(cand):
                return cand
    except OSError:
        pass
    return None


def read_originals(album_id):
    """返回 {photo_id: original_path} (thumbnail 表, 权威)."""
    dbp = find_project_db(album_id)
    if not dbp:
        return {}
    try:
        conn = sqlite3.connect(dbp, timeout=3)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT originalImagePath FROM thumbnail '
            'WHERE originalImagePath IS NOT NULL AND originalImagePath <> \'\''
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return {}
    out = {}
    for r in rows:
        p = str(r['originalImagePath']).replace('\\', '/')
        pid = os.path.splitext(os.path.basename(p))[0]
        if pid:
            out[pid] = p
    return out


def _cloudspace_files(album_id):
    """云空间里 {相册名}_{album_id} 相册根目录 → {photo_id: 绝对路径}."""
    out = {}
    for cs in discover_cloudspace():
        try:
            names = os.listdir(cs)
        except OSError:
            continue
        for name in names:
            m = re.match(r'^(.+)_%s$' % re.escape(album_id), name)
            if not m:
                continue
            root = os.path.join(cs, name)
            try:
                for fn in os.listdir(root):
                    if fn.startswith('.') or os.path.isdir(os.path.join(root, fn)):
                        continue
                    pid = os.path.splitext(fn)[0]
                    out.setdefault(pid, os.path.join(root, fn))
            except OSError:
                continue
    return out


def album_created_time(album_id):
    """返回相册创建时间字符串 (YYYY-MM-DD HH:MM), 用于摄影端相册显示名.
    来源: base.db project_operation_log (像素蛋糕创建相册时间戳, 权威)."""
    # base.db 与 project.db 同在 AppData/PixCake-qt_pro/db 下
    try:
        base_db = os.path.join(_appdata_db_dir(), 'base.db')
        if os.path.isfile(base_db):
            conn = sqlite3.connect(base_db, timeout=3)
            row = conn.execute(
                'SELECT time FROM project_operation_log WHERE projectId=? AND type=1 '
                'ORDER BY time LIMIT 1', (album_id,)).fetchone()
            conn.close()
            if row:
                import datetime
                dt = datetime.datetime.fromtimestamp(row[0] / 1000.0)
                return dt.strftime('%Y-%m-%d %H:%M')
    except sqlite3.Error:
        pass
    return ''


def resolve_originals(album_id, photo_ids):
    """为相册内 photo_ids 解析全尺寸原图绝对路径.
    顺序: project.db 记录的路径(存在) → 云空间相册根. 返回 {photo_id: 绝对路径}."""
    db_map = read_originals(album_id)
    cloud = _cloudspace_files(album_id) if len(db_map) < len(photo_ids) else {}
    found = {}
    for pid in photo_ids:
        p = db_map.get(pid)
        if p and os.path.isfile(p):
            found[pid] = p
        elif pid in cloud:
            found[pid] = cloud[pid]
    return found
