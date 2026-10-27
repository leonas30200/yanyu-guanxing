# -*- coding: utf-8 -*-
"""/proc/<pid>/maps 解析 + 内存区域 dump。

为什么不用 frida: MuMu 上的游戏是 32 位 + 有反调试，frida 一 attach 就崩；
直接读 /proc/<pid>/mem 反而稳。
"""
import os
import re
import shutil
import tempfile
from dataclasses import dataclass

MAPS_RE = re.compile(
    r'^([0-9a-f]+)-([0-9a-f]+)\s+(\S{4})\s+([0-9a-f]+)\s+(\S+)\s+(\d+)\s*(.*)$')

PAGE = 4096


@dataclass
class Region:
    start: int
    end: int
    perm: str
    path: str = ''

    @property
    def size(self):
        return self.end - self.start

    @property
    def is_anon(self):
        return (not self.path) or self.path.startswith('[')

    @property
    def readable(self):
        return 'r' in self.perm

    def __str__(self):
        return f'0x{self.start:x}-0x{self.end:x} {self.perm} {self.size/1048576:.1f}MB {self.path}'


def parse_maps(text):
    out = []
    for ln in text.splitlines():
        m = MAPS_RE.match(ln.strip())
        if not m:
            continue
        out.append(Region(int(m.group(1), 16), int(m.group(2), 16),
                          m.group(3), m.group(7).strip()))
    return out


def regions(text, anon_only=True, min_kb=64, max_size_mb=None, sort_desc=True):
    out = []
    for r in parse_maps(text):
        if not r.readable or r.size <= 0:
            continue
        if anon_only and not r.is_anon:
            continue
        if r.size < min_kb * 1024:
            continue
        if max_size_mb and r.size > max_size_mb * 1048576:
            continue
        out.append(r)
    if sort_desc:
        out.sort(key=lambda r: -r.size)
    return out


def read_maps(adb, pid):
    return adb.out(f'cat /proc/{pid}/maps')


def _nearest_page_down(x):
    return x - (x % PAGE)


def _read_pages(adb, pid, skip, pages):
    """读设备上 [skip*PAGE, +pages*PAGE) 这一段。

    优先 `exec-out dd`：设备端直接把字节喷到 stdout，一次往返、不经磁盘。
    老办法（写 /sdcard 再 pull）作为退路保留 —— 个别 adb 版本 exec-out 表现不好。
    """
    dd = (f'dd if=/proc/{pid}/mem bs={PAGE} skip={skip} count={pages} '
          f'2>/dev/null')
    try:
        data = adb.exec_out(dd, timeout=300)
        if data:
            return data
    except Exception:                                  # noqa: BLE001
        pass
    remote = '/sdcard/_yyjh_chunk.bin'
    adb.sh(f'{dd} of={remote}', timeout=300)
    tmp = tempfile.mkdtemp(prefix='yyjh_pull_')
    local = os.path.join(tmp, 'chunk')
    try:
        adb.pull(remote, local)
        with open(local, 'rb') as f:
            return f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        adb.rm(remote)


def dump_region(adb, pid, region, max_mb=64, chunk_mb=64, on_progress=None):
    """把一段内存拉回本地。返回 bytes（最多 max_mb MB）。

    分块读，避免设备端一次性生成超大文件。
    设备端算术是 32 位有符号，skip/count 一律用 PC 算好的十进制值。
    on_progress(got_bytes, total_bytes) 用于进度显示。
    """
    size = min(region.size, max_mb * 1048576) if max_mb else region.size
    size = _nearest_page_down(size)
    out = bytearray()
    step = _nearest_page_down(chunk_mb * 1048576)
    off = 0
    while off < size:
        n = min(step, size - off)
        pages = n // PAGE
        if pages <= 0:
            break
        skip = (region.start + off) // PAGE
        out += _read_pages(adb, pid, skip, pages)
        off += pages * PAGE
        if on_progress:
            on_progress(len(out), size)
    return bytes(out)


def iter_regions(adb, pid, region_list, max_mb=None, chunk_mb=64, on_progress=None,
                 on_bytes=None):
    """逐区域产出 (region, bytes)。

    on_progress(region, idx, total, 累计MB) / on_bytes(region, got, total)
    """
    got = 0
    for i, r in enumerate(region_list):
        try:
            data = dump_region(adb, pid, r, max_mb=max_mb, chunk_mb=chunk_mb,
                               on_progress=(lambda a, b, _r=r: on_bytes(_r, a, b))
                               if on_bytes else None)
        except Exception:
            continue
        got += len(data)
        if on_progress:
            on_progress(r, i, len(region_list), got / 1048576)
        yield r, data


def save_image(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)
    return path
