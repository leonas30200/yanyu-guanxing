# -*- coding: utf-8 -*-
"""keyhunt（游戏更新后补新版本密钥）自检 —— 离线可跑，不碰设备、不碰真 keys.user.json。

覆盖：
  · 后缀推导：找到渠道后缀就取它前面 8 字节（拼在长串里也要能抠出来）
  · 前 8 字节不可打印 -> 不收
  · inner_body_of：初版直接拆；新版先解一层外层
  · hunt：候选里能挑出对的，全错就老实返回 None
  · probe（key probe）：内存里有这一版就命中、没有就老实说不、
    被 --budget-mb 截断要如实标出来（否则"没解开"会被当成结论）
  · 深挖进度：批次大（8 片/次）但进度要"一片一动"（设备端 echo 标记 + 流式读）
  · 命中后写进 keys.user.json，lookup 立刻能查到
  · ★ 跨平台/跨渠道：只存了 ios 那条时，android 也要能用
    （psw 按版本共用；否则会出现"密钥明明存好了却还让重新挖"）
  · `key crack --words` / `--inner-key` 两条命令路径（含入库）返回 0
  · 外层密钥也丢了（删了 keys.user.json）-> 自动补挖外层再挖内层
  · 版本未知时的报错：是 InnerKeyMissing，且文案里
    必须有「游戏更新」「别反复」和 `key crack`（用户明确要求的，别删）

关键：整个过程把 paths.USER_KEYS / paths.CACHE_DIR 指到临时目录，
所以**不会**动用户真身的 keys.user.json 和内存候选缓存。

用法::

    cd tool_1.1
    python dev\\check_keyhunt.py
"""
import io
import json
import logging
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yyjh import __main__ as cli          # noqa: E402
from yyjh import ui                       # noqa: E402
from yyjh.config import keytables, paths  # noqa: E402
from yyjh.game import savefmt             # noqa: E402
from yyjh.localdb import sqlitedb         # noqa: E402
from yyjh.memscan import keyhunt          # noqa: E402

_PASS = []
_FAIL = []
_LOG = io.StringIO()
_TMP = []

SUF = 'po06ewkx'                 # 官服后缀
PSW = 'u52kw6lb'                 # 自检用的假 psw
KEY = PSW + SUF                  # 16 位内层密钥
IOS_KEY = PSW + 'yoqkmrtj'       # 同版本 iOS 那把（psw 共用，后缀不同）
VER = '00073460'                 # 表里没有这一版（真实更新后的版本号）
DATA = {'EncryptID': '0123456789abcdef0123456789abcdef', 'ServerID': 12,
        'DBVersion': VER, 'MainRole': {'Name': '自检角色'}}


def check(name, cond, detail=''):
    if cond:
        _PASS.append(name)
        print(f'  [OK]   {name}')
    else:
        _FAIL.append(name)
        print(f'  [FAIL] {name}  {detail}')


# ───────────────────────── 造一个 dat.db ─────────────────────────
def make_db(path, version, key):
    from yyjh.game import player as player_mod
    content = savefmt.build_old(DATA, version, key)
    sid = savefmt.save_id_of(DATA['EncryptID'])
    sqlitedb.write_save(path, sid, content, player_mod.build(DATA, version))
    return content


def args_of(argv):
    return cli.build_parser().parse_args(argv)


