# -*- coding: utf-8 -*-
"""★ 游戏更新后补新版本密钥：一把梭爆破 + 存档。

背景 —— 为什么更新一次就会全红
------------------------------
内层密钥 = psw(8) + suffix(8)：

  · suffix 按**渠道**走，而且我们已知（官服 po06ewkx / iOS yoqkmrtj / 先行服 cgkmi4cq …）
  · psw   按**版本**走 —— 游戏一更新，版本号一变（00073440 -> 00073460），
    psw 就换一把，上一版的密钥全部作废。

于是 capture / local 同时失效，报出来还很像"外层密钥不对"，让人往错方向查。

为什么一定挖得出来
------------------
游戏自己必须能存/读档，所以当前版本的 psw 必然在游戏进程内存里。
而 suffix 是已知的 —— 只要在内存里搜到 suffix，它**前面 8 个字节**就是 psw。

★ 四个实测出来的关键事实（都直接影响速度/命中率，别想当然）
--------------------------------------------------------
1. 内存里**常驻的只有 8 字符的版本段**（如 `u52kw6lb`）；拼好的 16 字符
   （`u52kw6lbpo06ewkx`）只在解密那一刻昙花一现 —— 所以必须用
   "字面量找到渠道后缀 -> 回读它前面 8 字节"，而不是干等那个拼好的形态。
2. 设备读内存 **256 MB/s**（dd 不是瓶颈 ✗），但 `grep -E` 跑 22 分支正则只有
   **6.6 MB/s**（几分钟全耗在正则引擎上）；换 `grep -F` 字面量 **282 MB/s**。
3. 每调一次 adb 要起一个进程（实测往返 **41 ms**）：一区域/一片调一次 -> 上千次
   -> 再赔进去好几分钟。**打包成批**后 CPU 开销降到可以忽略（碎堆多的时候
   batch=2 只有 10.8 MB/s，batch=8 是 19.9 MB/s）。
4. 只扫**可写匿名堆**就够了（密钥是运行时创建的字符串）——实测跳过 2 GB 只读/代码段。

当前量级（本机 + MuMu 实测，3.7 GB 可写堆）：

  · ① 字面量扫一遍 + 回读后缀前面 8 字节   -> **约 15 秒**
  · ③ 兜底深挖（捞全部 8 位串，不靠锚点）  -> **168 秒 / 2.8 分钟**（22 MB/s）

存什么（用户不用管，但得知道它存了）
------------------------------------
  · 挖到的密钥  -> keys.json              （可分享；以后 capture/local/rebuild 直接可用）
  · 顺便记住的  -> keys.local.json        （本机/账号相关，不进 git）
  · 候选池      -> output/_cache/heap_keys.bin（下次再更新直接复用，越用越快）

用法
----
    python -m yyjh key crack                     # 自动去设备上找最新存档，一把梭
    python -m yyjh key crack --db <dat.db>       # 用指定存档
    python -m yyjh key crack --suffix po06ewkx   # 只认某个渠道后缀（更快）
"""
import collections
import os
import re
import time

from .. import ui
from ..config import keytables, paths
from . import memory

__all__ = ['run', 'hunt', 'probe', 'probe_run', 'suffixed_candidates',
           'inner_body_of']


# ───────────────────────── 候选采集 ─────────────────────────
def suffixed_candidates(data, suffixes):
    """内存块里「紧跟在已知渠道后缀前面的 8 字节」—— 那就是 psw。

    比"把整块内存的可打印串全收下来"精确得多：suffix 是渠道固定的、我们已知的，
    所以每个出现位置只产生**一个**候选。就算 psw+suffix 是拼在一个长串里
    （比如 "xxpon1dxg7po06ewkx_yy"），只要后缀在，前 8 字节就是对的。
    """
    out = set()
    for suf in suffixes:
        needle = suf.encode('latin-1')
        start = 0
        while True:
            i = data.find(needle, start)
            if i < 0:
                break
            start = i + 1
            if i < 8:
                continue
            psw = data[i - 8:i]
            if all(32 <= c <= 126 for c in psw):
                out.add(psw + needle)
    return out


def ends_with_suffix(key, suffixes):
    """16 字节候选的后 8 字节是不是已知后缀。"""
    if len(key) != 16:
        return False
    tail = bytes(key)[8:]
    return any(tail == s.encode('latin-1') for s in suffixes)


def expand_suffixes(cands, suffixes):
    """把候选里的 psw 拆出来，和各个渠道后缀重新组合。

    ★ 这条是"内存里只有官服、却要解 iOS 抓包"时唯一的出路：
    内层密钥 = psw(按**版本**，跨渠道跨平台共用) + suffix(按**渠道**)，
    所以拿到任意一个渠道的那把，剩下几个渠道的就能就地推出来。

    实测例：安卓内存里只有 `u52kw6lbpo06ewkx`（官服后缀），
    而 iOS 抓包要的是 `u52kw6lbyoqkmrtj` —— 只差后 8 位，前面那 8 位是共用的。
    全量爆破救不了（iOS 后缀那串在安卓内存里根本不存在），必须靠这步推导。
    """
    out = set()
    for c in cands:
        c = bytes(c)
        out.add(c)
        if len(c) != 16:
            continue          # 长度不对的没法拆 psw，原样留着交给上层去试
        psw = c[:8]
        for s in suffixes:
            out.add(psw + s.encode('latin-1'))
    return out


def inner_body_of(content, runtime='android', outer_key=None, account=None,
                  save_id=None):
    """存档 content -> (x, version, body, used_outer)。

    x     = 初版 content（base64 文本：版本号 8 + 内层密文）
    body  = 内层 XXTEA 密文 —— 爆破就是拿它当靶子

    新版存档会先拿外层密钥解一层。外层密钥是挖出来记在 keys.local.json 里的，
    所以这一步通常不花时间；逐个候选都会走一遍 parse_old，解出来对不上就换下一把。
    """
    from ..game import savefmt
    from ..localdb import decrypt as dbdec

    fmt = savefmt.detect_format(content)
    if fmt is None:
        raise ValueError('这段 content 认不出来（既不是新版也不是初版）')

    if fmt == savefmt.NEW:
        last = None
        for k in dbdec.outer_candidates(account, outer_key, save_id):
            try:
                x = savefmt.unwrap_outer(content, k)
                version, body = savefmt.parse_old(x)
                return x, version, body, k
            except ValueError as e:
                last = e
                continue
        raise ValueError(
            '这是新版存档，但外层密钥不对、也没记录 —— 得先把外层密钥挖到。\n'
            f'  最后一次错误：{last}\n'
            '  -> python -m yyjh mem crack --db "<存档路径>"（先开游戏进一次角色）')

    version, body = savefmt.parse_old(content)
    return content, version, body, ''


# ───────────────────────── 爆破 ─────────────────────────
def _verify(body, cands):
    """精确复核候选：XXTEA -> zlib -> JSON 三关全过才算。返回 str 或 None。"""
    from ..game import savefmt
    cands = [c for c in cands if c]
    if not cands:
        return None
    data, key = savefmt.try_inner(body, sorted(cands))
    if key is None:
        return None
    return key.decode('latin-1') if isinstance(key, (bytes, bytearray)) else str(key)


TOKEN_RE = re.compile(rb'[0-9a-z]{8}')

# 密钥通常在"小堆区"里（Lua 字符串堆）。8 MB 是个经验值：
# 实测那块只有 3.5 MB，整块拉回来 0.2 秒，捞出的候选几十到几百条。
SMALL_REGION_MB = 8


def psw_tokens(data):
    """从内存里捞出"像版本段"的 8 位小写串（psw）。

    为什么要捞这个、而不是"拼好的 16 字符内层密钥"：
    实测游戏**只把 8 字符的版本段常驻在堆里**（`u52kw6lb`），
    后缀是解密那一刻才拼上去的 —— 拼好的形态只是昙花一现，
    指望扫到它全靠运气（同一个存档前后两次扫描结果就不一样）。
    """
    return {m for m in TOKEN_RE.findall(data)}


