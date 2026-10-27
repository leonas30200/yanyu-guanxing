# -*- coding: utf-8 -*-
"""路径解析：定位工作区、数据目录、输出目录、adb。

支持环境变量覆盖（方便换机器）:
    YYJH_WORKSPACE   工作区根目录
    YYJH_ADB         adb 可执行文件
    YYJH_SERIAL      设备 serial
"""
import os
import re

# tool_1.1/yyjh/config/paths.py
_THIS = os.path.abspath(__file__)
YYJH_PKG = os.path.dirname(os.path.dirname(_THIS))      # .../tool_1.1/yyjh
TOOL_DIR = os.path.dirname(YYJH_PKG)                    # .../tool_1.1
WORKSPACE = os.environ.get('YYJH_WORKSPACE') or os.path.dirname(TOOL_DIR)

DATA_DIR = os.path.join(WORKSPACE, 'data')
OUT_DIR = os.path.join(TOOL_DIR, 'output')     # 所有生成物都落在这里
# 中间产物（内存候选缓存之类）不乱丢，单独放
CACHE_DIR = os.path.join(OUT_DIR, '_cache')
DOCS_DIR = os.path.join(WORKSPACE, 'docs')

# 用户自定义密钥覆盖文件（可选），格式见 keytables.py
#
# 拆成两个文件的理由（重要）：
#   keys.json       —— 内层密钥表。**可以分享/提交/从别人那拉一份覆盖**，换机器照样用。
#   keys.local.json —— 本机信息（adb 路径/设备/包名）+ 外层密钥（跟账号绑），
#                      属于"不该给别人"的东西，已加进 .gitignore。
KEYS_FILE = os.path.join(TOOL_DIR, 'keys.json')
LOCAL_FILE = os.path.join(TOOL_DIR, 'keys.local.json')
USER_KEYS = KEYS_FILE                 # 兼容旧名字
# 早期把两样东西混在一个文件里，第一次运行会自动拆开（原文件改名保留）
LEGACY_KEYS = os.path.join(TOOL_DIR, 'keys.user.json')

# 设备相关：这里是**可选的显式覆盖**，真正的自动发现在 memscan.adb.find_adb()
# （会依次看 --adb / YYJH_ADB / keys.local.json / PATH / 常见安装位置 / 模拟器自带）
ADB = os.environ.get('YYJH_ADB') or ''
SERIAL = os.environ.get('YYJH_SERIAL') or ''
# adb server 端口。空 = 用默认的 5037。
# 有些机器上 5037 上的 daemon 会不停被别的 adb 踢掉（设备一直 offline），
# 这时另起一个常驻 server（adb -P 5039 start-server）并把端口写在这儿。
ADB_PORT = os.environ.get('YYJH_ADB_PORT') or ''
DEVICE_TMP = '/sdcard/_yyjh.bin'

# 设备上存档的位置模板（`{pkg}` 会被换包名）。
# 前两个是正常位置，后两个是部分模拟器的目录映射。
REMOTE_SAVE_ROOTS = (
    '/data/data/{pkg}/files',
    '/data/user/0/{pkg}/files',
    '/sdcard/Android/data/{pkg}/files',
    '/mnt/shared/MuMuShared/{pkg}/files',
)
# 存档文件的 glob（相对上面的 root）
REMOTE_SAVE_GLOB = '*/server*/dat.db'


def canon_remote(p):
    """把 `/data/user/0/<pkg>/…` 归一成 `/data/data/<pkg>/…`。

    两者在设备上是**同一个文件**（前者是后者的软链），不归一的话
    候选列表里会出现两份看起来一模一样的存档。
    """
    if not p:
        return p
    return p.replace('/data/user/0/', '/data/data/')


def ensure_dir(p):
    os.makedirs(p, exist_ok=True)
    return p


def resolve(p, base=None):
    """相对路径解析。

    优先级: 当前工作目录 -> base（默认工作区根）。
    这样在 tool_1.1 目录里敲 `..\\data\\x.har` 和在别处敲 `data\\x.har` 都对。
    """
    if not p:
        return p
    p = os.path.expanduser(p)
    if os.path.isabs(p):
        return p
    if base is None and os.path.exists(p):
        return os.path.abspath(p)
    return os.path.normpath(os.path.join(base or WORKSPACE, p))


def resolve_out(p):
    """输出路径解析：**一律相对当前工作目录**（不想 input 那样去猜工作区）。

    输入用 resolve()（会尝试已有文件），输出用这个，避免
    `--outdir out\\x` 被解析到工作区根而不是当前目录。
    """
    if not p:
        return p
    p = os.path.expanduser(p)
    if os.path.isabs(p):
        return p
    return os.path.abspath(p)


def rel(p, base=None):
    try:
        return os.path.relpath(p, base or WORKSPACE)
    except ValueError:
        return p


# ───────────────────── 设备路径识别 ─────────────────────
# `/data/data/.../dat.db` 或 `127.0.0.1:7555:/data/data/.../dat.db`
# （后者是常见的"从模拟器里的文件管理器复制出来的路径"写法）
_DEV_PATH = re.compile(r'^(?:(?P<serial>[A-Za-z0-9_.\-]+:\d{2,5}):)?(?P<path>/.*)$')


def parse_device_path(s):
    """看着像不像**设备上**的路径。是就返回 (serial|None, remote)，不是返回 None。

    注意要排除 Windows 盘符（`C:\\...`）：
        `C:\\x`       -> None（本地）
        `/data/x`     -> (None, '/data/x')
        `127.0.0.1:7555:/data/x` -> ('127.0.0.1:7555', '/data/x')
    """
    s = strip_quotes(s)
    if not s:
        return None
    if re.match(r'^[A-Za-z]:[\\/]', s):      # Windows 盘符
        return None
    m = _DEV_PATH.match(s)
    return (m.group('serial'), m.group('path')) if m else None


def strip_quotes(s):
    """去掉拖拽/复制粘贴常见的包裹引号。

    把文件拖进 cmd / PowerShell 窗口时会得到 `"C:\\a b\\dat.db"` 甚至
    `'C:\\a b\\dat.db'`，原样传给 argparse 会报"找不到文件"。
    """
    if s is None:
        return s
    s = str(s).strip()
    for q in ('"', "'"):
        if len(s) >= 2 and s[0] == q and s[-1] == q:
            s = s[1:-1].strip()
            break
    return s
