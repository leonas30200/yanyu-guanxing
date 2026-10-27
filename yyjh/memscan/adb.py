# -*- coding: utf-8 -*-
"""adb 薄封装 —— 兼容各种模拟器（MuMu / 雷电 / 夜神 / 蓝叠 / 真机）。

adb 在哪（`find_adb()`）
    1. 命令行 --adb 指定的
    2. 环境变量 YYJH_ADB
    3. keys.local.json 里记住的（上次用过的）
    4. 系统 PATH（shutil.which）
    5. 19 个常见安装位置（各模拟器自带 + platform-tools + Android SDK）
    6. 都找不到 → 在 C:\ D:\ 桌面里浅层搜 platform-tools\adb.exe

设备选哪台（`Adb.rank()` / `memscan.devices.pick_game_device()`）
    1. --serial / YYJH_SERIAL 指定的
    2. 否则**自动挑装了游戏的那台**
    3. 都没游戏 → 已知模拟器端口 > emulator-* > 真机
    （所以雷电/夜神/蓝叠这种 serial 不一样的模拟器不用手动配）

权限
    读游戏进程内存需要 root。MuMu 默认就是 root；雷电/夜神要在设置里开 root。
    `check_mem_access()` 会直接试读一次并给出明确结论。

坑（都踩过）
    * MuMu 的 shell 默认就是 root，命令**不要**带 `su -c`
    * 设备端是 32 位有符号算术，`$((0xb2180000/4096))` 会溢出成负数
      → dd 的 skip/count 一律在 PC 端算好传十进制
    * 别在双引号里塞 `$` 和 `/dev/null`，PowerShell 会抢先解释
    * 纯字符串不能以反斜杠结尾（`r'C:\\'` 是语法错误），写盘根目录用 `'C:\\\\'`
"""
import glob
import os
import shutil
import subprocess
import time

from ..config import keytables, paths


class AdbError(RuntimeError):
    pass


# ───────────────────── adb 自动发现 ─────────────────────
# 依次找这些地方（%VAR% 会展开）
_ADB_CANDIDATES = [
    r'%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe',
    r'%USERPROFILE%\AppData\Local\Android\Sdk\platform-tools\adb.exe',
    r'C:\Android\Sdk\platform-tools\adb.exe',
    r'C:\platform-tools\adb.exe',
    r'D:\platform-tools\adb.exe',
    r'D:\Android\Sdk\platform-tools\adb.exe',
    # 模拟器自带的 adb
    r'C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe',
    r'C:\Program Files\Netease\MuMu Player 12\shell\adb.exe',
    r'C:\Program Files (x86)\Netease\MuMuPlayer-12.0\shell\adb.exe',
    r'D:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe',
    r'C:\ChangZhi\LDPlayer\adb.exe',
    r'D:\ChangZhi\LDPlayer\adb.exe',
    r'C:\ChangZhi9\LDPlayer9\adb.exe',
    r'C:\Program Files (x86)\Nox\bin\adb.exe',
    r'D:\Program Files (x86)\Nox\bin\adb.exe',
    r'C:\Program Files\BlueStacks_nxt\HD-Adb.exe',
]

# 太大就跳过（找 adb 时做有界的浅层搜索）
_ADB_SEARCH_ROOTS = ['C:\\', 'D:\\', r'%USERPROFILE%', r'%LOCALAPPDATA%',
                     r'C:\Program Files', r'C:\Program Files (x86)',
                     r'%USERPROFILE%\Desktop', r'D:\Program Files']
_ADB_SEARCH_DEPTH = 4


def _expand(p):
    return os.path.expandvars(os.path.expanduser(p))


def _search_platform_tools(on_progress=None):
    """在常见目录里浅层找 platform-tools\\adb.exe。"""
    seen = set()
    for root in _ADB_SEARCH_ROOTS:
        root = _expand(root)
        if not os.path.isdir(root):
            continue
        pat = os.path.join(root, *(['*'] * 2), 'platform-tools', 'adb.exe')
        for p in glob.glob(pat):
            d = os.path.dirname(p)
            if d in seen:
                continue
            seen.add(d)
            yield p
    # 再深一层
    for root in _ADB_SEARCH_ROOTS[:6]:
        root = _expand(root)
        if not os.path.isdir(root):
            continue
        pat = os.path.join(root, *(['*'] * 3), 'adb.exe')
        for p in glob.glob(pat)[:200]:
            if os.path.basename(p).lower() in ('adb.exe', 'hd-adb.exe'):
                yield p