def scan_small_regions(adb, pid, region_list, suffixes, body,
                       max_region_mb=SMALL_REGION_MB, budget_mb=256,
                       on_progress=None):
    """★ 把"小区域"整块拉回来，捞 8 位版本段 × 各渠道后缀来爆破。

    这是**主力手段**：又快又准，不依赖那种昙花一现的"拼好形态"。
    region_list 请先按"大块优先"排好（实测密钥待的那块 3.5 MB，属于偏大的小堆）。
    返回 (命中的 key 或 None, 拉回字节数, 原始候选条数)。
    """
    tokens = set()
    got = 0
    checked = set()
    total = sum(r.size for r in region_list)
    for i, r in enumerate(region_list):
        if budget_mb and got >= budget_mb * 1048576:
            break
        try:
            data = memory.dump_region(adb, pid, r, max_mb=max_region_mb,
                                      chunk_mb=max_region_mb)
        except Exception:                              # noqa: BLE001
            continue
        got += len(data)
        tokens |= psw_tokens(data)
        if on_progress:
            on_progress(got, min(total, budget_mb * 1048576 if budget_mb else total),
                        len(tokens), i + 1, len(region_list))
        pool = expand_suffixes(tokens - checked, suffixes)
        if len(pool) >= 64:
            checked |= set(tokens)
            hit = _verify(body, pool)
            if hit:
                return hit, got, len(tokens)
    hit = _verify(body, expand_suffixes(tokens - checked, suffixes))
    return hit, got, len(tokens)


def known_psws():
    """表里所有已知的 8 字符版本段 —— 拿来当锚点用。"""
    from ..config import keytables
    out = set()
    try:
        for _rt, _ver, key, _src in keytables.all_inner_rows():
            if len(key) == 16:
                out.add(key[:8])
    except Exception:                                  # noqa: BLE001
        pass
    return out


def known_psw_map():
    """8 字符版本段 -> [(runtime, 版本号), ...]。挖不到时报诊断用：
    直接告诉使用者“内存里在的是哪几版”，而不是只说“没挖到”。"""
    from ..config import keytables
    out = {}
    try:
        for rt, ver, key, _src in keytables.all_inner_rows():
            if len(key) == 16:
                out.setdefault(key[:8], []).append((rt, ver))
    except Exception:                                  # noqa: BLE001
        pass
    return out


def describe_known_hits(seen, suffixes):
    """把“命中计数”翻译成人话。返回 (在内存里的版本列表, 说明或 None)。"""
    pm = known_psw_map()
    hot_p = [p for p in pm if seen.get(p)]
    lbl = []
    for p in sorted(hot_p, key=lambda x: -seen[x])[:6]:
        vers = '/'.join(sorted({v for _rt, v in pm[p]}))[:40]
        lbl.append(f'{p}×{seen[p]}（{vers}）')
    hot_s = [s for s in suffixes if seen.get(s)]
    if lbl or hot_s:
        txt = ''
        if lbl:
            txt += f'内存里在的版本段：{"、".join(lbl)}'
        if hot_s:
            txt += ('；' if txt else '') + \
                   f'渠道后缀：{"、".join(f"{s}×{seen[s]}" for s in hot_s)}'
        return hot_p, txt
    return [], None


def _grep_literals(suffixes, anchors=()):
    """设备端用 `grep -F`（字面量）搜这些东西：渠道后缀 + 已知版本段。

    ★ 为什么不用正则搜"8 位字母数字 + 后缀"那种一把梭的写法：
      实测同一个 64 MB 块，`grep -aboE` 那个 22 分支的正则只有 **6.6 MB/s**，
      而 `grep -aboF` 字面量是 **282 MB/s**（差 40 倍）—— 整轮几分钟全耗在
      正则引擎上。改成"先字面量找后缀，再回头读它前面 8 字节"，又准又快。
    """
    out = []
    for s in list(suffixes) + list(anchors):
        if s and s not in out:
            out.append(s)
    return out


def resolve_suffix_hits(adb, pid, hits):
    """后缀命中了 -> 把它**前面 8 字节**读回来就是版本段，拼成 16 字节候选。

    实测游戏内存里只有 8 字符的版本段常驻（后缀是运行时才拼的），
    所以"找到后缀 -> 回读前 8 字节"是拿到内层密钥最靠谱的方式。
    """
    out = set()
    page_cache = {}
    for addr, suffix in hits[:256]:
        page = (addr - 16) & ~0xFFF
        if page not in page_cache:
            try:
                page_cache[page] = adb.exec_out(
                    f'dd if=/proc/{pid}/mem bs={memory.PAGE} skip={page // memory.PAGE} '
                    f'count=1 2>/dev/null', timeout=60)
            except Exception:                          # noqa: BLE001
                page_cache[page] = b''
        data = page_cache[page]
        pos = addr - page
        if pos < 8 or pos + 8 > len(data):
            continue
        psw = data[pos - 8:pos]
        if all(33 <= b <= 126 for b in psw):
            out.add(psw + suffix.encode('latin-1', 'ignore'))
    return out


def _device_grep_ok(adb):
    """设备上到底有没有 grep -oE（没有就得退回本机读）。"""
    try:
        rc, txt, _err = adb.sh('echo zzzzpo06ewkx | grep -aoE "[0-9A-Za-z]{4}po06ewkx"',
                               timeout=30)
    except Exception:                                  # noqa: BLE001
        return False
    return rc == 0 and 'po06ewkx' in txt


