# -*- coding: utf-8 -*-
"""向导层：给"只想用一下"的人用的入口。

**它不改任何业务逻辑** —— 只是在 `yyjh local` / `yyjh capture` 前面套一层问答，
跑完再问一句要不要打开网页版模拟器。

流程::

    已导入过存档时先列出来，默认选最新的那条，选中就直接开浏览器
        └─ 选「导入新存档」→ 回到开头流程

    选择模式
        ├─ local    本地存档  → 我有文件 / 从模拟器拉取
        └─ capture  抓包导入  → 输入抓包文件路径

    导入成功 → 问「是否打开网页版」→ 打开(默认浏览器) / 退出

用法::

    python run.py            不带参数就是它
    python -m yyjh start

参数会原样转给底下的 local / capture，例如 `yyjh start --pkg huawei --report`。

调试用：设 `YYJH_WIZARD_TTY=1` 可以在管道/重定向下也走交互分支
（正常使用时不需要，它就是给测试用的口子）。
"""
import io
import json
import os
import subprocess
import sys
import webbrowser

from . import __version__, ui
from .config import paths

# 「已导入」列表最多显示几条（多了翻屏没意义）
LIST_LIMIT = 15
# 回车以外表示退出的输入
_QUIT = ('q', 'quit', 'exit')

# 与 web 端 shared/save_decrypt.js 里的键名保持一致
SAVE_SHARE_KEY = 'yyjh_share_save_v1'
SAVE_SHARE_META_KEY = 'yyjh_share_save_meta_v1'
# 自动载入入口页：生成在 web/ 下，但**不改动 web 里任何既有文件**
AUTOLOAD_NAME = '_autoload_last.html'


# ───────────────────────── 网页版 ─────────────────────────
def web_index():
    """网页版入口文件（tool_1.1/web/index.html）。"""
    return os.path.join(paths.TOOL_DIR, 'web', 'index.html')


def _uri_of(path):
    p = os.path.abspath(path)
    if not os.path.exists(p):
        return None
    try:
        from pathlib import Path
        return Path(p).as_uri()
    except Exception:                                  # pragma: no cover
        return 'file:///' + p.replace(os.sep, '/')


def _web_uri():
    return _uri_of(web_index())


def pack_save(data):
    """压成 web 端 loadShareStore() 认得的形式：base64(zlib(JSON))。

    web 端解压走 pako.inflate，与 Python 的 zlib 是同一个容器格式
    （RFC1950 头 + adler32 尾），两边互通。
    """
    import base64
    import zlib
    raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return base64.b64encode(zlib.compress(raw, 9)).decode('ascii')


def write_autoload(row):
    """在 web/ 下生成一个「自动载入」入口页，返回它的路径（不行就返回 None）。

    做法：把 web/index.html **原样复制**一份，只在 </head> 前插一段脚本，
    把存档写进 localStorage —— 打开它就等于「已经导入过」。
    web/ 里没有任何既有文件被改动，index.html 照旧能单独使用。
    """
    src = web_index()
    json_path = row.get('json')
    if not os.path.exists(src) or not json_path or not os.path.exists(json_path):
        return None
    with io.open(json_path, encoding='utf-8') as f:
        data = json.load(f)
    with io.open(src, encoding='utf-8') as f:
        html = f.read()

    old = row.get('meta') or {}
    seed = {
        'version': str(old.get('version') or ''),
        'platform': str(old.get('runtime') or old.get('platform') or 'android'),
        'channel': str(old.get('channel') or ''),
        'format': 'json-local',
        'fileName': os.path.basename(row.get('db') or json_path),
        'importedAt': int(_now() * 1000),
    }
    script = ''.join([
        '<script>(function(){try{',
        'localStorage.setItem(', json.dumps(SAVE_SHARE_KEY), ',',
        json.dumps(pack_save(data)), ');',
        'localStorage.setItem(', json.dumps(SAVE_SHARE_META_KEY), ',',
        json.dumps(json.dumps(seed, ensure_ascii=False)), ');',
        '}catch(e){document.addEventListener("DOMContentLoaded",function(){',
        'var m=document.getElementById("homeImportMessage");',
        'if(m){m.textContent="自动载入存档失败："+((e&&e.message)||e);',
        'm.classList.add("is-error");}});}})();</script>',
    ])
    out = os.path.join(os.path.dirname(src), AUTOLOAD_NAME)
    with io.open(out, 'w', encoding='utf-8', newline='\n') as f:
        f.write(html.replace('</head>', script + '\n</head>', 1))
    return out


