# -*- coding: utf-8 -*-
"""★★ 外层接口：一步出结果。

    capture  <抓包.har>       抓包结果  -> 初版 dat.db
    local    <dat.db>         本地存档  -> 初版 dat.db
    rebuild  <存档.json>      明文 JSON -> 初版 dat.db
    auto     <任意文件>       按扩展名自动选
    batch    <目录>           批量处理

输出（默认 tool_1.1/output/）：
    <平台>-<区服>-<用户名>-<时间戳>.db          初版 dat.db
    <平台>-<区服>-<用户名>-<时间戳>.json        解密后的完整存档
    <平台>-<区服>-<用户名>-<时间戳>.meta.json   元信息
（账号是敏感信息，只写在 meta.json 里，不进文件名）

为什么 capture / local 要"去内存抓"：
    外层密钥是**每账号一把**的随机 16 字符 token，只在游戏进程内存里，推导不出来。
      · local   要解新版 dat.db 就必须先拿到它 -> 扫内存爆破
      · capture 从抓包能免费拿到初版 content，但顺手把该账号的外层密钥也挖出来记进
                keys.local.json，以后同一账号解本地存档就不用再扫内存了
    rebuild 不需要 —— JSON 已是明文，重新加密用的是"版本 + 渠道"的固定内层密钥。

不支持的输入
    `.yanyu` 旧版导出容器（`yanyu-@…`）。它是旧版格式，用旧版工具即可；
    而且旧版工具也解不了现在的新密钥，实际没有使用需求，代码已删除。
"""
import argparse
import json
import os

from . import model, report, ui
from .config import channels, keytables, paths
from .game import player as player_mod
from .game import savefmt

MEM_MODES = ('auto', 'force', 'off')


