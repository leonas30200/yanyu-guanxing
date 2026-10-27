# -*- coding: utf-8 -*-
"""密钥表 —— 唯一真源。

内层密钥结构（逆向确认）:
    内层密钥(16) = psw(8, 按 **版本** 变化) + suffix(8, 按 **渠道** 变化)
    - psw    跨渠道、跨平台共用（同版本号只要一份）
    - suffix 每个渠道一份；iOS 与安卓 **不同**

外层密钥:
    每账号一把的随机 16 字符 token，只在游戏进程内存里，无法推导 —— 必须"挖"。

来源:
  · 游戏进程内存实读（自己没越狱设备时，iOS 那几个只能靠别的途径）
  · 第三方导出工具的内嵌密钥表（它把版本/渠道密钥写在 exe 里了，搜字符串就能抠出来）
    在无越狱 iOS 设备的情况下，这是拿 iOS 内层密钥最省事的办法。
更新游戏后: 跑 `python -m yyjh key crack` —— 自动去游戏内存里把新版本密钥挖出来
写进 keys.json（**不动本文件**，下次更新也不用改代码）；
或者 `python -m yyjh exe tables <工具.exe> --save` 从第三方导出工具里提表。
"""
import json
import os

from . import paths

# ────────────────────────────────────────────────────────────────
# 1. 渠道后缀
# ────────────────────────────────────────────────────────────────
CHANNEL_SUFFIXES = {
    'po06ewkx': '官服 / 安卓正式服',
    'cgkmi4cq': '先行服',
    'yoqkmrtj': 'iOS',
    'meeo0wmk': '新服（较新版本）',
}

# ────────────────────────────────────────────────────────────────
# 2. 内层密钥原始表:  (runtime, version, psw, suffix)
# ────────────────────────────────────────────────────────────────
INNER_KEY_ROWS = [
    # —— 安卓 ——
    ('android', '00073314', 'jkimeicc', 'po06ewkx'),
    ('android', '00073315', 'u6cgjphe', 'po06ewkx'),
    ('android', '00073420', 'n0b06b00', 'po06ewkx'),
    ('android', '00073440', 'pon1dxg7', 'po06ewkx'),      # 当前安卓
    ('android', '00073302', 'njglp5lv', 'cgkmi4cq'),      # 先行服
    ('android', '00073315', 'u6cgjphe', 'cgkmi4cq'),      # 先行服
    ('android', '00073645', '15fgt27w', 'meeo0wmk'),      # 新服
    ('android', '00073659', 'dasf34df', 'meeo0wmk'),
    ('android', '00073661', 'wmjhuy6d', 'meeo0wmk'),
    # —— iOS ——
    ('ios', '00073314', 'jkimeicc', 'yoqkmrtj'),
    ('ios', '00073315', 'u6cgjphe', 'yoqkmrtj'),
    ('ios', '00073440', 'pon1dxg7', 'yoqkmrtj'),          # 当前 iOS ✅ 已实测
    ('ios', '00073420', 'n0b06b00', 'po06ewkx'),          # ⚠ 全表唯一例外
    ('ios', '00071352', '15fgt27w', 'meeo0wmk'),
    ('ios', '00073661', 'wmjhuy6d', 'meeo0wmk'),
    # —— 73492: psw 不再写死在 JHCrypto.lua，而是随存档下发 ——
    # extra 字段用 realKey 解 XXTEA 即得 psw（实测 10-22 / 10-23 两次均为 xi9iz9h4）
    ('ios', '00073492', 'xi9iz9h4', 'yoqkmrtj'),           # ✅ 已实测（iOS 真机）
    ('android', '00073492', 'xi9iz9h4', 'po06ewkx'),       # ✅ 已实测（安卓官服）
    ('ios', '00073460', 'u52kw6lb', 'yoqkmrtj'),           # ✅ 已实测
    ('android', '00073460', '3598gjjd', 'tekelad9'),       # ✅ 已实测
    # —— 旧版本（早期从内存读到，官服后缀）——
    ('android', '00072280', 'hjo0e3q9', 'po06ewkx'),
    ('android', '00072288', 'jkds85we', 'po06ewkx'),
    ('android', '00072294', 'juhycd8e', 'po06ewkx'),
    ('android', '00073288', '90qabeon', 'po06ewkx'),
]

# 派生: {(runtime, version): 16位内层密钥}
INNER_KEYS = {(rt, ver): psw + suf for rt, ver, psw, suf in INNER_KEY_ROWS}

# 版本 -> psw（跨渠道共用，用于"同版本换后缀兜底"）
_PSW_BY_VERSION = {}
for _rt, _ver, _psw, _suf in INNER_KEY_ROWS:
    _PSW_BY_VERSION.setdefault(_ver, _psw)


