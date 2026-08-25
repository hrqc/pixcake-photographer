# -*- coding: utf-8 -*-
"""扫描像素蛋糕工作区 (与 pixcake-gallery/scanner.py 同构, 自包含拷贝避免打包依赖).
结构: <ws>/<user>/<album>/thumbnail_cache/<thumb>/c_p_f_e/<ID>_3000 / <ID>_375
                                        <thumb>/c_p_f_o/<ID>_3000 / <ID>_375
"""
import os
import re

_thumb_num_re = re.compile(r'thumbnail_(\d+)_')


def _thumb_sort_key(td):
    m = _thumb_num_re.match(td)
    return int(m.group(1)) if m else 1 << 30


def find_albums(ws_root):
    """返回 [{id, user, album_id, path}], 按 (user, album) 排序."""
    albums = []
    if not ws_root or not os.path.isdir(ws_root):
        return albums
    for user in sorted(os.listdir(ws_root)):
        up = os.path.join(ws_root, user)
        if not os.path.isdir(up):
            continue
        for alb in sorted(os.listdir(up)):
            ap = os.path.join(up, alb)
            if os.path.isdir(os.path.join(ap, 'thumbnail_cache')):
                albums.append({'id': '%s_%s' % (user, alb), 'user': user,
                               'album_id': alb, 'path': ap})
    return albums


def scan_project_photos(album_path):
    """返回每张照片的精修/原图 3000/375 路径与修改时间 (与服务器扫描口径一致)."""
    photos = []
    tc = os.path.join(album_path, 'thumbnail_cache')
    if not os.path.isdir(tc):
        return photos
    for td in sorted(os.listdir(tc), key=_thumb_sort_key):
        cfe = os.path.join(tc, td, 'c_p_f_e')
        cfo = os.path.join(tc, td, 'c_p_f_o')
        if not os.path.isdir(cfe):
            continue
        for f in os.listdir(cfe):
            if not f.endswith('_3000'):
                continue
            base = f[:-5]
            if base.endswith('.json') or base.endswith('_ext') or base == 'info':
                continue
            photos.append({
                'photo_id': base, 'thumb_dir': td,
                'src_3000': os.path.join(cfe, f),
                'src_375': os.path.join(cfe, base + '_375'),
                'src_o_3000': os.path.join(cfo, f) if os.path.isdir(cfo) else None,
                'src_o_375': os.path.join(cfo, base + '_375') if os.path.isdir(cfo) else None,
            })
    return photos


def album_upload_files(album_path):
    """相册需要上传的文件清单 (精修/原图的 3000+375, 逐张).
    返回 [{rel_path, abs_path, size, mtime}], rel_path 相对租户上传根.
    结构保持与服务器扫描完全一致: <user>/<album>/thumbnail_cache/..."""
    rel_user = os.path.basename(os.path.dirname(album_path))
    rel_album = os.path.basename(album_path)
    files = []
    tc = os.path.join(album_path, 'thumbnail_cache')
    if not os.path.isdir(tc):
        return files
    for td in sorted(os.listdir(tc), key=_thumb_sort_key):
        for sub in ('c_p_f_e', 'c_p_f_o'):
            d = os.path.join(tc, td, sub)
            if not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                if not (f.endswith('_3000') or f.endswith('_375')):
                    continue
                p = os.path.join(d, f)
                if not os.path.isfile(p):
                    continue
                st = os.stat(p)
                files.append({
                    'rel_path': '%s/%s/thumbnail_cache/%s/%s/%s' % (rel_user, rel_album, td, sub, f),
                    'abs_path': p,
                    'size': st.st_size,
                    'mtime': int(st.st_mtime_ns),
                })
    return files
