# -*- coding: utf-8 -*-
"""★ 第一层接口：分块基础功能。

设计目标：**把整条链路拆成互不依赖的小块**，游戏更新后哪一块对不上就单独调那一块，
不用每次都跑整条管道。每块只做"一件事"，输入输出都是文件或明文，方便逐步排查。

    python -m yyjh info     <任意文件>          自动识别这是什么
    python -m yyjh xxtea    ...                 XXTEA 加解密 / 爆破
    python -m yyjh key      ...                 查密钥表（crack = 更新后挖新密钥）
    python -m yyjh whale    ...                 .whale 容器
    python -m yyjh res      ...                 res_encrypt 资源
    python -m yyjh apk      ...                 APK
    python -m yyjh exe      ...                 exe/PE（含密钥表提取）
    python -m yyjh db       ...                 dat.db
    python -m yyjh har      ...                 抓包 HAR
    python -m yyjh mem      ...                 内存（adb / maps / dump / 爆破）
    python -m yyjh elf      ...                 .so 逆向
"""
import argparse
import json
import os
import sys

from . import __version__
from .config import channels, constants, keytables, paths
from .core import codecs, oracle, xxtea


# ────────────────────────── 通用 ──────────────────────────
def _p(x):
    """输入路径：先当 CWD 相对，再当工作区相对。"""
    return paths.resolve(x)


def _o(x):
    """输出路径：一律相对 CWD。"""
    return paths.resolve_out(x)


def _print_kv(d, indent=2):
    pad = ' ' * indent
    for k, v in d.items():
        if isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
            print(f'{pad}{k}:')
            for row in v:
                print(f'{pad}  - {row}')
        else:
            print(f'{pad}{k}: {v}')


def _progress(stage, msg):
    print(f'  [{stage}] {msg}', flush=True)


def cmd_info(a):
    """自动识别文件类型 —— 排查问题的第一步。"""
    p = _p(a.path)
    if not os.path.exists(p):
        print(f'文件不存在: {p}')
        return 1
    size = os.path.getsize(p)
    head = open(p, 'rb').read(4096)
    print(f'文件 : {p}')
    print(f'大小 : {size:,} B')

    guesses = []
    if head[:16] == b'SQLite format 3\x00':
        guesses.append('SQLite 数据库（很可能是 dat.db → 用 `yyjh db show`）')
    if head[:8] == b'YANYU201':
        guesses.append('.yanyu 旧版导出容器 —— 本工具**不支持**这种格式'
                       '（旧版格式，用旧版工具即可，见 README）')
    if head[:2] == b'MZ':
        guesses.append('PE 可执行文件（→ 用 `yyjh exe ...`）')
    if head[:4] == b'PK\x03\x04':
        guesses.append('ZIP/APK（→ 用 `yyjh apk ...`）')
    if head[:4] == b'\x7fELF':
        guesses.append('ELF 共享库（→ 用 `yyjh elf ...`）')
    if codecs.rol3(head[:8]) and (b'BIGDA' in codecs.rol3(head) or
                                  b'KEIIY' in codecs.rol3(head)):
        guesses.append('.whale 容器（→ 用 `yyjh whale info`）')
    if size > 8:
        try:
            import struct as _s
            declared, cnt, ver = _s.unpack('<IHH', head[:8])
            if 0 < ver < 4096 and declared <= size:
                pass
        except Exception:
            pass
    if p.lower().endswith('.har'):
        guesses.append('抓包 HAR（→ 用 `yyjh har list` / `yyjh run`）')
    if p.lower().endswith(('.whale',)):
        guesses.append('.whale 容器')
    if head[:5] in constants.RES_SIGN_XOR:
        guesses.append(f'res_encrypt 资源（签名 {head[:5].decode()}）'
                       f'（→ 用 `yyjh res dump`）')
    print('判断 :')
    for g in guesses or ['（没有明显特征，试试 `yyjh exe strings` 看看内容）']:
        print(f'  - {g}')

    if size < 5_000_000:
        strs = codecs.printable_strings(head, 8, limit=10)
        print('开头可见字符串:')
        for s in strs[:10]:
            print(f'  {s[:100]}')
    return 0


# ────────────────────────── XXTEA ──────────────────────────
def _load_ciphertext(a):
    if a.hex:
        import binascii
        return binascii.unhexlify(a.hex)
    if a.file:
        return open(_p(a.file), 'rb').read()
    raise SystemExit('需要 --file 或 --hex')


def cmd_xxtea(a):
    if a.action == 'selftest':
        print('XXTEA 自检:', '✅ 通过' if xxtea.self_test() else '❌ 失败')
        print('C 扩展加速:', '有' if xxtea.HAS_C_XXTEA else '无（纯 Python，较慢）')
        return 0

    if a.action in ('decrypt', 'encrypt', 'check'):
        ct = _load_ciphertext(a)
        if a.action == 'encrypt':
            out = xxtea.encrypt(ct, a.key)
            print(f'密文 {len(out)}B')
            if a.out:
                open(_o(a.out), 'wb').write(out)
                print(f'-> {_o(a.out)}')
            else:
                print(out.hex())
            return 0
        n = len(ct) // 4
        lo, hi = xxtea.length_window(n)
        ok, lw = xxtea.checks(ct, a.key)
        print(f'密文 {len(ct)}B  n={n}  合法末字区间=[{lo},{hi}]')
        print(f'末字 = {lw}   {"✅ 合法（钥匙正确）" if ok else "❌ 非法（钥匙不对）"}')
        if a.action == 'decrypt' and ok:
            pt = xxtea.decrypt(ct, a.key)
            print(f'明文 {len(pt)}B  头 64: {pt[:64]!r}')
            if a.out:
                open(_o(a.out), 'wb').write(pt)
                print(f'-> {_o(a.out)}')
        return 0

    if a.action == 'crack':
        ct = _load_ciphertext(a)
        cands = set()
        if a.candidates:
            from .memscan import crack as mc
            for c in a.candidates:
                cp = _p(c)
                if os.path.isdir(cp):
                    cands |= mc.candidates_from_files([cp])
                elif cp.endswith('.bin'):
                    cands |= mc.load_candidates(cp)
                else:
                    cands |= mc.candidates_from_files([cp])
        if a.words:
            cands |= set(w.encode() for w in a.words)
        if not cands:
            print('没给候选。用 --candidates <文件或目录> 或 --words a b c')
            return 1
        print(f'候选 {len(cands)} 条')
        hits = oracle.crack(ct, cands, workers=a.workers, progress=_progress)
        if not hits:
            print('未命中')
            return 2
        for k, lw, head in hits:
            print(f'✅ key={k!r}  末字={lw}  明文头={head[:48]!r}')
        return 0
    return 1