def find_adb(explicit=None, on_progress=None):
    """找一个能用的 adb。返回 (路径, 来源说明)。

    顺序: --adb > 环境变量 YYJH_ADB > keys.local.json 记过的 > PATH >
          常见安装位置 > 模拟器自带 > 浅层搜索
    """
    def ok(p):
        return bool(p) and os.path.isfile(p)

    if ok(explicit):
        return explicit, '命令行 --adb'

    env = os.environ.get('YYJH_ADB')
    if ok(env):
        return env, '环境变量 YYJH_ADB'

    saved = keytables.known_adb()
    if ok(saved):
        return saved, '上次用的（keys.local.json）'

    if ok(paths.ADB) and paths.ADB:
        return paths.ADB, '配置文件默认'

    which = shutil.which('adb') or shutil.which('adb.exe')
    if ok(which):
        return which, '系统 PATH'

    for p in _ADB_CANDIDATES:
        p = _expand(p)
        if ok(p):
            return p, '常见安装位置'

    if on_progress:
        on_progress('常见位置没找到 adb，正在浅层搜索硬盘 …')
    for p in _search_platform_tools():
        if ok(p):
            return p, '搜索找到'

    return None, '没找到'


class Adb:
    def __init__(self, exe=None, serial=None, on_progress=None, port=None):
        self.exe, self.exe_src = find_adb(exe, on_progress)
        if not self.exe:
            raise AdbError(
                ' 找不到 adb。\n'
                '  怎么办：\n'
                '    1. 用 --adb "D:\\...\\platform-tools\\adb.exe" 指定\n'
                '    2. 或者设环境变量 YYJH_ADB\n'
                '    3. 或者直接把 adb.exe 所在目录加进系统 PATH\n'
                '    （模拟器自带的 adb 也行，比如 MuMu 的 shell\\adb.exe）')
        self.serial = serial or paths.SERIAL or keytables.known_serial()
        # adb server 端口：命令行 > 环境变量 > 配置 > 默认(不加 -P)
        p = port or paths.ADB_PORT or keytables.known_adb_port()
        self.port = str(p).strip() if p else None
        self._resolved = False
        self._devs = None

    # ───────────────────── 基础 ─────────────────────
    def _base(self):
        cmds = [self.exe]
        if self.port:
            cmds += ['-P', self.port]
        if self.serial:
            cmds += ['-s', self.serial]
        return cmds

    def _run(self, args, timeout=300, binary=False):
        r = subprocess.run(self._base() + args, capture_output=True, timeout=timeout)
        if binary:
            return r.returncode, r.stdout, r.stderr
        return (r.returncode,
                r.stdout.decode('utf-8', 'replace'),
                r.stderr.decode('utf-8', 'replace'))

    def sh(self, cmd, timeout=300):
        return self._run(['shell', cmd], timeout=timeout)

    def stream(self, cmd, timeout=600, on_line=None, encoding='utf-8'):
        """流式跑 shell 命令：**每读到一行就回一次 on_line**。

        为什么需要它：`sh()` 是把命令跑完才一次性拿全部输出 —— 像深挖那样
        "一次 adb 调用跑 8 片内存"，进度条就只能等整批回来才动一下（半分钟）。
        设备端每跑完一片 echo 一个标记，这里逐行读就能"跑完一片动一格"。

        返回 (退出码, 全部输出)。`on_line` 抛异常不影响主流程。
        """
        p = subprocess.Popen(self._base() + ['shell', cmd],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL,
                             bufsize=0)
        buf = []
        t0 = time.time()
        rc = None
        try:
            for raw in iter(p.stdout.readline, b''):
                ln = raw.decode(encoding, 'replace').rstrip('\r\n')
                buf.append(ln)
                if on_line is not None:
                    try:
                        on_line(ln)
                    except Exception:                      # noqa: BLE001
                        pass
                if timeout and time.time() - t0 > timeout:
                    p.kill()
                    break
        except Exception:                                  # noqa: BLE001
            pass
        finally:
            try:
                p.stdout.close()
            except Exception:                              # noqa: BLE001
                pass
            try:
                rc = p.wait(timeout=10)
            except Exception:                              # noqa: BLE001
                p.kill()
                rc = -1
        return rc, '\n'.join(buf)

    def out(self, cmd, timeout=300):
        rc, o, e = self.sh(cmd, timeout)
        if rc != 0:
            raise AdbError(f'命令失败({rc}): {cmd}\n{e.strip()[:300]}')
        return o

    def push(self, local, remote):
        self._run(['push', local, remote])

    def pull(self, remote, local):
        os.makedirs(os.path.dirname(os.path.abspath(local)), exist_ok=True)
        rc, o, e = self._run(['pull', remote, local], timeout=1800)
        if rc != 0:
            raise AdbError(f'pull 失败: {remote} -> {local}\n{e.strip()[:300]}')

    # ───────────────────── 设备发现 ─────────────────────
    def devices(self, _retry=1):
        if self._devs is None:
            rc, o, _ = self._run(['devices'], timeout=30)
            devs = [ln.split()[0] for ln in o.splitlines()[1:]
                    if ln.strip() and 'device' in ln.split()[1:2]]
            # 刚连上（或 daemon 刚重启）那一次 adb 会先报 offline，
            # 第二次才变成 device。空结果别急着下结论，歇一下再问一次。
            if not devs and _retry:
                time.sleep(1.5)
                return self.devices(_retry=_retry - 1)
            self._devs = devs
        return self._devs

    def refresh(self):
        self._devs = None
        self._resolved = False

    def online(self):
        """确保选到一个可用设备。返回 bool。"""
        if self._resolved:
            return bool(self.serial)
        devs = self.devices()
        if self.serial in devs:
            self._resolved = True
            return True
        ranked = self.rank(devs)
        self.serial = ranked[0] if ranked else None
        self._resolved = True
        return bool(self.serial)

    # 常见模拟器的 serial（多设备时优先选这些）
    KNOWN_SERIALS = ('127.0.0.1:7555',       # MuMu
                     'emulator-5554',        # 通用 AVD / 蓝叠
                     'emulator-5558',        # 通用 AVD
                     '127.0.0.1:5555',       # 雷电 / 蓝叠
                     '127.0.0.1:62001',      # 夜神
                     '127.0.0.1:62025',      # 夜神
                     '127.0.0.1:21503',      # MuMu 旧版
                     )

    @classmethod
    def rank(cls, devs):
        """给设备排个序：已知模拟器端口 > emulator-* > 其它（真机）。"""
        known = [d for d in devs if d in cls.KNOWN_SERIALS]
        emu = [d for d in devs if d.startswith('emulator-') and d not in known]
        rest = [d for d in devs if d not in known and d not in emu]
        return known + emu + rest

    def device_list_text(self):
        rc, o, _ = self._run(['devices'], timeout=30)
        return o.strip() or '（没有设备）'

    def model(self):
        try:
            return self.out('getprop ro.product.model').strip()
        except Exception:
            return '?'

    def is_root(self):
        try:
            return self.out('id -u').strip() == '0'
        except Exception:
            return False

    # ───────────────────── 进程 / 包 ─────────────────────
    def pid_of(self, pkg):
        rc, o, _ = self.sh(f'pidof {pkg}', timeout=30)
        o = o.strip()
        return o.split()[0] if o else None

    def ps(self, pattern=None):
        rc, o, _ = self.sh('ps -A -o PID,NAME', timeout=60)
        rows = []
        for ln in o.splitlines()[1:]:
            parts = ln.split()
            if len(parts) >= 2:
                rows.append((parts[0], parts[1]))
        if pattern:
            rows = [r for r in rows if pattern in r[1]]
        return rows

    def packages(self):
        """设备上装的所有包名。"""
        rc, o, _ = self.sh('pm list packages 2>/dev/null', timeout=120)
        return [ln.strip()[len('package:'):] for ln in o.splitlines()
                if ln.strip().startswith('package:')]

    # ───────────────────── 内存可读性 ─────────────────────
    def check_mem_access(self, pid):
        """直接试读一次目标进程内存。返回 (ok, 说明)。"""
        if not self.online():
            return False, '没有可用设备'
        if not pid:
            return False, '目标进程不存在'
        try:
            rc, o, _ = self.sh(
                f'dd if=/proc/{pid}/mem bs=4096 skip=0 count=1 '
                f'of=/sdcard/_yyjh_probe.bin 2>&1; '
                f'ls -l /sdcard/_yyjh_probe.bin 2>/dev/null; '
                f'rm -f /sdcard/_yyjh_probe.bin', timeout=60)
        except Exception as e:
            return False, f'试读失败：{e}'
        if 'Permission denied' in o or 'Operation not permitted' in o:
            return False, ('没有 root 权限，读不了进程内存。'
                           'MuMu 默认带 root；雷电/夜神需要在设置里打开 root 开关')
        if 'No such file' in o or 'not found' in o:
            return False, '进程不存在（游戏没在运行？）'
        name = self.pid_name(pid)
        return True, f'可以读内存（{name}）'

    def pid_name(self, pid):
        try:
            return self.out(f'cat /proc/{pid}/cmdline 2>/dev/null '
                            f'| tr "\\0" " "').strip() or f'pid {pid}'
        except Exception:
            return f'pid {pid}'

    # ───────────────────── 文件 ─────────────────────
    def cat(self, remote, binary=False):
        if binary:
            rc, out, err = self._run(['exec-out', f'cat {remote}'], timeout=600,
                                     binary=True)
            if rc != 0:
                raise AdbError(err.decode('utf-8', 'replace')[:300])
            return out
        return self.out(f'cat {remote}')

    def exec_out(self, cmd, timeout=600):
        """在设备上跑命令，**原样收二进制 stdout**（exec-out，不过 sdcard）。

        读 /proc/<pid>/mem 这种大块数据时，走 sdcard 中转要多一次写盘 + 一次 pull，
        实测慢好几倍 —— 内存扫描的总耗时几乎全耗在这上面。
        """
        rc, out, err = self._run(['exec-out', cmd], timeout=timeout, binary=True)
        if rc != 0:
            raise AdbError(err.decode('utf-8', 'replace')[:300]
                           or f'exec-out 失败({rc})')
        return out

    def ls(self, pattern):
        rc, o, _ = self.sh(f'ls -d {pattern} 2>/dev/null', timeout=60)
        return [ln.strip() for ln in o.splitlines() if ln.strip()]

    def rm(self, remote):
        self.sh(f'rm -f {remote}', timeout=60)
