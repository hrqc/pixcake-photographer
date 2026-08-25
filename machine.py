# -*- coding: utf-8 -*-
"""机器码: MAC + 主机名 hash, 与授权服务器 license.machine_fp 完全一致.
客户端绑定在哪台电脑, 卡密就绑在哪台."""
import hashlib
import socket
import uuid


def machine_fp():
    raw = '%s|%s|pixcake' % (uuid.getnode(), socket.gethostname())
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]
