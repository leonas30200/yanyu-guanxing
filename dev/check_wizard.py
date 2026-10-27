# -*- coding: utf-8 -*-
"""向导层自检。

用脚本化的输入把 `yyjh start` 的每条分支都走一遍：
  · 历史存档的扫描 / 排序 / 描述 / 忽略 _cache
  · 「选中已有存档 -> 直接开浏览器」
  · 「导入新存档 -> local -> 我有文件」
  · 「导入新存档 -> local -> 从模拟器拉取」
  · 「导入新存档 -> capture」
  · 拖文件进来带的引号会被去掉
  · start 上的参数会转给底层子命令
  · 敲 q 走 Cancelled

为了没有副作用：浏览器、剪贴板、真实子命令全部换成桩，
输出目录一律用临时目录，跑完删掉。

用法::

    cd tool_1.1
    python dev\\check_wizard.py
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
from yyjh import ui, wizard               # noqa: E402
from yyjh.game import savefmt             # noqa: E402

_PASS = []
_FAIL = []
_LOG = io.StringIO()          # 收 ui.info/step/title 的输出（它们走 logging）
_TMP = []


def check(name, cond, detail=''):
    if cond:
        _PASS.append(name)
        print(f'  [OK]   {name}')
    else:
        _FAIL.append(name)
        print(f'  [FAIL] {name}  {detail}')


# ───────────────────────── 桩 ─────────────────────────
class Stub:
    """把浏览器 / 剪贴板 / 真实子命令都换成记录器。"""

    def __init__(self, outdir):
        self.outdir = outdir
        self.opened = []
        self.runs = []
        self.copied = []

    def __enter__(self):
        self._run = wizard._run_argv
        self._open = wizard.webbrowser.open
        self._copy = wizard._copy_path
        self._bundle = wizard.refresh_worker_bundle
        self._game = wizard._ensure_game_running
        wizard.refresh_worker_bundle = lambda: None      # 别在自检里动真实的 web 目录
        wizard._ensure_game_running = lambda args: True  # 自检不碰设备，默认"游戏开着"

        def fake_run(argv):
            self.runs.append(list(argv))
            n = len(self.runs)
            make_snapshot(self.outdir, snap=f'2026100{n}-12000{n}', nick='新角色')

        wizard._run_argv = fake_run
        wizard.webbrowser.open = lambda uri: (self.opened.append(uri), True)[1]
        wizard._copy_path = lambda text: (self.copied.append(text), True)[1]
        return self

    def __exit__(self, *exc):
        wizard._run_argv = self._run
        wizard.webbrowser.open = self._open
        wizard._copy_path = self._copy
        wizard.refresh_worker_bundle = self._bundle
        wizard._ensure_game_running = self._game
        return False


def make_snapshot(root, snap='20261001-174128', group='android-s12-示例角色',
                  nick='示例角色', runtime='android', server='12', mtime=None):
    d = os.path.join(root, group, snap)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'dat.db'), 'wb') as f:
        f.write(b'SQLite fake')
    with open(os.path.join(d, 'dat.json'), 'w', encoding='utf-8') as f:
        json.dump({'MainRole': {'Name': nick}, 'DBVersion': '00072274'},
                  f, ensure_ascii=False)
    with open(os.path.join(d, 'dat.meta.json'), 'w', encoding='utf-8') as f:
        json.dump({'nick': nick, 'runtime': runtime, 'server': server,
                   'field_count': 221, 'version': '00072274'},
                  f, ensure_ascii=False)
    if mtime is not None:
        os.utime(d, (mtime, mtime))
    return d


def new_outdir(seed=0):
    """新建一个临时输出目录；seed>0 时先塞几条"历史存档"进去。"""
    d = tempfile.mkdtemp(prefix='yyjh_wiz_')
    _TMP.append(d)
    for i in range(seed):
        make_snapshot(d, snap=f'2026090{i + 1}-09000{i}', group=f'ios-s2{i}-角色{i}',
                      nick=f'角色{i}', runtime='ios', server=f'2{i}',
                      mtime=1000 + i)
    return d


def args_for(outdir):
    return cli.build_parser().parse_args(['start', '--outdir', outdir])


def drive(answers, args):
    """把 answers 依次当作 input() 喂进去，返回 (返回值, 控制台文本)。"""
    buffered = io.StringIO()
    mark = len(_LOG.getvalue())
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin = io.StringIO('\n'.join(str(a) for a in answers) + '\n')
    sys.stdout = buffered
    try:
        rc = wizard._wizard(args)
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    return rc, buffered.getvalue() + _LOG.getvalue()[mark:]


def drive_raw(answers, args):
    """跟 drive 一样，但允许向导向上抛异常；返回 (raised, 控制台文本)。"""
    buffered = io.StringIO()
    mark = len(_LOG.getvalue())
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin = io.StringIO('\n'.join(str(a) for a in answers) + '\n')
    sys.stdout = buffered
    raised = False
    try:
        wizard._wizard(args)
    except ui.UserError:
        raised = True
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    return raised, buffered.getvalue() + _LOG.getvalue()[mark:]


# ───────────────────────── 用例 ─────────────────────────
def case_pure():
    print('— 纯函数 —')
    check('时间戳格式化', wizard._fmt_stamp('20261001-174128') == '2026-10-01 17:41',
          wizard._fmt_stamp('20261001-174128'))
    check('时间戳撞名后缀保留', wizard._fmt_stamp('20261001-174128-2').endswith('-2'),
          wizard._fmt_stamp('20261001-174128-2'))

    empty = tempfile.mkdtemp(prefix='yyjh_empty_')
    _TMP.append(empty)
    check('空目录返回空列表', wizard.list_snapshots(empty) == [])
    check('网页版入口存在', os.path.exists(wizard.web_index()), wizard.web_index())
    check('网页版能转成 file:// URI', (wizard._web_uri() or '').startswith('file://'),
          wizard._web_uri())


def case_scan():
    print('— 历史存档扫描 —')
    out = new_outdir(seed=2)
    rows = wizard.list_snapshots(out)
    check('扫到 2 条', len(rows) == 2, len(rows))
    check('按时间倒序（最新在前）', rows[0]['snap'] == '20260902-090001',
          [r['snap'] for r in rows])
    check('能读到 nick 与字段数', '角色1' in wizard.describe(rows[0])
          and '221 字段' in wizard.describe(rows[0]), wizard.describe(rows[0]))
    check('描述含平台与区服', 'ios' in wizard.describe(rows[0])
          and 's21' in wizard.describe(rows[0]), wizard.describe(rows[0]))

    cache = os.path.join(out, '_cache', '20261003-120003')
    os.makedirs(cache, exist_ok=True)
    with open(os.path.join(cache, 'dat.db'), 'wb') as f:
        f.write(b'x')
    check('忽略 _cache 这类中间目录', len(wizard.list_snapshots(out)) == 2,
          len(wizard.list_snapshots(out)))


def case_use_existing():
    print('— 选中已有存档 -> 直接开浏览器 —')
    out = new_outdir(seed=2)
    with Stub(out) as s:
        rc, txt = drive(['1'], args_for(out))
    check('返回 0', rc == 0, rc)
    check('调用了浏览器', len(s.opened) == 1, s.opened)
    check('打开的是 web/ 下的页面',
          bool(s.opened) and '/web/' in s.opened[0], s.opened)
    check('没有跑任何子命令（不重复导入）', s.runs == [], s.runs)
    check('剪贴板里是存档目录（不是文件路径）',
          bool(s.copied) and not s.copied[-1].endswith('.db'), s.copied)


def case_local_file():
    print('— 有历史 -> 选「导入新存档」-> local -> 我有文件 —')
    out = new_outdir(seed=2)          # 列表 3 项，[3] 就是导入新存档
    with Stub(out) as s:
        rc, txt = drive(['3', '1', '1', r'C:\some\dat.db', '2'], args_for(out))
    check('返回 0', rc == 0, rc)
    check('跑的是 local + 给的路径',
          s.runs == [['local', r'C:\some\dat.db', '--outdir', out]], s.runs)
    check('选了不打开就不开浏览器', s.opened == [], s.opened)
    check('提示了下次怎么再来', 'python run.py' in txt)


def case_local_device():
    print('— 无历史 -> local -> 从模拟器拉取 —')
    out = new_outdir()
    with Stub(out) as s:
        rc, txt = drive(['1', '2', '2'], args_for(out))
    check('第一个提示就是模式选择', '请选择模式' in txt, txt[:80])
    check('跑的是不带路径的 local（= 去设备上取）',
          s.runs == [['local', '--outdir', out]], s.runs)


def case_capture():
    print('— 无历史 -> capture —')
    out = new_outdir()
    with Stub(out) as s:
        rc, txt = drive(['2', r'C:\some\x.har', '2'], args_for(out))
    check('跑的是 capture',
          s.runs == [['capture', r'C:\some\x.har', '--outdir', out]], s.runs)
    check('第一个提示是模式选择', '请选择模式' in txt)


def case_key_expired():
    """游戏更新后：导入报"密钥表过期"，向导要就地问要不要挖，挖完自动重跑。"""
    print('— 导入报"游戏更新" -> 就地挖密钥 -> 重跑 —')
    out = new_outdir()
    calls = []

    def fake_run(argv):
        calls.append(list(argv))
        if argv[0] == 'local' and len(calls) == 1:
            err = ui.UserError('游戏更新了：存档版本 00073460 的内层密钥还没收录', [])
            err.__cause__ = savefmt.InnerKeyMissing('00073460', 'android')
            raise err
        make_snapshot(out, snap='20261003-120000', group='android-s12-自检',
                      nick='自检')
        return 0

    real = wizard._run_argv
    with Stub(out) as s:
        wizard._run_argv = fake_run          # 盖掉 Stub 里的假执行器
        try:
            rc, txt = drive(['1', '2', '1', '2'], args_for(out))
        finally:
            wizard._run_argv = real
    check('返回 0（挖到之后原命令跑通了）', rc == 0, rc)
    check('把"游戏更新了"原原本本告诉用户', '游戏更新了' in txt, txt[-200:])
    check('问了要不要现在挖', '要现在挖新版本密钥吗' in txt)
    check('第二条跑的是 key crack', bool(calls) and calls[0][:2] != ['key', 'crack']
          and len(calls) > 1 and calls[1][:2] == ['key', 'crack'], calls)
    check('挖完自动重跑原命令', len(calls) > 2 and calls[2][0] == 'local'
          and calls[2] == calls[0], calls)
    check('没有给 key crack 塞 --outdir（它不认）',
          bool(calls) and len(calls) > 1 and '--outdir' not in calls[1], calls)
    check('挖完没再被拦一次（说明真的去跑原命令了）', s.opened == [], s.opened)


def case_key_target():
    """挖密钥必须跟着"出问题的那一份"走，不能让用户再选一次。"""
    print('— 挖密钥要跟着出问题的存档/抓包走 —')

    def drive_fail(out, answers, fail_cmd):
        calls = []
        state = {'failed': False}

        def fake_run(argv):
            calls.append(list(argv))
            if argv and argv[0] == fail_cmd and not state['failed']:
                state['failed'] = True          # 只失败一次：挖完重跑就该成功
                raise ui.UserError(
                    '游戏更新了：存档版本 00073460 的内层密钥还没收录', [])
            return 0

        real = wizard._run_argv
        with Stub(out) as s:
            wizard._run_argv = fake_run
            try:
                drive(answers, args_for(out))
            finally:
                wizard._run_argv = real
        return calls

    # ① capture <har> -> crack --har（抓包不需要外层密钥）
    calls = drive_fail(new_outdir(), ['2', r'C:\some\x.har', '1', '2'], 'capture')
    check('capture 失败 -> crack 拿到 --har',
          len(calls) > 1 and calls[1][:3] == ['key', 'crack', '--har'], calls)
    check('capture 的 --har 就是那条抓包',
          len(calls) > 1 and calls[1][3] == r'C:\some\x.har', calls)

    # ② local <文件> -> crack --db <同一个文件>
    calls = drive_fail(new_outdir(), ['1', '1', r'C:\some\dat.db', '1', '2'],
                       'local')
    check('local 失败 -> crack 拿到 --db',
          len(calls) > 1 and calls[1][:3] == ['key', 'crack', '--db'], calls)
    check('--db 就是刚才那份存档',
          len(calls) > 1 and calls[1][3] == r'C:\some\dat.db', calls)

    # ③ 设备上的存档（local 不带路径）：路径挂在异常上，照样不用重选
    out = new_outdir()
    calls = []
    state = {'failed': False}

    def fake_dev(argv):
        calls.append(list(argv))
        if argv and argv[0] == 'local' and not state['failed']:
            state['failed'] = True
            e = ui.UserError('游戏更新了：存档版本 00073460 的内层密钥还没收录', [])
            e.source = '127.0.0.1:7555:/data/data/x/server39/dat.db'
            raise e
        return 0

    real = wizard._run_argv
    with Stub(out) as s:
        wizard._run_argv = fake_dev
        try:
            drive(['1', '2', '1', '2'], args_for(out))
        finally:
            wizard._run_argv = real
    check('设备存档失败 -> crack 拿到设备路径（不再弹选档菜单）',
          len(calls) > 1 and calls[1][:3] ==
          ['key', 'crack', '--db'] and ':7555:/data/' in calls[1][3], calls)


def case_fail_no_open():
    """导入没成功就不该再问"要不要打开网页版"（开了也是空的）。"""
    print('— 导入失败 -> 不问打开网页版 —')

    # ① 报"游戏更新了"，但用户选了"算了，先退出"
    out = new_outdir()

    def fake_expired(argv):
        raise ui.UserError('游戏更新了：存档版本 00073460 的内层密钥还没收录', [])

    real = wizard._run_argv
    with Stub(out) as s:
        wizard._run_argv = fake_expired
        try:
            rc, txt = drive(['1', '2', '2'], args_for(out))
        finally:
            wizard._run_argv = real
    check('放弃挖之后返回非 0', rc not in (0, None), rc)
    check('没弹"要不要打开网页版"', '要打开网页版' not in txt, txt[-200:])
    check('也没真去开浏览器', s.opened == [], s.opened)
    check('明说了这次没成功', '没导出成功' in txt, txt[-200:])

    # ② 其它错误（不是密钥过期）：原样抛给上层，由 start 问"要重来吗"
    out2 = new_outdir()

    def fake_other(argv):
        raise ui.UserError('连不上设备，没拿到存档', ['先开模拟器'])

    with Stub(out2) as s2:
        wizard._run_argv = fake_other
        try:
            raised, txt2 = drive_raw(['1', '2'], args_for(out2))
        finally:
            wizard._run_argv = real
    check('非密钥类错误原样上抛（交给 start 问"要重来吗"）', raised)
    check('这种情况也没开浏览器', s2.opened == [], s2.opened)
    check('也没弹打开网页版', '要打开网页版' not in txt2, txt2[-200:])


def case_key_retry():
    """挖失败时不能把用户甩回开头重跑 —— 要就地给"再挖一次"的选项。"""
    print('— 挖失败 -> 就地重挖，不回到开头 —')
    out = new_outdir()
    calls = []
    state = {'crack': 0}

    def fake_run(argv):
        calls.append(list(argv))
        if argv[0] == 'local' and not state.get('import_failed'):
            state['import_failed'] = True             # 只失败一次：挖到后重跑就该成功
            err = ui.UserError('游戏更新了：存档版本 00073460 的内层密钥还没收录', [])
            err.__cause__ = savefmt.InnerKeyMissing('00073460', 'android')
            raise err
        if argv[0] == 'key':
            state['crack'] += 1
            return 1 if state['crack'] == 1 else 0      # 第一次挖失败，第二次成功
        make_snapshot(out, snap='20261003-130000', group='android-s12-自检',
                      nick='自检')
        return 0

    real = wizard._run_argv
    with Stub(out) as s:
        wizard._run_argv = fake_run
        try:
            # local -> 设备 -> 要挖吗(1) -> 挖失败 -> 再挖一次(1) -> 成功 -> 重跑 -> 不打开(2)
            rc, txt = drive(['1', '2', '1', '1', '2'], args_for(out))
        finally:
            wizard._run_argv = real
    checks = [c for c in calls if c and c[0] == 'key']
    check('挖失败后没有回到开头（没有第二次进入模式选择）',
          txt.count('请选择模式') == 1, txt.count('请选择模式'))
    check('就地重挖了第二次', len(checks) == 2, calls)
    check('两次挖的目标一样（还是同一份存档）',
          len(checks) == 2 and checks[0] == checks[1], checks)
    check('重挖成功后返回 0 并把原命令跑通', rc == 0, rc)
    check('挖失败时提示了"再挖一次"', '再挖一次' in txt, txt[-300:])


def case_rc_of():
    """成功路径返回的是 SaveResult（**不是 0**）—— 别把它当成失败。

    老代码 `if rc:` 直接把 SaveResult 当真值，于是明明 ✅ 完成，
    却提示"这次没导出成功，就不开网页版了"。
    """
    print('— 成功返回 SaveResult 不能被当成失败 —')
    from yyjh import model
    check('SaveResult -> 0', wizard._rc_of(model.SaveResult()) == 0)
    check('整数退出码原样传递',
          wizard._rc_of(2) == 2 and wizard._rc_of(0) == 0)
    check('None -> 0', wizard._rc_of(None) == 0)

    out = new_outdir()

    def fake_run(argv):
        make_snapshot(out, snap='20261003-150000', group='ios-s9-自检3', nick='自检3')
        return model.SaveResult()          # 成功路径真实返回的就是它

    real = wizard._run_argv
    with Stub(out):
        wizard._run_argv = fake_run
        try:
            rc, txt = drive(['2', '"x.har"', '2'], args_for(out))
        finally:
            wizard._run_argv = real
    check('成功时不再提示"没导出成功"', '没导出成功' not in txt, txt[-300:])
    check('成功时返回 0', rc == 0, rc)


def case_key_raises():
    """挖的时候**抛异常**也不能冒泡回开头。

    用户实测：模拟器上没开游戏 → 挖的第一步就报错 → 异常冒泡到最外层
    → 问"要回到开头重来吗" → 又得从选 capture 开始重来一遍。
    """
    print('— 挖报错（不是返回码）-> 也留在原地 —')
    out = new_outdir()
    calls = []
    state = {'crack': 0, 'import_failed': False}

    def fake_run(argv):
        calls.append(list(argv))
        if argv[0] == 'local' and not state['import_failed']:
            state['import_failed'] = True
            err = ui.UserError('游戏更新了：存档版本 00073460 的内层密钥还没收录', [])
            err.__cause__ = savefmt.InnerKeyMissing('00073460', 'android')
            raise err
        if argv[0] == 'key':
            state['crack'] += 1
            if state['crack'] == 1:
                # 第一次挖：抛错（比如"没挖到"，keyhunt.run 就是这么报的）
                raise ui.UserError('没挖到版本 00073460 的密钥',
                                   ['游戏要开着，而且要让它碰到过这一版存档'])
            return 0
        make_snapshot(out, snap='20261003-140000', group='android-s12-自检2',
                      nick='自检2')
        return 0

    real = wizard._run_argv
    with Stub(out):
        wizard._run_argv = fake_run
        try:
            rc, txt = drive(['1', '2', '1', '1', '2'], args_for(out))
        finally:
            wizard._run_argv = real
    checks = [c for c in calls if c and c[0] == 'key']
    check('挖报错没有冒泡回开头（没再问一次模式）',
          txt.count('请选择模式') == 1, txt.count('请选择模式'))
    check('没有问"要回到开头重来吗"', '回到开头重来' not in txt, txt[-300:])
    check('挖报错后仍就地提示"再挖一次"', '再挖一次' in txt, txt[-300:])
    check('报错后还能继续挖成功并跑通', rc == 0, rc)
    check('一共挖了两次（都还在原地）', len(checks) == 2, calls)


def case_game_not_running():
    """游戏没开着时：先说清楚，别白扫几十秒再失败。"""
    print('— 游戏没开 -> 先说清楚，不白扫 —')
    out = new_outdir()
    calls = []

    def fake_run(argv):
        calls.append(list(argv))
        err = ui.UserError('游戏更新了：存档版本 00073460 的内层密钥还没收录', [])
        err.__cause__ = savefmt.InnerKeyMissing('00073460', 'android')
        raise err

    real = wizard._run_argv
    with Stub(out):
        wizard._run_argv = fake_run
        wizard._ensure_game_running = lambda args: False   # 游戏没开 + 用户选了"算了"
        try:
            rc, txt = drive(['1', '2', '1'], args_for(out))
        finally:
            wizard._run_argv = real
    check('游戏没开时直接给结论，不去硬扫内存',
          rc == 2 and not [c for c in calls if c and c[0] == 'key'], (rc, calls))
    check('没有回到开头重来', txt.count('请选择模式') == 1, txt.count('请选择模式'))


def case_quoted_path():
    print('— 把文件拖进窗口会带引号 —')
    out = new_outdir()
    with Stub(out) as s:
        drive(['1', '1', '"C:\\a b\\dat.db"', '2'], args_for(out))
    check('外层引号被去掉',
          bool(s.runs) and s.runs[0][1] == r'C:\a b\dat.db', s.runs)


def case_forward():
    print('— start 上的参数转给底层 —')
    out = new_outdir()
    args = cli.build_parser().parse_args(
        ['start', '--outdir', out, '--pkg', 'huawei', '--report', '--runtime', 'ios'])
    with Stub(out) as s:
        drive(['1', '2', '2'], args)
    got = s.runs[0] if s.runs else []
    check('--pkg / --report / --runtime 都转过去了',
          '--pkg' in got and 'huawei' in got and '--report' in got
          and '--runtime' in got and 'ios' in got, got)


def case_autoload():
    print('— 自动载入页（不改动 web 里任何既有文件）—')
    import base64
    import zlib

    compact = wizard.pack_save({'A': [1, 2, 3], 'Name': '示例角色'})
    check('存的是压缩形式（web 端会走 inflate 分支）', not compact.startswith('{'))
    check('压缩包能被 zlib 原样解回',
          json.loads(zlib.decompress(base64.b64decode(compact)))
          == {'A': [1, 2, 3], 'Name': '示例角色'})

    out = new_outdir()
    make_snapshot(out)
    page = wizard.write_autoload(wizard.list_snapshots(out)[0])
    check('生成了入口页', bool(page) and os.path.exists(page), page)

    with io.open(page, encoding='utf-8') as f:
        got = f.read()
    with io.open(wizard.web_index(), encoding='utf-8') as f:
        orig = f.read()

    marker = '<script>(function(){try{localStorage.setItem('
    i = got.index(marker)
    j = got.index('</script>', i) + len('</script>')
    check('除了插入的那段脚本，原有内容一字未改',
          got[:i] + got[j:] == orig.replace('</head>', '\n</head>', 1))
    check('插入的脚本里带了存档与 meta 两个键',
          'yyjh_share_save_v1' in got[i:j] and 'yyjh_share_save_meta_v1' in got[i:j])
    check('入口页和 index.html 同在 web/ 下（相对路径才成立）',
          os.path.dirname(page) == os.path.dirname(wizard.web_index()))

    os.remove(page)


def case_open_autoload():
    print('— 选已有存档时开的是自动载入页 —')
    out = new_outdir(seed=1)
    with Stub(out) as s:
        rc, txt = drive(['1'], args_for(out))
    check('浏览器打开的是自动载入页',
          bool(s.opened) and '_autoload_last.html' in s.opened[0], s.opened)
    check('剪贴板里是目录而不是文件路径',
          bool(s.copied) and not s.copied[-1].endswith('.db'), s.copied)
    page = os.path.join(os.path.dirname(wizard.web_index()), wizard.AUTOLOAD_NAME)
    if os.path.exists(page):
        os.remove(page)


def case_cancel():
    print('— 敲 q 退出 —')
    out = new_outdir(seed=1)
    raised = False
    with Stub(out):
        try:
            drive(['q'], args_for(out))
        except ui.Cancelled:
            raised = True
    check('q 抛 Cancelled（不是错误，不打印堆栈）', raised)


def case_bad_input():
    print('— 乱输编号会退回默认项 —')
    out = new_outdir(seed=2)
    with Stub(out) as s:
        rc, txt = drive(['999', '1', 'q'], args_for(out))
    check('无效编号用默认（= 最新那条）-> 开浏览器', len(s.opened) == 1, s.opened)


def main():
    h = logging.StreamHandler(_LOG)
    h.setFormatter(logging.Formatter('%(message)s'))
    ui.LOG.addHandler(h)
    ui.LOG.setLevel(logging.DEBUG)
    ui.LOG.propagate = False

    # open_web() 会在 web/ 里生成 _autoload_last.html —— 自检不该留痕，先备份后还原
    autoload = os.path.join(os.path.dirname(wizard.web_index()),
                            wizard.AUTOLOAD_NAME)
    backup = autoload + '.checkbak'
    had_autoload = os.path.exists(autoload)
    if had_autoload:
        shutil.copy2(autoload, backup)

    try:
        case_pure()
        case_scan()
        case_use_existing()
        case_local_file()
        case_local_device()
        case_capture()
        case_key_expired()
        case_key_target()
        case_key_retry()
        case_key_raises()
        case_game_not_running()
        case_rc_of()
        case_fail_no_open()
        case_quoted_path()
        case_forward()
        case_autoload()
        case_open_autoload()
        case_cancel()
        case_bad_input()
    finally:
        if had_autoload:
            shutil.move(backup, autoload)
        elif os.path.exists(autoload):
            os.remove(autoload)
        for d in _TMP:
            shutil.rmtree(d, ignore_errors=True)

    check('没在 web/ 里留下自动载入页（自检无副作用）',
          os.path.exists(autoload) == had_autoload)

    print()
    print(f'通过 {len(_PASS)} / 失败 {len(_FAIL)}')
    for n in _FAIL:
        print(f'  失败: {n}')
    return 1 if _FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
