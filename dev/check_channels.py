# -*- coding: utf-8 -*-
"""自检：多渠道 / 多设备下的"选哪个包"逻辑。

跑法: python -u dev\\check_channels.py
不连设备（用 FakeAdb），随时可跑。
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, '.')

from yyjh.config import channels, paths                # noqa: E402
from yyjh.memscan import devices                       # noqa: E402

_FAKE_DIR = tempfile.mkdtemp(prefix='yyjh_check_')


def use_keys(obj=None):
    """把 keytables 读的文件指到临时目录，免得受本机记录影响。

    注意要把**三个**路径都换掉：主密钥表、本机信息、以及老的单文件。
    """
    paths.KEYS_FILE = os.path.join(_FAKE_DIR, 'keys.json')
    paths.LOCAL_FILE = os.path.join(_FAKE_DIR, 'keys.local.json')
    paths.LEGACY_KEYS = os.path.join(_FAKE_DIR, 'legacy-none.json')
    paths.USER_KEYS = paths.KEYS_FILE
    p = paths.KEYS_FILE
    if obj is None:
        if os.path.exists(p):
            os.remove(p)
    else:
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(obj, f)


class FakeAdb:
    """只实现 choose_game 需要的那几个方法。"""

    def __init__(self, hit=False):
        self.hit = hit

    def sh(self, cmd, timeout=300):
        # 模拟 grep -l：不命中就空输出（注意输出的是文件名，不是 id）
        return 0, ('/data/data/x/files/a/server1/dat.db\n' if self.hit else ''), ''

    def ls(self, pattern):
        return ['/data/data/x/files/a/server1/dat.db'] if self.hit else []


def G(pkg, pid=None, saves=()):
    return devices.Game(pkg=pkg, channel=channels.channel_of_pkg(pkg),
                        pid=pid, saves=list(saves))


OFF = 'com.lanjing.yanyu.lanjing'
HW = 'com.lanjing.yanyu.huawei'
BI = 'com.lanjing.yanyu.bilibili'
SID = 'a' * 32


def check(name, got, want):
    ok = (got.pkg if got else None) == (want or None)
    print(f'{"✅" if ok else "❌"} {name:34} -> {got.pkg if got else None}')
    ok_list.append(ok)
    return ok


ok_list = []
use_keys(None)                       # 先屏蔽本机记录，保证可复现

check('单个包', devices.choose_game(FakeAdb(), [G(OFF)]), OFF)
check('一个都没有', devices.choose_game(FakeAdb(), []), None)
check('--pkg huawei',
      devices.choose_game(FakeAdb(), [G(OFF), G(HW)], pkg_hint='huawei'), HW)
check('--pkg 哔哩哔哩',
      devices.choose_game(FakeAdb(), [G(OFF), G(BI)], pkg_hint='哔哩哔哩'), BI)
check('--pkg 直接写包名',
      devices.choose_game(FakeAdb(), [G(OFF), G(BI)], pkg_hint=BI), BI)
check('save.id 命中（多渠道）',
      devices.choose_game(FakeAdb(hit=True), [G(OFF), G(HW), G(BI)], save_id=SID), OFF)

# 只有华为在运行 —— 应当压过"上次用过的官服"（只有它能给出密钥）
use_keys({'pkg': OFF})
check('正在运行 > 上次用过',
      devices.choose_game(FakeAdb(), [G(OFF), G(HW, pid='123'), G(BI)], save_id=SID), HW)

# 都没运行 —— 这时才轮到"上次用过的"
check('都没运行 -> 沿用上次用过',
      devices.choose_game(FakeAdb(), [G(OFF), G(HW)], save_id=SID), OFF)

# 上次用过的那个设备上根本没有 —— 非交互取第一个
use_keys({'pkg': BI})
check('上次用过的不在 -> 取第一个',
      devices.choose_game(FakeAdb(), [G(OFF), G(HW)], save_id=SID, allow_pick=False), OFF)

# Game.line() 要能认人（服号 / 运行状态 / 存档数）
use_keys(None)
line = G(OFF, pid='123', saves=['/data/data/x/files/a/server12/dat.db',
                                '/data/data/x/files/b/server20/dat.db']).line()
print(f'\n  Game.line() -> {line}')
ok = ('12/20' in line) and ('运行中' in line) and ('2 个存档' in line)
print(f'{"✅" if ok else "❌"} line() 含服号/运行状态/存档数')
ok_list.append(ok)

# ── local 不传参时的"从设备上挑存档"（pick_save）──
from unittest import mock                              # noqa: E402

from yyjh.localdb import device as devmod              # noqa: E402

_SAVES = [
    {'remote': '/data/data/xx/files/a/server12/dat.db',          # 旧
     'id': 'a' * 32, 'player': '{"nick":"示例角色"}', 'content': 'c'},
    {'remote': '/data/data/xx/files/b/server20/dat.db',          # 新
     'id': 'b' * 32, 'player': '{"nick":"翟阮天"}', 'content': 'c'},
]
_MTIME = {_SAVES[0]['remote']: 1000, _SAVES[1]['remote']: 2000}
NEWEST = _SAVES[1]['remote']          # server20 的 mtime 更大
SECOND = _SAVES[0]['remote']          # 排序后第二 = server12


def _fake_ls(adb, pkg=None):
    return [s['remote'] for s in _SAVES]


def _fake_inspect(adb, remote, local_dir=None):
    for s in _SAVES:
        if s['remote'] == remote:
            return dict(s, local=remote)
    return None


devmod.find_save_dbs = _fake_ls
devmod.inspect = _fake_inspect
devmod.stat_mtime = lambda adb, remote: _MTIME.get(remote, 0)
quiet = lambda m: None                                 # noqa: E731


def pick_name(info):
    return info['remote'] if info else None


def run_menu(ans=None, raise_=None):
    """跑一次 pick_save，返回 ('ok', 结果) / ('cancelled', None) / ('kb', None)。"""
    devmod.ui.is_tty = lambda: True
    try:
        kw = {'side_effect': raise_} if raise_ else {'new': lambda *a: ans}
        with mock.patch('builtins.input', **kw):
            return 'ok', devmod.pick_save(object(), allow_pick=True,
                                          say=quiet, on_progress=quiet)
    except devmod.ui.Cancelled:
        return 'cancelled', None
    except KeyboardInterrupt:
        return 'kb', None
    finally:
        devmod.ui.is_tty = lambda: False


got = devmod.pick_save(object(), allow_pick=False, say=quiet, on_progress=quiet)
ok = pick_name(got) == NEWEST
print(f'{"✅" if ok else "❌"} 多个档 + 非交互 -> 取最新改动的     -> {pick_name(got)}')
ok_list.append(ok)

_, got2 = run_menu('2')
ok = pick_name(got2) == SECOND
print(f'{"✅" if ok else "❌"} 菜单输 "2" -> 第二个               -> {pick_name(got2)}')
ok_list.append(ok)

_, got3 = run_menu('')                      # 直接回车 -> 默认 = 最新的
ok = pick_name(got3) == NEWEST
print(f'{"✅" if ok else "❌"} 菜单直接回车 -> 默认最新          -> {pick_name(got3)}')
ok_list.append(ok)

kind, _ = run_menu('q')                     # q -> 主动取消
ok = kind == 'cancelled'
print(f'{"✅" if ok else "❌"} 菜单输 "q" -> 取消退出（不取第一个）-> {kind}')
ok_list.append(ok)

kind, _ = run_menu(raise_=KeyboardInterrupt)   # Ctrl+C -> 原样抛给入口
ok = kind == 'kb'
print(f'{"✅" if ok else "❌"} 菜单 Ctrl+C -> 中断中止            -> {kind}')
ok_list.append(ok)

kind, got4 = run_menu('乱七八糟')            # 无效输入 -> 回退默认
ok = kind == 'ok' and pick_name(got4) == NEWEST
print(f'{"✅" if ok else "❌"} 菜单输无效值 -> 回退默认          -> {pick_name(got4)}')
ok_list.append(ok)

# 候选列表要能显示 **角色名 / 服号 / 修改时间**，并且按新到旧排
saves = devmod.list_saves(object())
txt = '\n'.join(devmod.describe_saves(saves))
print(f'\n  候选列表（按修改时间从新到旧）->\n{txt}')
ok = all(k in txt for k in ('示例角色', '翟阮天', '12', '20', '最后修改'))
print(f'{"✅" if ok else "❌"} 候选列表含角色名/服号/修改时间')
ok_list.append(ok)
ok = saves and saves[0]['remote'] == NEWEST
print(f'{"✅" if ok else "❌"} list_saves 按修改时间从新到旧排     -> '
      f'{saves[0]["remote"] if saves else None}')
ok_list.append(ok)
ok_list.append(ok)

print(f'\n通过 {sum(ok_list)}/{len(ok_list)}')
sys.exit(0 if all(ok_list) else 1)