def drop_key(runtime, version):
    """把临时 keys.json 里的某条删掉（自检专用，不碰真身）。"""
    d = json.load(open(paths.KEYS_FILE, encoding='utf-8'))
    d['inner_keys'] = [r for r in (d.get('inner_keys') or [])
                       if not (r.get('runtime') == runtime
                               and r.get('version') == version)]
    with open(paths.KEYS_FILE, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


# ───────────────────────── 用例 ─────────────────────────
def case_suffix():
    print('— 后缀推导（核心：suffix 已知 -> 前 8 字节就是 psw）—')
    blob = b'\x00\x01zz' + KEY.encode() + b'_tail'
    got = keyhunt.suffixed_candidates(blob, [SUF])
    check('长串里也能抠出前 8 字节', got == {KEY.encode()}, got)

    blob2 = b'\x01\x02\x03\x04\x05\x06\x07\x08' + SUF.encode()
    check('前 8 字节不可打印就不收', keyhunt.suffixed_candidates(blob2, [SUF]) == set(),
          keyhunt.suffixed_candidates(blob2, [SUF]))

    check('多个后缀一起找', keyhunt.suffixed_candidates(blob, [SUF, 'meeo0wmk'])
          == {KEY.encode()})

    check('ends_with_suffix 认尾',
          keyhunt.ends_with_suffix(KEY.encode(), [SUF])
          and not keyhunt.ends_with_suffix(b'xxxxxxxxxxxxxxxx', [SUF]))


def case_body():
    print('— 拆存档拿内层密文 —')
    old = savefmt.build_old(DATA, VER, KEY)
    x, ver, body, used = keyhunt.inner_body_of(old, 'android')
    check('初版：直接拆，不需要外层密钥',
          ver == VER and used == '' and len(body) > 0, (ver, used, len(body)))
    check('初版：拆出来的 x 就是它自己', x == old)

    new = savefmt.build_new(DATA, VER, KEY, 'outerkey12345678')
    x2, ver2, body2, used2 = keyhunt.inner_body_of(
        new, 'android', outer_key='outerkey12345678')
    check('新版：先解一层外层', ver2 == VER and used2 == 'outerkey12345678',
          (ver2, used2))
    check('新版解出来的内层密文和初版一致', body2 == body)

    check('外层密钥给错 -> 报错而不是静默',
          _raises(lambda: keyhunt.inner_body_of(new, 'android', outer_key='0' * 16),
                  ValueError))


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def case_hunt():
    print('— 爆破 —')
    old = savefmt.build_old(DATA, VER, KEY)
    _x, _v, body, _u = keyhunt.inner_body_of(old, 'android')

    key, how = keyhunt.hunt('android', VER, body, [SUF],
                            words=['wrongwrongwrong1', KEY], cache=None)
    check('候选里挑出对的', key == KEY, f'{key} / {how}')

    key2, how2 = keyhunt.hunt('android', VER, body, [SUF],
                              words=['aaaaaaaaaaaaaaaa'], cache=None)
    check('全错 -> 返回 None（不瞎报）', key2 is None, f'{key2} / {how2}')


def case_probe():
    print('— 探测（key probe：现在内存里有没有这一版的密钥）—')
    from yyjh.memscan import memory as mem

    old = savefmt.build_old(DATA, VER, KEY)
    _x, _v, body, _u = keyhunt.inner_body_of(old, 'android')

    # 造两块假内存：一块里有 psw（嵌在别的东西之间），一块全是干扰
    blob_hit = (b'\x00' * 64 + PSW.encode() + b'\x00' * 16 +
                b'junkjunk' + b'1234abcd' + b'\x00' * 32)
    blob_miss = b'abcdefgh12345678zzzzzzzz\x00' * 8
    regs = [mem.Region(0x1000 * 100, 0x1000 * 100 + 8 * 1048576, 'rw-p'),
            mem.Region(0x1000 * 3000, 0x1000 * 3000 + 8 * 1048576, 'rw-p')]
    real_maps, real_regions, real_dump = mem.read_maps, mem.regions, mem.dump_region
    real_probe_init = keyhunt._probe_init
    try:
        mem.read_maps = lambda adb, pid: ''            # probe 只用它喂 regions
        mem.regions = lambda *a, **k: list(regs)
        mem.dump_region = lambda adb, pid, r, **k: \
            blob_hit if r.start == regs[0].start else blob_miss

        res = keyhunt.probe(VER, body, [SUF], adb=object(), pid='1', workers=1)
        check('内存里有 psw -> 直接命中（× 后缀拼出来）',
              res['ok'] and res['key'] == KEY, f"{res['key']} / {res['tokens']}")
        check('把拉回来的块数/字节数报出来', res['regs'] == 2 and res['got'] > 0,
              res)
        check('拉完了 -> exhausted=True（结论可信）', res['exhausted'] is True, res)
        check('没有"已知版本段"命中也不硬凑',
              res['known_psws'] == [] or res['known_psws'][0][0] == PSW,
              res['known_psws'])

        # 密钥不在内存里的情况
        mem.dump_region = lambda adb, pid, r, **k: blob_miss
        res2 = keyhunt.probe(VER, body, [SUF], adb=object(), pid='1', workers=1)
        check('内存里没这一版 -> ok=False（不瞎报命中）',
              res2['ok'] is False and res2['key'] is None, res2['key'])

        # 预算截断要如实标出来（否则"没解开"会被当成结论）
        big = blob_miss * (3 * 1048576 // max(1, len(blob_miss)) + 1)
        mem.dump_region = lambda adb, pid, r, **k: big
        res3 = keyhunt.probe(VER, body, [SUF], adb=object(), pid='1',
                             workers=1, budget_mb=1)
        check('被 --budget-mb 截断 -> exhausted=False', res3['exhausted'] is False,
              res3)
    finally:
        mem.read_maps, mem.regions, mem.dump_region = real_maps, real_regions, real_dump
        keyhunt._probe_init = real_probe_init

    # 表里已经有（而且**真能解开这份靶子**）-> 直接短路，不去设备
    keytables.remember_inner_key('android', VER, KEY)
    real_dev, real_resolve = keyhunt._device_target, keyhunt._resolve_body
    try:
        keyhunt._device_target = lambda *a, **k: (object(), '1', None, None, None, '')
        keyhunt._resolve_body = lambda *a, **k: (VER, body, 'android', '', '')
        try:
            rc = keyhunt.probe_run(args_of(['key', 'probe', '--no-pick']))
        except Exception as e:                                 # noqa: BLE001
            rc = f'炸了: {e}'
        check('表里已有（且能解开）-> 直接退出 0（不白跑一趟设备）', rc == 0, rc)
    finally:
        keyhunt._device_target, keyhunt._resolve_body = real_dev, real_resolve
        # 清掉，别影响后面用例的"从零挖一遍"
        drop_key('android', VER)


def case_store():
    print('— 存储 —')
    db = os.path.join(tempfile.mkdtemp(prefix='yyjh_kh_'), 'dat.db')
    _TMP.append(os.path.dirname(db))
    make_db(db, VER, KEY)

    # ① --words 走完整流程（hunt + 入库），不碰设备
    ns = args_of(['key', 'crack', '--db', db, '--no-mem', '--words', KEY])
    rc = keyhunt.run(ns)
    check('key crack --words 返回 0', rc == 0, rc)
    found, note = keytables.lookup_inner_key('android', VER)
    check('挖到的密钥进了 keys.user.json', found == KEY, f'{found} / {note}')

    # ② --inner-key 直接入库（换个版本，避免和上一条打架）
    ver2 = '00099999'
    db2 = os.path.join(os.path.dirname(db), 'dat2.db')
    make_db(db2, ver2, 'q1w2e3r4po06ewkx')
    ns2 = args_of(['key', 'crack', '--db', db2, '--no-mem',
                   '--inner-key', 'q1w2e3r4po06ewkx'])
    check('key crack --inner-key 返回 0', keyhunt.run(ns2) == 0)
    found2, _ = keytables.lookup_inner_key('android', ver2)
    check('--inner-key 直接入库', found2 == 'q1w2e3r4po06ewkx', found2)

    # ③ 表里已有就别重复挖（返回 0 并且不报错）
    ns3 = args_of(['key', 'crack', '--db', db, '--no-mem'])
    check('表里已有 -> 直接退出 0', keyhunt.run(ns3) == 0)

    # ④ 落盘的文件得是真的 JSON（人手也能看懂/手改）
    raw = open(paths.KEYS_FILE, encoding='utf-8').read()
    check('keys.json 是合法 JSON 且有 inner_keys',
          '"inner_keys"' in raw and '"version": "00099999"' in raw, raw[:200])
    # 本机/账号相关的东西不能混进可分享的那份
    check('外层密钥进了 keys.local.json（不进 git 那份）',
          'outer_keys_by_id' in open(paths.LOCAL_FILE, encoding='utf-8').read()
          and 'outer_keys' not in raw, raw[-200:])

    # ⑤ 同版本换渠道/换平台：psw 共用，后缀换一下就该能试出来
    #    （安卓挖到 u52kw6lb+po06ewkx -> iOS 那边 u52kw6lb+yoqkmrtj 也要排进候选）
    cands = keytables.candidate_inner_keys('ios', VER)
    check('同版本别的平台会带上共用 psw 的候选',
          PSW + 'yoqkmrtj' in cands, [c for c in cands if c.startswith(PSW)])


def case_outer_lost():
    """外层密钥也丢了（比如把 keys.user.json 删了）-> 就地补挖外层，再挖内层。

    这就是"删掉 keys.user.json 之后连 local 都救不回来"的那个根因：
    新版存档要先用外层密钥解一层，而外层密钥也是记在 keys.user.json 里的。
    """
    print('— 外层密钥也丢了 -> 自动补挖 —')
    db = os.path.join(tempfile.mkdtemp(prefix='yyjh_kh2_'), 'dat.db')
    _TMP.append(os.path.dirname(db))
    outer = 'outerkey12345678'
    from yyjh.game import player as player_mod
    sid = savefmt.save_id_of(DATA['EncryptID'])
    sqlitedb.write_save(db, sid, savefmt.build_new(DATA, VER, KEY, outer),
                        player_mod.build(DATA, VER))

    calls = []
    real_dev, real_outer = keyhunt._device_target, keyhunt._crack_outer
    # 假装设备在、游戏在跑（pid 给个哨兵），但不真的碰内存
    keyhunt._device_target = (
        lambda a, rt, need_save=True: (object(), '19415', None, None, None, ''))

    def fake_outer(adb, pid, content, account, save_id, a):
        calls.append('crack_outer')
        keytables.remember_outer_key(account, outer, save_id=save_id)
        return outer

    keyhunt._crack_outer = fake_outer
    try:
        # --words 直接给内层候选，所以流程不会去扫内存
        ns = args_of(['key', 'crack', '--db', db, '--words', KEY])
        rc = keyhunt.run(ns)
    finally:
        keyhunt._device_target, keyhunt._crack_outer = real_dev, real_outer

    check('外层丢了也能跑通（返回 0）', rc == 0, rc)
    check('确实走了"补挖外层"这条路', calls == ['crack_outer'], calls)
    check('补挖到的外层密钥存住了', keytables.outer_key(save_id=sid) == outer,
          keytables.outer_key(save_id=sid))
    found, _ = keytables.lookup_inner_key('android', VER)
    check('内层密钥照样挖到并存住', found == KEY, found)


def case_swap():
    """★ 后缀互换：内存里只有安卓那把，也要能解 iOS 的存档。

    这是真踩过的坑 —— iOS 抓包报"密钥不在表里"，扫安卓内存只找到
    `...po06ewkx`（官服后缀），而 iOS 要的是 `...yoqkmrtj`。
    全量爆破救不了（那个串在安卓内存里压根不存在），只能靠 psw 共用这条规则推。
    """
    print('— 后缀互换（安卓内存 → iOS 密钥）—')
    pool = list(keytables.CHANNEL_SUFFIXES)
    got = keyhunt.expand_suffixes({KEY.encode()}, pool)
    check('一个渠道能推成所有渠道', IOS_KEY.encode() in got and len(got) == len(pool),
          got)
    check('不是 16 字节的原样留着（不炸）',
          keyhunt.expand_suffixes({b'abc'}, pool) == {b'abc'})

    # 真刀真枪：密文用的是 iOS 那把，而候选只给安卓那把
    content = savefmt.build_old(DATA, VER, IOS_KEY)
    _x, _v, body, _u = keyhunt.inner_body_of(content, 'ios')
    key, how = keyhunt.hunt('ios', VER, body, pool, words=[KEY], cache=None)
    check('只给安卓那把，也能把 iOS 的解出来', key == IOS_KEY, f'{key} / {how}')


def case_import():
    """并入别人的密钥表：只补缺，不覆盖本机已有的。"""
    print('— key import（并入外部的密钥表）—')
    tmpd = tempfile.mkdtemp(prefix='yyjh_kh3_')
    _TMP.append(tmpd)
    src = os.path.join(tmpd, 'other.json')
    with open(src, 'w', encoding='utf-8') as f:
        json.dump({'inner_keys': [
            {'runtime': 'android', 'version': '00070001',
             'key': 'zzzzzzzzpo06ewkx'},
            {'runtime': 'android', 'version': VER, 'key': 'bbbbbbbbpo06ewkx'},
        ]}, f)
    added, _total = keytables.import_keys(src)
    check('只补缺：新版本进来 1 条，已有的不覆盖', added == 1, added)
    found, _ = keytables.lookup_inner_key('android', VER)
    check('本机已有的没被改掉', found == KEY, found)
    found2, _ = keytables.lookup_inner_key('android', '00070001')
    check('外来的补进来了', found2 == 'zzzzzzzzpo06ewkx', found2)


def case_cli():
    print('— 命令行参数 —')
    ns = args_of(['key', 'crack', '--har', 'x.har', '--index', '2',
                  '--real-key', 'abc', '--db', 'd.db', '--no-mem',
                  '--suffix', SUF, '--force', '--all-regions'])
    check('--har / --index / --real-key 都注册了',
          ns.har == 'x.har' and ns.index == 2 and ns.real_key == 'abc', vars(ns))
    check('--db / --no-mem / --suffix / --force / --all-regions 也都在',
          ns.db == 'd.db' and ns.no_mem and ns.suffix == [SUF] and ns.force
          and ns.all_regions, vars(ns))
    ns2 = args_of(['key', 'sync'])
    check('key sync 注册了', hasattr(ns2, 'func'))


def case_deep_stream():
    """深挖的进度要"跑完一片就动一次"。

    批次大（一次 8 片）是为了速度（碎堆实测 batch=2 → 10.8 MB/s，batch=8 →
    19.9 MB/s），但**进度条不能再等一整批** —— 设备端每片完了 echo 个 `#N`，
    这边用 Adb.stream() 逐行读，读到就 tick。
    """
    print('— 深挖进度：跑完一片就动一次 —')
    from yyjh.memscan import memory as mem

    regs = [mem.Region(0x1000 * 100, 0x1000 * 100 + 8 * 4096, 'rw-p'),
            mem.Region(0x1000 * 900, 0x1000 * 900 + 8 * 4096, 'rw-p')]

    class FakeAdb:
        def stream(self, cmd, timeout=0, on_line=None):
            assert 'echo "#0"' in cmd and 'echo "#1"' in cmd, cmd
            for ln in ('u52kw6lb', '#0', 'abc', 'zzzzzzzz', '#1'):
                if on_line:
                    on_line(ln)
            return 0, ''

    ticks = []
    toks = keyhunt.deep_tokens(FakeAdb(), '1', regs, chunk_mb=1, batch=2,
                               on_progress=lambda *a: ticks.append(a))
    check('每片跑完都回调一次（2 片 -> 2 次）', len(ticks) == 2, ticks)
    check('字节按片累加', bool(ticks) and ticks[-1][0] == 8 * 4096 * 2, ticks)
    check('进度走到 2/2', bool(ticks) and ticks[-1][2] == ticks[-1][3] == 2, ticks)
    check('8 位串一个不漏（标记行不算候选）',
          toks == {b'u52kw6lb', b'zzzzzzzz'}, toks)

    class OldAdb:                      # 没有 stream 的旧封装/桩
        def sh(self, cmd, timeout=0):
            return 0, '#0\n#1\n', ''

    ticks2 = []
    toks2 = keyhunt.deep_tokens(OldAdb(), '1', regs, chunk_mb=1, batch=2,
                                on_progress=lambda *a: ticks2.append(a))
    check('没有 stream 时退回 sh()，不炸也不丢结构',
          toks2 == set() and len(ticks2) == 1, (toks2, ticks2))


def case_cross_platform():
    """内层密钥 = psw(按**版本**走, 跨平台跨渠道共用) + suffix(按**渠道**走)。

    所以只存了 ios 那条时，android 必须照样能用 —— 否则会出现
    "密钥明明存好了，却每次都提示还得重新挖"（用户实测撞到过）。
    """
    print('— 跨平台/跨渠道：只存 ios 那条，android 也得能用 —')
    body = keyhunt.inner_body_of(savefmt.build_old(DATA, VER, KEY), 'android')[2]

    # 把表改成"只有 ios/VER 一条"（临时目录里的 keys.json，改坏了也不影响真身）
    drop_key('android', VER)
    drop_key('ios', VER)
    keytables.remember_inner_key('ios', VER, IOS_KEY)

    k, note = keytables.lookup_inner_key('android', VER)
    check('只存 ios 那条时，查 android 也能拿到密钥', k == KEY, f'{k} / {note}')
    check('说明里讲清是"同版本换后缀"', '同版本换后缀' in note, note)

    got, note2 = keyhunt.known_inner_key('android', VER, body)
    check('闸门（真解一遍）认出"表里已有"', got == KEY, f'{got} / {note2}')

    # 真的用它解开一份 android 存档
    data, meta = savefmt.unwrap_old(savefmt.build_old(DATA, VER, KEY), 'android')
    check('android 存档照样解得开', meta['inner_key'] == KEY,
          f"{meta['inner_key']} / 试了 {meta['tried']} 个候选")

    # 命中后要能自动补上"当前平台"那条（写入逻辑也一起对齐）
    wrote = keyhunt.remember_for_runtime('android', VER, got)
    k2, note3 = keytables.lookup_inner_key('android', VER)
    check('命中后自动把 android 那条补进表', wrote and k2 == KEY, f'{wrote} / {k2}')
    check('补完之后就是精确命中（不再是"换后缀"）', '同版本换后缀' not in note3,
          note3)


def case_missing_message():
    print('— 版本未知的报错文案（用户明确要求的部分，别删）—')
    content = savefmt.build_old(DATA, '00099999', 'abcdefghijklmnop')
    try:
        savefmt.unwrap_old(content, 'android')
        check('未知版本 -> 抛 InnerKeyMissing', False, '没抛')
        return
    except savefmt.InnerKeyMissing as e:
        check('未知版本 -> 抛 InnerKeyMissing（不是普通 ValueError）', True)
        check('带版本号', e.version == '00099999', e.version)
        hints = '\n'.join(savefmt.inner_key_missing_hints(e))
        check('文案说明是游戏更新',
              '游戏刚更新' in hints or '游戏更新' in hints, hints[:120])
        check('文案里明说"别反复重跑"（别让人浪费时间乱试）', '别反复' in hints)
        check('文案给出了 key crack 命令', 'key crack' in hints)
        check('文案给了手动补 key add', 'key add' in hints)


def main():
    h = logging.StreamHandler(_LOG)
    h.setFormatter(logging.Formatter('%(message)s'))
    ui.LOG.addHandler(h)
    ui.LOG.setLevel(logging.DEBUG)
    ui.LOG.propagate = False

    # 隔离：别动用户真身的那几个文件
    tmp = tempfile.mkdtemp(prefix='yyjh_keyhunt_')
    _TMP.append(tmp)
    paths.KEYS_FILE = os.path.join(tmp, 'keys.json')
    paths.LOCAL_FILE = os.path.join(tmp, 'keys.local.json')
    paths.USER_KEYS = paths.KEYS_FILE
    paths.LEGACY_KEYS = os.path.join(tmp, 'legacy-none.json')
    paths.CACHE_DIR = os.path.join(tmp, '_cache')

    try:
        case_suffix()
        case_body()
        case_hunt()
        case_probe()
        case_swap()
        case_store()
        case_outer_lost()
        case_import()
        case_cli()
        case_missing_message()
        case_cross_platform()
        case_deep_stream()
    finally:
        for d in _TMP:
            shutil.rmtree(d, ignore_errors=True)

    print()
    print(f'通过 {len(_PASS)} / 失败 {len(_FAIL)}')
    for n in _FAIL:
        print(f'  失败: {n}')
    return 1 if _FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