# ───────────────────────── 内存步骤 ─────────────────────────
def _mem_outer_key(args, account, new_content=None, res=None, save_id=None):
    """拿到该账号的外层密钥。返回 (key, 说明)。

    `save_id` = md5(账号)。两个都给最好（一个用于记录，一个用于反查渠道）；
    只有 save_id 也能干活（`local` 路由一开始只知道 md5）。
    """
    mode = getattr(args, 'mem', 'auto')
    if mode == 'off':
        return None, '已用 --mem off 跳过'
    save_id = save_id or (res.save_id if res else None)
    if not account and not save_id:
        return None, '不知道账号（也没有存档 id），无法定位外层密钥'

    # ① 记录里**任何一把能解开这份 content** 的就算"已有" ——
    #    不只看"账号/存档 id 精确对上"的那条：外层密钥可能是在别的流程里
    #    （比如抓包自带 realKey）顺手记下来的。
    #    以前只看精确那条，导致"明明记下来了，却每次还得重新扫内存"。
    if mode != 'force' and new_content:
        from .game import savefmt
        from .localdb import decrypt as dbdec
        bad = []
        for k in dbdec.outer_candidates(account, None, save_id):
            try:
                savefmt.unwrap_outer(new_content, k)
            except Exception:                              # noqa: BLE001
                bad.append(k)
                continue
            note = '记录里已有（自动匹配）' if bad else '记录里已有'
            ui.step('外层密钥', f'{k}（{note}）')
            if k != keytables.outer_key(account, save_id):
                # 把"实际管用的这把"补到当前账号/存档 id 名下，下次就是精确命中
                keytables.remember_outer_key(account, k, save_id=save_id)
            return k, note

    k = keytables.outer_key(account, save_id)
    if k and mode != 'force':
        # 手里有 content 就先验证一把 —— 记的这把可能根本不是外层密钥
        # （比如抓包记下来的 realKey），不能让它"看着有记录"就挡住内存扫描。
        if new_content:
            from .game import savefmt as _sf
            try:
                _sf.unwrap_outer(new_content, k)
            except Exception:                              # noqa: BLE001
                ui.step('外层密钥',
                        f'{k} 记录里有，但解不开这份存档（可能是抓包记的 '
                        f'realKey）—— 继续找')
                k = None
        if k:
            ui.step('外层密钥', f'{k}（keys.local.json 里已有记录）')
            return k, '已有记录'

    try:
        from .memscan import devices
        from .memscan.adb import AdbError
        adb, pkg, chan = devices.pick_game_device(
            exe=getattr(args, 'adb', None),
            serial=getattr(args, 'serial', None),
            pkg_hint=getattr(args, 'pkg', None),
            save_id=save_id,
            allow_pick=not getattr(args, 'no_pick', False),
            on_progress=lambda m: ui.step('设备', m))
    except AdbError as e:
        return None, str(e)
    except Exception as e:
        return None, f'adb 不可用（{e}）'
    if not pkg:
        return None, ('没在任何设备上找到游戏。'
                      '确认模拟器开着、游戏装好了；渠道服可以用 --pkg huawei 指定')
    ui.info(f'  设备 {adb.serial}（{adb.model()}）　游戏 {channels.label(pkg)}')

    pid = getattr(args, 'pid', None) or adb.pid_of(pkg)
    if not pid:
        return None, (f'{channels.label(pkg)} 没在运行。\n'
                      '  同一台设备装了多个渠道时，只有**正在运行的那个**进程内存里\n'
                      '  才有这个账号的密钥 —— 请先把要导出的那个游戏打开、进一次角色。\n'
                      '  不确定是哪个渠道？工具会自动按存档 id 反查，不用你指定。')
    ok, why = adb.check_mem_access(pid)
    if not ok:
        return None, why
    ui.info(f'  去内存取外层密钥（进程 {pid}）…')

    if new_content is None:
        from .localdb import device
        ui.info(f'  先在设备上找这个账号的 dat.db（{channels.label(pkg)}）…')
        info = device.find_by_account(
            adb, account or '', pkg, want_id=save_id,
            on_progress=lambda m: ui.step('设备存档', m))
        if not info:
            return None, (f'设备上没找到该账号的 dat.db'
                          f'（账号 {account or save_id}，包 {pkg}）')
        new_content = info['content']
        ui.step('设备存档', f"{info['remote']}（content {len(new_content):,} 字符）")

    from .memscan import crack as mc
    hits = mc.crack_outer_key(
        adb, pid, new_content,
        anon_only=not getattr(args, 'all_regions', False),
        min_kb=getattr(args, 'min_kb', 64),
        max_mb=getattr(args, 'max_mb', 64),
        limit_mb=getattr(args, 'limit_mb', None),
        workers=getattr(args, 'workers', None))
    if not hits:
        return None, '内存爆破未命中'
    key = hits[0][0].decode('latin-1')
    keytables.remember_outer_key(account, key, save_id=save_id)
    ui.ok(f'外层密钥 {key}（已记入 keys.local.json，以后不用再扫内存）')
    return key, '内存爆破命中'


# ───────────────────────── 收尾 ─────────────────────────
def _finish(res, args):
    """定终稿 + 写盘。"""
    # 跨平台/跨渠道命中时，把"实际管用的那把"记到当前 runtime 名下。
    # 内层密钥 = psw(按**版本**走) + suffix(按**渠道**走)，psw 跨渠道、跨平台共用，
    # 所以安卓挖出来的 key，iOS 那边换后缀（或本来就是同一把）照样能用。
    # 记下来下次就不用靠候选表一个个试。
    if res.data and res.version and res.inner_key:
        known, _note = keytables.lookup_inner_key(res.runtime, res.version)
        if known != res.inner_key:
            try:
                keytables.remember_inner_key(res.runtime, res.version, res.inner_key)
                ui.step('内层密钥',
                        f'已记到 {res.runtime}/{res.version} 名下：{res.inner_key}')
            except Exception:                          # noqa: BLE001
                pass

    # 统一在这里定 content：**一定用 res.data 现构造**，
    # 所以 dat.db 和 dat.json 永远说的是同一件事。
    # （以前 capture/local 是把抓包/游戏文件里的 content 直接搬过来，
    #   一旦要换输出格式就对不上了。）
    fmt = getattr(args, 'save_format', 'v18')
    if res.data and res.version:
        if fmt == 'v18':
            key = savefmt.platform_key(res.runtime)
        else:
            key = res.inner_key or keytables.lookup_inner_key(
                res.runtime, res.version)[0]
        if key:
            res.content = savefmt.build_old(res.data, res.version, key)
            res.inner_key = key
            res.marker = ''          # v18 / 初版都没有外层标记
            res.save_format = fmt
            ui.step('输出格式', f'{fmt}　内层密钥 {key}')

    outdir = paths.resolve_out(args.outdir) if args.outdir else paths.OUT_DIR
    outs = report.write_all(res, outdir=outdir, name=getattr(args, 'name', None),
                            write_db=not getattr(args, 'no_db', False),
                            write_json=not getattr(args, 'no_json', False),
                            write_report=getattr(args, 'report', False),
                            write_meta=not getattr(args, 'no_meta', False))
    report.print_result(res, outs)
    return res