# ────────────────────────── 密钥表 ──────────────────────────
def cmd_key(a):
    if a.action == 'list':
        rows = keytables.all_inner_rows()
        print('内层密钥表（内层密钥 = psw + suffix）')
        print(f'{"runtime":9} {"version":10} {"psw":10} {"suffix":10} {"来源":16} 密钥')
        for rt, ver, key, src in rows:
            print(f'{rt:9} {ver:10} {key[:8]:10} {key[8:]:10} {src:16} {key}')
        builtin = sum(1 for r in rows if r[3] == '内置表')
        print(f'\n共 {len(rows)} 条：内置表 {builtin} 条（兜底）/ '
              f'{os.path.basename(paths.KEYS_FILE)} {len(rows) - builtin} 条')
        print()
        print(f'  密钥表  {paths.rel(paths.KEYS_FILE)}')
        print('          ↑ 这个文件可以分享/提交/从别人那拉一份覆盖（key import 合并）')
        print(f'  本机信息 {paths.rel(paths.LOCAL_FILE)}')
        print('          ↑ adb / 设备 / 外层密钥（跟账号绑），已在 .gitignore 里，别分享')
        print(f'  想只维护一个密钥文件：`python -m yyjh key sync`（把内置表也并进去）')
        print('\n渠道后缀:')
        for suf, name in keytables.CHANNEL_SUFFIXES.items():
            print(f'  {suf}  {name}')
        print('\n渠道服（包名各不相同；解密时工具会把各后缀都试一遍，不用你指定）:')
        for name, ck, pkg, suf in channels.describe_all():
            print(f'  {name:6} --pkg {ck:9} {pkg:46} 后缀 {suf}')
        print('  （工具默认在设备上自动探测，不用手写包名）')
        return 0

    if a.action == 'lookup':
        k, note = keytables.lookup_inner_key(a.runtime or 'android',
                                             a.version, extras=[a.key] if a.key else [])
        print(f'runtime={a.runtime} version={a.version}')
        if k:
            print(f'✅ 内层密钥 = {k}   （{note}）')
            return 0
        print(f'❌ 未找到：{note}')
        print('   候选（同版本换后缀）:')
        for c in keytables.candidate_inner_keys(a.runtime or 'android', a.version):
            print(f'     {c}')
        return 2

    if a.action == 'add':
        keytables.remember_inner_key(a.runtime or 'android', a.version, a.key)
        print(f'已写入 {paths.rel(paths.KEYS_FILE)}: {a.runtime}/{a.version} -> {a.key}')
        return 0

    if a.action == 'outer':
        if a.key:
            keytables.remember_outer_key(a.account, a.key)
            print(f'已记住 {a.account} 的外层密钥')
            return 0
        k = keytables.outer_key(a.account)
        print(f'{a.account} 的外层密钥 = {k or "（未记录）"}')
        return 0

    if a.action == 'sync':
        n = keytables.ensure_seeded()
        rows = keytables.all_inner_rows()
        print(f'已把内置表并进 {os.path.basename(paths.KEYS_FILE)}：'
              f'新补 {n} 条，现在共 {len(rows)} 条')
        if not n:
            print('（没有缺的，没动文件）')
        print(f'  密钥表 -> {paths.rel(paths.KEYS_FILE)}（可分享）')
        print(f'  本机信息 -> {paths.rel(paths.LOCAL_FILE)}（已 .gitignore）')
        return 0

    if a.action == 'import':
        p = _p(a.path)
        if not os.path.exists(p):
            print(f'找不到这个文件：{p}')
            return 1
        added, total = keytables.import_keys(p)
        print(f'并入 {os.path.basename(p)}：新补 {added} 条，现在共 {total} 条')
        if not added:
            print('（本机已有的都没被覆盖）')
        print(f'-> {paths.rel(paths.KEYS_FILE)}')
        return 0

    if a.action == 'crack':
        from .memscan import keyhunt
        return keyhunt.run(a)

    if a.action == 'probe':
        from .memscan import keyhunt
        return keyhunt.probe_run(a)
    return 1


# ────────────────────────── .whale ──────────────────────────
def cmd_whale(a):
    from .unpack import whale
    if a.action == 'info':
        w = whale.WhaleFile(_p(a.path))
        _print_kv(w.info())
        return 0
    if a.action == 'dump':
        target = _p(a.path)
        outdir = _o(a.out or os.path.join(paths.OUT_DIR, 'whale'))
        if os.path.isfile(target):
            w = whale.WhaleFile(target)
            ok, mods, failed = w.dump(outdir)
            print(f'{os.path.basename(target)}: {ok}/{mods} 模块成功'
                  + (f'（{failed} 失败）' if failed else ''))
            print(f'-> {outdir}')
            return 0
        results, ok, mods, strs = whale.dump_dir(target, outdir)
        for fn, msg in results:
            print(f'  {fn:40} {msg}')
        print(f'\n总计 {ok}/{mods} 模块')
        sp = os.path.join(outdir, '_strings.txt')
        with open(sp, 'w', encoding='utf-8') as f:
            f.write('\n'.join(strs))
        print(f'字符串 {len(strs)} 条 -> {sp}')
        return 0
    if a.action == 'find':
        target = _p(a.path)
        hits = []
        for root, _, fns in os.walk(target) if os.path.isdir(target) else [(None, None, [target])]:
            for fn in fns:
                p = os.path.join(root, fn) if root else fn
                if whale.is_whale(p):
                    hits.append(p)
        print(f'找到 {len(hits)} 个 .whale 文件')
        for h in hits:
            print('  ', h)
        return 0
    return 1


# ────────────────────────── res_encrypt ──────────────────────────
def cmd_res(a):
    from .unpack import binstrings, resenc
    target = _p(a.path)
    if a.action == 'dump':
        outdir = _o(a.out) if a.out else None
        ok, skip, fail, hits = resenc.decrypt_tree(
            target, outdir=outdir, on_progress=lambda i, n, o, f:
            print(f'  ...{i}/{n} 成功{o} 失败{f}', flush=True) if i % 500 == 0 else None)
        print(f'解密成功 {ok}，跳过（非加密）{skip}，失败 {fail}')
        if outdir:
            print(f'-> {outdir}')
        for p, found in hits:
            print(f'  ★ {os.path.basename(p)} 含 {found}')
        return 0
    if a.action == 'strings':
        d = open(target, 'rb').read()
        pt = resenc.decrypt_res(d)
        if pt is None:
            print('不是 res_encrypt 文件，或不支持该签名')
            return 1
        print(f'解密后 {len(pt):,} B，是 Lua: {resenc.looks_like_lua(pt)}')
        for s in codecs.printable_strings(pt, 6, limit=a.limit):
            print(s[:140])
        return 0
    return 1


