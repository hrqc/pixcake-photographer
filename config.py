# -*- coding: utf-8 -*-
"""本地配置与授权文件: 用户目录/.pixcake-photographer/config.json.
字段: server_url 服务器地址, workspace 像素蛋糕工作区, license 授权信息.
另维护 machine_workspace.json: 按机器码记录工作区位置, 即使 config.json 被
重置/损坏, 启动时仍能按机器码自动恢复相册位置 (用户诉求「按机器码自动记忆」)."""
import json
import os
import threading
import time

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


# ---------------- 按机器码记忆工作区 (machine_workspace.json) ----------------
# 作用: config.json 是用户级单份, 可能被重置/误改; 机器码记忆独立存放,
#       每台电脑首次设置后, 之后每次打开都能按机器码自动恢复相册位置.
# 结构: {<machine_fp>: {'workspace': <绝对路径>, 'saved_at': <epoch>}}

def _machine_workspace_path():
    return os.path.join(data_dir(), 'machine_workspace.json')


def load_machine_workspace(machine):
    """按机器码读回曾记录的工作区; 无记录或该机器无记忆返回 ''."""
    if not machine:
        return ''
    try:
        with open(_machine_workspace_path(), encoding='utf-8') as f:
            rec = json.load(f)
        ws = (rec.get(machine) or {}).get('workspace') or ''
        return ws.strip() if isinstance(ws, str) else ''
    except Exception:
        return ''


def save_machine_workspace(machine, ws):
    """按机器码记录工作区 (含时间戳). 失败静默 (仅记忆增强, 不阻断主流程)."""
    if not machine or not ws:
        return
    with _lock:
        try:
            with open(_machine_workspace_path(), encoding='utf-8') as f:
                rec = json.load(f)
        except Exception:
            rec = {}
        if not isinstance(rec, dict):
            rec = {}
        rec[machine] = {'workspace': ws.strip(), 'saved_at': int(time.time())}
        try:
            os.makedirs(data_dir(), exist_ok=True)
            tmp = _machine_workspace_path() + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(rec, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _machine_workspace_path())
        except Exception:
            pass
