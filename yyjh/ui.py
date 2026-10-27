# -*- coding: utf-8 -*-
"""控制台输出 —— 基于标准库 `logging` + `tqdm` 进度条，不自己造轮子。

日志等级约定
    DEBUG    流程细节（只有 -v 才显示）
    INFO     正常信息（默认显示）
    WARNING  警告（不致命，继续跑）
    ERROR    错误

用法
    from .. import ui
    ui.step('外层解密', '842,384B -> 842,380B')     # DEBUG，-v 才看得到
    ui.ok('完成：示例角色')
    ui.warn('没拿到外层密钥')
    with ui.progress(total, '扫描内存', 'B') as pb:
        pb.update(scanned, extra=f'候选 {n}')
"""
import logging
import os
import sys
import time

LOG = logging.getLogger('yyjh')

# ───────────────────── 控制台编码（中文乱码根治） ─────────────────────
def setup_console():
    """Windows 控制台默认 936(GBK)，Python 输出 UTF-8 会变乱码 -> 切到 65001。

    PowerShell 5.1 的 `>` 重定向还会把日志写成 UTF-16LE，
    所以推荐用 `--log 文件` 让工具自己写 UTF-8 日志。
    """
    if os.name == 'nt':
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleOutputCP(65001)
            k.SetConsoleCP(65001)
        except Exception:
            pass
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


# ───────────────────────── 格式化 ─────────────────────────
_COLOR = False
_CODES = {'WARNING': '33', 'ERROR': '31', 'CRITICAL': '31;1'}


class _Formatter(logging.Formatter):
    def format(self, record):
        msg = record.getMessage()
        if _COLOR and record.levelname in _CODES:
            msg = f'\033[{_CODES[record.levelname]}m{msg}\033[0m'
        return msg


class _Tee:
    """把裸 print() 的输出也抄一份进日志文件（layer1 里还有不少 print）。"""

    def __init__(self, stream, fh):
        self.stream = stream
        self.fh = fh

    def write(self, data):
        try:
            self.stream.write(data)
        except Exception:
            pass
        try:
            self.fh.write(data)
            self.fh.flush()
        except Exception:
            pass
        return len(data)

    def flush(self):
        for s in (self.stream, self.fh):
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self):
        try:
            return self.stream.isatty()
        except Exception:
            return False

    def fileno(self):
        return self.stream.fileno()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def configure(verbose=False, quiet=False, logfile=None, color=None):
    """配置根日志器。可重复调用（会先清掉旧 handler）。

    日志文件策略：
      · logging 的记录走 FileHandler（带时间戳）
      · 裸 print() 走 _Tee（也进同一个文件）
      · 两者都不重复：StreamHandler 直接写原始 stdout，不经过 Tee
    """
    global _COLOR
    _COLOR = (sys.stdout.isatty() and not os.environ.get('NO_COLOR')) \
        if color is None else bool(color)

    level = logging.DEBUG if verbose else (logging.ERROR if quiet else logging.INFO)
    LOG.setLevel(logging.DEBUG)
    LOG.propagate = False
    for h in list(LOG.handlers):
        LOG.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    console_stream = sys.stdout
    fh = None
    if logfile:
        os.makedirs(os.path.dirname(os.path.abspath(logfile)), exist_ok=True)
        fh = open(logfile, 'w', encoding='utf-8', newline='\n')
        sys.stdout = _Tee(console_stream, fh)
        sys.stderr = _Tee(sys.stderr, fh)

    ch = logging.StreamHandler(console_stream)
    ch.setLevel(level)
    ch.setFormatter(_Formatter())
    LOG.addHandler(ch)

    if fh is not None:
        fhandler = logging.FileHandler(logfile, mode='a', encoding='utf-8')
        fhandler.setLevel(logging.DEBUG)      # 日志文件里永远记全
        fhandler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)-7s %(message)s', datefmt='%H:%M:%S'))
        LOG.addHandler(fhandler)
    return LOG


def is_verbose():
    return LOG.isEnabledFor(logging.DEBUG)


# ───────────────────────── 便捷函数 ─────────────────────────
def info(msg=''):
    LOG.info(msg)


def step(label, value=''):
    """流程细节：只有 -v 显示。"""
    LOG.debug(f'  · {label}: {value}')


def debug(msg):
    LOG.debug(msg)


def ok(msg):
    LOG.info(f'✅ {msg}')


def warn(msg):
    LOG.warning(f'⚠️  {msg}')


def error(msg):
    LOG.error(f'❌ {msg}')


def hint(lines):
    """打印"怎么办"。传 list/tuple 就逐条打 —— 别把整个列表 repr 出来。"""
    if isinstance(lines, (list, tuple)):
        for item in lines:
            for line in str(item).splitlines():
                LOG.error(f'   {line}')
        return
    for line in str(lines).splitlines():
        LOG.error(f'   {line}')


def title(msg):
    LOG.info(msg)


def rule(n=60):
    LOG.info('─' * n)


# ───────────────────────── 格式化工具 ─────────────────────────
def fmt_duration(sec):
    if sec is None:
        return '?'
    sec = int(sec)
    if sec < 60:
        return f'{sec} 秒'
    if sec < 3600:
        return f'{sec // 60} 分 {sec % 60:02d} 秒'
    return f'{sec // 3600} 时 {(sec % 3600) // 60:02d} 分'