# ────────────────────────── APK / PE ──────────────────────────
def cmd_apk(a):
    from .unpack import binstrings
    p = _p(a.path)
    if a.action == 'list':
        names = binstrings.apk_entries(p)
        print(f'{len(names)} 个条目')
        for n in names[:a.limit or 200]:
            print('  ', n)
        return 0
    if a.action == 'unpack':
        n = binstrings.apk_extract(p, _o(a.out), only=a.only)
        print(f'解压 {n} 个条目 -> {_o(a.out)}')
        return 0
    if a.action == 'strings':
        hits = binstrings.apk_strings(p, minlen=a.minlen, only=a.only)
        for name, off, s in hits[:a.limit or 200]:
            print(f'{name}@{off:x}: {s[:140]}')
        print(f'共 {len(hits)} 条')
        return 0
    return 1


def cmd_exe(a):
    from .unpack import binstrings
    p = _p(a.path)
    data = open(p, 'rb').read()
    if a.action == 'info':
        _print_kv(binstrings.pe_info(p))
        print('打包方式线索:', binstrings.pe_runtime_hints(data) or '（无匹配）')
        return 0
    if a.action == 'strings':
        for off, s in binstrings.scan_strings(p, minlen=a.minlen, limit=a.limit):
            print(f'0x{off:x}: {s[:160]}')
        return 0
    if a.action == 'needle':
        for n in a.needle:
            offs = binstrings.find_needle(p, n)
            print(f'[{n}] {len(offs)} 处: ' + ', '.join(f'0x{o:x}' for o in offs[:8]))
            for o in offs[:2]:
                print('   ', binstrings.context(data, o))
        return 0
    if a.action == 'tables':
        rows = binstrings.extract_inner_key_rows(data)
        if not rows:
            print('没找到 npswsuffix 密钥表。')
            print('  提示: 老版本可能有别的关键字，先用 `yyjh exe needle npswsuffix runtime` 试试')
            return 2
        print('提取到的内层密钥表（内层密钥 = psw + suffix）:')
        print(f'{"runtime":9} {"version":10} {"psw":10} {"suffix":10} 内层密钥')
        for rt, ver, psw, suf in rows:
            print(f'{rt:9} {ver:10} {psw:10} {suf:10} {psw+suf}')
        print(f'\n共 {len(rows)} 条')
        suf = binstrings.extract_channel_suffixes(data)
        if suf:
            print('配置里的渠道后缀池:', suf)
        if a.save:
            for rt, ver, psw, s2 in rows:
                keytables.remember_inner_key(rt, ver, psw + s2)
            print(f'已写入 {paths.rel(paths.KEYS_FILE)}')
        return 0
    return 1


# ────────────────────────── dat.db ──────────────────────────
def cmd_db(a):
    from .localdb import decrypt as dbdec
    from .localdb import sqlitedb
    # create 没有 path 参数，所以这里不能直接摸 a.path
    p = _p(a.path) if getattr(a, 'path', None) else None

    if a.action == 'show':
        print(f'{p}   {os.path.getsize(p):,} B')
        print('库参数:', sqlitedb.pragmas(p))
        print('表:', sqlitedb.tables(p))
        for row in sqlitedb.read_all(p):
            print(f'  id={row["id"]}')
            print(f'    content {len(row["content"]):,} 字符  头 40 = {row["content"][:40]!r}')
            print(f'    player  {(row["player"] or "")[:120]}')
        return 0

    if a.action == 'decrypt':
        from . import report
        r = dbdec.decode_db(p, runtime=a.runtime, outer_key=a.outer_key,
                            inner_key=a.inner_key, account=a.account)
        for s in r.steps:
            print(f'  - {s.label}: {s.value}')
        if a.out:
            outs = report.write_all(r, outdir=_o(a.out), write_db=False)
            for k, v in outs.items():
                print(f'  -> {k}: {v}')
        else:
            print(json.dumps(r.data, ensure_ascii=False)[:2000])
        return 0

    if a.action == 'pragma':
        _print_kv(sqlitedb.pragmas(p))
        return 0

    if a.action == 'diff':
        other = _p(a.other)
        diffs = sqlitedb.structure_diff(p, other)
        if not diffs:
            print('✅ 结构完全同构')
            return 0
        for k, va, vb in diffs:
            print(f'❌ {k}: {va}  !=  {vb}')
        return 2

    if a.action == 'create':
        # 从 JSON 造一个 dat.db（需要显式给版本/内层密钥）
        from .game import player as player_mod
        from .game import savefmt
        data = json.load(open(_p(a.json), encoding='utf-8'))
        ver = a.version
        ik, ik_note = keytables.lookup_inner_key(a.runtime or 'android', ver)
        if a.inner_key:
            ik, ik_note = a.inner_key, '命令行指定'
        if not ik:
            print(f'缺版本 {ver} 的内层密钥，用 --inner-key 指定')
            return 1
        if '同版本换后缀' in ik_note:
            # 这是拿别的平台/渠道的 psw 拼上"本平台最可能的后缀"猜出来的 ——
            # 加密出来的档只有渠道一致才读得出，所以要说清楚，别让人白试。
            print(f'⚠ 内层密钥是按渠道推测的：{ik}')
            print(f'  {ik_note}')
            print('  渠道不对的话，用 --inner-key 指定正确的那把。')
        if a.outer_key:
            content = savefmt.build_new(data, ver, ik, a.outer_key, a.marker)
        else:
            content = savefmt.build_old(data, ver, ik)
        sid = a.save_id or savefmt.save_id_of(a.account or data.get('EncryptID', ''))
        ply = player_mod.build(data, ver)
        out = _o(a.out)
        sqlitedb.write_save(out, sid, content, ply)
        print(f'已写出 {out}  ({os.path.getsize(out):,} B)')
        print(f'  id      = {sid}')
        print(f'  版本    = {ver}   内层密钥 = {ik}')
        print(f'  格式    = {"新版" if a.outer_key else "初版"}')
        return 0
    return 1


