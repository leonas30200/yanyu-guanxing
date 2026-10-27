# -*- coding: utf-8 -*-
"""基础编解码：base64 / zlib / ROL3 / 循环 XOR。

游戏里这些"编码"和加密层层套：
    明文 JSON --zlib--> --XXTEA--> --base64--> --XXTEA--> --base64--> 落盘
"""
import base64
import binascii
import zlib


def b64d(s):
    """宽松 base64 解码（自动补 padding，忽略空白）。"""
    if isinstance(s, str):
        s = s.encode('ascii', 'ignore')
    s = bytes(c for c in s if c not in b' \t\r\n')
    if len(s) % 4:
        s += b'=' * (4 - len(s) % 4)
    try:
        return base64.b64decode(s, validate=False)
    except (binascii.Error, ValueError):
        return None


def b64e(b):
    return base64.b64encode(b).decode('ascii')


def zlib_d(b):
    try:
        return zlib.decompress(b)
    except zlib.error:
        return None


def zlib_c(b, level=6):
    return zlib.compress(b, level)


def rol3(b):
    """ResourceGroup::decryptPlistFile 的变换: ((x>>5)+(x<<3)) & 0xFF"""
    return bytes(((c >> 5) + (c << 3)) & 0xFF for c in b)


def unrol3(b):
    """rol3 的逆: ((x<<5)+(x>>3)) & 0xFF"""
    return bytes(((c << 5) + (c >> 3)) & 0xFF for c in b)


def xor_cycle(data, key):
    """循环 XOR —— .whale / res_encrypt 的模块级混淆。"""
    if isinstance(key, str):
        key = key.encode('ascii')
    if not key:
        return data
    return bytes(c ^ key[i % len(key)] for i, c in enumerate(data))


def is_printable_run(b, minlen=4):
    return len(b) >= minlen


def looks_like_zlib(b):
    return len(b) >= 2 and b[0] == 0x78 and b[1] in (0x01, 0x5E, 0x9C, 0xDA)


def looks_like_json(b):
    s = b.lstrip()[:1]
    return s in (b'{', b'[')


def looks_like_lua_bytecode(b):
    from ..config.constants import LUA_MAGIC
    return b[:len(LUA_MAGIC)] == LUA_MAGIC


def looks_like_sqlite(b):
    return b[:16] == b'SQLite format 3\x00'


def printable_strings(data, minlen=4, limit=None):
    """等价于 strings(1)，用于在二进制里捞常量。"""
    import re
    out = []
    for m in re.finditer(rb'[\x20-\x7e]{%d,}' % minlen, data):
        out.append(m.group().decode('latin-1'))
        if limit and len(out) >= limit:
            break
    return out