def refresh_worker_bundle():
    """刷新网页版的「后台计算线程整包」（file:// 下自动洗脉要用）。

    改过 web/shared 里的优化器却忘了重打时，这里会自动补上。
    失败不影响其它功能（http 下本来也不需要这个包）。
    """
    try:
        from . import webapp
        shared = os.path.join(os.path.dirname(web_index()), 'shared')
        path, changed = webapp.write_bundle(shared)
        if changed:
            ui.debug(f'已重新生成后台线程整包 {os.path.basename(path)}')
        return path
    except Exception as e:                             # noqa: BLE001
        ui.debug(f'刷新后台线程整包失败（不影响使用）：{e}')
        return None


def open_web(snapshot=None):
    """用默认浏览器打开网页版。

    给了 snapshot 就先生成自动载入页（进去直接就是已导入状态），
    否则就是普通的 web/index.html。
    """
    refresh_worker_bundle()
    target = web_index()
    if snapshot:
        try:
            page = write_autoload(snapshot)
            if page:
                target = page
            else:
                ui.debug('这条记录没有 dat.json，改开普通入口页')
        except Exception as e:
            ui.warn(f'生成自动载入页失败，改开普通入口页：{e}')

    uri = _uri_of(target)
    if not uri:
        ui.warn('找不到网页版，跳过打开浏览器')
        ui.info(f'  期望位置：{paths.rel(web_index())}')
        return False
    ok = False
    try:
        ok = bool(webbrowser.open(uri))
    except Exception as e:                             # pragma: no cover
        ui.debug(f'webbrowser.open 失败: {e}')
    if ok:
        ui.ok('已用默认浏览器打开网页版模拟器')
        if os.path.abspath(target) != os.path.abspath(web_index()):
            ui.info('  存档已自动载入，进去就能直接用。')
        else:
            ui.info('  在网页里点「导入数据」，选上面的 dat.db。')
    else:
        ui.warn('没能自动打开浏览器，请手动打开这个文件：')
        ui.info(f'  {paths.rel(target)}')
    return ok


def _copy_path(text):
    """把路径塞进剪贴板 —— 网页版的文件对话框里可以直接粘贴。"""
    if not text:
        return False
    try:
        if os.name == 'nt':
            quoted = "'" + str(text).replace("'", "''") + "'"
            cmd = ['powershell', '-NoProfile', '-NonInteractive',
                   '-Command', 'Set-Clipboard -Value ' + quoted]
            kwargs = {}
        elif sys.platform == 'darwin':
            cmd, kwargs = ['pbcopy'], {'input': str(text).encode('utf-8')}
        else:
            cmd = ['xclip', '-selection', 'clipboard']
            kwargs = {'input': str(text).encode('utf-8')}
        p = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=20, **kwargs)
        return p.returncode == 0
    except Exception:
        return False


# ───────────────────────── 历史存档 ─────────────────────────
def _fmt_stamp(snap):
    """`20261001-174128` -> `2026-10-01 17:41`（撞名的 -2 后缀原样保留）。"""
    parts = str(snap).split('-')
    if len(parts) >= 2 and len(parts[0]) == 8 and len(parts[1]) >= 4:
        d, t = parts[0], parts[1]
        tail = ('-' + '-'.join(parts[2:])) if len(parts) > 2 else ''
        return f'{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}{tail}'
    return str(snap)


def list_snapshots(outdir=None):
    """扫 `<output>/<平台-区服-用户名>/<时间戳>/`，按时间**倒序**返回。

    每条是 dict，字段见下；只认里面有 dat.db 或 dat.json 的目录。
    """
    root = outdir or paths.OUT_DIR
    rows = []
    if not os.path.isdir(root):
        return rows
    for group in sorted(os.listdir(root)):
        gdir = os.path.join(root, group)
        if group.startswith('_') or not os.path.isdir(gdir):   # _cache 之类
            continue
        for snap in sorted(os.listdir(gdir)):
            sdir = os.path.join(gdir, snap)
            if not os.path.isdir(sdir):
                continue
            db = os.path.join(sdir, 'dat.db')
            js = os.path.join(sdir, 'dat.json')
            if not os.path.exists(db) and not os.path.exists(js):
                continue
            meta = {}
            mp = os.path.join(sdir, 'dat.meta.json')
            if os.path.exists(mp):
                try:
                    with open(mp, encoding='utf-8') as f:
                        meta = json.load(f)
                except Exception:
                    meta = {}
            rows.append({
                'dir': sdir,
                'group': group,
                'snap': snap,
                'db': db if os.path.exists(db) else None,
                'json': js if os.path.exists(js) else None,
                'meta': meta,
                'mtime': os.path.getmtime(sdir),
            })
    rows.sort(key=lambda r: (r['mtime'], r['snap']), reverse=True)
    return rows