# ────────────────────────── HAR ──────────────────────────
def _probe_upload(ct, derived):
    """拿已知的几把钥匙 × 几种头部对齐试解 upload 请求体。

    只为判性质：全不中说明换的是算法，不是钥匙。返回 {'tries': n, 'hit': 名称或 None}。
    """
    from .core import xxtea
    if not ct:
        return {'tries': 0, 'hit': None}
    keys = {
        'realKey': derived.get('real_key', ''),
        'key0': derived.get('key0', ''),
        'inner': derived.get('inner_k', b''),
    }
    tries = 0
    for name, key in keys.items():
        if not key:
            continue
        for head in (0, 4, 8):
            data = ct[head:]
            if len(data) < 32 or len(data) % 4:
                continue
            tries += 1
            pt = xxtea.decrypt_raw(data, key)
            if not pt:
                continue
            head_txt = pt[:8]
            printable = sum(1 for b in pt[:96] if 32 <= b < 127)
            if head_txt in (b'MDAwNzM0', b'000734') or printable >= 92:
                return {'tries': tries, 'hit': f'{name}@+{head}'}
    return {'tries': tries, 'hit': None}


def cmd_har(a):
    from . import report
    from .capture import decode as capdec
    from .capture.har import Har
    p = _p(a.path)

    if a.action == 'info':
        _print_kv(Har(p).overview())
        return 0

    if a.action == 'list':
        har = Har(p)
        ups = har.uploads()
        biggest = max(range(len(ups)), key=lambda i: har.body_len(ups[i])) if ups else -1
        print(f'{len(har.entries)} 条记录，{len(ups)} 个 upload：')
        for i, e in enumerate(ups):
            mark = '★' if i == biggest else ' '
            print(f' {mark}[{i}] cmd={Har.header(e, "command")} '
                  f'acc={Har.header(e, "acc")} ver={Har.header(e, "ver")} '
                  f'area={Har.header(e, "area")} body={har.body_len(e):,}')
        print(f'k 响应头: {"有" if har.k_header() else "没有（抓包开始得太晚）"}')
        return 0

    if a.action == 'doctor':
        # 一次把"到底哪一环坏了"说清楚：realKey 好不好、哪个 upload 解得开、
        # 版本号多少。游戏每次更新后先跑这个，比反复重抓快得多。
        from .core import codecs, xxtea
        from .game import protocol

        har = Har(p)
        ups = har.uploads()
        k = har.k_header()
        print(f'文件      {a.path}')
        print(f'记录      {len(har.entries)} 条，其中 upload {len(ups)} 个')
        if not ups:
            print('❌ 没有 upload 请求 —— 抓包时游戏没上传存档')
            return 1
        if not k:
            print('❌ 没有带 k 的响应头 —— 抓包开始得太晚（要先开抓包再启动游戏）')
            return 1
        acc = har.account(ups[0])
        try:
            d = protocol.derive(acc, k)
        except ValueError as e:
            print(f'❌ k 头解不开：{e}')
            return 1
        rk = d['real_key']
        rev = Har.header(ups[0], 'whale') or '?'
        print(f'账号      {acc}　版本 {Har.header(ups[0], "ver")}　服 {Har.header(ups[0], "area")}')
        print(f'协议 rev  whale={rev}　命令={Har.header(ups[0], "command")}')
        print(f'realKey   {rk}（由 k 头推出，格式正常）')

        alive = []
        for i, e in enumerate(ups):
            body = Har.body(e)
            c = body if isinstance(body, (bytes, bytearray)) else codecs.b64d(body)
            ok = bool(c) and xxtea.decrypt(bytes(c), rk) is not None
            print(f'  {"✅" if ok else "❌"} [{i}] {Har.header(e, "command")} '
                  f'{har.body_len(e):,} 字符'
                  f'{"　← 能用这个" if ok else ""}')
            if ok:
                alive.append(i)

        if alive:
            print(f'\n✅ 结论：第 {alive[0]} 个 upload 可以正常解开，'
                  f'直接 `yyjh capture 文件` 即可')
            return 0
        if capdec.session_key_ok(har, rk):
            # 再多走一步：用已知的几把钥匙 × 几种对齐各试一次，
            # 用结果告诉用户"这不是换 key，是换算法"，省掉无谓的重抓。
            body = Har.body(ups[0])
            c = body if isinstance(body, (bytes, bytearray)) else codecs.b64d(body)
            probe = _probe_upload(bytes(c) if c else b'', d)
            print(f'\n⚠ 结论：realKey 是对的（同一份抓包里别的请求都能解开），')
            print(f'   但 {Har.header(ups[0], "command")} 的请求体换了加密方式'
                  f' —— 重抓/重跑都没用。')
            print(f'   已试 {probe["tries"]} 组「已知密钥 × 头部对齐」，全部无效'
                  f'（密文 {len(c or b""):,} 字节'
                  f'{"，非 16 的倍数，可排除 AES 分组模式" if c and len(c) % 16 else ""}）。')
            # v73492 的新特征：query 里多了 auto 字段，正好 256 字节(RSA-2048)
            auto = capdec.auto_blob(har, entry=ups[0], real_key=rk)
            if auto:
                print(f'   注意：query 里新增了 auto 字段（{len(auto)} 字节，'
                      f'73460 版本没有）—— 极可能是「每次随机密钥 + RSA 封装」。')
                print('   若属实，upload 主体在抓包层面数学上解不开（需服务器私钥）。')
            print('   可先跑 `yyjh har partial 文件` 出一份「角色数据最新」的降级存档；')
            print('   要完整存档则走本地存档路线：进一次角色后 `python run.py local`。')
            return 2
        print('\n❌ 结论：realKey 不对（别的小请求也解不开）——')
        print('   多半是抓包没从"启动游戏之前"开始，或 acc 取错了；重抓一次。')
        return 1

    if a.action == 'partial':
        # upload 主体换了加密（v73492 起）时的降级路线：
        # query 摘要 + uploadPower 队伍仍然可解，拿它们覆盖骨架存档的角色部分。
        from .capture import partial
        from .game import protocol

        har = Har(p)
        if a.real_key:
            rk = a.real_key.encode() if isinstance(a.real_key, str) else a.real_key
        else:
            k = har.k_header()
            if not k:
                print('❌ 没有带 k 的响应头 —— 抓包要先于游戏启动')
                return 1
            rk = protocol.derive(har.account(har.uploads()[0]), k)['real_key']
        print(f'realKey   {rk.decode() if isinstance(rk, bytes) else rk}')

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        base = a.base or partial.find_base(root)
        if not base:
            print('❌ output/ 下没有可当骨架的 dat.json，请用 --base 指定')
            return 1
        print(f'骨架存档  {base}')
        jp, mp, live = partial.build_partial(har, p, rk, base, root, outdir=_o(a.out))
        q = live['query']
        print(f'最新数据  昵称 {q.get("nick")}　等级 {q.get("lev")}　'
              f'银两 {q.get("money")}　声望 {q.get("prestige")}　'
              f'上阵 {len(live["team"])} 人')
        print(f'  -> dat.json: {jp}')
        print(f'  -> meta:     {mp}')
        print('\n⚠ 降级产物：MainRole / 上阵队友为最新，其余顶层字段沿用骨架存档，'
              '详见 dat.meta.json 的 warnings。')
        return 0

    if a.action == 'decrypt':
        r = capdec.decode(p, index=a.index, real_key=a.real_key, inner_key=a.inner_key)
        for s in r.steps:
            print(f'  - {s.label}: {s.value}')
        if a.out:
            outs = report.write_all(r, outdir=_o(a.out))
            for k, v in outs.items():
                print(f'  -> {k}: {v}')
        return 0
    return 1


