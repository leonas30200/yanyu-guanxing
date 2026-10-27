# -*- coding: utf-8 -*-
"""降级取数：upload3 主体解不开时，用 uploadPower 拼出可用的 dat.json。

为什么需要这个模块
------------------
v73492（whale=82）把存档上传命令从 `upload2` 换成 `upload3`，并且：

* 请求体不再能用会话 realKey 解开（266,508 字节，各种算法/密钥/对齐均已排除）；
* URL query 里**新增**了一个 `auto` 字段，正好 256 字节（= RSA-2048 密文长度），
  而 73460 的 query 里没有这个字段 —— 极可能是「每次存档随机生成的对称密钥，
  用服务器公钥封装」后随请求上传。

如果这个判断成立，`upload3` 的主体就是**设计上抓包解不开**的
（需要服务器私钥，或客户端那一瞬间内存里的明文密钥）。

但同一份抓包里还有两个仍然可解的富数据源：

1. **query**（realKey 可解）—— 角色摘要：昵称/等级/银两/声望/存档码/体力/月卡…
2. **uploadPower**（realKey 可解，内含 base64+zlib）—— 上阵队伍 5 人，
   每人 **137 个字段**，与存档里 `MainRole` 的字段集**完全一致**。

所以：拿最近一次能完整解出的存档当骨架，用上面两份最新数据替换
`MainRole` / `Roles`，就能得到一份「结构完整、角色数据是最新的」dat.json。
其余顶层字段（Items / Task / Letters …）会停留在骨架那一天，需要在 meta 里标注。
"""
import os
import re
import json
import base64
import zlib
import datetime
import hashlib

from ..core import codecs, xxtea

__all__ = ['extract_live', 'find_base', 'build_partial']

LIVE_COMMANDS = ('uploadPower',)


def _cmd(entry):
    for h in entry['request'].get('headers') or []:
        if (h.get('name') or '').lower() == 'command':
            return (h.get('value') or '').strip()
    return ''


def _query_text(entry):
    url = entry['request'].get('url') or ''
    return url.split('?', 1)[1] if '?' in url else ''


def extract_live(har, real_key):
    """从 HAR 里取出**仍然可解**的最新数据：query 摘要 + uploadPower 队伍。

    返回 dict(query=..., team=..., version=..., account=...)，取不到的部分为 None。
    """
    out = {'query': None, 'team': None, 'version': None, 'account': None, 'area': None}

    # 781: v73492 起 download/upload3 的 query 都能用 realKey 解开，
    # 但 download 的 query 只有 {"sn":..}。要挑**字段最全**的那条（upload3 摘要）。
    best_q, best_score = None, -1
    for e in har.entries:
        qt = _query_text(e)
        if not qt:
            continue
        cipher = codecs.b64d(qt)
        if not cipher:
            continue
        raw = xxtea.decrypt(bytes(cipher), real_key)
        if not raw or not raw.lstrip().startswith(b'{'):
            continue
        try:
            obj = json.loads(raw.decode('utf-8'))
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        score = len(obj) + (100 if obj.get('nick') else 0) + (50 if obj.get('saveVer') else 0)
        if score > best_score:
            best_q, best_score = obj, score
    out['query'] = best_q

    for e in har.entries:
        if _cmd(e) not in LIVE_COMMANDS:
            continue
        body = Har_body(e)
        cipher = body if isinstance(body, (bytes, bytearray)) else codecs.b64d(body or '')
        if not cipher:
            continue
        raw = xxtea.decrypt(bytes(cipher), real_key)
        if not raw:
            continue
        try:
            outer = json.loads(raw.decode('utf-8'))
        except ValueError:
            continue
        data = outer.get('data')
        if not isinstance(data, str):
            continue
        blob = codecs.b64d(data)
        if not blob:
            continue
        try:
            plain = zlib.decompress(blob)
        except zlib.error:
            continue
        try:
            out['team'] = json.loads(plain.decode('utf-8'))
        except ValueError:
            continue
        break

    if out['query']:
        out['version'] = str(out['query'].get('saveVer') or out['query'].get('version') or '')
        out['account'] = (out['query'].get('acc')
                          or har.account(har.entries[0]) or '')

    # query 里没有 area/acc 时，从明文响应（api_check_user）补
    if not out['query'] or not out['query'].get('area'):
        for e in har.entries:
            for side in ('request', 'response'):
                txt = ''
                if side == 'request':
                    txt = ((e['request'].get('postData') or {}).get('text') or '').strip()
                else:
                    txt = ((e['response'].get('content') or {}).get('text') or '').strip()
                if not txt.startswith('{'):
                    continue
                try:
                    obj = json.loads(txt)
                except ValueError:
                    continue
                blob = obj.get('data') if isinstance(obj.get('data'), dict) else obj
                if isinstance(blob, dict) and blob.get('area'):
                    out['area'] = str(blob['area'])
                    out['account'] = out['account'] or str(blob.get('acc') or '')
                    break
            if out.get('area'):
                break
    return out


