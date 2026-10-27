# -*- coding: utf-8 -*-
"""从设备上找 / 拉取 dat.db。

支持多渠服
    不同渠道包名不同（官服 / 华为 / B站 …），所以这里**不写死一个包名**：
    先按已知渠道表逐个找，找不到就盲扫设备上所有"像这个游戏"的包
    （包含 lanjing / yanyu 的）。也可以用 `--pkg` 直接指定包名。

为什么要 -wal
    SQLite 可能把最新数据放在 `dat.db-wal` 里，只拷 dat.db 会拿到旧数据。
    这里会**连 -wal 一起拉**，让 sqlite3 自己合并。
"""
import hashlib
import json
import os
import re
import tempfile

from .. import ui
from ..config import channels, paths
from ..config.paths import REMOTE_SAVE_GLOB, REMOTE_SAVE_ROOTS as _REMOTE_ROOTS
from . import sqlitedb


def candidate_packages(adb, pkg=None):
    """要去找的包名列表：指定的 > 设备上装了的已知渠道 > 全部已知渠道。"""
    if pkg:
        return [channels.resolve_pkg(pkg)]
    try:
        installed = set(adb.packages())
    except Exception:
        installed = set()
    hit = [c.pkg for c in channels.CHANNELS if c.pkg in installed]
    if hit:
        return hit
    return channels.all_packages()


def find_save_dbs(adb, pkg=None):
    """列出设备上所有 dat.db 的远端路径。pkg 可为渠道名/包名/None(自动)。"""
    found, seen = [], set()
    for p in candidate_packages(adb, pkg):
        for root in _REMOTE_ROOTS:
            r = root.format(pkg=p)
            for ln in adb.ls(f'{r}/{REMOTE_SAVE_GLOB}'):
                key = paths.canon_remote(ln)
                if key not in seen:
                    seen.add(key)
                    found.append(key)
    return found


def pull(adb, remote, local=None):
    """拉 dat.db（顺带拉 -wal / -shm）。返回本地路径。"""
    if local is None:
        d = tempfile.mkdtemp(prefix='yyjh_db_')
        local = os.path.join(d, os.path.basename(os.path.dirname(remote)) + '_dat.db')
    adb.pull(remote, local)
    for ext in ('-wal', '-shm'):
        if adb.ls(remote + ext):
            try:
                adb.pull(remote + ext, local + ext)
            except Exception:
                pass
    return local


def inspect(adb, remote, local_dir=None):
    """拉回并读出 save 行。返回 dict 或 None。"""
    try:
        local = None
        if local_dir:
            local = os.path.join(local_dir,
                                 os.path.basename(os.path.dirname(remote)) + '_dat.db')
        local = pull(adb, remote, local)
        row = sqlitedb.read_save(local)
    except Exception:
        return None
    row['remote'] = remote
    row['local'] = local
    return row


def find_save_by_id(adb, save_id, pkgs=None, on_progress=None):
    """按 `save.id`（= md5(账号)）反查到**具体哪个包的哪个文件**。

    `dat.db` 的 id 列是明文 32 位十六进制，直接 grep 就行 —— 比逐个 pull
    回来读 sqlite 快得多，也是解决"同一设备装了多个渠道"的关键：
    用户不需要知道自己的渠道叫什么。

    返回 (pkg, 远端路径)；没找到返回 (None, None)。
    """
    if not save_id:
        return None, None
    if pkgs is None:
        pkgs = candidate_packages(adb)
    for p in pkgs:
        for root in _REMOTE_ROOTS:
            try:
                fs = adb.ls(f'{root.format(pkg=p)}/{REMOTE_SAVE_GLOB}')
            except Exception:
                continue
            if not fs:
                continue
            fs = list(dict.fromkeys(paths.canon_remote(x) for x in fs))
            q = ' '.join(f'"{x}"' for x in fs)
            # 注意：grep -l 输出的是**文件名**，所以判据是"有输出"而不是"输出里有 id"
            rc, out, _ = adb.sh(f'grep -l {save_id} {q} 2>/dev/null', timeout=300)
            hit = out.strip().splitlines()[0].strip() if out.strip() else ''
            if hit:
                if on_progress:
                    on_progress(f'  save.id {save_id[:8]}… 在 {p} 里找到')
                return p, hit
    return None, None


def find_by_account(adb, account, pkg=None, want_id=None, local_dir=None,
                    on_progress=None):
    """在设备上找出属于这个账号的 dat.db。

    判据：save 表的 id 列 == md5(账号)。
    先用 grep 精确定位（快），定位不到再退化成逐个 pull 回来读。
    """
    if want_id is None and account:
        want_id = hashlib.md5(account.encode('utf-8')).hexdigest()

    if want_id:
        p, remote = find_save_by_id(adb, want_id,
                                    candidate_packages(adb, pkg), on_progress)
        if p:
            info = inspect(adb, remote, local_dir)
            if info:
                return info

    remotes = find_save_dbs(adb, pkg)
    if on_progress:
        on_progress(f'设备上找到 {len(remotes)} 个 dat.db')
    if not remotes:
        if on_progress:
            on_progress('一个都没找到（游戏没登录过？包名不对？试试 --pkg）')
        return None
    for r in remotes:
        info = inspect(adb, r, local_dir)
        if not info:
            continue
        if on_progress:
            on_progress(f'  {r}   账号 {info["id"]}')
        if info['id'] == want_id:
            return info
    return None