def grep_memory(adb, pid, regs, suffixes, anchors=(), chunk_mb=64, batch=4,
                limit_mb=None, on_progress=None):
    """★ 让**设备自己**扫一遍内存：一次 adb 调用里跑很多片 `dd | grep -aboE`。

    一遍同时收两样东西：
      ① `版本段+后缀` 拼好的 16 字符（真在的话直接就能用）
      ② 已知版本段出现在哪块区域（**锚点**：拿到它的位置，
         就能只把那几块堆拉回来慢慢挑，不用把几 GB 全传回来）

    为什么必须在设备上 grep、而不是拉回本机：走 adb 把几 GB 传回来只有几 MB/s
    （实测最慢的块掉到 0.05 MB/s，整轮十几分钟）；设备端读内存实测 **260 MB/s**。

    为什么要**打包成批**：真正的开销不是数据，而是**每调一次 adb 就要起一个
    adb 进程**（实测 41 ms 往返）。一个区域/一片调一次 -> 上千次调用 ->
    好几分钟，数据才几 GB、明明只要十几秒。打包后调用次数降到个位数。
    实测（一块块 1~8 MB 的碎堆）：batch=2 只有 10.8 MB/s，batch=4 是
    16.7 MB/s，batch=8 是 19.9 MB/s —— 碎堆越多，批大小越重要。

    返回 (拼好的候选集, 已扫字节, 跳过的片数, 锚点命中 [(区域, 绝对地址, 串)],
          字面量命中计数 Counter)。候选集为 None = 设备上缺工具，交给上层退回本机逐块读。
    """
    if not _device_grep_ok(adb):
        return None, 0, 0, [], collections.Counter()
    lits = _grep_literals(suffixes, anchors)
    if not lits:
        return set(), 0, 0, [], collections.Counter()
    grep_args = ' '.join(f'-e "{x}"' for x in lits)
    suf_set = set(suffixes)

    tasks = []
    step = max(1, int(chunk_mb)) * 1048576
    for r in regs:
        off = 0
        while off < r.size:
            n = min(step, r.size - off)
            pages = n // memory.PAGE
            if pages <= 0:
                break
            tasks.append((r, r.start + off, pages))
            off += pages * memory.PAGE

    out = set()
    anchor_hits = []
    suf_hits = []          # (绝对地址, 后缀) —— 回头读它前面 8 字节
    seen = collections.Counter()     # 字面量命中计数：挖不到时用来报“内存里在的是哪一版”
    scanned = 0
    skipped = 0
    t0 = time.time()
    for start in range(0, len(tasks), max(1, batch)):
        group = tasks[start:start + max(1, batch)]
        parts = []
        for (_r, addr, pages) in group:
            # 每片先 echo 自己的基准地址（比每行跑一次 sed 便宜得多：
            # 922 片就是 922 个 sed 进程，实测能吃掉一半时间）；
            # LC_ALL=C 很关键：locale 一开，grep 会慢一大截。
            parts.append(
                f'echo "#{addr}"; '
                f'dd if=/proc/{pid}/mem bs={memory.PAGE} '
                f'skip={addr // memory.PAGE} count={pages} 2>/dev/null '
                f'| LC_ALL=C grep -aboF {grep_args}')
        try:
            rc, txt, _err = adb.sh('; '.join(parts), timeout=300)
        except Exception:                              # noqa: BLE001
            rc, txt = 1, ''
            skipped += len(group)
        if rc not in (0, 1):        # grep 没命中时返回 1，是正常的
            skipped += len(group)
            txt = ''
        base = group[0][1]
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith('#'):
                try:
                    base = int(line[1:])
                except ValueError:
                    pass
                continue
            off, _, tok = line.partition(':')
            if not tok:
                continue
            try:
                addr = base + int(off)
            except ValueError:
                continue
            seen[tok] += 1
            if tok in suf_set:
                suf_hits.append((addr, tok))
                # ★ 后缀命中点也是锚点：游戏把 8 位版本段单独放着、后缀另存，
                #   光看它前 8 字节可能扑空 —— 捞它周围一片出来的 8 位串更稳
                reg2 = group[0][0]
                for (_x, a, p) in group:
                    if a <= addr < a + p * memory.PAGE:
                        reg2 = _x
                        break
                anchor_hits.append((reg2, addr, tok))
                continue
            # 锚点：把绝对地址映射回它所在的区域（后面要按区域捞回来）
            reg = group[0][0]
            for (_x, a, p) in group:
                if a <= addr < a + p * memory.PAGE:
                    reg = _x
                    break
            anchor_hits.append((reg, addr, tok))
        for r, addr, pages in group:
            scanned += pages * memory.PAGE
        if on_progress:
            on_progress(scanned, out, min(start + len(group), len(tasks)),
                        len(tasks), time.time() - t0)
        if limit_mb and scanned >= limit_mb * 1048576:
            break

    if suf_hits:
        out |= resolve_suffix_hits(adb, pid, suf_hits)
    return out, scanned, skipped, anchor_hits, seen


def deep_tokens(adb, pid, regs, chunk_mb=64, batch=8, on_progress=None):
    """兜底深挖：把内存里**所有** 8 位小写串去重捞出来（去重在设备端做）。

    实测 18~35 MB/s（同一台机器、同样 128 MB：3.6s~7.0s，取决于堆里有多少
    文字/JSON —— 全是零页的大块最快），3.6 GB 大约 **2~4 分钟** —— 慢，
    但**不依赖任何锚点**：只要那一版的 8 位版本段还在内存里，就一定捞得到。

    batch 默认 8：真实内存里绝大多数是 1~8 MB 的碎堆，一次 adb 只带 2 片的话
    41 ms 的往返开销占掉近一半时间（实测碎堆 batch=2 → 10.8 MB/s，batch=8 →
    19.9 MB/s）。

    ★ 但批次大 ≠ 进度卡：每片结尾 echo 一个 `#N` 标记，用 `Adb.stream()` 逐行读，
    **一片跑完就回调一次** —— 既保住吞吐，进度条也不会半分钟才动一格。

    为什么不在本机做：全传回来是几 GB；设备端 `grep -o | sort -u` 之后
    只剩去重后的几万~几十万条，传回来只有几百 KB。
    """
    tasks = []
    step = max(1, int(chunk_mb)) * 1048576
    for r in regs:
        off = 0
        while off < r.size:
            n = min(step, r.size - off)
            pages = n // memory.PAGE
            if pages <= 0:
                break
            tasks.append((r.start + off, pages))
            off += pages * memory.PAGE
    out = set()
    scanned = 0
    done = 0
    t0 = time.time()
    for start in range(0, len(tasks), max(1, batch)):
        group = tasks[start:start + max(1, batch)]
        # 每片结尾 echo `#序号` —— 设备端跑完一片就吐一个标记，
        # 下面流式读到时立刻推进度条（批次大也不影响"多久动一下"）。
        parts = [
            f'dd if=/proc/{pid}/mem bs={memory.PAGE} skip={a // memory.PAGE} '
            f'count={p} 2>/dev/null | LC_ALL=C grep -aoE "[0-9a-z]{{8}}" '
            f'| LC_ALL=C sort -u; echo "#{j}"'
            for j, (a, p) in enumerate(group)]

        def _line(ln, _group=group):
            nonlocal scanned, done
            if ln.startswith('#'):
                try:
                    j = int(ln[1:])
                except ValueError:
                    return
                if 0 <= j < len(_group):
                    scanned += _group[j][1] * memory.PAGE
                done += 1
                if on_progress:
                    on_progress(scanned, out, min(done, len(tasks)),
                                len(tasks), time.time() - t0)
                return
            s = ln.strip()
            if len(s) == 8:                    # 捞到的 8 位串（去重靠设备端 sort -u）
                out.add(s.encode('latin-1', 'ignore'))

        try:
            adb.stream('; '.join(parts), timeout=900, on_line=_line)
        except Exception:                              # noqa: BLE001
            # 没有 stream（旧 adb 封装 / 桩对象）就退回一次性拿全部输出
            try:
                _rc, txt, _err = adb.sh('; '.join(parts), timeout=600)
            except Exception:                          # noqa: BLE001
                txt = ''
            for t in (txt or '').split():
                if len(t) == 8:
                    out.add(t.encode('latin-1', 'ignore'))
            for (_a, p) in group:
                scanned += p * memory.PAGE
            done += len(group)
            if on_progress:
                on_progress(scanned, out, min(done, len(tasks)),
                            len(tasks), time.time() - t0)
    return out


PROBE_SMALL_MB = 16      # 密钥住的都是这种小堆（实测那块 3.5 MB，属于偏大的小堆）
PROBE_BUDGET_MB = 0      # 0 = 小堆全拉完（结论才可信）；watch 模式默认给个小的


# 多进程 worker 的全局状态（Windows 的 spawn 要求 worker 是模块级函数）
_PROBE_BODY = None
_PROBE_SUFS = ()


def _probe_init(body, sufs):
    global _PROBE_BODY, _PROBE_SUFS
    _PROBE_BODY, _PROBE_SUFS = body, sufs


def _probe_one(tok):
    """worker：一个 8 位版本段 × 各渠道后缀 -> 命中的 16 位密钥（都没有就 None）。

    为什么 × 各后缀：靶子是用**它自己渠道**的后缀加密的（iOS 存档用 yoqkmrtj），
    而内存里常驻的只是 8 位版本段 —— 所以必须交叉试。
    """
    from ..game import savefmt
    for s in _PROBE_SUFS:
        k = tok + s
        if savefmt.try_inner(_PROBE_BODY, [k])[1] is not None:
            return k
    return None


