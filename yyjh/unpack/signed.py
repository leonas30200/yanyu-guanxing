# -*- coding: utf-8 -*-
""""签名 + 循环XOR + XXTEA" 这一族的通用解密。

`.whale` 里的每个模块 和 `res_encrypt/` 下的资源文件，用的是同一套：
    明文 = XXTEA_dec( xor_cycle(密文, 签名对应的XOR密钥), "LanJingNo1" )

唯一区别：`.whale` 模块的密文长度是按"下一个签名位置"自然切出来的，
有时会多出 4/8 字节，需要用长度字预言机"收缩搜索"找到真实长度。
"""
from ..config.constants import (CACHE_XXTEA_KEY, RES_SIGN_XOR,
                                WHALE_MAX_SHRINK_WORDS)
from ..core import codecs, xxtea


def sign_of(data):
    """返回数据开头的签名（b'BIGDA' / b'KEIIY'），没有则 None。"""
    for sign in RES_SIGN_XOR:
        if data[:len(sign)] == sign:
            return sign
    return None


def decrypt_blob(ct, sign, shrink_words=0, key=CACHE_XXTEA_KEY):
    """解密一段（不含签名的）密文。

    shrink_words: 允许在尾部砍掉多少个 4 字节来试（.whale 模块用得上）
    返回明文 bytes，失败返回 None。
    """
    xk = RES_SIGN_XOR.get(sign)
    if not xk:
        return None
    for k in range(shrink_words + 1):
        L = len(ct) - 4 * k
        if L <= 0 or L % 4:
            continue
        xored = codecs.xor_cycle(ct[:L], xk)
        raw = xxtea.decrypt_raw(xored, key)
        if raw is None:
            continue
        n = len(raw) // 4
        lw = xxtea.tail_length(raw)
        if xxtea.length_ok(n, lw):
            return raw[:lw]
    return None


def decrypt_file(data, shrink_words=0, key=CACHE_XXTEA_KEY):
    """整文件解密（自带签名）。返回 (明文, 签名) 或 (None, 签名)。"""
    sign = sign_of(data)
    if sign is None:
        return None, None
    return decrypt_blob(data[len(sign):], sign, shrink_words, key), sign


def encrypt_blob(pt, sign, key=CACHE_XXTEA_KEY):
    """逆向：明文 -> 密文（用于把改过的资源塞回去）。"""
    xk = RES_SIGN_XOR.get(sign)
    if not xk:
        raise ValueError(f'未知签名 {sign!r}')
    return codecs.xor_cycle(xxtea.encrypt(pt, key), xk)


def decrypt_tree(target, outdir=None, keep_rel=True, shrink_words=0,
                 on_progress=None):
    """批量解密一个目录/文件。返回 (成功数, 跳过数, 失败数, 命中列表)。

    命中列表 = [(路径, [命中的关键词])]，关键词见 `INTERESTING`。
    """
    import os
    from .binstrings import INTERESTING

    files = []
    if os.path.isfile(target):
        files = [target]
    else:
        for root, _, fns in os.walk(target):
            for fn in fns:
                files.append(os.path.join(root, fn))

    ok = skip = fail = 0
    hits = []
    for i, p in enumerate(files):
        try:
            data = open(p, 'rb').read()
        except OSError:
            fail += 1
            continue
        if sign_of(data) is None:
            skip += 1
        else:
            pt, sign = decrypt_file(data, shrink_words)
            if pt is None:
                fail += 1
            else:
                ok += 1
                found = [w for w in INTERESTING if w.encode() in pt]
                if found:
                    hits.append((p, found))
                if outdir:
                    rel = os.path.relpath(p, target) if keep_rel else os.path.basename(p)
                    o = os.path.join(outdir, rel)
                    os.makedirs(os.path.dirname(o), exist_ok=True)
                    with open(o, 'wb') as f:
                        f.write(pt)
        if on_progress and i % 200 == 0:
            on_progress(i, len(files), ok, fail)
    if on_progress:
        on_progress(len(files), len(files), ok, fail)
    return ok, skip, fail, hits