# ────────────────────────────────────────────────────────────────
# 3. 其它已知密钥（.whale / res_encrypt / webview 等）
# ────────────────────────────────────────────────────────────────
CACHE_KEYS = {
    'bigda': 'JJyyt973',
    'keiiy': 'NanShuGo',
    'lua_xxtea': 'LanJingNo1',
    'webview': ['NatcGX9tQa', 'iGdDQMh1'],
}


# ────────────────────────────────────────────────────────────────
# 5. 查询 / 覆盖
# ────────────────────────────────────────────────────────────────
def _load_json(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:                                  # noqa: BLE001
        return {}


def _write_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


KEYS_NOTE = [
    "内层密钥表（可以任人分享、任意提交，跟账号无关）。",
    "inner_keys：runtime + 版本号 -> 16 位内层密钥（内层密钥 = psw(8) + suffix(8)）",
    "新密钥：python -m yyjh key crack（会自动写进来）；并入别处的表：key import <文件>",
]
LOCAL_NOTE = [
    "本机 / 账号相关，**不要分享、不要提交**（已在 .gitignore 里）。",
    "outer_keys      ：账号 -> 16 位外层密钥（每账号一把，别人拿去也没用）",
    "outer_keys_by_id：save 表里的 id（md5）-> 16 位外层密钥",
    "adb / serial / pkg：上次用的环境，留空就自动发现",
]


def _load_overlay():
    """合并视图：keys.json（可分享）+ keys.local.json（本机）+ 老的 keys.user.json。

    优先级从低到高：老的合并文件 -> keys.json -> keys.local.json。
    """
    out = {}
    for src in (_load_json(paths.LEGACY_KEYS), _load_json(paths.KEYS_FILE),
                _load_json(paths.LOCAL_FILE)):
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = dict(out[k], **v)
            else:
                out[k] = v
    return out


def _save_overlay(ov):
    """写盘：inner_keys 去 keys.json（可以给出去），其余去 keys.local.json（不进 git）。"""
    keys = {'_说明': KEYS_NOTE}
    local = {'_说明': LOCAL_NOTE}
    for k, v in ov.items():
        if k == '_说明':
            continue
        (keys if k == 'inner_keys' else local)[k] = v
    _write_json(paths.KEYS_FILE, keys)
    _write_json(paths.LOCAL_FILE, local)


def migrate_legacy():
    """早期的 keys.user.json 把密钥和本机信息混在一起 -> 拆成两个文件。

    返回给用户看的说明（没什么可做的就返回 None）。原文件会**改名保留**，不删。
    """
    if not os.path.exists(paths.LEGACY_KEYS):
        return None
    old = _load_json(paths.LEGACY_KEYS)
    _save_overlay(_load_overlay())
    bak = paths.LEGACY_KEYS + '.bak'
    try:
        if os.path.exists(bak):
            os.remove(bak)
        os.replace(paths.LEGACY_KEYS, bak)
    except OSError:
        bak = paths.LEGACY_KEYS
    return ('已把老的 keys.user.json 拆成两个文件：\n'
            f'  · {os.path.basename(paths.KEYS_FILE)} —— 密钥表，'
            '可以分享/提交/从别人那拉一份覆盖\n'
            f'  · {os.path.basename(paths.LOCAL_FILE)} —— 本机与账号信息，'
            '已加进 .gitignore，别分享\n'
            f'  （原文件改名保留：{os.path.basename(bak)}；'
            f'共 {len(old.get("inner_keys") or [])} 条密钥）')


def import_keys(src_path):
    """把另一份密钥表（keys.json）并进来 —— 只补缺的，不动本机已有的。

    对应"去 GitHub 重新拉一份 / 别人发我一份"的玩法。返回 (新增, 总数)。
    """
    data = _load_json(src_path)
    rows = data.get('inner_keys') or []
    ov = _load_overlay()
    mine = ov.setdefault('inner_keys', [])
    have = {(r.get('runtime'), r.get('version')) for r in mine}
    added = 0
    for r in rows:
        k = (r.get('key') or '').strip()
        if len(k) != 16:
            continue
        rt, ver = r.get('runtime') or 'android', r.get('version') or ''
        if (rt, ver) in have:
            continue
        mine.append({'runtime': rt, 'version': ver, 'key': k})
        have.add((rt, ver))
        added += 1
    _save_overlay(ov)
    return added, len(mine)


def psw_of_version(version):
    """版本号 -> 8 位 psw（**跨平台、跨渠道共用**）。

    数据来源两处都看：keys.json（新挖到的）优先，再退到内置表。
    为什么要单独抽这个函数：安卓挖到的那把，iOS 那边换个后缀就能直接用 ——
    而 `runtime` 只是"在哪台客户端上观察到的"，不该成为能不能用的门槛。
    """
    for row in (_load_overlay().get('inner_keys') or []):
        if row.get('version') != version:
            continue
        k = row.get('key') or ''
        if len(k) == 16:
            return k[:8]
    return _PSW_BY_VERSION.get(version)


# 各平台「最可能用的后缀」顺序。只是**排序**，不是硬规则：
# 后缀是按渠道走的 —— iOS 客户端也可能走官服后缀（表里 00073420 就是）。
# 真正能不能解开，最终靠 `try_inner()` 把全部候选试一遍，所以这里猜错不会出错。
_PREFERRED_SUFFIX = {
    'ios': ['yoqkmrtj', 'meeo0wmk', 'po06ewkx', 'cgkmi4cq'],
    'android': ['po06ewkx', 'meeo0wmk', 'cgkmi4cq', 'yoqkmrtj'],
}


def suffix_order(runtime):
    """"这个平台最可能用哪个后缀"的顺序。

    先看**这个平台其它版本实际用过的后缀**（玩家玩哪个渠道是固定的 ——
    官服玩家历史上几条都是 po06ewkx，那新版本大概率也是），再退到默认顺序。
    """
    seen = []
    for rt, _ver, _psw, suf in INNER_KEY_ROWS:
        if rt == runtime and suf not in seen:
            seen.append(suf)
    for row in (_load_overlay().get('inner_keys') or []):
        if (row.get('runtime') or 'android') != runtime:
            continue
        k = row.get('key') or ''
        if len(k) == 16 and k[8:] not in seen:
            seen.append(k[8:])
    for s in _PREFERRED_SUFFIX.get(runtime, list(CHANNEL_SUFFIXES)):
        if s not in seen:
            seen.append(s)
    return seen


def lookup_inner_key(runtime, version, extras=()):
    """查内层密钥。返回 (key, note)；找不到返回 (None, reason)。

    查找顺序:
      1. keys.json 里精确的 (runtime, version)
      2. 内置表里精确的 (runtime, version)
      3. ★ 同版本的**其它平台/渠道** —— 因为
         内层密钥 = psw(8, 按**版本**走) + suffix(8, 按**渠道**走)，
         而 psw 是跨平台、跨渠道共用的。只存了 ios 那条时，android 一样能用：
         拿它的 psw 拼上 android 可能的后缀即可（解的时候会把候选全试一遍，
         所以这里只是"猜一个最可能的"，猜错也不会出错）。
      4. extras（调用方显式给的候选）
    """
    ov = _load_overlay()
    for row in ov.get('inner_keys', []):
        if row.get('runtime') == runtime and row.get('version') == version:
            return row['key'], os.path.basename(paths.KEYS_FILE)

    k = INNER_KEYS.get((runtime, version))
    if k:
        return k, '内置表'

    psw = psw_of_version(version)
    if psw:
        others = sorted({rt for (rt, ver) in INNER_KEYS if ver == version} |
                        {r.get('runtime') or 'android'
                         for r in (ov.get('inner_keys') or [])
                         if r.get('version') == version})
        where = '、'.join(others) or '别的平台'
        suf = suffix_order(runtime)[0]
        return psw + suf, (f'同版本换后缀：{psw} + {suf}'
                           f'（记录里只有 {where} 那条，psw 是共用的；'
                           f'解的时候会把各渠道后缀都试一遍）')

    for cand in extras:
        if cand and len(cand) == 16:
            return cand, '调用方指定'

    return None, f'未知版本 {runtime}/{version}（请跑 `yyjh key crack` 或 `yyjh exe tables`）'


def candidate_inner_keys(runtime, version):
    """给定版本号能想到的所有候选（用于逐个试解密）。"""
    out = []
    k = INNER_KEYS.get((runtime, version))
    if k:
        out.append(k)
    psw = _PSW_BY_VERSION.get(version)
    if psw:
        for suf in CHANNEL_SUFFIXES:
            c = psw + suf
            if c not in out:
                out.append(c)
        # 未知后缀时，干脆把"任意版本已出现过的 psw"也排上
    for (rt, ver), k2 in INNER_KEYS.items():
        if ver == version and k2 not in out:
            out.append(k2)
    # keys.json 里同版本的也要算 —— psw 按版本走、跨渠道跨平台共用，
    # 所以安卓挖到的 psw，iOS 那边换个后缀就能直接用
    for row in (_load_overlay().get('inner_keys') or []):
        if row.get('version') != version:
            continue
        k2 = row.get('key') or ''
        if len(k2) == 16:
            psw2 = k2[:8]
            for suf in CHANNEL_SUFFIXES:
                c = psw2 + suf
                if c not in out:
                    out.append(c)
    return out


def all_versions(runtime=None):
    return sorted({ver for rt, ver in INNER_KEYS if runtime in (None, rt)})


# ───────────────────── 主表/兜底的合并 ─────────────────────
def ensure_seeded():
    """把内置表灌进 keys.json（只补缺的，不动用户自己加/改过的）。

    设计意图：**keys.json 是主表** —— 新挖到的密钥都写它，
    代码里的 INNER_KEY_ROWS 只当兜底（永远能查、不会被改坏）。
    这样「密钥」只有一个地方要改要看，游戏更新后不用碰代码。

    返回新补了几条。
    """
    ov = _load_overlay()
    rows = ov.setdefault('inner_keys', [])
    have = {(r.get('runtime'), r.get('version')) for r in rows}
    added = 0
    for rt, ver, psw, suf in INNER_KEY_ROWS:
        if (rt, ver) in have:
            continue
        rows.append({'runtime': rt, 'version': ver, 'key': psw + suf})
        have.add((rt, ver))
        added += 1
    if added:
        _save_overlay(ov)
    return added


def all_inner_rows():
    """内置表 + keys.json 合并后的全部内层密钥：[(runtime, version, key, 来源)]。"""
    out = []
    have = set()
    src = os.path.basename(paths.KEYS_FILE)
    for row in (_load_overlay().get('inner_keys') or []):
        k = row.get('key') or ''
        if len(k) == 16:
            rt, ver = row.get('runtime') or 'android', row.get('version') or ''
            out.append((rt, ver, k, src))
            have.add((rt, ver))
    for rt, ver, psw, suf in INNER_KEY_ROWS:
        if (rt, ver) not in have:
            out.append((rt, ver, psw + suf, '内置表'))
    return out


def outer_key(account=None, save_id=None):
    """查已知的外层密钥（每账号一把，只能从内存里挖）。

    `dat.db` 里只有 `md5(账号)`（= save_id），没有账号本身，
    所以两种都支持：账号优先，再用 save_id 兑。
    """
    ov = _load_overlay()
    if account:
        k = (ov.get('outer_keys') or {}).get(account)
        if k:
            return k
    if save_id:
        return (ov.get('outer_keys_by_id') or {}).get(save_id)
    return None


# ───────────────────────── 设备记忆 ─────────────────────────
def known_adb():
    """上次用过的 adb 路径（找不到 adb 时可以少让用户配一次）。"""
    return _load_overlay().get('adb')


def known_serial():
    """上次用过的设备 serial。"""
    return _load_overlay().get('serial')


def known_adb_port():
    """上次用过的 adb server 端口（可为空 = 用默认 5037）。"""
    return _load_overlay().get('adb_port')


def known_pkg():
    return _load_overlay().get('pkg')


def remember_device(adb=None, serial=None, pkg=None):
    """把用顺手的 adb / serial / 包名记下来，下次自动用。"""
    ov = _load_overlay()
    if adb:
        ov['adb'] = adb
    if serial:
        ov['serial'] = serial
    if pkg:
        ov['pkg'] = pkg
    _save_overlay(ov)


def remember_outer_key(account, key, save_id=None):
    """把挖到的外层密钥写进 keys.local.json（本机/账号相关，不进 git）。

    save_id = md5(账号)，两个索引都记 —— 因为 `local` 路由先只能拿到 save_id，
    解出来之后才补上账号。
    """
    ov = _load_overlay()
    if account:
        ov.setdefault('outer_keys', {})[account] = key
    if save_id:
        ov.setdefault('outer_keys_by_id', {})[save_id] = key
    _save_overlay(ov)


def remember_real_key(account, key):
    """抓包拿到的**协议 realKey**（acc[:5] + "eakec" + 6 位）。

    它只在网络上用（upload/download 载荷），**不是**本地 dat.db 的外层密钥 ——
    而本地存档的外层密钥是内存里的一串 16 位随机值 —— 同一个账号上两者也毫无关系，
    形状、来源都不一回事。
    以前把 realKey 记进 outer_keys，结果 `local` 路由拿它去解本地存档必然失败，
    还会"先命中错误的记录"从而跳过内存扫描 —— 所以单独开一个桶存它。
    """
    if not account or not key:
        return
    ov = _load_overlay()
    ov.setdefault('real_keys', {})[account] = key
    _save_overlay(ov)


def remember_inner_key(runtime, version, key):
    # 先保证主表是完整的（内置表也并进来）—— 只补缺的，不会覆盖已有的
    try:
        ensure_seeded()
    except Exception:                                  # noqa: BLE001
        pass
    ov = _load_overlay()
    rows = ov.setdefault('inner_keys', [])
    for r in rows:
        if r.get('runtime') == runtime and r.get('version') == version:
            r['key'] = key
            break
    else:
        rows.append({'runtime': runtime, 'version': version, 'key': key})
    _save_overlay(ov)
