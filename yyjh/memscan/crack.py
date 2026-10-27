# -*- coding: utf-8 -*-
"""候选密钥采集 + 爆破（带进度与预计时间）。

候选从哪里来（可靠性从高到低）:
  1. 游戏进程内存里的**可打印串**（游戏要解密，钥匙必然在内存里）
  2. 内存里的 16 字节二进制窗口
  3. 已知文件（HAR / 游戏资源 / 上次跑出来的 heap_keys.bin）里的可打印串

判据：core.oracle 的 XXTEA 长度字预言机（误报率 ~1e-9）。
"""
import os
import re
import time

from .. import ui
from ..config import paths
from ..core import codecs, oracle, xxtea
from . import memory

PRINTABLE_RE = re.compile(rb'[\x20-\x7e]{6,80}')


def _variants(s):
    return {s, s[:16], s[-16:], s[:24], s[:32]}


# ───────────────────────── 候选采集 ─────────────────────────
def harvest_strings(data):
    """从二进制块里提取候选钥匙（可打印串及其变体）。"""
    out = set()
    for m in PRINTABLE_RE.finditer(data):
        for v in _variants(m.group()):
            if len(v) >= 6:
                out.add(v[:16].ljust(16, b'\x00'))
    return out


def harvest_windows(data, size=16, step=1):
    return {data[i:i + size] for i in range(0, max(0, len(data) - size + 1), step)}


def candidates_from_files(paths_, max_mb=200, on_progress=None):
    """从若干文件/目录里捞候选。"""
    out = set()
    files = []
    for p in paths_:
        if os.path.isfile(p):
            files.append(p)
        elif os.path.isdir(p):
            for root, _, fns in os.walk(p):
                for fn in fns:
                    files.append(os.path.join(root, fn))
    if on_progress:
        on_progress(len(files), 0)
    for i, f in enumerate(files, 1):
        try:
            if os.path.getsize(f) <= max_mb * 1048576:
                with open(f, 'rb') as fh:
                    out |= harvest_strings(fh.read())
        except OSError:
            pass
        if on_progress and i % 50 == 0:
            on_progress(len(files), i)
    if on_progress:
        on_progress(len(files), len(files))
    return out


def save_candidates(path, cands):
    """存成定长 16 字节 .bin（可复用）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'wb') as f:
        for c in cands:
            f.write(bytes(c)[:16].ljust(16, b'\x00'))
    return path


def load_candidates(path):
    if not os.path.exists(path):
        return set()
    data = open(path, 'rb').read()
    return {data[i:i + 16] for i in range(0, len(data) - 15, 16)}


def ciphertext_from_content(content):
    """从 dat.db 的 content 里取出"外层密文"（新版）。"""
    from ..config.constants import SAVE_MARKER_LEN
    from ..game import savefmt
    if not content:
        raise ValueError('content 为空')
    fmt = savefmt.detect_format(content)
    if fmt == savefmt.NEW:
        ct = codecs.b64d(content[SAVE_MARKER_LEN:])
        if ct is None:
            raise ValueError('content 外层不是合法 base64')
        return ct
    if fmt == savefmt.OLD:
        raise ValueError('这是初版存档，没有外层加密，不需要爆破外层密钥')
    raise ValueError('无法识别的存档格式')


# ───────────────────────── 爆破 ─────────────────────────
def crack_ciphertext(ct, candidates, workers=None, label='爆破密钥'):
    """对一段密文爆破钥匙。返回 [(key, length_word, head)]，带进度条。"""
    cands = oracle.normalize_candidates(candidates)
    n = len(ct) // 4
    lo, hi = xxtea.length_window(n)
    if not cands:
        ui.warn('没有候选密钥')
        return []

    ui.info(f'  密文 {len(ct):,} B　合法长度字区间 [{lo}, {hi}]　候选 {len(cands):,} 条')
    # 阈值与 core.oracle.crack 的 threshold 保持一致：过了它才会真的开多进程
    if len(cands) >= 20000 and workers != 1:
        ui.info('  注意：候选较多，会开多进程把 CPU 吃满 —— 电脑可能发卡、风扇变响，')
        ui.info('        属正常现象，跑完就恢复。嫌吵可以加 --workers 4 限制核数。')
    hit_n = 0
    with ui.progress(len(cands), label, ' 条') as pb:
        t0 = time.time()

        def _tick(done, total, elapsed):
            pb.update(done, f'命中 {hit_n}')

        hits = oracle.crack(ct, cands, workers=workers, progress=_tick) or []
        hit_n = len(hits)
    ui.info(f'  用时 {ui.fmt_duration(time.time() - t0)}')
    for k, lw, head in hits:
        ui.ok(f'命中密钥 {k!r}　长度字 {lw}　明文头 {head[:32]!r}')
    return hits


def crack_outer_key(adb, pid, content, anon_only=True, min_kb=64, max_mb=64,
                    limit_mb=None, workers=None, cache=None, progress=None):
    """完整流程：扫内存 -> 采集候选 -> 爆破 dat.db 外层密钥。

    返回 [(key, length_word, head)]。全过程有进度条与预计时间。
    """
    ct = ciphertext_from_content(content)
    n = len(ct) // 4
    lo, hi = xxtea.length_window(n)
    ui.step('目标', f'外层密文 {len(ct):,} B　n={n}　合法长度字区间 [{lo}, {hi}]')

    if cache is None:
        cache = os.path.join(paths.CACHE_DIR, 'heap_keys.bin')

    ui.info('  读取进程内存映射 ...')
    maps = memory.read_maps(adb, pid)
    regs = memory.regions(maps, anon_only=anon_only, min_kb=min_kb)
    total = sum(r.size for r in regs)
    ui.step('内存区域', f'{len(regs)} 个，共 {total / 1048576:.0f} MB')

    cands = set(load_candidates(cache))
    if cands:
        ui.step('缓存候选', f'{cache} 里已有 {len(cands):,} 条')

    ui.info()
    ui.info('  注意：进度条上的“预计剩余”很不准 —— 经常显示成一小时以上，')
    ui.info('        实际一般几分钟就扫完，看见候选数在涨就是正常的。')

    # —— 扫描内存（进度按"已扫字节数"）——
    scanned = 0
    t0 = time.time()
    with ui.progress(total, '扫描内存', ' B') as pb:
        for r, data in memory.iter_regions(adb, pid, regs, max_mb=max_mb):
            cands |= harvest_strings(data)
            scanned += len(data)
            pb.update(scanned, f'候选 {len(cands):,}')
            if limit_mb and scanned >= limit_mb * 1048576:
                ui.step('扫描', f'达到 --limit-mb {limit_mb}，提前停止')
                break
    ui.info(f'  已扫 {ui.fmt_size(scanned)}，用时 {ui.fmt_duration(time.time() - t0)}')

    if not cands:
        ui.warn('没采集到任何候选')
        return []
    save_candidates(cache, cands)
    ui.step('候选', f'{len(cands):,} 条（已缓存到 {cache}，下次可复用）')

    # —— 爆破 ——
    return crack_ciphertext(ct, cands, workers=workers, label='测试候选密钥')