def _require(cond, msg, hints):
    if not cond:
        raise ui.UserError(msg, hints)


# ───────────────────────── capture：抓包 ─────────────────────────
def pipeline_capture(args):
    from .capture import decode as capdec

    p = paths.resolve(args.input)
    _require(os.path.exists(p), f'找不到抓包文件：{p}',
             ['确认路径；路径带空格时加引号', '支持 ProxyPin / mitmproxy 导出的 .har'])

    ui.title('从抓包导出存档')
    ui.info(f'  输入: {os.path.basename(p)}')
    try:
        res = capdec.decode(p, index=args.index, real_key=args.outer_key,
                            inner_key=args.inner_key,
                            avatar_field=args.avatar_field)
    except capdec.NoDownloadStep as e:
        # ← 抓包**少了一步**，不是游戏改算法、更不是密钥不对。
        #   73492 起 upload 是 RSA 封装的（服务端才解得开），抓包路线唯一的
        #   进料口就是"下载存档"那条响应 —— 没点它，神仙也解不出。
        raise ui.UserError(
            f'这份抓包缺了「下载存档」这一步，所以解不开'
            f'（抓到 {len(e.cmds)} 种请求：{"、".join(e.cmds) or "空"}）',
            [
                '⚠ 别怀疑密钥，也先别重试 capture —— 缺的是**原料**，不是钥匙。',
                '   73492 起"保存存档(upload3)"的请求体是 RSA 封装的，只有服务端解得开，',
                '   客户端没有私钥；抓包路线唯一的进料口是服务端**下发**给你的那份存档。',
                '',
                '补抓一次，按这个顺序（★ 是这次漏掉的一步）：',
                '  1. 进游戏 → 回到主界面 → **退出登录**（否则抓不到带 k 的登录/startGame）',
                '  2. 打开 ProxyPin，开始抓包',
                '  3. 登录 → 选区服进入 → 进到角色界面后，点「下载存档」★',
                '  4. 需要的话再点一次「保存存档」（这一步可有可无）',
                '  5. 回到 ProxyPin 停止抓包 → 导出 HAR',
                '',
                '导出后直接重跑 capture 就行，不用删 keys.local.json。',
            ]) from e
    except capdec.UploadCipherChanged as e:
        # ← 这条**不是**密钥问题：realKey 已经用它解开了同一份抓包里的
        #   lookmail / 响应体，重抓一百遍都是同一个结果。
        raise ui.UserError(
            f'游戏更新了：版本 {e.version} 的 {e.cmd} 请求体换了新的加密方式',
            [
                '⚠ 别重抓、别重跑 capture —— realKey 是好的（同一份抓包里别的请求都能解开），',
                '   换的是**上传存档这一个请求**的加密，密钥没变，重试没有意义。',
                '',
                '现在还能拿到存档的两条路：',
                '  1. ★ 安卓模拟器路线（最稳，不受这次改动影响）：',
                '       打开游戏进一次角色，然后  python run.py local',
                '       （会自动拉 dat.db、扫内存挖外层密钥；新版本的内层密钥跑 key crack）',
                '  2. 只想要新版本的内层密钥（以后抓包/本地存档都用得上）：',
                '       python -m yyjh key crack',
                '',
                '想继续用抓包路线的话：需要游戏这次改的加密算法 ——',
                '   把客户端的 libcocos2dlua.so（APK）或 IPA 里的主二进制发我，',
                '   我照着新函数把 capture 的这层补上。',
            ]) from e
    except savefmt.InnerKeyMissing as e:
        raise ui.UserError(
            f'游戏更新了：存档版本 {e.version} 的内层密钥还没收录',
            savefmt.inner_key_missing_hints(e)) from e
    except ValueError as e:
        # 兜底：realKey 压根没推导出来 / 推导错了。
        # ★ 这段以前写的是「确认上传过存档（command=upload2）」—— 73492 起
        #   上传命令早就是 upload3、而且是 RSA 封装（抓包解不开），
        #   照那句去"补上传"纯属白费劲，真正需要的是 download。
        raise ui.UserError(str(e), [
            '这份抓包里 realKey 推导不出来/不对，常见三种原因：',
            '  1. 抓包不是从"打开游戏之前"开始的 —— 没抓到带 k 的登录/startGame 响应',
            '  2. acc 取错了（同一份抓包里混进了别的账号）',
            '  3. 手动给的 realKey 不对',
            '',
            '补抓顺序：退出登录 → 开 ProxyPin → 登录进区服 → 点「下载存档」→ 停止并导出。',
            '（73492 起上传请求是 RSA 封装、抓包解不开，只能靠 download 响应取数，',
            ' 光"上传存档"没用。）',
            '',
            '也可以手动给 realKey：python run.py capture "抓包.har" --outer-key <realKey>',
        ]) from e
    res.kind = 'har'
    for s in res.steps:
        ui.step(s.label, s.value)
    ui.info(f'  账号: {res.account}（{res.runtime}，{res.server or "?"} 服）')

    if getattr(args, 'mem', 'auto') != 'off':
        # ★ 抓包自带 realKey —— 白送的，记下来（下次还能直接复用到别的抓包上）。
        # 注意：realKey 只管**网络载荷**，本地 dat.db 的外层密钥是另外一把
        # （每账号随机 16 位，只在游戏内存里）。以前把它记进 outer_keys，
        # `local` 路由会先命中这把错的、还以为"已有记录"就不去扫内存了。
        if res.outer_key:
            keytables.remember_real_key(res.account, res.outer_key)
            ui.info(f'  协议密钥 {res.outer_key}'
                    '（抓包自带 realKey，已记入 keys.local.json，'
                    '与本地存档的外层密钥不是同一把）')
        key, note = _mem_outer_key(args, res.account, new_content=None, res=res)
        if key:
            res.outer_key = key
        else:
            # ★ 把"本次已经成了"写在**最前面**。
            #   以前是先甩一大段 adb 排障（"adb 里没有任何设备…怎么办 1/2/3"），
            #   "本次输出不受影响"压在最后一句 —— 十个人里九个以为解析失败了。
            #   外层密钥本来就只用于解本地 dat.db，抓包路线根本用不到它。
            short = (note or '未知原因').strip().splitlines()[0].strip()
            ui.step('外层密钥', f'未取到（不影响本次存档）：{short}')
            res.warn(f'【不影响本次】存档已完整解出，可以正常使用，不用重跑。'
                     f'没拿到外层密钥（{short}）—— 它只在"以后解这个账号的本地 '
                     f'dat.db"时才用得上，抓包路线本来就不靠它；'
                     f'真到那天，先把模拟器开起来再扫一次内存即可。')
    return _finish(res, args)


