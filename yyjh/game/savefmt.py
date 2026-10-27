# -*- coding: utf-8 -*-
"""存档格式 —— 两层加密，和最外层的一个标记。

初版(old)：
    content = base64( 版本号(8) + XXTEA( zlib(json), 内层密钥 ) )

新版(new)：
    content = marker(8) + base64( XXTEA( base64(版本号(8) + XXTEA(zlib(json), 内层密钥)),
                                        外层密钥 ) )
    其中 X = base64(版本号(8) + XXTEA(zlib(json), 内层密钥))  —— 就是初版的 content

抓包能直接得到 X（网络外层用的是服务器下发的 realKey），
所以 **抓包 -> 初版 dat.db 不需要重新加密任何一个字节**。

v1.8（存档工具认的那种）：
    content = base64( 版本号(8) + XXTEA( zlib(json), 平台固定密钥 ) )
    和初版**同一形状**，只是内层密钥是 `constants.V18_PLATFORM_KEYS` 里那几把
    **固定**的（不靠版本号推导）—— 见 `build_v18()` / `platform_key()`。
"""
import hashlib
import json
import re

from ..config import constants, keytables
from ..core import codecs, xxtea

OLD = 'old'
NEW = 'new'
V18 = 'v18'

_VERSION_RE = re.compile(r'^\d{8}$')


class InnerKeyMissing(ValueError):
    """外层解得开，但**这个版本的内层密钥不在表里**。

    ⚠ 为什么单独一个异常类：调用方必须能把
        「外层密钥挖错了」和「游戏更新了、新版本密钥还没收录」
    分开报。以前两者都只是 ValueError，结果"内层缺密钥"被当成
    "外层密钥不对"报出去 —— 用户会跑回去狂扫内存，方向完全错了。
    """

    def __init__(self, version, runtime, marker='', tried=0, fmt=OLD):
        super().__init__(f'版本 {version} 的内层密钥不在表里（试了 {tried} 个候选）')
        self.version = version
        self.runtime = runtime
        self.marker = marker
        self.tried = tried
        self.format = fmt


def inner_key_missing_hints(exc):
    """版本未知时给用户的"怎么办"清单。

    layer1 / layer2 共用 —— 措辞必须一致，而且**第一句就要说清是游戏更新**，
    否则用户只会觉得是自己操作错了，一遍遍重跑。
    """
    ver = getattr(exc, 'version', '?')
    return [
        f'存档版本号是 {ver}，密钥表里没有这一版 —— 多半是**游戏刚更新**，不是你操作的问题。',
        '⚠ 别反复重跑 capture / local：密钥没变，重试多少遍都是同一个错，纯浪费时间。',
        '怎么办（下面任一条成功，密钥就永久记进 keys.json，以后不用再管）:',
        '  1. 打开游戏、进一次角色（密钥只在游戏运行时存在内存里），然后一把梭：',
        '       python -m yyjh key crack',
        '       （会自动去设备上找最新存档，扫内存把新密钥挖出来并存好）',
        '  2. 第三方导出工具更新了的话，从它里面直接提表：',
        '       python -m yyjh exe tables <导出工具.exe> --save',
        f'  3. 已知密钥就手动补一条：python -m yyjh key add {ver} <16位密钥>',
        '  4. 只想解这一次：加 --inner-key <16位密钥>',
    ]


# ─────────────────────────── 识别 ───────────────────────────
def detect_format(content):
    """返回 OLD / NEW / None。"""
    if not content or len(content) < 16:
        return None
    head = content[:constants.SAVE_MARKER_LEN]
    if head in constants.SAVE_MARKER_MAP:
        return NEW
    env = codecs.b64d(content)
    if env and len(env) > constants.SAVE_VERSION_LEN:
        ver = env[:constants.SAVE_VERSION_LEN].decode('ascii', 'ignore')
        if _VERSION_RE.match(ver) and (len(env) - constants.SAVE_VERSION_LEN) % 4 == 0:
            return OLD
    return None


# ─────────────────────────── 解析 ───────────────────────────
def parse_old(content):
    """初版: 返回 (version, body)。body 是内层 XXTEA 密文。"""
    env = codecs.b64d(content)
    if env is None:
        raise ValueError('初版 content 不是合法 base64')
    n = constants.SAVE_VERSION_LEN
    version = env[:n].decode('ascii', 'replace')
    if not _VERSION_RE.match(version):
        raise ValueError(f'版本号非法: {version!r}')
    body = env[n:]
    if len(body) % 4:
        raise ValueError(f'内层密文长度未对齐: {len(body)}')
    return version, body


def parse_new(content):
    """新版: 返回 (marker, version, body)，body 仍是**外层密文**。"""
    marker = content[:constants.SAVE_MARKER_LEN]
    if marker not in constants.SAVE_MARKER_MAP:
        raise ValueError(f'未知外层标记 {marker!r}')
    raw = codecs.b64d(content[constants.SAVE_MARKER_LEN:])
    if raw is None:
        raise ValueError('外层不是合法 base64')
    return marker, raw


def unwrap_outer(content, outer_key):
    """新版: 用外层密钥解出 X（= 初版 content）。返回 base64 文本 str。"""
    _, cipher = parse_new(content)
    plain = xxtea.decrypt(cipher, outer_key)
    if plain is None:
        raise ValueError('外层 XXTEA 解密失败（外层密钥不对）')
    return plain.decode('ascii')