# ────────────────────────── 内存 ──────────────────────────
def cmd_mem(a):
    from .memscan import crack as mc
    from .memscan import devices, memory
    from .memscan.adb import Adb, AdbError

    if a.action == 'devices':
        try:
            adb = devices.make_adb(getattr(a, 'adb', None), getattr(a, 'serial', None))
        except AdbError as e:
            print(e)
            return 1
        print(f'adb: {adb.exe}   （来源: {adb.exe_src}）')
        devs = adb.devices()
        if not devs:
            print('没有任何设备。')
            print('  · 先启动模拟器（MuMu / 雷电 / 夜神 / 蓝叠…）')
            print('  · 看不到就试 adb connect 127.0.0.1:7555（MuMu）')
            print('                   adb connect 127.0.0.1:5555（雷电/蓝叠）')
            return 1
        # 排序：模拟器端口优先
        ranked = Adb.rank(devs)
        print(f'在线设备 {len(devs)} 台（按优先级排）：')
        for d in ranked:
            adb.serial = d
            adb._resolved = True
            try:
                pkgs = devices.probe_game(adb)
                info = ', '.join(channels.label(p) for p, _c in pkgs) or '没有游戏'
                root = 'root' if adb.is_root() else '无 root'
                print(f'  {d:20} {adb.model():16} {root:8} {info}')
            except Exception as e:
                print(f'  {d:20} （读取失败: {e}）')
        print('\n用哪台由工具自动选（装了游戏的那台）；也可以 --serial 指定')
        return 0

    if a.action == 'pkgs':
        print('已知渠道表（包名各不相同）：')
        for name, ck, pkg, suf in channels.describe_all():
            print(f'  {name:6} --pkg {ck:9} {pkg:46} 后缀 {suf}')
        try:
            adb = devices.make_adb(getattr(a, 'adb', None), getattr(a, 'serial', None))
        except AdbError as e:
            print(f'\n（找不到 adb: {e}）')
            return 1
        if not adb.online():
            print('\n（没有在线设备）')
            return 1
        print(f'\n设备 {adb.serial} 上装的游戏（同一台可能装了好几个渠道）：')
        games = devices.list_games(adb)
        if not games:
            print('  没有。')
            return 1
        for i, g in enumerate(games, 1):
            print(f'  [{i}] {g.line()}')
            print(f'       存档: ' + (', '.join(g.saves) if g.saves else '（没有 dat.db）'))
        print('\n跑 capture/local 时会自动选（优先按存档 id 反查，反查不到才问你）；')
        print('也可以固定：--pkg huawei（用上面表格里的简写或中文名）')
        return 0

    if a.action == 'procs':
        try:
            adb = devices.make_adb(getattr(a, 'adb', None), getattr(a, 'serial', None))
        except AdbError as e:
            print(e)
            return 1
        if not adb.online():
            print(f'没有可用设备（{adb.serial or "无"}）')
            return 1
        rows = adb.ps(a.pattern or 'lanjing')
        print(f'设备 {adb.serial} 上匹配的进程：')
        for pid, name in rows:
            print(f'  {pid:>8}  {name}')
        print(f'共 {len(rows)} 个（--pattern 过滤，默认 lanjing）')
        return 0

    # 下面几个都需要"设备 + 游戏进程"
    try:
        adb, pkg, ch = devices.pick_game_device(
            exe=getattr(a, 'adb', None), serial=getattr(a, 'serial', None),
            pkg_hint=getattr(a, 'pkg', None), on_progress=print)
    except AdbError as e:
        print(e)
        return 1
    if not pkg:
        print('没在任何设备上找到游戏包。')
        print('  · 确认模拟器开着、游戏装好了')
        print('  · 渠道服用 --pkg 指定，如 --pkg huawei')
        print('  · 或者先跑 `yyjh mem devices` 看设备情况')
        return 1
    print(f'游戏: {channels.label(pkg)}   @ {adb.serial}（{adb.model()}）')
    pid = getattr(a, 'pid', None) or adb.pid_of(pkg)
    if not pid:
        print(f'{pkg} 没在运行。先把游戏打开、进一次角色。')
        return 1
    ok, why = adb.check_mem_access(pid)
    print(f'内存读取: {"✅" if ok else "❌"} {why}')
    if not ok:
        return 1

    if a.action == 'maps':
        regs = memory.regions(memory.read_maps(adb, pid),
                              anon_only=not a.all_regions, min_kb=a.min_kb)
        total = sum(r.size for r in regs)
        print(f'pid={pid}  可读区域 {len(regs)} 个  共 {total/1048576:.0f} MB')
        for r in regs[:a.limit or 50]:
            print(f'  {r}')
        return 0

    if a.action == 'scan':
        from .unpack.binstrings import INTERESTING
        needles = a.needle or INTERESTING
        regs = memory.regions(memory.read_maps(adb, pid), anon_only=not a.all_regions,
                              min_kb=a.min_kb)
        print(f'在 {len(regs)} 个区域里找 {needles}')
        for r, data in memory.iter_regions(adb, pid, regs, max_mb=a.max_mb,
                                           on_progress=lambda *x: None):
            for n in needles:
                off = data.find(n.encode())
                if off >= 0:
                    ctx = codecs.printable_strings(
                        data[max(0, off - 40):off + 80], 4)[:3]
                    print(f'  ★ {n} @0x{r.start+off:x}  {ctx}')
        return 0

    if a.action == 'dump':
        regs = memory.regions(memory.read_maps(adb, pid), anon_only=not a.all_regions,
                              min_kb=a.min_kb)
        outdir = paths.ensure_dir(_o(a.out or os.path.join(paths.OUT_DIR, 'memdump')))
        got = 0
        for r, data in memory.iter_regions(adb, pid, regs, max_mb=a.max_mb):
            p = os.path.join(outdir, f'region_0x{r.start:x}.bin')
            memory.save_image(p, data)
            got += len(data)
            print(f'  -> {p}  ({len(data):,} B)')
            if a.limit_mb and got >= a.limit_mb * 1048576:
                break
        print(f'共 {got/1048576:.0f} MB -> {outdir}')
        return 0

    if a.action == 'harvest':
        regs = memory.regions(memory.read_maps(adb, pid), anon_only=not a.all_regions,
                              min_kb=a.min_kb)
        cands = set()
        scanned = 0
        for r, data in memory.iter_regions(adb, pid, regs, max_mb=a.max_mb):
            cands |= mc.harvest_strings(data)
            scanned += len(data)
            print(f'  扫描 {r}  候选 {len(cands)}  已扫 {scanned/1048576:.0f}MB', flush=True)
            if a.limit_mb and scanned >= a.limit_mb * 1048576:
                break
        out = _o(a.out or os.path.join(paths.CACHE_DIR, 'heap_keys.bin'))
        mc.save_candidates(out, cands)
        print(f'候选 {len(cands)} 条 -> {out}（以后可用 `yyjh xxtea crack --candidates` 复用）')
        return 0

    if a.action == 'crack':
        from .localdb import sqlitedb
        if a.db:
            row = sqlitedb.read_save(_p(a.db))
            content = row['content']
            who = os.path.basename(_p(a.db))
        elif a.content:
            content = open(_p(a.content), encoding='utf-8').read().strip()
            who = os.path.basename(_p(a.content))
        else:
            print('需要 --db <dat.db> 或 --content <含content的文件>')
            return 1
        print(f'目标: {who}')
        hits = mc.crack_outer_key(
            adb, pid, content, anon_only=not a.all_regions, min_kb=a.min_kb,
            max_mb=a.max_mb, limit_mb=a.limit_mb, workers=a.workers,
            progress=_progress)
        if hits and a.account:
            keytables.remember_outer_key(a.account, hits[0][0].decode('latin-1'))
            print(f'已记住 {a.account} 的外层密钥')
        return 0 if hits else 2
    return 1


