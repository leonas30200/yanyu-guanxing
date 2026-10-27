# -*- coding: utf-8 -*-
"""`.whale` 容器 —— 游戏把 Lua 源码打包成的自定义格式。

容器结构:
    [u32 data_end][u16 module_count][u16 version] | 模块区 | 目录(TOC)
    整个 8 字节之后的内容先做一次 ROL3 还原：
        body = rol3(file[8:])

模块定位:
    模块区里散布着 `BIGDA` / `KEIIY` 签名，**每个签名的位置就是一个模块的起点**，
    下一个签名之前的自然长度就是该模块密文长度上限（可能要收缩搜索）。

单模块解密:
    xor_cycle(ct, "JJyyt973" 或 "NanShuGo") -> XXTEA("LanJingNo1") -> Lua 字节码
"""
import os
import re
import struct

from ..config.constants import (LUA_MAGIC, RES_SIGN_XOR, WHALE_HEADER,
                                WHALE_MAX_SHRINK_WORDS)
from ..core import codecs
from . import signed


class WhaleFile:
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.raw = f.read()
        if len(self.raw) < 8:
            raise ValueError('文件太小，不是 .whale')
        self.declared_end, self.module_count, self.version = struct.unpack_from(
            WHALE_HEADER, self.raw, 0)
        self.body = codecs.rol3(self.raw[8:])
        self.data_end = min(self.declared_end, len(self.body))
        self.signatures = []
        for sg in RES_SIGN_XOR:
            for m in re.finditer(re.escape(sg), self.body):
                if m.start() < self.data_end:
                    self.signatures.append((m.start(), sg))
        self.signatures.sort()

    # ── 信息 ──
    def info(self):
        return {
            'file': os.path.basename(self.path),
            'size': len(self.raw),
            'declared_end': self.declared_end,
            'module_count': self.module_count,
            'version': self.version,
            'found_signatures': len(self.signatures),
            'first_signature': self.signatures[0][1].decode()
            if self.signatures else None,
            'magic_at_0': self.body[:8].hex(),
        }

    # ── 解包 ──
    def modules(self, max_shrink=WHALE_MAX_SHRINK_WORDS):
        """逐个产出 (index, sign, 明文)。失败的模块跳过并计数。"""
        sigs = self.signatures
        failed = 0
        for i, (pos, sg) in enumerate(sigs):
            nxt = sigs[i + 1][0] if i + 1 < len(sigs) else self.data_end
            natural = nxt - pos - len(sg)
            if natural <= 0:
                failed += 1
                continue
            ct = self.body[pos + len(sg): pos + len(sg) + natural]
            pt = signed.decrypt_blob(ct, sg, shrink_words=max_shrink)
            if pt is None:
                failed += 1
                continue
            yield i, sg, pt
        self.failed = failed

    def dump(self, outdir, prefix=None, max_shrink=WHALE_MAX_SHRINK_WORDS):
        """解包并写盘。返回 (成功数, 签名总数, 失败的数)。"""
        os.makedirs(outdir, exist_ok=True)
        prefix = prefix or os.path.basename(self.path)[:-len('.whale')]
        ok = 0
        for i, sg, pt in self.modules(max_shrink):
            out = os.path.join(outdir, f'{prefix}.{i:03d}{_ext(pt)}')
            with open(out, 'wb') as f:
                f.write(pt)
            ok += 1
        return ok, len(self.signatures), getattr(self, 'failed', 0)

    def strings(self, minlen=4, max_shrink=WHALE_MAX_SHRINK_WORDS):
        out = []
        for _, _, pt in self.modules(max_shrink):
            out += codecs.printable_strings(pt, minlen)
        return out


def _ext(pt):
    if pt[:len(LUA_MAGIC)] == LUA_MAGIC:
        return '.luac'
    return '.bin'


def is_whale(path):
    try:
        with open(path, 'rb') as f:
            head = f.read(8)
    except OSError:
        return False
    if len(head) < 8:
        return False
    ver = struct.unpack_from('<H', head, 6)[0]
    if ver > 4096:
        return False
    body = codecs.rol3(head)
    return b'BIGDA' in body or b'KEIIY' in body


def dump_dir(dirpath, outdir, max_shrink=WHALE_MAX_SHRINK_WORDS, pattern='.whale'):
    os.makedirs(outdir, exist_ok=True)
    all_str = []
    total_ok = total_mod = 0
    results = []
    for fn in sorted(os.listdir(dirpath)):
        if pattern and not fn.endswith(pattern):
            continue
        p = os.path.join(dirpath, fn)
        if not os.path.isfile(p):
            continue
        try:
            w = WhaleFile(p)
            ok, mods, failed = w.dump(outdir, max_shrink=max_shrink)
            all_str += w.strings(max_shrink=max_shrink)
        except Exception as e:
            results.append((fn, f'错误: {type(e).__name__} {e}'))
            continue
        total_ok += ok
        total_mod += mods
        results.append((fn, f'{ok}/{mods} 模块成功' + (f'（{failed} 个失败）' if failed else '')))
    return results, total_ok, total_mod, all_str