def probe(version, body, suffixes, *, adb, pid, small_mb=PROBE_SMALL_MB,
          budget_mb=PROBE_BUDGET_MB, workers=None, on_progress=None):
    """轻量探测：**现在**内存里有没有能解开这一版的密钥（十几秒~一分钟）。

    为什么要单独有它：内存里只留得住"游戏最近解密过的那几版"的密钥，而靶子
    必须是同一版才挖得到。要确认"我刚在游戏里做的那个动作到底有没有把这一版
    解出来"，不用等 `key crack` 跑到底（不命中就得 2.8 分钟兜底深挖）——
    这个命令给一个 yes/no，配合 `--watch` 还能在游戏里做动作时盯着看。

    只拉"小可写堆"（密钥就住在这种 3~8 MB 的小堆里），一趟数据同时收两样：
      · 8 位串 × 各渠道后缀 -> 拿去真解密 -> 能不能解开这一版（命中提前返回）
      · 里面的 8 位串里有没有表里已知的版本段 / 渠道后缀 -> 游戏最近解密过哪版

    ★ 每条候选要整段解一次（525 KB 的密文实测 1.2 ms）—— 这是唯一的瓶颈，
    所以**验证要开多进程**（8 核下几十万候选从 2 分钟降到十几秒）。
    拉数据只有 45 MB/s、本机正则 68 MB/s，都不是大头。

    实测（本机 + MuMu，730 MB 小堆 / 约 2.2 万条 8 位串）：
      拉 16s + 正则 11s + 验证 13s（8 进程）≈ **40 秒**

    返回 dict：ok / key / tokens / got / regs / known_psws / known_suffixes /
    elapsed / exhausted（False = 被 --budget-mb 截断了，不是拉完了）
    """
    import multiprocessing as mp

    cap = int(small_mb or 0) * 1048576
    budget = int(budget_mb or 0) * 1048576
    step = max(1, int(small_mb or 64))
    sufs = list(suffixes)
    regs = memory.regions(memory.read_maps(adb, pid), anon_only=True, min_kb=4)
    cand = sorted([r for r in regs if 'w' in r.perm and (not cap or r.size <= cap)],
                  key=lambda r: -r.size)          # 大块优先：密钥那块属于偏大的小堆
    kmap = known_psw_map()

    nproc = 1 if workers == 1 else int(workers or min(8, os.cpu_count() or 4))
    pool = None
    if nproc > 1:
        pool = mp.Pool(nproc, initializer=_probe_init, initargs=(body, tuple(sufs)))
    else:
        _probe_init(body, tuple(sufs))

    def _try(toks):
        """toks: list[str]（8 位串）-> 命中的 16 位密钥或 None。"""
        if not toks:
            return None
        if pool is None:
            for t in toks:
                r = _probe_one(t)
                if r:
                    return r
            return None
        chunk = max(1, len(toks) // (nproc * 8))
        for r in pool.imap_unordered(_probe_one, toks, chunksize=chunk):
            if r:
                return r
        return None

    cnt = collections.Counter()
    checked = 0
    got = n = 0
    hit = None
    t0 = time.time()
    try:
        for r in cand:
            if budget and got >= budget:
                break
            try:
                data = memory.dump_region(adb, pid, r, max_mb=step, chunk_mb=step)
            except Exception:                              # noqa: BLE001
                continue
            if not data:
                continue
            n += 1
            got += len(data)
            cnt.update(t.decode('latin-1') for t in TOKEN_RE.findall(data))
            if on_progress:
                on_progress(got, n, len(cand), len(cnt), time.time() - t0)
            # 边拉边验：攒够一批就试一批（单条 1.2 ms，碎着验会被调度开销盖住）
            if len(cnt) - checked >= 4000:
                todo = list(cnt)[checked:]
                checked = len(cnt)
                hit = _try(todo)
                if hit:
                    break
        if not hit:
            todo = list(cnt)[checked:]
            checked = len(cnt)
            hit = _try(todo)
    finally:
        if pool is not None:
            pool.terminate()
            pool.join()

    hot_p = sorted([(p, cnt[p], kmap[p]) for p in kmap if cnt.get(p)],
                   key=lambda x: -x[1])
    hot_s = [(s, cnt[s]) for s in sufs if cnt.get(s)]
    return {
        'ok': bool(hit), 'key': hit, 'tokens': len(cnt),
        'got': got, 'regs': n, 'known_psws': hot_p, 'known_suffixes': hot_s,
        'elapsed': time.time() - t0, 'workers': nproc,
        'exhausted': not (budget and got >= budget),
    }


def known_inner_key(runtime, version, body):
    """表里（含"同版本换后缀 / 换平台"）有没有能解开**这份靶子**的钥匙。

    为什么不能只看 `lookup_inner_key` 的 (runtime, version) 字面匹配：
    内层密钥 = psw(8, 按**版本**走) + suffix(8, 按**渠道**走)，psw 跨平台、
    跨渠道共用 —— 只存了 ios 那条时，android 其实一样解得出，字面匹配却会说
    "没有这一版"，于是每次都白挖一遍（用户看到的"密钥都存好了还让我重新扫"）。

    返回 (key, 说明)。
    """
    cands = keytables.candidate_inner_keys(runtime, version)
    k = _verify(body, cands)
    if k:
        return k, f'表里已有能解开这一版的钥匙（{len(cands)} 个候选里命中）'
    return None, f'表里没有能解开这一版的钥匙（试了 {len(cands)} 个候选）'


def remember_for_runtime(runtime, version, key):
    """把"实际管用的那把"也记到当前平台名下（表里没有才写）。

    理由：`runtime` 只是"在哪台客户端上观察到的"。挖到 / 解出之后顺手记一条，
    下次同一平台就是精确命中，也不用再看别的平台的脸色。
    """
    have = {(rt, ver) for rt, ver, _k, _s in keytables.all_inner_rows()}
    if (runtime, version) in have:
        return False
    keytables.remember_inner_key(runtime, version, key)
    ui.info(f'  顺手记到 {runtime}/{version} 名下：{key}')
    return True


def hunt(runtime, version, body, suffixes, *, files=(), cache=None, words=(),
         adb=None, pid=None, min_kb=64, max_mb=0, chunk_mb=64, limit_mb=None,
         all_regions=False, small_mb=0, workers=None, deep=True,
         use_oracle=True):
    """挖内层密钥。返回 (密钥, 说明)；没挖到返回 (None, 说明)。

    顺序（从便宜到贵）:
      0. 表里"同版本换后缀"的兜底           —— 免费
      1. 候选缓存 heap_keys.bin             —— 免费（上次扫内存留下的）
      2. --files / --words 给的候选         —— 便宜
      3. 游戏内存里"后缀前面 8 字节"        —— 要开游戏，但命中率高
      4. 全量候选过预言机                   —— 慢，兜底
    """
    from . import crack as mc

    cands_suf = set()     # 高信度：要么像后缀尾，要么是表里来的
    cands_all = set()     # 全量：内存/文件里的可打印串

    for k in keytables.candidate_inner_keys(runtime, version):
        cands_suf.add(k.encode('latin-1'))
    for w in words:
        b = str(w).encode('latin-1', 'ignore')[:16].ljust(16, b'\x00')
        cands_suf.add(b)
        cands_all.add(b)

    if cache and os.path.exists(cache):
        c = mc.load_candidates(cache)
        cands_all |= c
        cands_suf |= {k for k in c if ends_with_suffix(k, suffixes)}
        ui.step('候选缓存', f'{cache} 里 {len(c):,} 条')

    if files:
        c = mc.candidates_from_files(list(files))
        cands_all |= c
        cands_suf |= {k for k in c if ends_with_suffix(k, suffixes)}
        ui.step('文件候选', f'{len(c):,} 条')

    # ★ 换后缀推导：psw 跨渠道/跨平台共用，拿到一个渠道的就能推其余几个
    before = len(cands_suf)
    cands_suf = expand_suffixes(cands_suf, suffixes)
    if len(cands_suf) > before:
        ui.step('换后缀推导', f'{before} -> {len(cands_suf)} 条候选')

    # —— 先拿高信度候选试一遍（快，且判据精确）——
    if cands_suf:
        ui.info(f'  先试 {len(cands_suf)} 条高信度候选（表内 / 后缀结尾 / 手给）')
        key = _verify(body, cands_suf)
        if key:
            return key, f'高信度候选命中（{len(cands_suf)} 条）'

    # —— 扫内存 ——
    if adb and pid:
        regs = memory.regions(memory.read_maps(adb, pid),
                              anon_only=not all_regions, min_kb=min_kb)
        # 只扫"可写匿名堆"：密钥是运行时创建的字符串，一定在堆里。
        # 代码段 / 只读映射白扫（实测有几块 1 GB 的 r--p 全是 0，读了纯浪费）。
        cand = [r for r in regs if all_regions or 'w' in r.perm]
        total = sum(r.size for r in cand)
        skipped_mb = (sum(r.size for r in regs) - total) / 1048576
        ui.info(f'  可写堆 {len(cand)} 块 / {ui.fmt_size(total)}'
                f'（跳过只读/代码段 {skipped_mb:.0f} MB）')
        checked = set()
        last_check = 0
        scanned = 0
        anchors = []

        # ① 主力：设备端扫一遍（打包成批，每次 adb 调用跑 2 片）
        #    · 为什么在设备上扫：几 GB 走 adb 传回本机只有几 MB/s（最慢的块 0.05 MB/s），
        #      设备端读内存实测 260 MB/s
        #    · 为什么打包：真正的开销是**每调一次 adb 起一个进程**（约 60 ms），
        #      一片一调 -> 上千次 -> 好几分钟；打包后降到几十次
        slice_mb = max(1, int(chunk_mb))
        n_slice = sum(max(1, (r.size + slice_mb * 1048576 - 1) // (slice_mb * 1048576))
                      for r in cand)
        ui.info(f'  ① 设备端扫 {ui.fmt_size(total)}（{n_slice} 片 × {slice_mb}MB）')
        t0 = time.time()
        # 进度按**字节**而不是按片：真实内存里片子大小差几十倍（64 MB 的大片
        # 和 1 MB 的碎堆混在一起），按片显示的 ETA 会离谱到几十分钟。
        with ui.progress(total, '设备端搜索', ' B') as pb:
            got, scanned, skipped, anchors, seen = grep_memory(
                adb, pid, cand, suffixes, anchors=known_psws(), chunk_mb=slice_mb,
                limit_mb=limit_mb,
                on_progress=lambda a, o, i, n, t: pb.update(
                    a, f'{a/max(t, 1e-9)/1048576:.0f} MB/s　命中 {len(o)}'))
        ui.info(f'      完成：{n_slice} 片 / {ui.fmt_size(scanned)}，'
                f'用时 {ui.fmt_duration(time.time() - t0)}')
        if got is not None:
            cands_suf |= got
            ui.info(f'  设备端扫完 {ui.fmt_size(scanned)}'
                    + (f'（{skipped} 块读不了）' if skipped else '（全覆盖）')
                    + f'：拼好的密钥 {len(got)} 条，已知版本段 {len(anchors)} 处')
            # ★ 直接报“内存里在的是哪几版”：挖不到时，这一行就能告诉使用者
            #   是“这一版没在内存里”（去游戏里碰一次这一版的存档）还是扫漏了。
            hot_p, txt = describe_known_hits(seen, suffixes)
            if txt:
                ui.info(f'      {txt}')
                ui.info('      ↑ 要挖的那一版不在里面就挖不到 —— 先去游戏里'
                        '碰一次**那一版**的存档（进角色 / 手动上传存档）')
            else:
                # 注意：这里**不能**说“游戏没解密过存档” —— 挖全新一版时
                # 内存里那个版本段本来就不在表里（也就搜不到），这一步判断不了。
                ui.info('      内存里没出现表里的任何已知版本段/渠道后缀'
                        '（要挖的是全新一版时很正常）')
            key = _verify(body, expand_suffixes(cands_suf, suffixes))
            if key:
                return key, f'设备端内存搜索命中（{len(cands_suf)} 条原始候选）'

            # ② 锚点收网：字面量命中的地方（后缀 / 已知版本段），把周围捞回来
            #    挑所有 8 位串 × 渠道后缀去爆 —— 不依赖"拼好的形态"（它只是昙花一现）
            if anchors:
                ui.info(f'  ② 锚点收网：{len(anchors)} 处命中，'
                        '把周围捞回来看有哪些 8 位版本段')
                tokens = set()
                seen = []
                for r, addr, tok in anchors:
                    if any(r0 == r.start for r0, _t in seen):
                        continue
                    seen.append((r.start, tok))
                    if len(seen) > 24:
                        break
                    back = min(8 * 1048576, addr - r.start)
                    want = 16 * 1048576
                    start = addr - back
                    pages = min(want // memory.PAGE, (r.end - start) // memory.PAGE)
                    if pages <= 0:
                        continue
                    try:
                        data = adb.exec_out(
                            f'dd if=/proc/{pid}/mem bs={memory.PAGE} '
                            f'skip={start // memory.PAGE} count={pages} '
                            f'2>/dev/null', timeout=600)
                    except Exception:                  # noqa: BLE001
                        continue
                    tokens |= psw_tokens(data)
                ui.info(f'      从 {len(seen)} 处捞到 {len(tokens)} 条 8 位候选'
                        '（× 渠道后缀后逐条去爆）')
                key = _verify(body, expand_suffixes(tokens, suffixes))
                if key:
                    return key, f'锚点附近版本段爆破命中（{len(tokens)} 条候选）'
                ui.info('      这些候选都不对 —— 可能这一版还没被游戏解密过')

        # ③ 设备上没 grep —— 一块块拉回本机，边扫边验（慢，但一样扫全）
        if got is None:
            ui.warn('设备上没有可用的 grep，退回本机逐块读（几 MB/s，会慢很多）')
            t0 = time.time()
            with ui.progress(len(cand), '扫描内存', ' 块') as pb:
                for i, (_r, data) in enumerate(
                        memory.iter_regions(adb, pid, cand, max_mb=max_mb,
                                            chunk_mb=chunk_mb)):
                    cands_suf |= suffixed_candidates(data, suffixes)
                    cands_all |= mc.harvest_strings(data)
                    scanned += len(data)
                    pb.update(i + 1, f'已扫 {ui.fmt_size(scanned)}　'
                                     f'命中 {len(cands_suf)}')
                    pending = expand_suffixes(cands_suf - checked, suffixes)
                    if len(pending) >= 4 or \
                            (pending and scanned - last_check >= 128 * 1048576):
                        checked |= pending
                        last_check = scanned
                        hit = _verify(body, pending)
                        if hit:
                            ui.info(f'  命中！用时 {ui.fmt_duration(time.time() - t0)}')
                            return hit, f'内存候选命中（共 {len(cands_suf)} 条原始候选）'
                    if limit_mb and scanned >= limit_mb * 1048576:
                        ui.step('扫描', f'达到 --limit-mb {limit_mb}，提前停止')
                        break
            ui.info(f'  已扫 {ui.fmt_size(scanned)}，用时 {ui.fmt_duration(time.time() - t0)}')
            key = _verify(body, expand_suffixes(cands_suf - checked, suffixes))
            if key:
                return key, f'内存候选命中（共 {len(cands_suf)} 条原始候选）'

        # ③ 小堆直挑：内层密钥是运行时创建的字符串，实测就住在 3~8 MB 的**小可写堆**里
        #    —— 直接把这种小堆拉回来，本地多进程把"8 位串 × 各渠道后缀"全爆一遍。
        #    比 ④ 全量深挖快 4 倍（约 40 秒 vs 2.8 分钟），所以排在它前面。
        if deep and adb and pid:
            small_regs = [r for r in cand if r.size <= PROBE_SMALL_MB * 1048576]
            small_b = sum(r.size for r in small_regs)
            ui.info(f'  ③ 小堆直挑：拉 {len(small_regs)} 块 ≤{PROBE_SMALL_MB} MB 的可写小堆'
                    f'（{ui.fmt_size(small_b)}），8 位串 × 各渠道后缀全爆一遍'
                    f'（实测约 {ui.fmt_duration(small_b / 1048576 * 0.06)}）...')
            with ui.progress(max(1, small_b), '小堆直挑', ' B') as pb:
                pr = probe(version, body, suffixes, adb=adb, pid=pid,
                           small_mb=PROBE_SMALL_MB, budget_mb=0, workers=workers,
                           on_progress=lambda g, n, tot, tk, t: pb.update(
                               g, f'{n}/{tot} 块　{tk:,} 条 8 位串'))
            ui.info(f'      拉了 {ui.fmt_size(pr["got"])}'
                    + ('（命中即停）' if pr['ok'] else '')
                    + f'，{pr["tokens"]:,} 条 8 位串，'
                    f'用时 {ui.fmt_duration(pr["elapsed"])}'
                    f'（{pr["workers"]} 进程验证）')
            if pr['ok']:
                return pr['key'], f'小堆直挑命中（{pr["tokens"]:,} 条 8 位串）'
            if pr['known_psws']:
                ui.info('      内存里在的版本段：' + '、'.join(
                    f'{p}×{c}（' + '/'.join(sorted({v for _rt, v in vs}))[:26] + '）'
                    for p, c, vs in pr['known_psws'][:5]))
            else:
                ui.info('      内存里没有表里的任何已知版本段'
                        '（可能游戏还没解密过存档，也可能这一版确实还没收录）')

        # ④ 兜底深挖：把内存里**所有** 8 位小写串去重捞出来（慢，但不靠任何锚点）
        #    只要那一版的 8 位版本段还在内存里，这一步就一定能兜住。
        if deep and adb and pid:
            # 实测 18~35 MB/s，取保守的 20 MB/s 估个时间给使用者心里有数。
            est = total / (20 * 1048576)
            ui.info(f'  ④ 兜底深挖：把内存里所有 8 位小写串去重捞出来'
                    f'（{ui.fmt_size(total)}，实测约 20 MB/s，'
                    f'预计 {ui.fmt_duration(est)}；这一步不靠锚点，'
                    '只要这一版被游戏解密过就一定能兜住）...')
            t0 = time.time()
            # 进度按字节：按“块/片”显示的 ETA 会把碎堆当成 64 MB 的大片来外推，
            # 动不动就报几十分钟（实测真实耗时只有 2~4 分钟）。
            with ui.progress(total, '深挖中', ' B') as pb:
                toks = deep_tokens(
                    adb, pid, cand, chunk_mb=chunk_mb,
                    on_progress=lambda a, o, i, n, t:
                        pb.update(a, f'{a/max(t, 1e-9)/1048576:.0f} MB/s　'
                                     f'候选 {len(o):,}'))
            ui.info(f'      捞到 {len(toks):,} 条 8 位串，用时 '
                    f'{ui.fmt_duration(time.time() - t0)}')
            if toks:
                extra = expand_suffixes(toks, suffixes)
                cands_all |= extra
                key = _verify(body, extra)
                if key:
                    return key, f'兜底深挖命中（{len(toks):,} 条 8 位串）'

        # ⑤ 可选：本机拉小堆区慢慢挑（`--small-mb 8` 才开）。
        #    现在 ③ 小堆直挑已经把这个思路做得更好（多进程验证 + 报诊断），
        #    这条留着是给"想自己指定小堆上限"的人一个明确入口。
        if small_mb:
            small = [r for r in cand if r.size <= small_mb * 1048576]
            small.sort(key=lambda r: r.size)
            ui.info(f'  ⑤ 本机扫小堆区（≤{small_mb}MB，{len(small)} 块）...')
            hit, got_b, ntok = scan_small_regions(
                adb, pid, small, suffixes, body, max_region_mb=small_mb,
                budget_mb=256)
            ui.info(f'      拉回 {ui.fmt_size(got_b)}，{ntok} 条 8 位候选')
            if hit:
                return hit, f'小堆区版本段爆破命中（{ntok} 条原始候选）'

        stats = f'后缀候选 {len(cands_suf)} 条'
        if cands_all:
            stats += f' / 全量候选 {len(cands_all):,} 条'
        ui.info(f'  内存候选: {stats}')

    # —— 全量兜底 ——
    if use_oracle and cands_all:
        ui.info('  后缀候选没命中 -> 退回全量爆破（慢，会把 CPU 吃满）')
        hits = mc.crack_ciphertext(body, cands_all, workers=workers,
                                   label='测试候选密钥')
        for h in hits:
            key = _verify(body, [h[0]])
            if key:
                return key, f'全量爆破命中（{len(cands_all):,} 条里）'

    return None, '没挖到'


# ───────────────────────── 目标存档 ─────────────────────────
def _device_target(a, runtime, need_save=True):
    """去设备上把最新存档捞回来（含 pid，后面扫内存要用）。

    need_save=False 时只找设备 + 游戏进程，**不拉存档** —— 已经有靶子
    （比如 --har）时就别再多此一举把 2 MB 存档拷一遍、还弹个选档菜单。
    """
    from ..localdb import device as dev
    from ..memscan import devices

    adb, pkg, _ch = devices.pick_game_device(
        exe=getattr(a, 'adb', None), serial=getattr(a, 'serial', None),
        pkg_hint=getattr(a, 'pkg', None),
        on_progress=lambda m: ui.step('设备', m))
    if not pkg:
        return None, None, None, None, None, '没在任何设备上找到游戏'
    pid = str(getattr(a, 'pid', None) or '') or adb.pid_of(pkg)
    if pid:
        ok, why = adb.check_mem_access(pid)
        if not ok:
            ui.warn(f'内存读不了：{why}')
            pid = None
    else:
        ui.warn('游戏没在运行 —— 内存挖不动，只能靠文件/缓存里的候选')

    if not need_save:
        return adb, pid, None, None, None, ''

    info = dev.pick_save(adb, pkg, say=ui.info,
                         allow_pick=not getattr(a, 'no_pick', False),
                         on_progress=lambda m: ui.step('设备存档', m))
    if not info:
        return adb, pid, None, None, None, f'{pkg} 上没找到 dat.db'
    label = f"{adb.serial}:{info['remote']}（{info['id']}）"
    return adb, pid, info['local'], info['id'], label, ''


def _crack_outer(adb, pid, content, account, save_id, a):
    """内存里把**外层密钥**挖出来（本地存档没有它就走不动）。"""
    from . import crack as mc
    ui.info('  扫内存挖外层密钥（每账号一把，只在游戏运行时存在内存里）...')
    try:
        hits = mc.crack_outer_key(
            adb, pid, content,
            anon_only=not getattr(a, 'all_regions', False),
            min_kb=getattr(a, 'min_kb', 64) or 64,
            max_mb=getattr(a, 'max_mb', 64) or 64,
            limit_mb=getattr(a, 'limit_mb', None),
            workers=getattr(a, 'workers', None))
    except Exception as e:                             # noqa: BLE001
        ui.warn(f'挖外层密钥出错：{e}')
        return None
    if not hits:
        ui.warn('内存里没找到外层密钥')
        return None
    key = hits[0][0].decode('latin-1')
    keytables.remember_outer_key(account, key, save_id=save_id)
    ui.ok(f'外层密钥 {key}（已记入 keys.local.json，以后不用再挖）')
    return key


def _read_db_arg(db, a):
    """--db 既可以是本地文件，也可以是**设备路径**（`serial:/path` 或 `/data/...`）。

    支持设备路径是为了让向导/上层能"就地重试出问题的那一份存档"，
    而不是让用户再选一次（选来选去很容易选到别的服、别的版本上）。
    """
    from ..localdb import device as dev
    from ..localdb import sqlitedb
    from . import devices

    devp = paths.parse_device_path(db)
    if devp:
        serial, remote = devp
        adb = devices.make_adb(getattr(a, 'adb', None),
                               serial or getattr(a, 'serial', None))
        info = dev.inspect(adb, remote)
        if not info:
            raise ui.UserError(f'读不了设备上这个路径：{remote}', [
                '确认路径拼写；设备上要能 cat 这个文件（需要 root）',
                '或者干脆不给 --db，让工具自己去设备上找最新的存档',
            ])
        return sqlitedb.read_save(info['local']), f'{adb.serial}:{remote}'
    p = paths.resolve(db)
    return sqlitedb.read_save(p), p


def game_running(a):
    """轻量检查：设备在不在、游戏进程在不在（**不拉存档、不扫内存**）。

    挖密钥前先问一句"游戏开着吗"：没开的话内存里根本没有密钥，
    白扫几十秒再失败，用户只会觉得"又让我重来一遍"。

    返回 (adb, pid, 说明)；pid 为空 = 挖不了。
    """
    runtime = getattr(a, 'runtime', None) or 'android'
    try:
        adb, pid, _p, _i, _l, why = _device_target(a, runtime, need_save=False)
    except Exception as e:                                 # noqa: BLE001
        return None, None, str(e)
    if adb is None:
        return None, None, why or '没找到设备 / 没找到游戏'
    if not pid:
        return adb, None, why or '游戏没在运行'
    return adb, pid, ''


def _resolve_body(a, runtime, account, adb, pid, dev_path, dev_label):
    """把“--har / --db / --content / 设备上最新那份”变成 (version, body, runtime, account, save_id)。

    run() 和 probe_run() 共用这一段 —— 两边口径必须一模一样（同一个靶子）。
    """
    body = version = ''
    save_id = ''
    if getattr(a, 'har', None):
        from ..capture import decode as capdec
        p = paths.resolve(getattr(a, 'har'))
        ui.info(f'  目标抓包: {p}')
        try:
            info = capdec.inner_body(p, index=getattr(a, 'index', None),
                                     real_key=getattr(a, 'real_key', None))
        except ValueError as e:
            raise ui.UserError('这个抓包读不出来', [str(e)]) from e
        version, body = info['version'], info['body']
        runtime = info['runtime'] or runtime
        save_id = info['save_id']
        account = account or info['account']
        ui.info('  外层由抓包自带的 realKey 解开 —— 不需要外层密钥，'
                '但内层还得去内存里挖')
    else:
        content = getattr(a, 'content', None) or ''
        if not content and getattr(a, 'db', None):
            row, label = _read_db_arg(getattr(a, 'db'), a)
            content, save_id = row['content'], row['id']
            ui.info(f'  目标存档: {label}（{len(content):,} 字符）')
        if not content and dev_path:
            from ..localdb import sqlitedb
            row = sqlitedb.read_save(dev_path)
            content, save_id = row['content'], row['id']
            ui.info(f'  目标存档: {dev_label or dev_path}')

        if not content:
            raise ui.UserError('没有可用的存档', [
                '给一个抓包：python -m yyjh key crack --har "抓包.har"',
                '给一个存档：python -m yyjh key crack --db <dat.db>',
                '或者让工具自己去设备上取：确保模拟器开着、游戏装了、进过一次角色',
            ])

        # 拆出内层密文（新版要先过外层）
        ui.info('  拆存档：新版先用外层密钥解一层 ...')
        try:
            _x, version, body, used_outer = inner_body_of(
                content, runtime, outer_key=getattr(a, 'outer_key', None),
                account=account, save_id=save_id)
            if used_outer:
                ui.info(f'  外层密钥: {used_outer}（已记录，直接可用）')
        except ValueError as e:
            if not (adb and pid):
                raise ui.UserError('拆不开这个存档', [
                    str(e),
                    '挖外层密钥得让游戏跑着（钥匙只在进程内存里）：',
                    '  打开游戏、进一次角色，再跑同一条命令；',
                    '  或者改用抓包当靶子：--har "抓包.har"（不需要外层密钥）',
                ]) from e
            ui.warn(str(e).splitlines()[0])
            ok = _crack_outer(adb, pid, content, account, save_id, a)
            if not ok:
                raise ui.UserError('外层密钥也没挖到', [
                    '确认游戏正在运行、并且**进过一次角色**（存档加载后钥匙才在内存里）',
                    '也可以直接给：--outer-key <16 位外层密钥>',
                ]) from e
            _x, version, body, _u = inner_body_of(
                content, runtime, outer_key=ok, account=account, save_id=save_id)

    ui.info(f'  版本号: {version}')
    ui.step('内层密文', f'{len(body):,} B')
    return version, body, runtime, account, save_id


def probe_run(a):
    """`yyjh key probe` 的实现。返回进程退出码。"""
    runtime = getattr(a, 'runtime', None) or 'android'
    suffixes = [s for s in (getattr(a, 'suffix', None) or []) if s] \
        or list(keytables.CHANNEL_SUFFIXES)
    account = getattr(a, 'account', None) or ''

    ui.title('探测：内存里现在有没有这一版的密钥')
    ui.info(f'  渠道后缀池: {" ".join(suffixes)}')

    adb = pid = None
    dev_path = dev_label = None
    try:
        adb, pid, dev_path, _id, dev_label, why = _device_target(
            a, runtime, need_save=not getattr(a, 'har', None))
    except Exception as e:                                 # noqa: BLE001
        why = str(e)
    if adb is None or pid is None:
        raise ui.UserError('探测需要"设备 + 正在运行的游戏"', [
            why or '没找到游戏进程',
            '探测就是去游戏内存里找钥匙：模拟器要开着、游戏要装着、要进过一次角色',
            '（如果只是想挖密钥，可以改用：python -m yyjh key crack --har "抓包.har"）',
        ])

    version, body, runtime, account, save_id = _resolve_body(
        a, runtime, account, adb, pid, dev_path, dev_label)

    known, note = known_inner_key(runtime, version, body)
    if known and not getattr(a, 'force', False):
        ui.ok(f'版本 {version} 的密钥表里已经有了：{known}')
        ui.info(f'  {note}')
        remember_for_runtime(runtime, version, known)
        ui.info('  不用探也不用挖 —— 想重挖的话加 --force 跑 key crack')
        return 0

    small = getattr(a, 'small_mb', None)
    small = PROBE_SMALL_MB if small is None else int(small)
    watch = int(getattr(a, 'watch', 0) or 0)
    budget = getattr(a, 'budget_mb', None)
    if budget is None:
        # watch 是"盯着看"的：默认只拉一部分，保证每轮十几秒能出结果；
        # 单次探测默认拉完（结论才可信）。
        budget = 192 if watch else PROBE_BUDGET_MB
    budget = int(budget)

    # 先算一下要拉多少：进度条的总量 + 给使用者一个预估时间
    regs = memory.regions(memory.read_maps(adb, pid), anon_only=True, min_kb=4)
    cap = small * 1048576 if small else 0
    tot_b = sum(r.size for r in regs if 'w' in r.perm and (not cap or r.size <= cap))
    if budget:
        tot_b = min(tot_b, budget * 1048576)

    ui.info(f'  靶子版本 {version}，内层密文 {len(body):,} B')
    ui.info(f'  只看 ≤{small or "不限"} MB 的小可写堆，共 {ui.fmt_size(tot_b)}'
            + (f'（每轮最多拉 {budget} MB）' if budget else '（拉完）'))
    ui.info(f'  按实测速度估：约 {ui.fmt_duration(tot_b / 1048576 * 0.06)}'
            '（拉取 45 MB/s、本机正则 68 MB/s、验证开多进程）')
    if watch:
        ui.info(f'  --watch {watch}：每 {watch} 秒探一次，成功就停（Ctrl+C 退出）')
        ui.info('  → 现在去游戏里**进一次角色**（要进到能看到角色的界面），'
                '或手动点一次"上传存档"，我盯着屏幕')

    rounds = 0
    while True:
        rounds += 1
        if rounds > 1:
            ui.info()
            ui.info(f'第 {rounds} 次探测 ...')
        with ui.progress(max(1, tot_b), '探测中', ' B') as pb:
            res = probe(version, body, suffixes, adb=adb, pid=pid,
                        small_mb=small, budget_mb=budget,
                        workers=getattr(a, 'workers', None),
                        on_progress=lambda g, n, tot, tk, t: pb.update(
                            g, f'{n}/{tot} 块　{tk:,} 条 8 位串'))

        ui.info(f'  拉了 {ui.fmt_size(res["got"])} / {res["regs"]} 块，'
                f'8 位串 {res["tokens"]:,} 条，用时 {ui.fmt_duration(res["elapsed"])}'
                f'（{res["workers"]} 进程验证）')
        if not res['exhausted']:
            ui.warn(f'  这轮是被 --budget-mb {budget} 截断的：'
                    '"没解开"不能当结论，要确认得加 --budget-mb 0')

        if res['ok']:
            ui.ok(f'内存里有能解开 {version} 的密钥：{res["key"]}')
            ui.info('  → 现在马上跑这条（会把密钥存进 keys.json）：')
            ui.info('     python -m yyjh key crack'
                    + (f' --har "{getattr(a, "har")}"' if getattr(a, 'har', None)
                       else ' --db "<刚才那份存档>"'))
            return 0

        hot_p, hot_s = res['known_psws'], res['known_suffixes']
        if hot_p or hot_s:
            bits = []
            if hot_p:
                bits.append('版本段 ' + '、'.join(
                    f'{p}×{c}（' + '/'.join(sorted({v for _rt, v in vs}))[:26] + '）'
                    for p, c, vs in hot_p[:5]))
            if hot_s:
                bits.append('渠道后缀 ' + '、'.join(f'{s}×{c}' for s, c in hot_s))
            ui.info('  内存里在的：' + '；'.join(bits))
            ui.info(f'  ↑ 但没有 {version} 那一版 —— 说明游戏还没碰过这一版的存档')
        else:
            ui.warn('  内存里连表里任何已知版本段/渠道后缀都没有'
                    ' —— 这个进程根本没解密过任何一版存档')
        ui.info(f'  这 {res["tokens"]:,} 条候选 × {len(suffixes)} 个后缀都解不开 '
                f'{version}')

        if not watch:
            ui.info('  → 去游戏里**进一次角色**（要进到能看到角色的界面），'
                    '或手动点一次"上传存档"，再探一次')
            ui.info('     想自动盯着：加 --watch 5（每 5 秒探一次，成功就停）')
            return 2
        ui.info(f'  {watch} 秒后再探一次（现在去游戏里做那个动作）...')
        time.sleep(watch)


def run(a):
    """`yyjh key crack` 的实现。返回进程退出码。"""
    runtime = getattr(a, 'runtime', None) or 'android'
    suffixes = [s for s in (getattr(a, 'suffix', None) or []) if s] \
        or list(keytables.CHANNEL_SUFFIXES)
    account = getattr(a, 'account', None) or ''

    ui.title('补新版本密钥（爆破 + 存档）')
    ui.info(f'  渠道后缀池: {" ".join(suffixes)}')

    # ① 设备：既是"没给目标时的存档来源"，也是扫内存的入口
    adb = pid = None
    dev_path = dev_id = dev_label = None
    if not getattr(a, 'no_mem', False):
        try:
            adb, pid, dev_path, dev_id, dev_label, why = _device_target(
                a, runtime, need_save=not getattr(a, 'har', None))
        except Exception as e:                      # adb 不在 / 连不上都不是致命的
            ui.warn(f'设备这条线用不了：{e}')
            adb = pid = None
            why = str(e)
        if why:
            ui.warn(why)

    # ② 靶子（和设备那条线分开，probe 也走这一段）
    version, body, runtime, account, save_id = _resolve_body(
        a, runtime, account, adb, pid, dev_path, dev_label)

    # ④ 已知就直接存（--inner-key），不用挖
    explicit = (getattr(a, 'inner_key', None) or '').strip()
    if explicit:
        if len(explicit) != 16:
            raise ui.UserError(
                f'--inner-key 得是 16 个字符，现在给了 {len(explicit)} 个',
                ['内层密钥 = psw(8) + suffix(8)，例如 pon1dxg7po06ewkx'])
        keytables.remember_inner_key(runtime, version, explicit)
        ui.ok(f'已存档：{runtime}/{version} -> {explicit}')
        ui.info(f'  {paths.rel(paths.KEYS_FILE)}')
        ui.info('  以后 capture / local / rebuild 直接用。')
        return 0

    # ⑤ 表里已经有就不用挖了 —— 判定标准是"**能不能解开这份靶子**"，
    #    而不是"runtime+version 字面能不能对上":
    #    psw 按版本走、跨平台跨渠道共用，只存了 ios 那条时 android 一样能用。
    known, note = known_inner_key(runtime, version, body)
    if known and not getattr(a, 'force', False):
        ui.ok(f'版本 {version} 的密钥已经在表里了：{known}（不用挖）')
        ui.info(f'  {note}')
        remember_for_runtime(runtime, version, known)
        ui.info('  要重挖加 --force')
        return 0

    # ⑥ 挖
    cache = os.path.join(paths.CACHE_DIR, 'heap_keys.bin')
    key, how = hunt(runtime, version, body, suffixes,
                    files=[paths.resolve(p) for p in (getattr(a, 'from_files', None) or [])],
                    cache=cache,
                    words=getattr(a, 'words', None) or [],
                    adb=adb, pid=pid,
                    min_kb=getattr(a, 'min_kb', 64) or 64,
                    max_mb=getattr(a, 'max_mb', 0) or 0,        # 0 = 不限（要扫全）
                    chunk_mb=getattr(a, 'chunk_mb', 64) or 64,
                    limit_mb=getattr(a, 'limit_mb', None),
                    all_regions=getattr(a, 'all_regions', False),
                    small_mb=getattr(a, 'small_mb', 0),
                    deep=not getattr(a, 'no_deep', False),
                    workers=getattr(a, 'workers', None),
                    use_oracle=not getattr(a, 'no_oracle', False))

    # ⑦ 顺手把候选池存下来（下次更新直接复用）
    if cache and os.path.exists(cache):
        ui.info(f'  候选池留在 {cache} 里了，下次更新可以直接复用')

    if not key:
        raise ui.UserError(f'没挖到版本 {version} 的密钥', [
            f'怎么试都没命中（{how}）。',
            '按这个顺序排查：',
            f'  1. 游戏要**开着**，而且要让它**碰到过版本 {version} 的存档**：',
            '     内存里只留得住"最近解密过的那几版"的密钥（上面那行会列出',
            '     内存里实际在的版本段）。如果里面没有这一版，多半是**游戏已经',
            '     更新到更新的一版**了 —— 那就换一份"和游戏当前版本一致"的',
            '     存档来挖（比如让游戏自己导出一次当前存档）',
            '  2. 确认扫的就是报错的那份存档：--db 指过去（本地路径和设备路径都行）',
            '  3. 加 --all-regions --min-kb 0 再扫一遍（区域过滤太狠会漏）',
            '  4. 渠道后缀不确定就多给几个：--suffix po06ewkx --suffix cgkmi4cq',
            '  5. 还是不行就换来源：--from "导出工具.exe"',
            '     或者 python -m yyjh exe tables <导出工具.exe> --save',
        ])

    # ⑧ 存 + 复核
    keytables.remember_inner_key(runtime, version, key)
    again = _verify(body, [key.encode('latin-1')])
    ui.ok(f'挖到内层密钥：{key}　（{how}）')
    ui.info('  复核: ' + ('XXTEA -> zlib -> JSON 全部通过' if again
                        else '⚠ 没过，请反馈'))
    ui.info(f'  已存档: {paths.rel(paths.KEYS_FILE)}')
    ui.info(f'          {runtime} / {version} -> {key}')
    ui.info('  以后 capture / local / rebuild 直接用，不用再挖。')
    return 0
