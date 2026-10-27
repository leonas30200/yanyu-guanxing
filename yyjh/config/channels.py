# -*- coding: utf-8 -*-
"""渠道 ↔ 包名表。

为什么需要它
    游戏有十几个渠道包，**包名都不一样**（官服 `com.lanjing.yanyu.lanjing`，
    华为 `.huawei`，B站 `.bilibili`…）。找设备上的 dat.db、找游戏进程
    都依赖包名，所以不能写死一个。

渠道名从哪来
    从游戏 APK / 第三方工具里提取的渠道映射表（`pm list packages` 里的真实包名）。

包名不确定怎么办
    工具会在设备上**直接扫包名**（含 `lanjing` / `yanyu` 的都算），
    所以即使这里没收录，一般也能自动找到。实在不行用 `--pkg 直接写包名`。

渠道和"内层密钥后缀"的关系
    内层密钥后 8 位是按渠道分的（官服 `po06ewkx`、先行服 `cgkmi4cq`、iOS `yoqkmrtj`…）。
    但**解密时不需要你告诉我们渠道** —— 工具会把同版本的所有后缀都试一遍，
    用 zlib+JSON 双重校验挑出对的那个（见 game/savefmt.try_inner）。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Channel:
    key: str            # 简写，命令行用得到（--pkg huawei）
    name: str           # 中文名，给人看
    pkg: str            # 安卓包名
    suffix: str = ''    # 已知的内层密钥渠道后缀（空 = 未知，靠自动试）


CHANNELS = [
    Channel('official', '官服', 'com.lanjing.yanyu.lanjing', 'po06ewkx'),
    Channel('preview', '先行服', 'com.lanjing.yanyu.ljpre', 'cgkmi4cq'),
    Channel('huawei', '华为', 'com.lanjing.yanyu.huawei'),
    Channel('honor', '荣耀', 'com.lanjing.yanyu.honor'),
    Channel('harmony', '鸿蒙', 'com.lanjing.yanyu.hwharmony'),
    Channel('xiaomi', '小米', 'com.lanjing.yanyu.mi'),
    Channel('meizu', '魅族', 'com.lanjing.yanyu.mz'),
    Channel('oppo', 'OPPO', 'com.lanjing.yanyu.net.nearme.gamecenter'),
    Channel('vivo', 'vivo', 'com.lanjing.yanyu.vivo'),
    Channel('aligames', '九游', 'com.lanjing.yanyu.aligames'),
    Channel('m4399', '4399', 'com.lanjing.yanyu.m4399'),
    Channel('bilibili', 'B站', 'com.lanjing.yanyu.bilibili'),
    Channel('huluxia', '葫芦侠', 'com.lanjing.yanyu.huluxia'),
    Channel('yuedong', '悦动', 'com.lanjing.yanyu.yuedong'),
    Channel('uc', 'UC', 'com.hetun.uc.yanyu'),
    Channel('tencent', '腾讯', 'com.tencent.tmgp.lanjing.yanyu'),
    Channel('oversea', '国际服', 'com.wuxia.novastar'),
]

# 默认（找不到别的就用这个）
DEFAULT_PKG = CHANNELS[0].pkg

BY_KEY = {c.key: c for c in CHANNELS}
BY_PKG = {c.pkg: c for c in CHANNELS}
ALIASES = {
    '官服': 'official', '正式服': 'official',
    '先行服': 'preview', '体验服': 'preview',
    '国际服': 'oversea', '海外': 'oversea',
    '小米': 'xiaomi', '华为': 'huawei', '荣耀': 'honor', '鸿蒙': 'harmony',
    '魅族': 'meizu', '九游': 'aligames', '4399': 'm4399', '哔哩哔哩': 'bilibili',
    '葫芦侠': 'huluxia', '悦动': 'yuedong', '腾讯': 'tencent',
}


# 这些词出现在包名里说明是**工具**，不是游戏本体（盲扫时要排掉）
_TOOL_HINTS = ('exporter', 'yanyustars', 'tool', 'helper', 'assistant',
               'mod', 'plugin', 'assist')


def looks_like_game(pkg):
    """包名长得像不像这个游戏本体（用来在设备上盲扫）。

    会排掉第三方工具包，比如 `dev.local.yanyustars`、`cn.suprecore.yanyuexporter`
    —— 它们名字里也有 yanyu，但不是游戏。
    """
    p = (pkg or '').lower()
    if any(t in p for t in _TOOL_HINTS):
        return False
    return 'lanjing' in p or 'yanyu' in p or 'novastar' in p


def all_packages():
    return [c.pkg for c in CHANNELS]


def resolve_pkg(value):
    """把 `--pkg` 的输入（渠道简写 / 中文名 / 包名）统一成包名。"""
    if not value:
        return DEFAULT_PKG
    v = value.strip()
    if v in BY_PKG:                       # 直接是包名
        return v
    low = v.lower()
    if low in BY_KEY:                     # 渠道简写
        return BY_KEY[low].pkg
    if v in ALIASES:                      # 中文名
        return BY_KEY[ALIASES[v]].pkg
    return v                              # 原样当包名（支持没收录的渠道）


def channel_of_pkg(pkg):
    """包名 -> Channel（没收录时返回 None）。"""
    if pkg in BY_PKG:
        return BY_PKG[pkg]
    for c in CHANNELS:
        if looks_like_game(pkg) and c.pkg == pkg:
            return c
    return None


def label(pkg):
    """给日志/报告用的显示名。"""
    c = channel_of_pkg(pkg)
    return f'{c.name}（{pkg}）' if c else pkg


def describe_all():
    """`yyjh key list` / 文档里用的表格数据。"""
    return [(c.name, c.key, c.pkg, c.suffix or '自动') for c in CHANNELS]


# ───────────────────────── 设备端探测 ─────────────────────────
def installed_packages(adb):
    """设备上装的所有包名。"""
    rc, out, _ = adb.sh('pm list packages 2>/dev/null', timeout=120)
    pkgs = []
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.startswith('package:'):
            pkgs.append(ln[len('package:'):])
    return pkgs


def find_game_packages(adb):
    """设备上所有"像这个游戏"的包（含已知渠道 + 盲扫 lanjing/yanyu）。

    返回 [(pkg, Channel|None, 是否已安装)]，已知渠道优先。
    """
    installed = set(installed_packages(adb))
    found = []
    for c in CHANNELS:
        if c.pkg in installed:
            found.append((c.pkg, c, True))
    for p in sorted(installed):
        if looks_like_game(p) and p not in BY_PKG:
            found.append((p, None, True))
    return found


def detect_pkg(adb, hint=None, on_progress=None):
    """自动挑一个游戏包名。

    hint 优先（可以是渠道名/中文名/包名）；否则用设备上扫到的第一个。
    返回 (pkg, Channel|None) 或 (None, None)。
    """
    if hint:
        pkg = resolve_pkg(hint)
        return pkg, channel_of_pkg(pkg)

    if on_progress:
        on_progress('在设备上找游戏包名 …')
    found = find_game_packages(adb)
    if not found:
        return None, None
    if on_progress:
        for p, c, _ in found:
            on_progress(f'  找到 {p}' + (f'（{c.name}）' if c else '（未收录渠道）'))
    pkg, c, _ = found[0]
    return pkg, c