def pick_any(adb, pkg=None, local_dir=None, on_progress=None):
    """没有指定账号时，取第一个能读到的 dat.db。"""
    for r in find_save_dbs(adb, pkg):
        info = inspect(adb, r, local_dir)
        if info:
            if on_progress:
                on_progress(f'  {r}   账号 {info["id"]}')
            return info
    return None


# ────────────────── 不给账号时：列出候选让用户挑 ──────────────────
_SERVER_RE = re.compile(r'/server(\d+)/')


def server_of(remote):
    """从远端路径里抠服号：`…/server12/dat.db` -> `12`。"""
    m = _SERVER_RE.search(remote or '')
    return m.group(1) if m else ''


def stat_mtime(adb, remote):
    """取设备上的最后修改时间（epoch 秒）。

    同时看 `dat.db-wal`：最新的数据可能还在 WAL 里，只算 dat.db 会显得旧。
    拿不到返回 0。
    """
    rc, o, _ = adb.sh(f'stat -c %Y "{remote}" 2>/dev/null; '
                      f'stat -c %Y "{remote}-wal" 2>/dev/null', timeout=60)
    vals = [int(x) for x in o.split() if x.strip().isdigit()]
    return max(vals) if vals else 0


def nick_of(info):
    """从 save.player 里抠角色名 —— 用户认不出 md5，但认得角色名。"""
    try:
        return str(json.loads(info.get('player') or '{}').get('nick') or '')
    except Exception:
        return ''


def list_saves(adb, pkg=None, local_dir=None, on_progress=None):
    """把设备上的候选 dat.db 都拉回来读一行（服号 / 角色名 / 修改时间）。

    比 `find_save_dbs` 多花几次 pull，但能拿到**角色名** ——
    这是唯一能让用户一眼认出"哪个档是我的"的东西（服号、修改时间次之）。
    结果按**修改时间从新到旧**排，默认项就是最近动过的那个。
    """
    out = []
    remotes = find_save_dbs(adb, pkg)
    if on_progress:
        on_progress(f'设备上找到 {len(remotes)} 个 dat.db')
    for r in remotes:
        info = inspect(adb, r, local_dir)
        if info:
            info['remote'] = r
            info['server'] = server_of(r)
            info['nick'] = nick_of(info)
            info['mtime'] = stat_mtime(adb, r)
            out.append(info)
    out.sort(key=lambda s: s.get('mtime') or 0, reverse=True)
    return out


def describe_saves(saves):
    """候选列表的展示行（给菜单/日志用）。"""
    lines = []
    for i, s in enumerate(saves, 1):
        try:
            size = os.path.getsize(s.get('local') or '')
        except OSError:
            size = 0
        lines.append(f'服 {s.get("server") or "?":<4} '
                     f'角色 {s.get("nick") or "（读不到）":<12} '
                     f'{size / 1048576:5.1f} MB   '
                     f'最后修改 {ui.fmt_time(s.get("mtime"))}   '
                     f'id {s.get("id", "")[:8]}…')
    return lines


def pick_save(adb, pkg=None, local_dir=None, allow_pick=True,
              on_progress=None, say=None):
    """没给账号时，从设备上的候选里挑一个。

    1 个就直接用；多个先把候选列出来（默认走 `ui.info`，用户看得到）。
    能交互就让用户点编号（默认 = 最新改动的那个），敲 q 则抛 `Cancelled`。
    """
    say = say or ui.info
    saves = list_saves(adb, pkg, local_dir, on_progress)
    if not saves:
        return None
    if len(saves) == 1:
        return saves[0]

    # 能交互就只让菜单打印一遍，别再打一遍重复的
    if allow_pick and ui.is_tty():
        i = ui.choose('请选一个（默认 = 最近改动的那个）：',
                      describe_saves(saves))
        return saves[i]

    say(f'设备上有 {len(saves)} 个存档（按修改时间从新到旧）：')
    for ln in describe_saves(saves):
        say(f'  {ln}')
    say('  （不能交互选择，取最新改动的那个；要指定请用 --pkg / --serial）')
    return saves[0]


def find_and_report(adb, account=None, pkg=None, on_progress=None):
    """给 `mem devices/scan` 之类的命令用：列一下设备上都有啥。"""
    chans = candidate_packages(adb, pkg)
    ui.info(f'  候选包名 {len(chans)} 个：')
    for c in chans[:8]:
        ui.info(f'    {channels.label(c)}')
    if len(chans) > 8:
        ui.info('    …')
    if account:
        return find_by_account(adb, account, pkg, on_progress=on_progress)
    return pick_any(adb, pkg, on_progress=on_progress)