# ───────────────────────── local：本地存档 / 设备存档 ─────────────────────────
def _local_from_device(args, remote=None, serial=None):
    """不给路径（或给了设备路径）时：直接去模拟器上把 dat.db 拿回来。

    为什么这个比"手动 adb pull"好：
      · 自动带上 `dat.db-wal` / `-shm` —— 手动拷最容易漏这个，一漏就是旧数据
      · 多个账号/服时列出来让你挑（显示**角色名**，md5 认不出来）
    """
    from .localdb import device as dev
    from .memscan import devices
    from .memscan.adb import AdbError

    ui.title('从设备取存档导出')

    try:
        adb, pkg, chan = devices.pick_game_device(
            exe=getattr(args, 'adb', None),
            serial=serial or getattr(args, 'serial', None),
            pkg_hint=getattr(args, 'pkg', None),
            allow_pick=not getattr(args, 'no_pick', False),
            on_progress=lambda m: ui.step('设备', m))
    except AdbError as e:
        raise ui.UserError('连不上设备，没拿到存档', [str(e)]) from e
    if not pkg:
        raise ui.UserError('没在任何设备上找到游戏', [
            '确认模拟器开着、游戏装好了',
            '渠道服可以用 --pkg 指定，例如 --pkg huawei',
            '先跑 `python -m yyjh mem devices` 看设备情况',
        ])
    ui.info(f'  设备: {adb.serial}（{adb.model()}）　{channels.label(pkg)}')

    if remote:
        info = dev.inspect(adb, remote)
        if not info:
            raise ui.UserError(f'读不了这个设备路径：{remote}', [
                '确认路径拼写；路径里的空格要加引号',
                '设备上要能 cat 这个文件（需要 root）',
            ])
    else:
        info = dev.pick_save(adb, pkg, say=ui.info,
                             allow_pick=not getattr(args, 'no_pick', False),
                             on_progress=lambda m: ui.step('设备存档', m))
        if not info:
            raise ui.UserError('设备上没找到 dat.db', [
                f'在 {channels.label(pkg)} 里没找到存档文件',
                '进游戏登一次号、存一次档再试',
                '或者从模拟器里把 dat.db 拷出来，直接 `yyjh local 路径`',
            ])

    # 后面走和本地文件完全一样的流程；顺手把设备信息填回去，
    # 免得内存步骤又去扫一遍设备 / 选错渠道
    args.serial = adb.serial
    args.pkg = pkg
    src = f"{adb.serial}:{info['remote']}"
    try:
        res = _local_decode(args, info['local'], save_id=info['id'], label=src)
    except ui.UserError as e:
        # 把"是这份存档出的问题"告诉上层：向导要就地重试同一份，别再让用户选一次
        e.source = src
        raise
    res.source = src
    res.runtime = 'android'
    return _finish(res, args)


