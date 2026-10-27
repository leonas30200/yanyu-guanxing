# -*- coding: utf-8 -*-
"""协议层密钥推导（对应 APK 里的 ProtocolEnvelope）。

抓包流程:
    key0    = acc[:5] + "eakec" + "000000"          ← 半固定，只跟账号有关
    innerK  = XXTEA_dec(base64(k响应头), key0)       ← 服务器下发的"种子"，1~128 字节
    realKey = acc[:5] + "eakec" + innerK[:6]         ← 真正用来解开 upload 请求体

设计意图: key0 藏在 APK 里，服务器只发一个用 key0 加密过的种子，
          这样即使被抓包，不知道 key0 也拿不到 realKey。
"""
from ..config.constants import (PROTOCOL_INNER_MAX, PROTOCOL_INNER_MIN,
                                PROTOCOL_KEY_LEN, PROTOCOL_MIDDLE,
                                PROTOCOL_SEED_ZERO)
from ..core import codecs, xxtea


def protocol_key(account, seed):
    """ProtocolEnvelope.key(String account, byte[] seed)

    buf = 16 个 '0'
    buf[0:10]  = account[:5] + "eakec"
    buf[10:16] = seed[:6]
    """
    buf = bytearray(b'0' * PROTOCOL_KEY_LEN)
    prefix = (account[:5] + PROTOCOL_MIDDLE).encode('ascii', 'ignore')[:10]
    buf[:len(prefix)] = prefix
    if seed:
        take = min(6, len(seed))
        buf[10:10 + take] = seed[:take]
    return bytes(buf)


def key0(account):
    return protocol_key(account, b'').decode('ascii')


def derive(account, k_header):
    """返回 dict(key0, inner_k, real_key)。k_header 是响应头 k 的 base64 值。"""
    if not k_header:
        raise ValueError('缺少 k 响应头（需要从"打开游戏之前"开始抓包）')
    k0 = protocol_key(account, b'')
    raw = codecs.b64d(k_header)
    if raw is None:
        raise ValueError(f'k 头不是合法 base64: {k_header[:40]!r}')
    inner = xxtea.decrypt(raw, k0)
    if not inner or not (PROTOCOL_INNER_MIN <= len(inner) <= PROTOCOL_INNER_MAX):
        raise ValueError('k 头解密失败或种子长度非法（acc 取错了？）')
    return {
        'key0': k0.decode('ascii'),
        'inner_k': inner,
        'real_key': protocol_key(account, inner).decode('ascii'),
    }


def is_ios(account):
    """iOS 的 acc 是邮箱，安卓是 32 位十六进制。"""
    return '@' in (account or '')