def fmt_size(n):
    n = float(n)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{int(n)} B' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024


def fmt_time(epoch):
    """设备上的 epoch 秒 -> `10-01 14:11`；拿不到显示 `—`。"""
    if not epoch:
        return '—'
    try:
        return time.strftime('%m-%d %H:%M', time.localtime(int(epoch)))
    except Exception:
        return '—'


# ───────────────────────── 交互式菜单 ─────────────────────────
class Cancelled(Exception):
    """用户主动取消（菜单里敲了 q）。不是错误，不打印堆栈。"""


def is_tty():
    """stdin/stdout 都是终端 -> 可以交互。"""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def choose(title, lines, prompt='请选择: ', default=0):
    """列编号菜单让用户选。

    title : 标题行
    lines : 候选描述（一条一行，不含编号）
    返回  : 选中下标；用户敲 q -> 抛 `Cancelled`

    Ctrl+C **原样抛出** KeyboardInterrupt —— 让入口直接中止，
    而不是"猜到第一个接着跑"（那样用户以为取消了，结果文件已经生成）。
    """
    print()
    print(title)
    for i, ln in enumerate(lines, 1):
        print(f'  [{i}] {ln}')
    print(f'  （直接回车 = [{default + 1}]；q + 回车 = 退出，什么都不做）')
    try:
        ans = input(prompt).strip()
    except EOFError:                 # 输入被重定向/管道结束
        print()
        return default
    if ans.lower() in ('q', 'quit', 'exit'):
        print('  已取消')
        raise Cancelled()
    if not ans:
        return default
    if ans.isdigit() and 1 <= int(ans) <= len(lines):
        return int(ans) - 1
    print(f'  「{ans}」不是有效编号，用默认的 [{default + 1}]')
    return default


# ───────────────────────── 进度条 ─────────────────────────
try:
    from tqdm import tqdm as _tqdm
    HAS_TQDM = True
except Exception:                                     # pragma: no cover
    _tqdm = None
    HAS_TQDM = False


class _TqdmProgress:
    def __init__(self, total, desc, unit):
        self._pb = _tqdm(total=total, desc=f'  {desc}', unit=unit or 'it',
                         dynamic_ncols=True, leave=False)
        self.done = 0

    def advance(self, n=1, extra=''):
        if extra:
            self._pb.set_postfix_str(extra)
        self._pb.update(n)
        self.done += n

    def update(self, done, extra=''):
        if extra:
            self._pb.set_postfix_str(extra)
        d = done - self.done
        if d > 0:
            self._pb.update(d)
        self.done = max(self.done, done)

    @property
    def eta(self):
        return getattr(self._pb, 'eta', None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._pb.close()


class _LogProgress:
    """没装 tqdm 时的降级：每 10% 打一条 INFO。"""

    def __init__(self, total, desc, unit):
        self.total = max(1, int(total or 1))
        self.desc = desc
        self.unit = unit
        self.done = 0
        self.t0 = time.time()
        self._step = -1

    def _emit(self, extra=''):
        pct = int(self.done * 100 / self.total)
        if self.done < self.total and pct // 10 == self._step:
            return
        self._step = pct // 10
        eta = self.eta
        if self.unit == ' B':                    # 按字节走 -> 显示成人看的单位
            cur = f'{fmt_size(self.done)}/{fmt_size(self.total)}'
        else:
            cur = f'{self.done}/{self.total}{self.unit}'
        LOG.info(f'  {self.desc}: {pct}% ({cur})'
                 + (f'  剩余 ~{fmt_duration(eta)}' if eta else '')
                 + (f'  {extra}' if extra else ''))

    def advance(self, n=1, extra=''):
        self.done += n
        self._emit(extra)

    def update(self, done, extra=''):
        self.done = max(self.done, done)
        self._emit(extra)

    @property
    def eta(self):
        el = time.time() - self.t0
        rate = self.done / el if el > 0 else 0
        return (self.total - self.done) / rate if rate > 0 else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        LOG.info(f'  {self.desc}: 完成（{self.done}/{self.total}，'
                 f'耗时 {fmt_duration(time.time() - self.t0)}）')


def progress(total, desc, unit=''):
    """带进度显示的对象：有 tqdm 用 tqdm，否则降级成日志。

    支持 `advance(n)` / `update(done)` 和 `with` 语句。
    """
    total = max(1, int(total or 1))
    if HAS_TQDM:
        return _TqdmProgress(total, desc, unit)
    return _LogProgress(total, desc, unit)


# ───────────────────────── 错误 ─────────────────────────
class UserError(Exception):
    """面向使用者的错误：只印 message + 怎么办，不印堆栈。"""

    def __init__(self, message, hint=None):
        super().__init__(message)
        self.hint = hint


def report_error(exc, verbose=False):
    if isinstance(exc, UserError):
        error(str(exc))
        if exc.hint:
            hint(exc.hint)
    elif verbose:
        LOG.error('%s', exc, exc_info=True)
    else:
        error(str(exc))
        hint('加 -v 可以看到完整错误堆栈')