# ────────────────────────── ELF ──────────────────────────
def cmd_elf(a):
    from .reverse import elf as E
    p = _p(a.path)
    with E.Elf(p) as e:
        if a.action == 'info':
            secs = e.sections()
            print(f'{os.path.basename(p)}  节区 {len(secs)} 个，符号 {len(e.symbols())} 个')
            for s in secs[:a.limit or 30]:
                print(f"  {s['name']:20} addr=0x{s['addr']:x} size={s['size']:,}")
            return 0
        if a.action == 'syms':
            syms = e.symbols()
            import re
            pat = re.compile(a.pattern, re.I) if a.pattern else None
            n = 0
            for name, (addr, size, t) in sorted(syms.items(), key=lambda kv: kv[1][0]):
                if pat and not pat.search(name):
                    continue
                print(f'  0x{addr:08x}  {size:>7}  {name}')
                n += 1
                if a.limit and n >= a.limit:
                    break
            print(f'共 {len(syms)} 个符号')
            return 0
        if a.action == 'disasm':
            for line in e.disasm(int(a.addr, 0), a.count):
                print(' ', line)
            return 0
        if a.action == 'calls':
            targets = dict(constants.KNOWN_SYMS)
            if a.target:
                targets = {int(a.target, 0): 'target'}
            hits = e.find_call_sites(targets)
            print(f'找到 {len(hits)} 个调用点')
            for addr, t, name in hits:
                print(f'  0x{addr:08x} -> {name}  [在 {e.symbol_at(addr)}]')
            return 0
        if a.action == 'strref':
            for s in a.string:
                refs = e.refs_to_string(s)
                print(f'[{s}] 引用 {len(refs)} 处')
                for adrp, add, val in refs:
                    print(f'   0x{adrp:08x}  -> 0x{val:x}  [在 {e.symbol_at(adrp)}]')
            return 0
    return 1


# ────────────────────────── 注册 ──────────────────────────
BLOCKS = {
    'info': '自动识别文件类型',
    'xxtea': 'XXTEA 加解密 / 爆破',
    'key': '密钥表查询 / 补新版本密钥',
    'whale': '.whale 容器',
    'res': 'res_encrypt 资源',
    'apk': 'APK 解包/字符串',
    'exe': 'exe/PE 字符串与密钥表',
    'db': 'dat.db 读写/解密',
    'har': '抓包 HAR',
    'mem': '内存（adb/maps/dump/爆破）',
    'elf': '.so 逆向',
}

HANDLERS = {'info': cmd_info, 'xxtea': cmd_xxtea, 'key': cmd_key, 'whale': cmd_whale,
            'res': cmd_res, 'apk': cmd_apk, 'exe': cmd_exe, 'db': cmd_db,
            'har': cmd_har, 'mem': cmd_mem, 'elf': cmd_elf}