def describe(row):
    """列表里那一行。"""
    meta = row['meta']
    nick = meta.get('nick') or meta.get('account') or row['group']
    runtime = meta.get('runtime') or '?'
    server = meta.get('server')
    field = meta.get('field_count')
    bits = [f'{nick}']
    loc = f'{runtime}' + (f'·s{server}' if server else '')
    bits.append(loc)
    bits.append(_fmt_stamp(row['snap']))
    if field:
        bits.append(f'{field} 字段')
    if not row['db']:
        bits.append('无 dat.db')
    return '　'.join(bits)


# ───────────────────────── 跑底层子命令 ─────────────────────────
def _run_argv(argv):
    """按子命令的参数定义解析并执行 —— 复用同一套默认值，不另起一份。"""
    from . import __main__ as cli
    ns = cli.build_parser().parse_args(argv)
    func = getattr(ns, 'func', None)
    if func is None:                                   # pragma: no cover
        raise ui.UserError('内部错误：子命令没绑定执行函数')
    return func(ns)


def _forward(args):
    """把 start 上给的参数转成底层子命令的参数（没给的就不传）。"""
    out = []
    for name, flag in (('outdir', '--outdir'), ('runtime', '--runtime'),
                       ('pkg', '--pkg'), ('account', '--account')):
        v = getattr(args, name, None)
        if v:
            out += [flag, str(v)]
    if getattr(args, 'no_pick', False):
        out.append('--no-pick')
    if getattr(args, 'report', False):
        out.append('--report')
    return out


def _newest_snapshot(outdir, since):
    """刚跑完的那一次输出 —— 按修改时间挑，比猜文件名稳。"""
    rows = list_snapshots(outdir)
    if not rows:
        return None
    fresh = [r for r in rows if r['mtime'] >= since - 2]
    return (fresh or rows)[0]


def _ask_path(prompt, hint):
    """让用户给一个路径（可以直接把文件拖进窗口）。"""
    for _ in range(5):
        print()
        try:
            ans = input(prompt).strip()
        except EOFError:
            print()
            raise ui.Cancelled()
        if ans.lower() in _QUIT:
            print('  已取消')
            raise ui.Cancelled()
        if ans:
            return paths.strip_quotes(ans)
        print(f'  {hint}')
    raise ui.Cancelled()


# ───────────────────────── 向导主体 ─────────────────────────
def _pick_saved(rows):
    """列已导入的存档。返回选中的 row，或 None 表示"导入新存档"。"""
    shown = rows[:LIST_LIMIT]
    lines = [describe(r) for r in shown]
    lines.append('＋　导入新存档')
    idx = ui.choose('已导入的存档（默认最新的那条）：', lines, default=0)
    if idx >= len(shown):
        return None
    return shown[idx]


def _forward_key(args):
    """转给 `key crack` 的参数（它没有 --outdir，所以不能直接复用 _forward）。"""
    out = []
    for name, flag in (('runtime', '--runtime'), ('pkg', '--pkg'),
                       ('account', '--account')):
        v = getattr(args, name, None)
        if v:
            out += [flag, str(v)]
    if getattr(args, 'no_pick', False):
        out.append('--no-pick')
    return out


def _is_key_expired(err):
    """这条错是不是「游戏更新了、密钥表过期」。"""
    from .game import savefmt
    if isinstance(getattr(err, '__cause__', None), savefmt.InnerKeyMissing):
        return True
    return '游戏更新了' in str(err)


def _crack_target(argv, err):
    """挖密钥时要跟着**出问题的那一份**走，不能让用户再选一遍。

    优先级：命令里显式给的路径 > 设备上那份（local 失败时挂在异常上）> 自动取最新。
    （选来选去很容易选到别的服、别的版本上，白挖一场）
    """
    if len(argv) > 1 and not str(argv[1]).startswith('-'):
        flag = '--har' if argv and argv[0] == 'capture' else '--db'
        return [flag, str(argv[1])]
    src = getattr(err, 'source', '')
    if src:
        return ['--db', src]
    return ['--no-pick']