def _local_decode(args, path, save_id=None, label=None):
    """给定一个**本地** dat.db 路径，走完整解密流程。返回 SaveResult。"""
    from .localdb import decrypt as dbdec
    from .localdb import sqlitedb

    try:
        row = sqlitedb.read_save(path)
    except Exception as e:
        raise ui.UserError(f'打不开这个 dat.db：{e}',
                           ['确认是游戏的存档文件（SQLite，含 save 表）',
                            '如果是从设备拷出来的，dat.db-wal 也要一起拷']) from e
    content = row['content']

    ui.info(f'  输入: {label or os.path.basename(path)}（{len(content):,} 字符）')
    fmt = savefmt.detect_format(content)
    _require(fmt is not None, '这个文件不像是游戏的存档（格式认不出来）',
             ['确认是 dat.db，不是别的数据库'])
    ui.step('格式', '新版（需要外层密钥）' if fmt == savefmt.NEW else '初版（无需外层密钥）')

    account = args.account or ''
    outer_key = args.outer_key
    mem_note = '不适用'
    if fmt == savefmt.NEW and not outer_key:
        # dat.db 里没有账号，只有 md5(账号)（= row['id']）——
        # 把它传给内存步骤：既能定位到正确的渠道包，又能查/存已记录的外层密钥
        outer_key, mem_note = _mem_outer_key(args, account, new_content=content,
                                             save_id=save_id or row['id'])
        if not outer_key:
            ui.step('外层密钥', f'{mem_note} -> 改用已知密钥表尝试')

    try:
        res = dbdec.decode_db(path, runtime=args.runtime, outer_key=outer_key,
                              inner_key=args.inner_key, account=account,
                              avatar_field=args.avatar_field)
    except savefmt.InnerKeyMissing as e:
        # 注意：走到这里说明**外层密钥其实是对的**（不然解不出合法的初版格式），
        # 缺的是这一版的内层密钥 —— 千万别再报成"外层密钥不对"。
        ui.step('版本号', e.version)
        if outer_key:
            ui.step('外层密钥', f'{outer_key}（已确认可用，问题不在这里）')
        raise ui.UserError(
            f'游戏更新了：存档版本 {e.version} 的内层密钥还没收录',
            savefmt.inner_key_missing_hints(e)) from e
    except ValueError as e:
        msg = str(e)
        if '外层' in msg:
            raise ui.UserError('拿不到可用的外层密钥，解不开这个新版存档', [
                f'内存步骤：{mem_note}',
                f'具体原因：{msg}',
                '怎么办：',
                '  1. 先把游戏打开、进一次角色（外层密钥只在游戏运行时存在内存里），再跑一次',
                '  2. 或者用 --outer-key 直接指定（16 个字符）',
                f'  3. 或者先单独挖：python -m yyjh mem crack --db "{os.path.basename(path)}"',
            ]) from e
        raise ui.UserError(msg, [
            '这个存档版本的内层密钥不在表里。',
            '怎么办：',
            '  1. 从第三方导出工具里提取密钥表：',
            '       python -m yyjh exe tables <工具.exe> --save',
            '  2. 或手动补：python -m yyjh key add <版本> <16位密钥>',
            '  3. 或 --inner-key 直接指定',
        ]) from e
    res.kind = 'db'
    for s in res.steps:
        ui.step(s.label, s.value)
    # 解出来才知道账号 —— 把刚才用 save_id 换来的外层密钥补记到账号名下
    if res.account and res.outer_key and not keytables.outer_key(res.account, res.save_id):
        try:
            keytables.remember_outer_key(res.account, res.outer_key, save_id=res.save_id)
            ui.step('外层密钥', f'已记到账号 {res.account} 名下')
        except Exception:
            pass
    return res


