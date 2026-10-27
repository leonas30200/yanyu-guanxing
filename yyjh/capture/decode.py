# -*- coding: utf-8 -*-
"""抓包 HAR -> 存档（SaveResult）。

关键点：**外层解开后拿到的 base64 文本就是"初版" content**
（网络外层用的是服务器下发的 realKey，正好替代了那个每账号随机、
 只存在内存里的"外层密钥"），所以不需要重新加密任何数据。
"""
import json
import os

from .. import model
from ..core import codecs, xxtea
from ..game import player as player_mod
from ..game import protocol, savefmt
from .har import Har


class NoDownloadStep(ValueError):
    """upload 解不开，而且**整份抓包里根本没有 download 请求**。

    ⚠ 这种情况和「游戏改了加密」是两回事，提示必须区分开：
        73492 起 upload 请求体是 RSA 封装的，只有服务端解得开，客户端拿不到私钥；
        所以抓包路线**唯一**的进料口是"下载存档"那条响应（服务端把存档下发给你）。
        如果抓包时只点了"保存存档"、没点"下载存档"，那就是无米之炊 ——
        重试一百遍也一样，唯一的解法是**补抓那一步**。

    以前这种情况会一路掉进 UploadCipherChanged，报「游戏更新了、换个算法」，
    把用户彻底带偏（本人 2026-10-23 就踩了一次：某 iOS 角色那份 8 条请求的 HAR）。
    """

    def __init__(self, account='', cmds=()):
        self.account = account
        self.cmds = list(cmds)
        super().__init__(
            f'抓包里没有 download 请求（只有 {"/".join(self.cmds) or "空"}）')


class UploadCipherChanged(ValueError):
    """realKey 是对的，但**这一版的 upload 请求体换了加密方式**。

    ⚠ 为什么要单独一个异常：
        以前所有失败都报「外层解密失败（realKey 不对）」，用户于是反复重抓、
        反复重跑，方向完全错了 —— 实际上 realKey 一点问题都没有
        （同一个 realKey 能解开 lookmail / getHuodong / upload 的响应）。
        游戏在某次更新里把 upload 请求体换成了另一套加密（73460 -> 73492
        可见：命令从 upload2 变成 upload3，请求体再也不是 XXTEA(realKey)）。
        这种情况重抓一百遍也没用，必须换路线。
    """

    def __init__(self, version='', cmd='', size=0, session_ok=True):
        self.version = version
        self.cmd = cmd
        self.size = size
        self.session_ok = session_ok
        super().__init__(
            f'版本 {version or "?"} 的 {cmd or "upload"} 请求体用了新的加密方式'
            f'（{size:,} 字节），realKey 本身是好的')


def _body_text(entry, where='request'):
    node = (entry.get(where) or {})
    if where == 'request':
        return ((node.get('postData') or {}).get('text') or '')
    return ((node.get('content') or {}).get('text') or '')


def _looks_like_json(raw):
    if not raw:
        return False
    head = raw.lstrip()[:8]
    return head.startswith(b'{') or head.startswith(b'[')


def session_key_ok(har, real_key, skip=None):
    """用 realKey 去解**别的小请求/响应**，判断这把钥匙到底对不对。

    realKey 是整条会话共用的，所以只要有任何一个小信封能解成 JSON，
    就证明 realKey 没问题 —— 那么 upload 解不开就只能是 upload 自己换了加密。
    这样报错才能指对方向。
    """
    if not real_key:
        return False
    for e in har.entries:
        if e is skip:
            continue
        for where in ('response', 'request'):
            text = _body_text(e, where)
            if not text or len(text) > 8192:
                continue
            cipher = codecs.b64d(text)
            if not cipher:
                continue
            raw = xxtea.decrypt(bytes(cipher), real_key)
            if raw and _looks_like_json(raw):
                return True
    return False