def _rc_of(v):
    """把流程函数的返回值归一成退出码。

    ★ 成功路径返回的是 SaveResult（**不是 0**）—— 别把它当真值当成失败，
    否则会出现"明明 ✅ 完成，却提示没导出成功、还不开网页版"。
    """
    if isinstance(v, bool):
        return 0 if v else 2
    if isinstance(v, int):
        return int(v)
    return 0


def _ensure_game_running(args):
    """挖之前先确认游戏开着。返回 True = 可以挖。

    密钥只存在**游戏进程的内存**里。以前不检查，模拟器上没开游戏也会去扫，
    扫完失败再把用户用回第一步 —— 白花几十秒，还让人以为工具坏了。
    """
    from .memscan import keyhunt
    while True:
        _adb, pid, why = keyhunt.game_running(args)
        if pid:
            return True
        ui.warn(f'现在挖不了：{why}')
        ui.info('  密钥只存在**游戏进程内存**里 —— 模拟器上要先把游戏打开，')
        ui.info('  并且**进一次角色**（要进到能看到角色的界面），然后才能挖。')
        if ui.choose('接下来怎么办？', [
            '我已经把游戏打开、进到角色里了 —— 继续挖',
            '这次先算了（回头重跑 python run.py）',
        ], default=0) != 0:
            return False


def _run_import(argv, args):
    """跑 local / capture；如果报的是「更新了、密钥表过期」，就地问要不要挖一下。

    用户的反馈很直白：这种情况重试多少遍都一样，不该让人白试。
    所以这里直接把「挖密钥 -> 重跑原命令」串起来。
    ★ 挖的过程**绝不冒泡异常**：一旦冒泡上去，最外层会问"要回到开头重来吗"，
    用户看到的就是"又让我从选模式重来一遍"（实测撞到过）。
    """
    try:
        return _run_argv(argv)
    except ui.UserError as e:
        if not _is_key_expired(e):
            raise
        ui.report_error(e, ui.is_verbose())
        if ui.choose('要现在挖新版本密钥吗？（游戏要开着，工具全自动搞定）',
                     ['挖（推荐，几分钟）', '算了，先退出'], default=0) != 0:
            return 2
        ui.info()
        while True:
            if not _ensure_game_running(args):
                return 2
            try:
                rc = _run_argv(['key', 'crack'] + _crack_target(argv, e)
                               + _forward_key(args))
            except ui.UserError as e2:
                # 挖的过程中报错（游戏没开/没解密过这一版/设备掉了）——
                # 当成"没挖到"处理，就地给选择，别让异常冒泡回开头
                ui.report_error(e2, ui.is_verbose())
                rc = 2
            if not rc:
                ui.info()
                ui.info('  密钥已存好，刚才那条命令重新跑一遍：')
                return _run_argv(argv)

            # 没挖到：**别把用户甩回开头重跑一遍整个流程**，就地给几个选择
            ui.error('这次没挖到密钥（多半是游戏那边还没把这一版存档解密过）。')
            pick = ui.choose('接下来怎么办？', [
                '再挖一次（先去游戏里**进一次角色**，或手动**上传一次存档**）',
                '我知道密钥，手动填进去',
                '放弃这次导入',
            ], default=0)
            if pick == 0:
                ui.info()
                continue
            if pick == 1:
                key = _ask_path('粘贴 16 位内层密钥: ', '应该是 16 个字符')
                if len(key) == 16:
                    try:
                        rc2 = _run_argv(['key', 'crack', '--inner-key', key]
                                        + _crack_target(argv, e)
                                        + _forward_key(args))
                    except ui.UserError as e3:
                        ui.report_error(e3, ui.is_verbose())
                        rc2 = 2
                    if not rc2:
                        ui.info()
                        ui.info('  已经存好了，刚才那条命令重新跑一遍：')
                        return _run_argv(argv)
                ui.warn('这条密钥没被接受。')
                return 2
            return 2


def _flow_import(args):
    mode = ui.choose('请选择模式：', [
        'local　　—— 本地存档（我有文件 / 从模拟器拉取）',
        'capture —— 抓包导入（ProxyPin / mitmproxy 的 .har）',
    ], default=0)

    if mode == 1:
        p = _ask_path('抓包文件路径（可直接把文件拖进来）: ',
                      '路径不能为空；q + 回车 = 退出')
        ui.info()
        return _run_import(['capture', p] + _forward(args), args)

    src = ui.choose('本地存档来源：', [
        '我有存档文件，手动给路径',
        '从模拟器 / 设备上拉取（游戏要在运行）',
    ], default=0)
    if src == 0:
        p = _ask_path('存档文件路径（可直接把文件拖进来）: ',
                      '路径不能为空；q + 回车 = 退出')
        ui.info()
        return _run_import(['local', p] + _forward(args), args)
    ui.info()
    return _run_import(['local'] + _forward(args), args)