def pipeline_local(args):
    raw = paths.strip_quotes(getattr(args, 'input', None))

    # ① 不给路径 -> 直接去模拟器上取（安卓专属；iOS 只能抓包）
    if not raw:
        return _local_from_device(args)

    # ② 给的是设备上的路径（`/data/...` 或 `127.0.0.1:7555:/data/...`）
    devpath = paths.parse_device_path(raw)
    if devpath:
        return _local_from_device(args, remote=devpath[1], serial=devpath[0])

    # ③ 本地文件
    p = paths.resolve(raw)
    _require(os.path.exists(p), f'找不到存档文件：{p}',
             ['确认路径；路径带空格时加引号',
              '安卓存档一般叫 dat.db',
              '不给路径直接跑 `yyjh local`，会自己去模拟器上取'])
    ui.title('从本地存档导出')
    return _finish(_local_decode(args, p), args)


# ───────────────────────── rebuild：明文 JSON ─────────────────────────
def pipeline_rebuild(args):
    p = paths.resolve(args.input)
    _require(os.path.exists(p), f'找不到 JSON：{p}', ['确认路径；带空格时加引号'])

    ui.title('从明文 JSON 重建存档')
    ui.info(f'  输入: {os.path.basename(p)}')
    try:
        data = json.load(open(p, encoding='utf-8'))
    except Exception as e:
        raise ui.UserError(f'这个 JSON 读不了：{e}',
                           ['确认是解密后的存档 JSON（顶层是对象）']) from e
    _require(isinstance(data, dict), 'JSON 顶层不是对象，不像是存档',
             ['这个接口要的是"解密后的存档 JSON"'])

    res = model.SaveResult(source=p, kind='json', runtime=args.runtime)
    res.data = data
    ui.step('读取 JSON', f'{len(data)} 个顶层字段')

    res.account = args.account or savefmt.account_from_json(data)
    _require(res.account, 'JSON 里没有 EncryptID，也没给 --account',
             ['用 --account 指定账号（安卓是 32 位十六进制，iOS 是邮箱）'])
    res.save_id = savefmt.save_id_of(res.account)
    ui.step('账号', f'{res.account}（save.id = {res.save_id}）')

    version = args.version or savefmt.version_from_json(data)
    _require(version, 'JSON 里没有 DBVersion，也没给 --version',
             ['用 --version 指定，8 位数字，例如 00073440'])
    res.version = str(version).zfill(8)
    ui.step('版本号', res.version)
    res.server = savefmt.server_from_json(data)
    ui.step('服务器', res.server or '?')

    key, note = keytables.lookup_inner_key(
        res.runtime, res.version, extras=[args.inner_key] if args.inner_key else [])
    _require(key, f'没有版本 {res.version} 的内层密钥，无法重新加密', [
        '怎么办：',
        '  1. 从第三方导出工具 exe 里提取密钥表：',
        '       python -m yyjh exe tables <工具.exe> --save',
        f'  2. 或者手动补：python -m yyjh key add {res.version} <16位密钥>',
        '  3. 或者用 --inner-key 直接指定',
    ])
    res.inner_key = key
    ui.info(f'  内层密钥 {key}（{note}）')

    res.content = savefmt.build_old(data, version, key)
    ui.step('重新加密', f'初版 content {len(res.content):,} 字符')
    res.player = player_mod.build(data, res.version, args.avatar_field)

    try:
        back, _ = savefmt.unwrap_old(res.content, res.runtime, key)
        if back == data:
            ui.step('往返自检', '✅ 解密结果与输入完全一致')
        else:
            res.warn('往返自检不一致：请确认版本号/内层密钥与这个存档匹配')
    except Exception as e:
        res.warn(f'往返自检失败：{e}')
    return _finish(res, args)


