# -*- coding: utf-8 -*-
"""v3: 租户会话自动补登录 — 软件重启后不再被服务器 401「摄影师未登录」."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = tempfile.mkdtemp(prefix='pixphoto-ts-')
os.environ['PIXCAKE_PHOTOGRAPHER_DATA'] = DATA

import config  # noqa: E402
import remote  # noqa: E402
import app     # noqa: E402

PASS = []


def check(name, cond, detail=''):
    PASS.append((name, cond))
    print(('  PASS  ' if cond else '  FAIL  ') + name + (('  | ' + detail) if detail else ''))


# 1. 无卡密 → 明确提示, 不发起登录
config.set_many(license=None)
check('无卡密返回提示', app._ensure_tenant_session() == '尚未激活卡密')

# 2. 有卡密 + 登录成功 → 返回 None, 且只登录一次
calls = []
orig = remote.Remote.tenant_login


def fake_login(self, key):
    calls.append(key)
    self._cookies['g'] = 'p_xxx.hmac'


remote.Remote.tenant_login = fake_login
config.set_many(license={'key': 'TEST-0000'})
r1 = app._ensure_tenant_session()
check('首次自动登录成功', r1 is None, str(r1))
check('用卡密登录', calls == ['TEST-0000'], str(calls))
r2 = app._ensure_tenant_session()
check('已有会话不重复登录', r2 is None and len(calls) == 1, str(calls))

# 3. 登录失败 → 返回服务器真实原因 (不再笼统 无法连接服务器)
config.set_many(license={'key': 'TEST-1111'})
app._remote = None  # 模拟重启: 内存会话丢失


def fail_login(self, key):
    raise remote.RemoteError(403, '卡密已绑定其他设备')


remote.Remote.tenant_login = fail_login
r3 = app._ensure_tenant_session()
check('登录失败返回真实原因', r3 == '卡密已绑定其他设备', str(r3))

remote.Remote.tenant_login = orig
print('\n==== 结果: %d/%d PASS ====' % (sum(1 for _, c in PASS if c), len(PASS)))
if not all(c for _, c in PASS):
    sys.exit(1)
