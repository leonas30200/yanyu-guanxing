# -*- coding: utf-8 -*-
"""二进制里捞字符串 / 常量 —— APK、.so、.exe 都适用。

最有价值的一个功能：**从导出工具 exe 里把内层密钥表抠出来**
（`npswsuffix` 表，游戏更新后重新跑一次就有新版本的密钥）。
"""
import json
import os
import re
import struct
import zipfile

# 出现这些词的文件/字符串值得看一眼
INTERESTING = ['dnkemiea', 'm_tKeys', 'getDBKey', 'getDBCKey', 'po06ewkx', 'cgkmi4cq',
               'yoqkmrtj', 'meeo0wmk', 'eakec', 'yanyucache', 'parsekey',
               'npswsuffix', 'saveVer', 'encryptDBData', 'converDBData',
               'dnkemiea', 'Naj', 'LanJingNo1', 'BIGDA', 'KEIIY']

_PRINTABLE_FMT = rb'[\x20-\x7e]{%d,}'


def scan_strings(src, minlen=6, limit=None):
    """src 可以是路径或 bytes。返回 [(offset, text)]。"""
    if isinstance(src, (bytes, bytearray)):
        data = bytes(src)
    else:
        with open(src, 'rb') as f:
            data = f.read()
    out = []
    for m in re.finditer(_PRINTABLE_FMT % minlen, data):
        out.append((m.start(), m.group().decode('latin-1')))
        if limit and len(out) >= limit:
            break
    return out


def find_needle(src, needle):
    """返回 needle 出现的所有偏移。"""
    data = src if isinstance(src, (bytes, bytearray)) else open(src, 'rb').read()
    if isinstance(needle, str):
        needle = needle.encode('utf-8')
    out, i = [], 0
    while True:
        i = data.find(needle, i)
        if i < 0:
            return out
        out.append(i)
        i += 1


def context(src, offset, before=80, after=160, as_text=True):
    data = src if isinstance(src, (bytes, bytearray)) else open(src, 'rb').read()
    seg = data[max(0, offset - before): offset + after]
    if not as_text:
        return seg
    return ''.join(chr(c) if 32 <= c < 127 else '.' for c in seg)


# ───────────────────────── PE (.exe/.dll) ─────────────────────────
def pe_info(path):
    with open(path, 'rb') as f:
        data = f.read()
    if data[:2] != b'MZ':
        raise ValueError('不是 PE 文件')
    e_lfanew = struct.unpack_from('<I', data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b'PE\0\0':
        raise ValueError('PE 头损坏')
    coff = e_lfanew + 4
    machine, nsec, tstamp, _, _, optsize, chars = struct.unpack_from(
        '<HHIIIHH', data, coff)
    optoff = coff + 20
    magic = struct.unpack_from('<H', data, optoff)[0]
    secoff = optoff + (240 if magic == 0x20B else 224)
    secs = []
    for i in range(nsec):
        o = secoff + i * 40
        name = data[o:o + 8].rstrip(b'\0').decode('latin-1')
        vsize, vaddr, rawsize, rawptr = struct.unpack_from('<IIII', data, o + 8)
        secs.append({'name': name, 'vsize': vsize, 'rawsize': rawsize,
                     'rawptr': rawptr})
    return {'machine': hex(machine), 'bits': 64 if magic == 0x20B else 32,
            'sections': secs, 'size': len(data), 'timestamp': tstamp}


def pe_runtime_hints(data):
    """猜一下是什么打包的（Rust/Tauri、PyInstaller、Go、.NET…）。"""
    hints = {
        'Rust/Tauri': [b'tauri', b'__TAURI', b'Cargo', b'cargo\\registry'],
        'PyInstaller': [b'PyInstaller', b'_MEIPASS', b'pyi-'],
        'Go': [b'Go build ID', b'runtime.main'],
        '.NET': [b'mscorlib', b'System.Private.CoreLib'],
        'Electron/Node': [b'ELECTRON', b'app.asar'],
        'Qt': [b'Qt5Core', b'Qt6Core'],
    }
    out = []
    for name, pats in hints.items():
        if any(p in data for p in pats):
            out.append(name)
    return out


# ─────────────────── 内层密钥表提取（核心） ───────────────────
_TABLE_RE = re.compile(rb'\[\s*\{\s*"runtime"\s*:.*?\}\s*\]', re.S)


def extract_inner_key_rows(src):
    """从导出工具 exe 里抠出内层密钥表。

    返回 [(runtime, version, psw, suffix), ...]（已去重）
    对应文档里的 `npswsuffix` 表：内层密钥 = psw + suffix
    """
    data = src if isinstance(src, (bytes, bytearray)) else open(src, 'rb').read()
    rows, seen = [], set()
    for m in _TABLE_RE.finditer(data):
        txt = m.group().decode('latin-1')
        try:
            arr = json.loads(txt)
        except json.JSONDecodeError:
            # 表被截断时，逐条抢救
            arr = []
            for one in re.finditer(r'\{[^{}]*\}', txt):
                try:
                    arr.append(json.loads(one.group()))
                except json.JSONDecodeError:
                    pass
        for it in arr:
            try:
                key = (it['runtime'], it['version'], it['psw'], it['suffix'])
            except (KeyError, TypeError):
                continue
            if key not in seen:
                seen.add(key)
                rows.append(key)
    return rows


def extract_channel_suffixes(src):
    """从配置 JSON 里抠出渠道后缀数组：{"suffixes":[...]}"""
    data = src if isinstance(src, (bytes, bytearray)) else open(src, 'rb').read()
    out = []
    for m in re.finditer(rb'\{\s*"version"\s*:\s*\d+\s*,\s*"suffixes"\s*:\s*\[(.*?)\]',
                         data, re.S):
        for s in re.findall(rb'"([0-9a-z]{6,12})"', m.group(1)):
            v = s.decode('latin-1')
            if v not in out:
                out.append(v)
    return out


# ───────────────────────── APK ─────────────────────────
def apk_entries(path):
    with zipfile.ZipFile(path) as z:
        return z.namelist()


def apk_extract(path, outdir, only=None):
    """解压 APK。only = 子串或子串列表（只解压匹配的条目）。"""
    os.makedirs(outdir, exist_ok=True)
    if isinstance(only, str):
        only = [only]
    n = 0
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if only and not any(o in name for o in only):
                continue
            dst = os.path.join(outdir, name)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with z.open(name) as fsrc, open(dst, 'wb') as fdst:
                fdst.write(fsrc.read())
            n += 1
    return n


def apk_strings(path, minlen=6, only=None):
    """扫 APK 里所有条目（含压缩内容）的字符串。"""
    out = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if only and not any(o in name for o in only):
                continue
            try:
                data = z.read(name)
            except Exception:
                continue
            for off, s in scan_strings(data, minlen):
                out.append((name, off, s))
    return out
