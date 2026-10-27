# -*- coding: utf-8 -*-
"""设备 / 渠道选择 —— 多台设备、同一台设备装多个渠道服，都要自动挑对。

两级选择
    ① 选**设备**：--serial 指定 > keys.local.json 记过的 > 自动（逐台看，
       挑装了游戏的那台；已知模拟器端口优先）
    ② 选**渠道**：同一台模拟器上同时装了官服 + 华为服 + B站服是常态，
       顺序是
         1. `--pkg huawei` 显式指定
         2. **按 save.id 反查**（`find_pkg_by_save_id`）—— 最准
         3. keys.local.json 里上次用过的
         4. 正在运行的那个（用户一般在看着它）
         5. 还是多个 + 交互式终端 → 列编号菜单让用户点
         6. 兜底：取第一个，并把候选都打出来提醒可以用 --pkg 指定

为什么"按 save.id 反查"是解决多渠道的关键
    渠道服之间唯一区别就是包名，而用户既不知道自己装的是哪个渠道，也可能
    同时装了官服和渠道服。但 `dat.db` 里 `id = md5(账号)` 是**明文字符串**，
    拿它去设备上 grep 一下就能定位到具体包 —— 用户不需要提供任何渠道信息。

用顺手的 adb / serial / 包名会记进 `keys.local.json`，下次自动带上。
"""
import re
from dataclasses import dataclass, field

from .. import ui
from ..config import channels, keytables
from ..config.channels import Channel
from ..config.paths import (REMOTE_SAVE_GLOB, REMOTE_SAVE_ROOTS,
                            canon_remote)
from .adb import Adb, AdbError

_SERVER_RE = re.compile(r'/server(\d+)/')


def make_adb(exe=None, serial=None, on_progress=None):
    """建一个 Adb（会自动找 adb.exe）。"""
    return Adb(exe=exe, serial=serial, on_progress=on_progress)


# ───────────────────────── 候选：这台设备上有哪些游戏 ─────────────────────────
@dataclass
class Game:
    """设备上的一个渠道包。"""
    pkg: str
    channel: Channel = None
    pid: str = None                              # 正在运行的 pid
    saves: list = field(default_factory=list)    # 这个包下的 dat.db 远端路径

    @property
    def name(self):
        return self.channel.name if self.channel else '未收录渠道'

    @property
    def running(self):
        return bool(self.pid)

    def servers(self):
        """从存档路径里解析出服号（`.../server12/dat.db` -> ['12']）。"""
        out = []
        for p in self.saves:
            m = _SERVER_RE.search(p)
            if m and m.group(1) not in out:
                out.append(m.group(1))
        return sorted(out)

    def line(self):
        """给菜单/日志用的一行描述（用户能认出"哪个是我的"）。"""
        parts = [f'{self.name:<6}', self.pkg]
        parts.append(f'运行中({self.pid})' if self.running else '未运行')
        srv = self.servers()
        if srv:
            parts.append('服 ' + '/'.join(srv))
        if self.saves:
            parts.append(f'{len(self.saves)} 个存档')
        return '  '.join(parts)


def list_games(adb, with_saves=True):
    """这台设备上所有像游戏本体的包。返回 [Game]。

    `with_saves=True` 会顺便列出每个包下的 dat.db 路径（要多发几次 shell，
    但能拿到服号，让用户认得出哪个是自己的号）。
    """
    try:
        found = channels.find_game_packages(adb)
    except Exception:
        return []

    # 进程只查一次（而不是每个包 pidof 一次）
    pids = {}
    try:
        for pid, name in adb.ps():
            pids.setdefault(name, pid)
    except Exception:
        pass

    games = []
    for pkg, chan, _ok in found:
        g = Game(pkg=pkg, channel=chan, pid=pids.get(pkg))
        if with_saves:
            seen = set()
            for root in REMOTE_SAVE_ROOTS:
                try:
                    for x in adb.ls(f'{root.format(pkg=pkg)}/{REMOTE_SAVE_GLOB}'):
                        c = canon_remote(x)
                        if c not in seen:      # /data/user/0 是 /data/data 的软链
                            seen.add(c)
                            g.saves.append(c)
                except Exception:
                    pass
        games.append(g)
    return games


def probe_game(adb, on_progress=None):
    """看这台设备上装了什么游戏包。返回 [(pkg, Channel|None)]（兼容旧调用）。"""
    return [(g.pkg, g.channel) for g in list_games(adb, with_saves=False)]


def find_pkg_by_save_id(adb, save_id, pkgs=None, on_progress=None):
    """按存档 id（= md5(账号)）反查到到底是哪个渠道包。

    真正干活的是 `localdb.device.find_save_by_id`（惰性 import，避免
    同层模块互相 import）。这里只是给只需包名的调用方一个薄包装。
    """
    from ..localdb.device import find_save_by_id
    pkg, _remote = find_save_by_id(adb, save_id, pkgs, on_progress)
    return pkg


def describe(adb, on_progress=None):
    """打印当前设备情况，给用户看。"""
    devs = adb.devices()
    if not devs:
        ui.info('  adb 里一台设备都没有。')
        ui.info('  · 模拟器要开着，并且 adb 能连上（有些要在模拟器设置里打开"ADB 调试"）')
        ui.info('  · 雷电/夜神/蓝叠的 serial 一般是 emulator-5554 或 127.0.0.1:5555')
        return []
    ui.info(f'  adb: {adb.exe}  （{adb.exe_src}）')
    ui.info(f'  在线设备 {len(devs)} 台: {", ".join(devs)}')
    if len(devs) > 1:
        ui.info(f'  当前用: {adb.serial}   （想换用 --serial 指定，见上面列表）')
    return devs