def add_parsers(sub):
    """把第一层的所有子命令挂到 argparse 上。"""
    # info
    p = sub.add_parser('info', help=BLOCKS['info'])
    p.add_argument('path')
    p.set_defaults(func=cmd_info, layer=1)

    # xxtea
    p = sub.add_parser('xxtea', help=BLOCKS['xxtea'])
    sx = p.add_subparsers(dest='action', required=True)
    for act, help_ in (('decrypt', '解密'), ('encrypt', '加密'),
                       ('check', '只查长度字对不对'), ('crack', '爆破钥匙'),
                       ('selftest', '自检')):
        q = sx.add_parser(act, help=help_)
        if act != 'selftest':
            q.add_argument('--file')
            q.add_argument('--hex')
            q.add_argument('--key')
            q.add_argument('--out')
        if act == 'crack':
            q.add_argument('--candidates', action='append', default=[],
                           help='候选来源：文件/目录/heap_keys.bin（可多次）')
            q.add_argument('--words', nargs='*', help='直接给候选字符串')
            q.add_argument('--workers', type=int)
        q.set_defaults(func=cmd_xxtea, layer=1)

    # key
    p = sub.add_parser('key', help=BLOCKS['key'])
    sx = p.add_subparsers(dest='action', required=True)
    q = sx.add_parser('list', help='列出全部密钥表')
    q.set_defaults(func=cmd_key, layer=1)
    q = sx.add_parser('lookup', help='查某版本的密钥')
    q.add_argument('version')
    q.add_argument('--runtime', default='android', choices=['android', 'ios'])
    q.add_argument('--key', help='额外候选')
    q.set_defaults(func=cmd_key, layer=1)
    q = sx.add_parser('add', help='补一条密钥到 keys.json')
    q.add_argument('version')
    q.add_argument('key')
    q.add_argument('--runtime', default='android', choices=['android', 'ios'])
    q.set_defaults(func=cmd_key, layer=1)
    q = sx.add_parser('outer', help='查/记外层密钥')
    q.add_argument('account')
    q.add_argument('--key', help='给了就记住')
    q.set_defaults(func=cmd_key, layer=1)
    q = sx.add_parser('sync', help='把内置表并进 keys.json（统一成一个可分享的文件）')
    q.set_defaults(func=cmd_key, layer=1)
    q = sx.add_parser('import', help='并入另一份 keys.json（只补缺，不覆盖）')
    q.add_argument('path', help='别人给的 / 从仓库拉的 keys.json')
    q.set_defaults(func=cmd_key, layer=1)
    q = sx.add_parser('crack', help='★ 游戏更新后补新版本密钥（挖 + 存）')
    q.add_argument('--har', help='抓包 .har（不需要外层密钥，最省事）')
    q.add_argument('--index', type=int, help='HAR 里第几个 upload（默认取最大的）')
    q.add_argument('--real-key', help='抓包的外层密钥（一般从 HAR 里自动取）')
    q.add_argument('--db', help='存档路径，本地或设备路径都行（不给就自己去设备上找最新的）')
    q.add_argument('--content', help='直接给 content（一般不用）')
    q.add_argument('--runtime', default='android', choices=['android', 'ios'])
    q.add_argument('--outer-key', help='外层密钥（已经记录过就不用给）')
    q.add_argument('--inner-key', help='已知就直接给，省得挖（会给存进 keys.json）')
    q.add_argument('--account')
    q.add_argument('--suffix', action='append',
                   help='渠道后缀，可多次（不给我就把已知后缀全试一遍）')
    q.add_argument('--from', dest='from_files', action='append',
                   help='额外候选来源：文件/目录，可多次（比如导出工具的 exe）')
    q.add_argument('--words', nargs='*', help='直接给候选密钥')
    q.add_argument('--force', action='store_true', help='表里已经有也重挖一遍')
    q.add_argument('--no-oracle', action='store_true',
                   help='不做全量兜底爆破（快，但万一后缀候选没命中就真没辙了）')
    q.add_argument('--no-mem', action='store_true', help='完全不碰设备/内存')
    q.add_argument('--all-regions', action='store_true', help='挖外层时含文件映射区域')
    q.add_argument('--adb'); q.add_argument('--serial')
    q.add_argument('--pkg', default=None, help='包名或渠道名（默认自动探测）')
    q.add_argument('--no-pick', action='store_true', help='不交互选，直接用最新的')
    q.add_argument('--pid')
    q.add_argument('--min-kb', type=int, default=64)
    q.add_argument('--max-mb', type=int, default=0,
                   help='单区域最多读多少 MB（0 = 不限，默认；扫全才不容易漏）')
    q.add_argument('--chunk-mb', type=int, default=64, help='每次读多少 MB（越大越快）')
    q.add_argument('--small-mb', type=int, default=0,
                   help='关掉设备端搜索、改成本机拉小堆区（≤N MB）慢慢挑（慢，少用）')
    q.add_argument('--no-deep', action='store_true',
                   help='跳过最后的兜底深挖（会快，但万一前面没中就只能自己想办法了）')
    q.add_argument('--workers', type=int,
                   help='多进程爆破时的核数（只影响最后的全量兜底）')
    q.add_argument('--limit-mb', type=int, help='总共最多扫多少 MB')
    q.set_defaults(func=cmd_key, layer=1)
    q = sx.add_parser('probe', help='★ 轻量探测：现在内存里有没有这一版的密钥（几秒）')
    q.add_argument('--har', help='抓包 .har（不需要外层密钥，最省事）')
    q.add_argument('--index', type=int, help='HAR 里第几个 upload（默认取最大的）')
    q.add_argument('--real-key', help='抓包的外层密钥（一般从 HAR 里自动取）')
    q.add_argument('--db', help='存档路径，本地或设备路径都行（不给就自己去设备上找最新的）')
    q.add_argument('--content', help='直接给 content（一般不用）')
    q.add_argument('--runtime', default='android', choices=['android', 'ios'])
    q.add_argument('--outer-key', help='外层密钥（已经记录过就不用给）')
    q.add_argument('--account')
    q.add_argument('--suffix', action='append',
                   help='渠道后缀，可多次（不给我就把已知后缀全试一遍）')
    q.add_argument('--small-mb', type=int, default=None,
                   help=f'只看 ≤N MB 的小可写堆（默认 {16}；0 = 全部可写堆，慢）')
    q.add_argument('--budget-mb', type=int, default=None,
                   help='一次最多拉多少 MB 回来（默认 0 = 拉完；--watch 模式默认 192）')
    q.add_argument('--workers', type=int,
                   help='验证候选的进程数（默认最多 8；1 = 单进程）')
    q.add_argument('--watch', type=int, metavar='N', default=0,
                   help='每 N 秒探一次，成功就停（在游戏里试动作时盯着用）')
    q.add_argument('--force', action='store_true',
                   help='表里已经有这一版也照样探')
    q.add_argument('--adb'); q.add_argument('--serial')
    q.add_argument('--pkg', default=None, help='包名或渠道名（默认自动探测）')
    q.add_argument('--no-pick', action='store_true', help='不交互选，直接用最新的')
    q.add_argument('--pid')
    q.set_defaults(func=cmd_key, layer=1)

    # whale
    p = sub.add_parser('whale', help=BLOCKS['whale'])
    sx = p.add_subparsers(dest='action', required=True)
    q = sx.add_parser('info'); q.add_argument('path'); q.set_defaults(func=cmd_whale, layer=1)
    q = sx.add_parser('dump'); q.add_argument('path'); q.add_argument('--out')
    q.set_defaults(func=cmd_whale, layer=1)
    q = sx.add_parser('find'); q.add_argument('path'); q.set_defaults(func=cmd_whale, layer=1)

    # res
    p = sub.add_parser('res', help=BLOCKS['res'])
    sx = p.add_subparsers(dest='action', required=True)
    q = sx.add_parser('dump'); q.add_argument('path'); q.add_argument('--out')
    q.set_defaults(func=cmd_res, layer=1)
    q = sx.add_parser('strings'); q.add_argument('path'); q.add_argument('--limit', type=int)
    q.set_defaults(func=cmd_res, layer=1)

    # apk
    p = sub.add_parser('apk', help=BLOCKS['apk'])
    sx = p.add_subparsers(dest='action', required=True)
    q = sx.add_parser('list'); q.add_argument('path'); q.add_argument('--limit', type=int)
    q.set_defaults(func=cmd_apk, layer=1)
    q = sx.add_parser('unpack'); q.add_argument('path'); q.add_argument('--out')
    q.add_argument('--only', action='append'); q.set_defaults(func=cmd_apk, layer=1)
    q = sx.add_parser('strings'); q.add_argument('path'); q.add_argument('--minlen', type=int, default=8)
    q.add_argument('--only', action='append'); q.add_argument('--limit', type=int)
    q.set_defaults(func=cmd_apk, layer=1)

    # exe
    p = sub.add_parser('exe', help=BLOCKS['exe'])
    sx = p.add_subparsers(dest='action', required=True)
    q = sx.add_parser('info'); q.add_argument('path'); q.set_defaults(func=cmd_exe, layer=1)
    q = sx.add_parser('strings'); q.add_argument('path')
    q.add_argument('--minlen', type=int, default=8); q.add_argument('--limit', type=int)
    q.set_defaults(func=cmd_exe, layer=1)
    q = sx.add_parser('needle'); q.add_argument('path'); q.add_argument('needle', nargs='+')
    q.set_defaults(func=cmd_exe, layer=1)
    q = sx.add_parser('tables', help='★ 提取内层密钥表')
    q.add_argument('path'); q.add_argument('--save', action='store_true',
                                           help='顺便写入 keys.json')
    q.set_defaults(func=cmd_exe, layer=1)

    # db
    p = sub.add_parser('db', help=BLOCKS['db'])
    sx = p.add_subparsers(dest='action', required=True)
    q = sx.add_parser('show'); q.add_argument('path'); q.set_defaults(func=cmd_db, layer=1)
    q = sx.add_parser('decrypt'); q.add_argument('path')
    q.add_argument('--runtime', default='android', choices=['android', 'ios'])
    q.add_argument('--outer-key'); q.add_argument('--inner-key'); q.add_argument('--account')
    q.add_argument('--out', help='给目录则输出文档')
    q.set_defaults(func=cmd_db, layer=1)
    q = sx.add_parser('pragma'); q.add_argument('path'); q.set_defaults(func=cmd_db, layer=1)
    q = sx.add_parser('diff'); q.add_argument('path'); q.add_argument('other')
    q.set_defaults(func=cmd_db, layer=1)
    q = sx.add_parser('create', help='用 JSON 造 dat.db')
    q.add_argument('--json', required=True); q.add_argument('--out', required=True)
    q.add_argument('--version', required=True); q.add_argument('--inner-key')
    q.add_argument('--outer-key', help='给了就造新版格式，不给造初版')
    q.add_argument('--marker', default='dnkemiea')
    q.add_argument('--account'); q.add_argument('--save-id')
    q.add_argument('--runtime', default='android', choices=['android', 'ios'])
    q.set_defaults(func=cmd_db, layer=1)

    # har
    p = sub.add_parser('har', help=BLOCKS['har'])
    sx = p.add_subparsers(dest='action', required=True)
    q = sx.add_parser('info'); q.add_argument('path'); q.set_defaults(func=cmd_har, layer=1)
    q = sx.add_parser('list'); q.add_argument('path'); q.set_defaults(func=cmd_har, layer=1)
    q = sx.add_parser('doctor',
                      help='体检：realKey 对不对 / 哪个 upload 解得开'
                           '（退出码 2 = 游戏改了上传加密）')
    q.add_argument('path'); q.set_defaults(func=cmd_har, layer=1)
    q = sx.add_parser('decrypt'); q.add_argument('path')
    q.add_argument('--index', type=int); q.add_argument('--real-key'); q.add_argument('--inner-key')
    q.add_argument('--out', help='给目录则输出文档')
    q.set_defaults(func=cmd_har, layer=1)
    q = sx.add_parser('partial',
                      help='★ 降级取数：upload 主体解不开时，用 uploadPower + query '
                           '拼一份「角色数据最新、结构完整」的 dat.json')
    q.add_argument('path')
    q.add_argument('--real-key', help='一般不用给，会自动从 k 头推')
    q.add_argument('--base', help='骨架存档 dat.json（默认在 output/ 下找最近一份）')
    q.add_argument('--out', help='输出目录')
    q.set_defaults(func=cmd_har, layer=1)

    # mem
    p = sub.add_parser('mem', help=BLOCKS['mem'])
    sx = p.add_subparsers(dest='action', required=True)
    q = sx.add_parser('devices')
    q.add_argument('--adb'); q.add_argument('--serial')
    q.set_defaults(func=cmd_mem, layer=1)
    for act in ('pkgs', 'procs', 'maps', 'scan', 'dump', 'harvest', 'crack'):
        q = sx.add_parser(act)
        q.add_argument('--adb'); q.add_argument('--serial')
        q.add_argument('--pkg', default=None,
                       help='包名或渠道名（默认自动探测，如 --pkg huawei）')
        q.add_argument('--pid')
        q.add_argument('--all-regions', action='store_true', help='含文件映射区域')
        q.add_argument('--min-kb', type=int, default=64)
        q.add_argument('--max-mb', type=int, default=64, help='单区域最多读多少 MB')
        q.add_argument('--limit-mb', type=int, help='总共最多扫多少 MB')
        q.add_argument('--limit', type=int)
        q.add_argument('--out')
        q.set_defaults(func=cmd_mem, layer=1)
        if act == 'procs':
            q.add_argument('--pattern')
        if act == 'scan':
            q.add_argument('--needle', action='append')
        if act == 'crack':
            q.add_argument('--db'); q.add_argument('--content')
            q.add_argument('--workers', type=int); q.add_argument('--account')

    # elf
    p = sub.add_parser('elf', help=BLOCKS['elf'])
    sx = p.add_subparsers(dest='action', required=True)
    q = sx.add_parser('info'); q.add_argument('path'); q.add_argument('--limit', type=int)
    q.set_defaults(func=cmd_elf, layer=1)
    q = sx.add_parser('syms'); q.add_argument('path'); q.add_argument('--pattern')
    q.add_argument('--limit', type=int); q.set_defaults(func=cmd_elf, layer=1)
    q = sx.add_parser('disasm'); q.add_argument('path'); q.add_argument('addr')
    q.add_argument('--count', type=int, default=40); q.set_defaults(func=cmd_elf, layer=1)
    q = sx.add_parser('calls', help='找 xxtea 等函数的调用点')
    q.add_argument('path'); q.add_argument('--target'); q.set_defaults(func=cmd_elf, layer=1)
    q = sx.add_parser('strref', help='找字符串常量的引用处')
    q.add_argument('path'); q.add_argument('string', nargs='+')
    q.set_defaults(func=cmd_elf, layer=1)
