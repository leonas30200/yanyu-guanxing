# -*- coding: utf-8 -*-
"""XXTEA —— 游戏全套加密的基础。

游戏用的 XXTEA 有两个"怪癖"，必须照做：

1. **明文尾部有一个 4 字节长度字**
   解密缓冲区 = 明文(补零到 4 字节对齐) + u32(原始长度)
   -> 所以 `decrypt()` 才能把补的零切掉

2. **长度字是极好的"钥匙对不对"预言机**
   n 个字时原始长度必须满足 `n*4-7 <= len <= n*4-4`
   随机钥匙误报概率 4/2^32 ≈ 1e-9 —— 所有密钥爆破都靠它。
"""
import struct

from ..config.constants import (MASK32, XXTEA_DELTA, XXTEA_LEN_HEAD,
                                XXTEA_LEN_TAIL)

# C 扩展加速（可选）。它和游戏语义一致：decrypt(..., padding=False) 返回**未裁剪**缓冲
try:
    import xxtea as _cxxtea
    HAS_C_XXTEA = True
except Exception:                                     # pragma: no cover
    _cxxtea = None
    HAS_C_XXTEA = False


# ─────────────────────────── 工具 ───────────────────────────
def norm_key(key):
    """key -> 16 字节（不足补 0，超出截断）。游戏就是这么做的。"""
    if isinstance(key, str):
        key = key.encode('utf-8')
    return key[:16].ljust(16, b'\x00')


def bytes_to_words(data):
    """小端 4 字节 -> uint32 列表；末尾不足 4 字节补 0。"""
    pad = (-len(data)) % 4
    if pad:
        data = data + b'\x00' * pad
    return list(struct.unpack('<%dI' % (len(data) // 4), data))


def words_to_bytes(words):
    return struct.pack('<%dI' % len(words), *[w & MASK32 for w in words])


def length_window(n):
    """n 个字时，合法长度字区间。"""
    return n * 4 - XXTEA_LEN_TAIL, n * 4 - XXTEA_LEN_HEAD


def length_ok(n, lw):
    lo, hi = length_window(n)
    return lo <= lw <= hi


def tail_length(raw):
    """从未裁剪的明文缓冲里读出长度字。"""
    if len(raw) < 4:
        return -1
    return struct.unpack_from('<I', raw, len(raw) - 4)[0]


# ─────────────────────────── 解密 ───────────────────────────
def _decrypt_py(data, key):
    n = len(data) // 4
    if n < 2:
        return None
    v = list(struct.unpack('<%dI' % n, data[:n * 4]))
    k = bytes_to_words(norm_key(key))
    rounds = 6 + 52 // n
    total = (rounds * XXTEA_DELTA) & MASK32
    y = v[0]
    while total:
        e = (total >> 2) & 3
        for p in range(n - 1, 0, -1):
            z = v[p - 1]
            mx = (((z >> 5) ^ ((y << 2) & MASK32)) +
                  ((y >> 3) ^ ((z << 4) & MASK32))) & MASK32
            mx = (mx ^ ((total ^ y) + (k[(p & 3) ^ e] ^ z))) & MASK32
            v[p] = (v[p] - mx) & MASK32
            y = v[p]
        z = v[n - 1]
        mx = (((z >> 5) ^ ((y << 2) & MASK32)) +
              ((y >> 3) ^ ((z << 4) & MASK32))) & MASK32
        mx = (mx ^ ((total ^ y) + (k[e] ^ z))) & MASK32
        v[0] = (v[0] - mx) & MASK32
        y = v[0]
        total = (total - XXTEA_DELTA) & MASK32
    return words_to_bytes(v)


def decrypt_raw(data, key):
    """解密但**不裁剪**（保留尾部长度字）。失败返回 None。"""
    if not data:
        return None
    n = len(data) // 4
    if n < 2:
        return None
    data = data[:n * 4]
    if HAS_C_XXTEA:
        try:
            return _cxxtea.decrypt(data, norm_key(key), padding=False)
        except Exception:
            pass
    return _decrypt_py(data, key)


def decrypt(data, key):
    """解密并裁掉长度字/补零。钥匙不对返回 None。"""
    raw = decrypt_raw(data, key)
    if raw is None:
        return None
    n = len(raw) // 4
    lw = tail_length(raw)
    if lw < 0 or lw > (n - 1) * 4:
        return None
    return raw[:lw]


def checks(data, key):
    """返回 (是否合法, 长度字) —— 爆破时用这个，避免反复复制明文。"""
    raw = decrypt_raw(data, key)
    if raw is None:
        return False, -1
    lw = tail_length(raw)
    return length_ok(len(raw) // 4, lw), lw


# ─────────────────────────── 加密 ───────────────────────────
def _encrypt_py(data, key):
    """标准 XXTEA 加密部分（Wheeler & Needham）。

    注意：加密部分**携带的是 z**，解密部分**携带的是 y** —— 这是两者的不对称点，
    写错就会"自己解不开自己"（本文件早期版本就踩过这个坑）。
    """
    n = len(data) // 4
    if n < 2:
        return None
    v = list(struct.unpack('<%dI' % n, data[:n * 4]))
    k = bytes_to_words(norm_key(key))
    rounds = 6 + 52 // n
    total = 0
    z = v[n - 1]
    for _ in range(rounds):
        total = (total + XXTEA_DELTA) & MASK32
        e = (total >> 2) & 3
        for p in range(n - 1):
            y = v[p + 1]
            mx = (((z >> 5) ^ ((y << 2) & MASK32)) +
                  ((y >> 3) ^ ((z << 4) & MASK32))) & MASK32
            mx = (mx ^ ((total ^ y) + (k[(p & 3) ^ e] ^ z))) & MASK32
            v[p] = (v[p] + mx) & MASK32
            z = v[p]                       # 携带更新后的 v[p]
        y = v[0]
        mx = (((z >> 5) ^ ((y << 2) & MASK32)) +
              ((y >> 3) ^ ((z << 4) & MASK32))) & MASK32
        mx = (mx ^ ((total ^ y) + (k[((n - 1) & 3) ^ e] ^ z))) & MASK32
        v[n - 1] = (v[n - 1] + mx) & MASK32
        z = v[n - 1]
    return words_to_bytes(v)


def encrypt(data, key):
    """加密（自动补零 + 附长度字），与 `decrypt` 严格互逆。"""
    if not data:
        return b''
    body = data + b'\x00' * ((-len(data)) % 4)
    buf = body + struct.pack('<I', len(data))
    if HAS_C_XXTEA:
        try:
            out = _cxxtea.encrypt(buf, norm_key(key), padding=False)
            if out and len(out) == len(buf):
                return out
        except Exception:
            pass
    return _encrypt_py(buf, key)


def self_test():
    """自检：加解密往返 + 预言机。启动时可用 --self-test 调用。"""
    import os
    k = 'LanJingNo1'
    for n in (1, 2, 3, 5, 16, 100, 1000):
        pt = os.urandom(n)
        ct = encrypt(pt, k)
        assert len(ct) % 4 == 0, '密文未对齐'
        ok, lw = checks(ct, k)
        assert ok and lw == n, f'长度字错: {lw} != {n}'
        assert decrypt(ct, k) == pt, '往返失败'
    # 错钥匙必须被预言机拒掉
    ct = encrypt(b'hello world' * 7, k)
    bad = decrypt(ct, 'wrongkey12345678')
    assert bad is None or bad != b'hello world' * 7
    return True