# ───────────────────────── 交互式选择 ─────────────────────────
def _interactive():
    return ui.is_tty()


def _menu(cand, on_progress=None):
    """多个渠道候选时列编号菜单。用户不需要事先知道渠道叫什么。

    用户敲 q -> 抛 `ui.Cancelled`（上层直接退出，不猜一个用）；
    Ctrl+C 原样向上抛，由入口中止。
    """
    i = ui.choose(
        f'这台设备上装了 {len(cand)} 个渠道的游戏，请选一个（你正在玩的那个）：',
        [g.line() for g in cand])
    return cand[i]


def choose_game(adb, games, pkg_hint=None, save_id=None, allow_pick=True,
                on_progress=None):
    """在"这台设备上的多个渠道"里选一个。返回 Game 或 None。"""
    def say(m):
        if on_progress:
            on_progress(m)

    if pkg_hint:
        pkg = channels.resolve_pkg(pkg_hint)
        for g in games:
            if g.pkg == pkg:
                return g
        return Game(pkg=pkg, channel=channels.channel_of_pkg(pkg))

    if not games:
        return None
    if len(games) == 1:
        return games[0]

    # ① save.id 反查（最准）
    if save_id:
        say(f'  这台设备上有 {len(games)} 个渠道的游戏，按存档 id 反查 …')
        pkg = find_pkg_by_save_id(adb, save_id, [g.pkg for g in games],
                                  on_progress=on_progress)
        for g in games:
            if g.pkg == pkg:
                say(f'  → 是「{g.name}」')
                return g

    # ② 正在运行的那个 —— 只有运行中的进程内存里才有密钥，
    #    所以这个信号比"上次用过"更硬（上次用过的那个可能根本没开）
    run = [g for g in games if g.running]
    if len(run) == 1:
        say(f'  「{run[0].name}」正在运行，用它')
        return run[0]

    # ③ 上次用过的
    last = keytables.known_pkg()
    for g in games:
        if last and g.pkg == last:
            say(f'  沿用上次用过的「{g.name}」')
            return g

    cand = run or games

    # ④ 问用户
    if allow_pick and len(cand) > 1 and _interactive():
        return _menu(cand, on_progress)
    if len(cand) > 1:
        say('  （不能交互选择，默认用第一个；要指定请加 --pkg <渠道名>）')
    return cand[0]


# ───────────────────────── 总入口 ─────────────────────────
def pick_game_device(exe=None, serial=None, pkg_hint=None, save_id=None,
                     allow_pick=True, on_progress=None, remember=True):
    """挑一台**装着游戏**的设备 + 上面正确的**渠道包**。

    save_id = md5(账号)：给了就能在同一设备的多个渠道里精确定位。
    返回 (Adb, pkg, Channel|None)；找不到时 pkg 为 None。
    """
    def say(m):
        if on_progress:
            on_progress(m)

    adb = make_adb(exe, serial, on_progress)
    devs = adb.devices()
    if not devs:
        raise AdbError(
            ' adb 里没有任何设备。\n'
            '  怎么办：\n'
            '    1. 先启动模拟器（MuMu / 雷电 / 夜神 / 蓝叠…）\n'
            '    2. 确认命令行里 `adb devices` 能看到它\n'
            '    3. 看不到就试 `adb connect 127.0.0.1:7555`（MuMu）\n'
            '       或 `adb connect 127.0.0.1:5555`（雷电/蓝叠）')

    # 显式指定 serial 就只看它，否则按优先级逐台看
    order = [serial] if (serial and serial in devs) else Adb.rank(devs)
    say(f'看 {len(order)} 台设备：{", ".join(order)}')

    fallback = None      # 第一台装了游戏的（save.id 都没命中时用它）
    for d in order:
        adb.serial = d
        adb._resolved = True
        try:
            mod = adb.model()
        except Exception:
            mod = '?'
        games = list_games(adb)
        if not games:
            say(f'  {d}（{mod}）→ 没装这个游戏')
            continue
        say(f'  {d}（{mod}）→ ' + '、'.join(f'{g.name}' + ('(运行中)' if g.running else '')
                                            for g in games))

        # 有 save_id：逐台找"就是它"的那台/那个包
        if save_id and not pkg_hint:
            pkg = find_pkg_by_save_id(adb, save_id, [g.pkg for g in games],
                                      on_progress=on_progress)
            g = next((x for x in games if x.pkg == pkg), None)
            if g:
                say(f'  存档 id 命中：「{g.name}」')
                _remember(adb, g.pkg, remember)
                return adb, g.pkg, g.channel
            fallback = fallback or (d, games)
            continue

        g = choose_game(adb, games, pkg_hint, None, allow_pick, on_progress)
        _remember(adb, g.pkg if g else None, remember)
        return adb, (g.pkg if g else None), (g.channel if g else None)

    # 没有一台"命中"（或都没装）—— 回到第一台装了游戏的
    if fallback:
        d, games = fallback
        adb.serial = d
        adb._resolved = True
        say(f'  存档 id 没在任何包里的找到，回到 {d}')
        g = choose_game(adb, games, pkg_hint, None, allow_pick, on_progress)
        _remember(adb, g.pkg if g else None, remember)
        return adb, (g.pkg if g else None), (g.channel if g else None)

    adb.serial = order[0]
    adb._resolved = True
    _remember(adb, None, remember)
    return adb, None, None


def _remember(adb, pkg, remember=True):
    if not remember:
        return
    try:
        keytables.remember_device(adb=adb.exe, serial=adb.serial, pkg=pkg)
    except Exception:
        pass