def Har_body(entry):
    """entry -> 请求体文本（兼容 bytes/str）。"""
    pd = entry['request'].get('postData') or {}
    return pd.get('text') or ''


def find_base(root, exclude=None):
    """在 output/ 下找最近一份完整 dat.json 当骨架（跳过降级产物）。"""
    best = None
    outdir = os.path.join(root, 'output')
    if not os.path.isdir(outdir):
        return None
    for dirpath, _dirnames, filenames in os.walk(outdir):
        if 'dat.json' not in filenames:
            continue
        p = os.path.join(dirpath, 'dat.json')
        if exclude and os.path.abspath(p) == os.path.abspath(exclude):
            continue
        meta_p = os.path.join(dirpath, 'dat.meta.json')
        if os.path.exists(meta_p):
            try:
                if (json.load(open(meta_p, encoding='utf-8')) or {}).get('kind') == 'har+partial':
                    continue          # 别拿降级产物当骨架，否则会一层层叠上去
            except ValueError:
                pass
        mt = os.path.getmtime(p)
        if best is None or mt > best[0]:
            best = (mt, p)
    return best[1] if best else None


def build_partial(har, har_path, real_key, base_json, root, outdir=None):
    """生成降级 dat.json，返回 (json_path, meta_path, info)。"""
    live = extract_live(har, real_key)
    if not live['query'] or not live['team']:
        raise ValueError('这份抓包里没有可解的最新数据'
                         '（需要 query + uploadPower 两条都用同一个 realKey 能解开）')
    if not base_json or not os.path.exists(base_json):
        raise ValueError('找不到可用的骨架存档 dat.json，请用 --base 指定一份')

    base = json.load(open(base_json, encoding='utf-8'))
    q = live['query']
    team = live['team']
    nick = q.get('nick') or ''

    master = next((c for c in team if c.get('Name') == nick), team[0])
    base['MainRole'] = master

    roles = base.get('Roles') or []
    idx = {r.get('Name'): i for i, r in enumerate(roles)}
    replaced = []
    for c in team:
        nm = c.get('Name')
        if nm in idx and nm != nick:
            roles[idx[nm]] = c
            replaced.append(nm)

    ver = q.get('saveVer') or q.get('version')
    base['DBVersion'] = int(ver) if ver else base.get('DBVersion')
    if q.get('acc'):
        base['EncryptID'] = str(q['acc'])
    for src, dst in (('money', 'Money'), ('energy', 'Energy'), ('yue', 'Yue'),
                     ('saveCode', 'SavCode')):
        if src in q and q[src] is not None:
            base[dst] = q[src]
    gv = base.setdefault('globalVariable', {})
    if q.get('prestige') is not None:
        gv['repute'] = q['prestige']

    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    if outdir is None:
        runtime = 'ios' if str(q.get('acc', '')).startswith('1') else 'android'
        area = q.get('area') or live.get('area') or q.get('BaseSID') or base.get('ServerID') or '0'
        safe = re.sub(r'[\\/:*?"<>|]', '_', nick) or 'unknown'
        outdir = os.path.join(root, 'output', f'{runtime}-s{area}-{safe}', ts)
    os.makedirs(outdir, exist_ok=True)
    jp = os.path.join(outdir, 'dat.json')
    json.dump(base, open(jp, 'w', encoding='utf-8'), ensure_ascii=False)

    meta = {
        'source': har_path,
        'kind': 'har+partial',
        'runtime': 'ios',
        'account': str(q.get('acc') or ''),
        'save_id': hashlib.md5(str(q.get('acc') or '').encode()).hexdigest(),
        'server': str(q.get('area') or base.get('ServerID') or ''),
        'version': str(ver or ''),
        'outer_key': real_key.decode('ascii', 'replace') if isinstance(real_key, bytes) else str(real_key),
        'inner_key': '',
        'save_format': 'v18(partial)',
        'nick': nick,
        'field_count': len(base),
        'warnings': [
            f'MainRole 与上阵队友（{", ".join(replaced) or "无"}）为本次抓包的最新数据',
            '其余顶层字段（Items / Task / Letters / Branch 等）来自骨架存档，可能滞后',
            '原因：本版 upload3 主体改用随机密钥 + auto(256B) 封装，抓包无法解出',
        ],
        'base_snapshot': base_json,
        'generator': 'yyjh tool_1.1 v1.1.0',
        'generated_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'snapshot_dir': outdir,
        'outputs': {'json': 'dat.json'},
    }
    mp = os.path.join(outdir, 'dat.meta.json')
    json.dump(meta, open(mp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return jp, mp, live