def _confirm_open(row):
    folder = None
    if row:
        folder = os.path.dirname(row.get('db') or row.get('json') or '')
    if folder and _copy_path(folder):
        ui.info('  存档目录已复制到剪贴板（要手动导入就粘到对话框地址栏）')
    pick = ui.choose('要打开网页版模拟器吗？', [
        '打开（默认浏览器，存档自动载入）',
        '不打开，直接退出',
    ], default=0)
    if pick == 0:
        open_web(row)
    else:
        ui.info('  已退出。下次直接跑 `python run.py` 就能再选这个存档。')
    return 0


def _wizard(args):
    ui.title(f'烟雨观星 · 存档工具 v{__version__}')
    ui.info('  存档导入向导 —— 全程本地，不联网')

    rows = list_snapshots(getattr(args, 'outdir', None))
    if rows:
        picked = _pick_saved(rows)
        if picked is not None:
            # 已经有存档了：直接开网页（并自动载入），不重复导入
            ui.info()
            ui.step('使用存档', describe(picked))
            folder = os.path.dirname(picked.get('db') or picked.get('json') or '')
            if folder and _copy_path(folder):
                ui.info('  存档目录已复制到剪贴板（要手动导入就粘到对话框地址栏）')
            ui.info()
            open_web(picked)
            return 0
    else:
        ui.info('  （还没有导入过存档）')

    stamp = _now()
    rc = _rc_of(_flow_import(args))
    if rc:
        # 没导出成功就别问要不要打开网页版了 —— 开了也是空的
        ui.info()
        ui.warn('这次没导出成功，就不开网页版了。')
        ui.info('  修好上面的问题后重跑 `python run.py` 即可。')
        return rc
    row = _newest_snapshot(getattr(args, 'outdir', None), stamp)
    if row:
        ui.info()
        ui.step('本次输出', paths.rel(row['dir']) + os.sep)
    return _confirm_open(row)


def _now():
    import time
    return time.time()


# ───────────────────────── 子命令 ─────────────────────────
def pipeline_start(args):
    if not ui.is_tty() and not os.environ.get('YYJH_WIZARD_TTY'):
        ui.warn('当前不是交互终端，向导用不了。')
        ui.info('  直接用子命令：')
        ui.info('    yyjh local [dat.db]      本地存档 / 从模拟器取')
        ui.info('    yyjh capture <抓包.har>  从抓包导出')
        return 1
    while True:
        try:
            return _wizard(args)
        except ui.UserError as e:
            ui.report_error(e, ui.is_verbose())
            if ui.choose('要回到开头重来吗？', ['重来一次', '放弃退出'],
                         default=1) != 0:
                return 2


def commands_of(parser):
    """取出 parser 里注册的子命令名（含别名）。"""
    import argparse
    names = set()
    for action in getattr(parser, '_actions', ()):
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices)
    return names


def with_default_start(argv, parser):
    """没给子命令时补一个 `start` —— 这样 `run.py --outdir x` 也能跑。

    · 空参数            由调用方决定（交互终端才走向导，否则打帮助）
    · -h/--help/-V      原样，交给 argparse 打全局帮助
    · 已含某个子命令    原样
    · 其它（只有向导的参数）→ ['start'] + argv
    """
    argv = list(argv)
    if not argv:
        return ['start']
    if any(a in ('-h', '--help', '-V', '--version') for a in argv):
        return argv
    if any(a in commands_of(parser) for a in argv):
        return argv
    return ['start'] + argv


def add_parsers(sub):
    p = sub.add_parser('start', aliases=['guide'],
                       help='★ 向导：选模式 → 导入存档 → 打开网页版')
    p.add_argument('--outdir', help='输出根目录（默认 tool_1.1/output）')
    p.add_argument('--runtime', choices=['android', 'ios'],
                   help='平台（一般不用给，自动判断）')
    p.add_argument('--pkg', help='游戏包名或渠道名（同设备多渠道时指定）')
    p.add_argument('--account', help='账号（用于查已记住的外层密钥）')
    p.add_argument('--no-pick', action='store_true',
                   help='同设备装了多个渠道时不弹菜单，直接取第一个')
    p.add_argument('--report', action='store_true', help='额外写一份流程报告')
    p.set_defaults(func=pipeline_start)
    return p