def auto_blob(har, entry=None, real_key=None):
    """取出 query 明文里的 `auto` 字段（已 base64 解码），没有则 None。

    v73492 才出现这个字段，固定 256 字节 —— 与 RSA-2048 密文等长，
    是"每存档随机密钥被服务器公钥封装"的最可能载体，记下来便于判断换代原因。
    """
    if not real_key:
        return None
    cands = [entry] if entry is not None else har.entries
    for e in cands:
        url = (e.get('request') or {}).get('url') or ''
        if '?' not in url:
            continue
        cipher = codecs.b64d(url.split('?', 1)[1])
        if not cipher:
            continue
        raw = xxtea.decrypt(bytes(cipher), real_key)
        if not raw or not raw.lstrip().startswith(b'{'):
            continue
        try:
            obj = __import__('json').loads(raw.decode('utf-8'))
        except ValueError:
            continue
        val = obj.get('auto')
        if isinstance(val, str):
            return codecs.b64d(val)
    return None


def pick_entry(har, index=None):
    ups = har.uploads()
    if not ups:
        raise ValueError('HAR 里没有 upload 请求（抓包时游戏没上传存档？）')
    if index is not None:
        if not (0 <= index < len(ups)):
            raise ValueError(f'--index 超范围（共 {len(ups)} 个）')
        return ups[index], len(ups)
    return max(ups, key=har.body_len), len(ups)


# ─────────────────── 下载存档兜底路线（v73492 起） ───────────────────
# 73492 把 upload3 的请求体换成 RSA-2048 封装（256 字节），抓包层数学上解不开。
# 但服务器下发的 **download 响应**里有 saveCode / extra 两个字段：
#   extra    = XXTEA(realKey).encode(psw)      ← psw 就是内层密钥的前 8 位
#   saveCode = XXTEA(realKey).encode(存档序号)
#   data     = '00073492' + XXTEA(psw+渠道后缀).encode(zlib(JSON))
# 所以"上传解不开"完全不影响拿存档 —— 从下载那条响应照样能还原完整 JSON。


def _download_entry(har):
    """找 command=download 的响应（抓不到就退回"字段长得像"的那条）。"""
    for e in har.entries:
        if (har.header(e, 'command') or '').strip().lower() == 'download':
            return e
    for e in har.entries:
        t = _body_text(e, 'response') or ''
        if '"saveCode"' in t and '"data"' in t:
            return e
    return None


def _resp_json(har, entry, real_key):
    """响应体 -> dict。明文 JSON 直接用；否则先用 realKey 解一层。"""
    t = _body_text(entry, 'response') or ''
    if not t:
        return None
    t = t.strip()
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    c = codecs.b64d(t)
    if c:
        p = xxtea.decrypt(bytes(c), real_key)
        if p:
            try:
                obj = json.loads(p.decode('utf-8'))
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                pass
    return None


def runtime_of(har, acc=''):
    """判断抓包来自哪个平台。

    ★ 优先看请求头 `plat`（客户端自己报的，最准）。
    以前只看 acc 里有没有 '@' —— iOS 的**手机号/邮箱混合账号**会被误判成安卓，
    于是内层密钥先试安卓后缀、输出目录也落到 android-xxx 下面，白绕一圈。
    """
    for e in har.entries:
        p = (har.header(e, 'plat') or '').strip().lower()
        if p in ('ios', 'android'):
            return p
    return 'ios' if protocol.is_ios(acc) else 'android'


def real_key_of(har, explicit=None):
    """只推导 realKey（不碰 upload），供下载路线使用。"""
    if explicit:
        return explicit
    acc = (har.account() or '').strip()
    k = har.k_header()
    if not acc or not k:
        raise ValueError('HAR 里没有 acc / k 头 —— 请从"打开游戏之前"开始抓，'
                         '或用 --outer-key 手动指定 realKey')
    return protocol.derive(acc, k)['real_key']


