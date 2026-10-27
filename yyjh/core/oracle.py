# -*- coding: utf-8 -*-
"""密钥爆破引擎 —— 靠 XXTEA 的"长度字预言机"判定候选钥匙。

核心思想（也是本项目所有成功爆破的基础）:
    正确的钥匙 -> 解出的末 4 字节是一个合理长度（落在 [n*4-7, n*4-4]）
    错误的钥匙 -> 末 4 字节是随机数
    随机误报概率 4 / 2^32 ≈ 1e-9  ==> 命中即可信，不会误报

10 万级候选用单进程，百万级自动多进程。
"""
import multiprocessing as mp
import time

from . import xxtea

# 多进程 worker 的全局状态（Windows spawn 要求 worker 是模块级函数）
_CT = None
_LO = 0
_HI = 0


def _init_worker(ct, lo, hi):
    global _CT, _LO, _HI
    _CT, _LO, _HI = ct, lo, hi


def _check(key):
    raw = xxtea.decrypt_raw(_CT, key)
    if raw is None:
        return None
    lw = xxtea.tail_length(raw)
    if _LO <= lw <= _HI:
        return (key, lw, raw[:64])
    return None


def crack_single(ct, candidates):
    """单进程爆破。candidates 为 16 字节或 str 的可迭代对象。"""
    n = len(ct) // 4
    lo, hi = xxtea.length_window(n)
    hits = []
    for k in candidates:
        raw = xxtea.decrypt_raw(ct, k)
        if raw is None:
            continue
        lw = xxtea.tail_length(raw)
        if lo <= lw <= hi:
            hits.append((bytes(k) if isinstance(k, (bytes, bytearray)) else str(k).encode(),
                         lw, raw[:64]))
    return hits


def crack(ct, candidates, workers=None, threshold=20000, progress=None):
    """爆破。返回 [(key_bytes, length_word, plaintext_head), ...]

    workers  : 进程数，None = CPU 核数
    threshold: 候选数超过多少才用多进程
    progress : 可选回调 progress(done, total, elapsed)
    """
    candidates = list(candidates)
    n = len(ct) // 4
    lo, hi = xxtea.length_window(n)
    if not candidates:
        return []
    if len(candidates) < threshold or workers == 1:
        t0 = time.time()
        hits = crack_single(ct, candidates)
        if progress:
            progress(len(candidates), len(candidates), time.time() - t0)
        return hits

    nproc = workers or max(2, (mp.cpu_count() or 4))
    chunk = max(64, len(candidates) // (nproc * 16))
    t0 = time.time()
    hits = []
    with mp.Pool(nproc, initializer=_init_worker, initargs=(ct, lo, hi)) as pool:
        for i, r in enumerate(pool.imap_unordered(_check, candidates, chunksize=chunk), 1):
            if r:
                hits.append(r)
            if progress and i % max(1, len(candidates) // 20) == 0:
                progress(i, len(candidates), time.time() - t0)
    if progress:
        progress(len(candidates), len(candidates), time.time() - t0)
    return hits


# ─────────────────────── 候选钥匙生成 ───────────────────────
def windows_from_bytes(data, size=16, step=1):
    """把一段二进制里所有 size 字节窗口当候选（内存镜像用）。"""
    out = []
    for i in range(0, max(0, len(data) - size + 1), step):
        out.append(data[i:i + size])
    return out


def normalize_candidates(items):
    """统一成 16 字节、去重，保留顺序。"""
    seen = set()
    out = []
    for c in items:
        if isinstance(c, str):
            c = c.encode('utf-8', 'ignore')
        c = c[:16].ljust(16, b'\x00')
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out
