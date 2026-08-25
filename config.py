# -*- coding: utf-8 -*-
"""本地配置与授权文件: 用户目录/.pixcake-photographer/config.json.
字段: server_url 服务器地址, workspace 像素蛋糕工作区, license 授权信息."""
import json
import os
import threading

APP_NAME = '.pixcake-photographer'
DEFAULT_SERVER = 'https://hrqc105.icu'

_OVERRIDE_DIR = None  # 测试/打包时可用环境变量指向数据目录

# 读写锁: 后台 verify 线程每 45s 写 license 与前端设置 workspace 并发,
# 不锁会 read-modify-write 互相覆盖 (workspace 偶发被冲掉 = "不稳定")
_lock = threading.RLock()


def data_dir():
    if _OVERRIDE_DIR:
        return _OVERRIDE_DIR
    override = os.environ.get('PIXCAKE_PHOTOGRAPHER_DATA')
    if override:
        return override
    return os.path.join(os.path.expanduser('~'), APP_NAME)


def _path():
    return os.path.join(data_dir(), 'config.json')


def load():
    with _lock:
        try:
            with open(_path(), encoding='utf-8') as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                return cfg
        except Exception:
            pass
        return {}


def save(cfg):
    with _lock:
        os.makedirs(data_dir(), exist_ok=True)
        tmp = _path() + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _path())


def get(key, default=None):
    with _lock:
        return load().get(key, default)


def set_many(**kw):
    with _lock:
        cfg = load()
        cfg.update(kw)
        save(cfg)
        return cfg
