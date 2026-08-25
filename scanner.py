# -*- coding: utf-8 -*-
"""扫描像素蛋糕工作区 (与 pixcake-gallery/scanner.py 同构, 自包含拷贝避免打包依赖).
结构: <ws>/<user>/<album>/thumbnail_cache/<thumb>/c_p_f_e/<文件名>_<长边像素>
                                        <thumb>/c_p_f_o/<文件名>_<长边像素>
"""
import os
import re

_thumb_num_re = re.compile(r'thumbnail_(\d+)_')
# 像素蛋糕文件命名: <文件名>_<长边像素> (相机 3000/手机 2000~4000/微信 1584 等)
_dim_re = re.compile(r'_(\d+)$')


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


def _dim_files(folder):
    """folder 里带尺寸后缀的文件 → {base: [(dim, name)]}.
    按「末尾 _数字」识别 (3000/375/1584/396...), 自然排除 .json/_ext/info."""
    result = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return result
    for f in names:
        m = _dim_re.search(f)
        if not m:
            continue
        base = f[:m.start()]
        result.setdefault(base, []).append((int(m.group(1)), f))
    return result


def scan_project_photos(album_path):
    """返回每张照片的精修/原图 全尺寸/缩略图 路径 (与服务器扫描口径一致).
    像素蛋糕按「文件名_长边像素」命名 (佳能3000/索尼4000/手机2000/微信1584 等),
    不硬编码像素: 尺寸最大 = 全尺寸, 尺寸最小 = 缩略图."""
    photos = []
    tc = os.path.join(album_path, 'thumbnail_cache')
    if not os.path.isdir(tc):
        return photos
    for td in sorted(os.listdir(tc), key=_thumb_sort_key):
        cfe = os.path.join(tc, td, 'c_p_f_e')
        cfo = os.path.join(tc, td, 'c_p_f_o')
        if not os.path.isdir(cfe):
            continue
        fe = _dim_files(cfe)
        fo = _dim_files(cfo)
        for base, sizes in fe.items():
            sizes.sort()
            src_full = os.path.join(cfe, sizes[-1][1])
            src_thumb = os.path.join(cfe, sizes[0][1]) if len(sizes) > 1 else None
            o_sizes = sorted(fo.get(base) or [])
            src_o_full = os.path.join(cfo, o_sizes[-1][1]) if o_sizes else None
            src_o_thumb = os.path.join(cfo, o_sizes[0][1]) if len(o_sizes) > 1 else None
            photos.append({
                'photo_id': base, 'thumb_dir': td,
                'src_3000': src_full,
                'src_375': src_thumb,
                'src_o_3000': src_o_full,
                'src_o_375': src_o_thumb,
            })
    return photos


def album_upload_files(album_path):
    """相册需要上传的文件清单 (精修/原图的所有尺寸文件, 逐张).
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
                if not _dim_re.search(f):   # 只要带尺寸后缀的 FXIP 图片 (排除 json/_ext/info)
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