# ─────────────────────── 内层（核心） ───────────────────────
def try_inner(body, candidates):
    """逐个试内层密钥。返回 (data, key)。用 zlib+json 双重校验，比单靠长度字更稳。"""
    seen = set()
    for k in candidates:
        if not k or k in seen:
            continue
        seen.add(k)
        comp = xxtea.decrypt(body, k)
        if not comp:
            continue
        raw = codecs.zlib_d(comp)
        if raw is None:
            continue
        try:
            return json.loads(raw.decode('utf-8')), k
        except Exception:
            continue
    return None, None


def inner_candidates(runtime, version, explicit=None):
    """候选内层密钥的优先级顺序。

    里面包含 **v1.8 的平台固定密钥** —— 这样从存档工具/网站拿回来的
    旧档（版本号可能根本不在任何表里）也能直接被认出来。
    """
    out = []
    if explicit:
        out.append(explicit)
    remembered = keytables.lookup_inner_key(runtime, version)
    if remembered[0]:
        out.append(remembered[0])
    pk = constants.V18_PLATFORM_KEYS.get(runtime)
    if pk and pk not in out:
        out.append(pk)
    for k in keytables.candidate_inner_keys(runtime, version):
        if k not in out:
            out.append(k)
    for k in constants.V18_PLATFORM_KEYS.values():
        if k not in out:
            out.append(k)
    return out


def unwrap_old(content, runtime, explicit_key=None):
    """初版 content -> (data, meta)。"""
    version, body = parse_old(content)
    cands = inner_candidates(runtime, version, explicit_key)
    data, key = try_inner(body, cands)
    if data is None:
        raise InnerKeyMissing(version, runtime, tried=len(cands), fmt=OLD)
    return data, {'version': version, 'inner_key': key,
                  'marker': '', 'format': OLD, 'tried': len(cands)}


def unwrap_new(content, outer_key, runtime, explicit_key=None):
    """新版 content -> (data, meta)。"""
    marker, _ = parse_new(content)
    x = unwrap_outer(content, outer_key)
    version, body = parse_old(x)
    cands = inner_candidates(runtime, version, explicit_key)
    data, key = try_inner(body, cands)
    if data is None:
        raise InnerKeyMissing(version, runtime, marker=marker,
                              tried=len(cands), fmt=NEW)
    return data, {'version': version, 'inner_key': key, 'marker': marker,
                  'format': NEW, 'tried': len(cands)}


def unwrap_any(content, runtime, outer_key=None, explicit_key=None):
    """不关心新版/初版，自动处理。"""
    fmt = detect_format(content)
    if fmt == NEW:
        if not outer_key:
            raise ValueError('这是新版存档，必须提供外层密钥（每账号一把）')
        return unwrap_new(content, outer_key, runtime, explicit_key)
    if fmt == OLD:
        return unwrap_old(content, runtime, explicit_key)
    raise ValueError('无法识别的存档格式（既不是新版也不是初版）')


# ─────────────────────────── 构造 ───────────────────────────
def build_old(data, version, inner_key, level=6):
    """构造初版 content。"""
    comp = codecs.zlib_c(json.dumps(data, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8'), level)
    body = xxtea.encrypt(comp, inner_key)
    env = version.encode('ascii') + body
    return codecs.b64e(env)


# ───────────────────── v1.8（存档工具认的格式） ─────────────────────
def platform_key(runtime):
    """运行时 -> v1.8 平台固定密钥。认不出来的平台退回 android。"""
    keys = constants.V18_PLATFORM_KEYS
    return keys.get(runtime) or keys[constants.V18_DEFAULT_PLATFORM]


def build_v18(data, version, runtime, level=6):
    """构造 v1.8 content —— 和初版同形状，内层换成平台固定密钥。

    这样出来的 `.db` 直接拖进「本地存档工具」/ 解析网站就能读：
    那边只要求文件名以 `.db` 结尾、里面没有 `-vX.Y` 后缀也没有 `@8位@`。
    """
    return build_old(data, version, platform_key(runtime), level)


def build_new(data, version, inner_key, outer_key, marker='dnkemiea', level=6):
    """构造新版 content。"""
    x = build_old(data, version, inner_key, level)
    cipher = xxtea.encrypt(x.encode('ascii'), outer_key)
    return marker + codecs.b64e(cipher)


def save_id_of(account):
    """save 表 id = md5(账号)。"""
    return hashlib.md5(account.encode('utf-8')).hexdigest()


def version_from_json(data):
    """从解密后的存档 JSON 里读版本号。

    存档 JSON 顶层有 `DBVersion`（如 73440），补零成 8 位就是版本号字符串。
    读不到返回 None。
    """
    v = (data or {}).get('DBVersion')
    if v is None:
        return None
    s = str(v)
    if not s.isdigit():
        return None
    return s.zfill(constants.SAVE_VERSION_LEN)


def account_from_json(data):
    """存档 JSON 里的账号（EncryptID）。"""
    return (data or {}).get('EncryptID') or ''


def server_from_json(data):
    v = (data or {}).get('ServerID')
    return '' if v is None else str(v)