# ───────────────────────── auto / batch ─────────────────────────
def detect_kind(path):
    name = os.path.basename(path)
    low = name.lower()
    if low.endswith('.har'):
        return 'capture'
    if low.endswith('.json'):
        return 'rebuild'
    try:
        with open(path, 'rb') as f:
            head = f.read(16)
    except OSError:
        return None
    if head[:16] == b'SQLite format 3\x00' or low.endswith('.db'):
        return 'local'
    return None


def pipeline_auto(args):
    kind = detect_kind(paths.resolve(args.input))
    if kind == 'capture':
        return pipeline_capture(args)
    if kind == 'local':
        return pipeline_local(args)
    if kind == 'rebuild':
        return pipeline_rebuild(args)
    raise ui.UserError(f'认不出这个文件该用哪个接口：{args.input}', [
        '支持的输入：抓包 .har / 存档 .db / 明文 .json',
        '或者显式指定：捕获 capture / 本地 local / 重建 rebuild',
    ])


SKIP_DIRS = {'output', 'out', '.git', '__pycache__', 'node_modules',
             'tool_1.0', 'tool_1.1'}


def find_inputs(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            low = fn.lower()
            if low.endswith('.har') or low == 'dat.db' or low.endswith('_dat.db'):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def pipeline_batch(args):
    root = paths.resolve(args.root)
    _require(os.path.isdir(root), f'不是一个目录：{root}', ['给一个包含抓包/存档的目录'])

    files = find_inputs(root)
    if getattr(args, 'only', None):
        files = [f for f in files if args.only in f]
    _require(files, f'目录里没找到可处理的文件：{root}',
             ['支持 *.har 和 dat.db'])
    ui.title(f'批量处理 {len(files)} 个文件')

    rows = []
    for i, p in enumerate(files, 1):
        ui.info()
        ui.info(f'[{i}/{len(files)}] {paths.rel(p)}')
        sub = argparse.Namespace(**vars(args))
        sub.input = p
        # 批量时不要弹"选哪个渠道"的菜单，否则卡在等输入
        sub.no_pick = True
        try:
            res = pipeline_auto(sub)
            ui.ok(f'{res.nick or res.account}  {res.field_count} 字段')
            rows.append((paths.rel(p), 'OK', res.nick, res.runtime, res.version,
                         res.field_count))
        except Exception as e:
            ui.error(f'{str(e).splitlines()[0]}')
            rows.append((paths.rel(p), '失败', '', '', '',
                         str(e).splitlines()[0]))
    ui.info()
    ui.rule(96)
    for r in rows:
        ui.info(f'{r[0][:48]:48} {r[1]:5} {str(r[2])[:10]:10} {str(r[3]):8} '
                f'{str(r[4]):10} {r[5]}')
    ok = sum(1 for r in rows if r[1] == 'OK')
    ui.rule(96)
    ui.info(f'成功 {ok}/{len(rows)}')
    if ok != len(rows):
        ui.hint('失败的文件用 -v 跑一次可以看到具体是卡在哪一步')
    return 0 if ok == len(rows) else 1


# ───────────────────────── 注册 ─────────────────────────
def _add_out(p):
    p.add_argument('--outdir', help='输出根目录（默认 tool_1.1/output）')
    p.add_argument('--name', help='换掉第一层目录名（默认 平台-区服-用户名）')
    p.add_argument('--save-format', choices=('v18', 'current'), default='v18',
                   help='dat.db 里的存档格式：v18(默认)=平台固定密钥，'
                        '存档工具/解析网站直接能读；current=按版本的内层密钥')
    p.add_argument('--no-db', action='store_true', help='不写 dat.db')
    p.add_argument('--no-json', action='store_true', help='不写 JSON')
    p.add_argument('--no-meta', action='store_true', help='不写 meta.json')
    p.add_argument('--report', action='store_true', help='额外写一份流程报告 .report.md')
    p.add_argument('--inner-key', help='内层密钥（一般不用给，自动查表）')
    p.add_argument('--avatar-field', default='auto',
                   help='player.avatar 取 MainRole 的哪个字段（默认 auto=Cloth）')


def _add_mem(p):
    p.add_argument('--mem', choices=MEM_MODES, default='auto',
                   help='内存步骤：auto(默认，没记录才扫) / force(强制重扫) / off(跳过)')
    p.add_argument('--adb', help='adb 路径')
    p.add_argument('--serial', help='设备 serial（不填自动挑装了游戏的那台）')
    p.add_argument('--pkg', default=None,
                   help='游戏包名或渠道名（默认自动探测；同设备多渠道时可写 --pkg huawei）')
    p.add_argument('--no-pick', action='store_true',
                   help='同设备装了多个渠道时不弹菜单，直接取第一个')
    p.add_argument('--pid', help='直接指定游戏进程 pid')
    p.add_argument('--all-regions', action='store_true', help='连文件映射区域一起扫')
    p.add_argument('--min-kb', type=int, default=64)
    p.add_argument('--max-mb', type=int, default=64, help='单区域最多读多少 MB')
    p.add_argument('--limit-mb', type=int, help='总共最多扫多少 MB')
    p.add_argument('--workers', type=int, help='爆破进程数')


def add_parsers(sub):
    p = sub.add_parser('capture',
                       help='★ 从抓包导出（抓包 .har -> 初版 dat.db）')
    p.add_argument('input', metavar='抓包.har')
    p.add_argument('--index', type=int, help='用第几个 upload（默认取请求体最大的）')
    p.add_argument('--outer-key', help='HAR 里的 realKey（没有 k 响应头时手动给）')
    _add_out(p)
    _add_mem(p)
    p.set_defaults(func=pipeline_capture)

    p = sub.add_parser('local',
                       help='★ 从存档导出（不给路径 = 直接去模拟器上取）')
    p.add_argument('input', nargs='?', metavar='dat.db',
                   help='本地 dat.db；不填则自己到设备上找并拉回来；'
                        '也可直接给设备路径（/data/... 或 serial:/data/...）')
    p.add_argument('--outer-key', help='外层密钥（不给就自动：先查记录，再扫内存）')
    p.add_argument('--account', help='账号（用于查已记住的外层密钥）')
    p.add_argument('--runtime', default='android', choices=['android', 'ios'])
    _add_out(p)
    _add_mem(p)
    p.set_defaults(func=pipeline_local)

    p = sub.add_parser('rebuild',
                       help='★ 从明文 JSON 重建（存档.json -> 初版 dat.db）')
    p.add_argument('input', metavar='存档.json')
    p.add_argument('--version', help='存档版本号 8 位（默认读 JSON 里的 DBVersion）')
    p.add_argument('--account', help='账号（默认读 JSON 里的 EncryptID）')
    p.add_argument('--runtime', default='android', choices=['android', 'ios'])
    _add_out(p)
    p.set_defaults(func=pipeline_rebuild)

    p = sub.add_parser('auto', help='★ 按扩展名自动选 capture/local/rebuild')
    p.add_argument('input')
    p.add_argument('--index', type=int)
    p.add_argument('--outer-key')
    p.add_argument('--account')
    p.add_argument('--version')
    p.add_argument('--runtime', default='android', choices=['android', 'ios'])
    _add_out(p)
    _add_mem(p)
    p.set_defaults(func=pipeline_auto)

    p = sub.add_parser('batch', help='★ 批量处理一个目录')
    p.add_argument('root', metavar='目录')
    p.add_argument('--only', help='只处理路径里含该子串的')
    p.add_argument('--outer-key')
    p.add_argument('--account')
    p.add_argument('--version')
    p.add_argument('--runtime', default='android', choices=['android', 'ios'])
    p.add_argument('--index', type=int)
    _add_out(p)
    _add_mem(p)
    p.set_defaults(func=pipeline_batch)