def download_body(har, real_key, runtime, steps=None):
    """从 download 响应直接还原完整存档。返回 None 表示这条路也走不通。"""
    say = steps or (lambda *a, **k: None)
    e = _download_entry(har)
    if e is None:
        return None
    obj = _resp_json(har, e, real_key)
    if not obj:
        return None
    data_b64, extra = obj.get('data') or '', obj.get('extra') or ''
    if not data_b64 or not extra:
        return None

    # extra -> psw
    psw = None
    b = codecs.b64d(extra)
    if b:
        p = xxtea.decrypt(bytes(b[:len(b) // 4 * 4]), real_key)
        if p:
            psw = p.rstrip(b'\x00').decode('ascii', 'replace')
    if not psw:
        say('下载响应', 'extra 解不出 psw（realKey 不对？）')
        return None
    sc = ''
    b2 = codecs.b64d(obj.get('saveCode') or '')
    if b2:
        p2 = xxtea.decrypt(bytes(b2[:len(b2) // 4 * 4]), real_key)
        if p2:
            sc = p2.rstrip(b'\x00').decode('ascii', 'replace')
    say('下载响应', f'存档序号 {sc or "?"}　psw = {psw}（extra 用 realKey 解出）')

    raw = codecs.b64d(data_b64) or b''
    if len(raw) < 12:
        return None
    version = raw[:8].decode('ascii', 'replace')
    body = raw[8:]
    body = body[:len(body) // 4 * 4]

    from ..config import keytables
    cands = [psw + s for s in keytables.suffix_order(runtime)]
    cands.append(psw)
    plain, used = savefmt.try_inner(body, cands)
    if plain is None:
        say('下载响应', f'存档主体解不开（psw={psw}，试了 {len(cands)} 个后缀）')
        return None
    say('版本号', version)
    say('内层密钥', f'{used}（psw + 渠道后缀）')
    return {'version': version, 'data': plain, 'inner_key': used,
            'save_code': sc}


def inner_body(har_path, index=None, real_key=None, steps=None):
    """HAR -> 只做到「初版 content」为止。

    返回 dict: version / body / runtime / account / save_id / server / content
    其中 body 是**内层 XXTEA 密文** —— 缺内层密钥时拿它去爆破
    （`yyjh key crack --har` 就是这么干的）。

    为什么单独抽出来：内层密钥不知道时，外层其实已经能免费解开 ——
    抓包里的 realKey 顶替了那把只存在内存里的「外层密钥」，
    所以「密钥表过期」根本不该妨碍我们从抓包里拿到爆破靶子。
    """
    say = steps or (lambda *a, **k: None)
    har = Har(har_path)
    say('HAR', f'{os.path.basename(har_path)}  共 {len(har.entries)} 条记录')

    entry, total = pick_entry(har, index)
    if total > 1:
        say('选择 upload',
            f'共 {total} 个，选请求体最大的（{har.body_len(entry):,} 字符）')

    acc = har.account(entry)
    if not acc:
        raise ValueError('upload 请求里没有 acc 请求头')
    runtime = runtime_of(har, acc)
    server = har.server(entry) or ''
    say('账号 acc', f'{acc}   平台 = {runtime}   服 = {server or "?"}')
    save_id = savefmt.save_id_of(acc)
    say('save.id', f'md5(acc) = {save_id}')

    # ── 协议密钥 ──
    if real_key:
        say('realKey', f'{real_key}  (命令行指定)')
    else:
        k = har.k_header()
        if not k:
            # 抓包路线（run.py capture）的参数名是 --outer-key，
            # key crack 那边是 --real-key，两个都写上免得对不上号。
            raise ValueError('HAR 里没有带 k 的响应头 —— 请从"打开游戏之前"开始抓，'
                             '或用 --outer-key / --real-key 手动指定 realKey')
        keys = protocol.derive(acc, k)
        say('key0', keys['key0'])
        say('innerK', repr(keys['inner_k']))
        say('realKey', keys['real_key'])
        real_key = keys['real_key']

    # ── 外层 -> 初版 content ──
    # 同一份抓包里可能有好几个 upload（翻页上传、多个角色…）。以前只用"最大的那个"，
    # 一旦最大的那个刚好是新格式解不开，就会整个失败 —— 而旁边可能就躺着能解的老格式。
    # 所以：没显式指定 --index 时，按体积从大到小逐个试，谁解得开用谁。
    cands = [entry] if index is not None else sorted(
        har.uploads(), key=har.body_len, reverse=True)
    x = None
    cipher = b''
    for cand in cands:
        body = Har.body(cand)
        if not body:
            continue
        c = body if isinstance(body, (bytes, bytearray)) else codecs.b64d(body)
        if not c:
            continue
        plain = xxtea.decrypt(bytes(c), real_key)
        if plain is not None:
            x, cipher = plain, bytes(c)
            if cand is not entry:
                say('改用 upload', f'{har.header(cand, "command")}'
                                     f'（{har.body_len(cand):,} 字符，前面的解不开）')
            break
    if x is None:
        # 关键分流：realKey 到底对不对？
        # 能解开别的小信封 -> realKey 没问题，是这一版的 upload 换了加密；
        # 一个都解不开 -> 才是真的 realKey 不对（抓包没从登录开始 / acc 取错）。
        if session_key_ok(har, real_key, skip=entry):
            raise UploadCipherChanged(
                har.header(entry, 'ver') or '',
                har.header(entry, 'command') or '',
                har.body_len(entry))
        raise ValueError('外层解密失败（realKey 不对）')
    try:
        content = x.decode('ascii')
    except UnicodeDecodeError:
        raise ValueError('外层解出的不是 ASCII base64（realKey 不对？）')
    say('外层解密', f'{len(cipher):,}B -> {len(content):,} 字符 base64  ← 即初版 content')

    version, inner = savefmt.parse_old(content)
    say('版本号', version)
    return {'version': version, 'body': inner, 'runtime': runtime, 'account': acc,
            'save_id': save_id, 'server': server, 'content': content,
            # realKey 就是「外层密钥」那把（它只存在服务器的响应里，
            # 本地存档要解时得去内存挖）—— 白送的，调用方可以记下来复用
            'outer_key': real_key}


def decode(har_path, index=None, real_key=None, inner_key=None,
           avatar_field='auto'):
    """返回 SaveResult。"""
    r = model.SaveResult(source=har_path, kind='har')
    saved_exc = None
    try:
        info = inner_body(har_path, index=index, real_key=real_key, steps=r.step)
    except UploadCipherChanged as e:
        # upload 换加密了 —— 先试着走 download 响应那条路，能走通就当没发生过
        saved_exc = e
        info = None

    if info is None:
        har = Har(har_path)
        rk = real_key_of(har, real_key)
        acc = (har.account() or '').strip()
        runtime = runtime_of(har, acc)
        r.step('改走下载路线',
               'upload3 是新加密（RSA 封装），改从 download 响应还原存档')
        dl = download_body(har, rk, runtime, steps=r.step)
        if dl is None:
            # 先分清到底是"缺下载存档这一步"还是"游戏真改了加密" ——
            # 两者提示完全不同，混为一谈会让人反复重抓。
            if _download_entry(har) is None:
                cmds = sorted({(har.header(e, 'command') or '').strip()
                               for e in har.entries
                               if (har.header(e, 'command') or '').strip()})
                r.step('抓包体检', f'没有 download 请求　实际只有：{"/".join(cmds)}')
                raise NoDownloadStep(acc, cmds) from saved_exc
            raise saved_exc
        r.account, r.runtime = acc, runtime
        r.server = har.server() or ''
        r.save_id = savefmt.save_id_of(acc)
        r.version, r.data = dl['version'], dl['data']
        r.inner_key = dl['inner_key']
        r.outer_key = rk
        r.content = ''            # _finish() 会用 res.data 重新构造
        r.step('解密结果', f'{len(r.data)} 个字段   角色 = {r.nick!r}')
        r.player = player_mod.build(r.data, r.version, avatar_field)
        r.step('player 摘要', r.player)
        enc = r.data.get('EncryptID')
        if enc and enc != acc:
            r.warn(f'JSON 里的 EncryptID（{enc}）与 acc 不一致')
        return r

    version, inner = info['version'], info['body']
    r.account, r.runtime = info['account'], info['runtime']
    r.server, r.save_id = info['server'], info['save_id']
    r.content, r.version = info['content'], version
    r.outer_key = info.get('outer_key') or ''

    # ── 内层 -> JSON ──
    cands = savefmt.inner_candidates(r.runtime, version, inner_key)
    data, used = savefmt.try_inner(inner, cands)
    if data is None:
        raise savefmt.InnerKeyMissing(version, r.runtime,
                                      tried=len(cands), fmt=savefmt.OLD)
    r.data = data
    r.inner_key = used
    r.step('内层密钥', f'{used}  ({len(cands)} 个候选中命中)')
    r.step('解密结果', f'{len(data)} 个字段   角色 = {r.nick!r}')

    r.player = player_mod.build(data, version, avatar_field)
    r.step('player 摘要', r.player)

    enc = data.get('EncryptID')
    if enc and enc != info['account']:
        r.warn(f'JSON 里的 EncryptID（{enc}）与 acc 不一致')
    return r
