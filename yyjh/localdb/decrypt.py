# -*- coding: utf-8 -*-
"""dat.db -> SaveResult（本地存档路线）。

新版需要"外层密钥"（每账号一把、只在内存里），初版不需要。
外层密钥候选顺序：
    1) 调用方显式传入
    2) keys.local.json 里按 account / save_id 记录
    3) config.constants.DEFAULT_OUTER_KEY（已知的安卓账号1）
    4) keys.local.json 里所有记过的外层密钥
每个候选都用"版本号合法 + 内层能解开"双重校验，所以多试几个几乎无成本。
"""
import os

from .. import model
from ..config import constants, keytables
from ..game import player as player_mod
from ..game import savefmt
from . import sqlitedb


def outer_candidates(account=None, explicit=None, save_id=None):
    out = []
    if explicit:
        out.append(explicit)
    k = keytables.outer_key(account, save_id)
    if k:
        out.append(k)
    if constants.DEFAULT_OUTER_KEY:
        out.append(constants.DEFAULT_OUTER_KEY)
    ov = keytables._load_overlay()
    for d in ('outer_keys', 'outer_keys_by_id'):
        for v in (ov.get(d) or {}).values():
            if v not in out:
                out.append(v)
    return [k for k in out if k]


def decode_db(path, runtime='android', outer_key=None, inner_key=None,
              account=None, avatar_field='auto'):
    """返回 SaveResult。"""
    r = model.SaveResult(source=path, kind='db', runtime=runtime)
    row = sqlitedb.read_save(path)
    r.save_id = row['id']
    r.account = account or ''
    r.step('读取 dat.db', f"id = {row['id']}   content = {len(row['content']):,} 字符")

    content = row['content']
    fmt = savefmt.detect_format(content)
    if fmt is None:
        raise ValueError('content 既不是新版也不是初版格式（文件损坏？）')
    r.step('格式判定', '新版（带标记 + 外层加密）' if fmt == savefmt.NEW
           else '初版（纯 base64，无外层）')

    if fmt == savefmt.NEW:
        last_err = None
        for key in outer_candidates(account, outer_key, row['id']):
            try:
                dataset, meta = savefmt.unwrap_new(content, key, runtime, inner_key)
                r.outer_key = key
                if key != (outer_key or ''):
                    r.step('外层密钥', f'{key}  (自动匹配成功)')
                else:
                    r.step('外层密钥', key)
                break
            except savefmt.InnerKeyMissing:
                # 外层已经解出**合法的初版格式**了（版本号是 8 位数字、长度也对齐），
                # 说明这把外层密钥是对的原配；缺的是这一版的内层密钥。
                # 换别的外层密钥不可能变好 —— 直接抛出去，别再瞎试。
                raise
            except ValueError as e:
                last_err = e
                continue
        else:
            raise ValueError(
                '新版存档的外层密钥不对（每账号一把，只在游戏内存里）\n'
                f'  试过: {outer_candidates(account, outer_key, row["id"])}\n'
                '  -> 用 `python -m yyjh mem crack` 从内存挖，或 --outer-key 指定\n'
                f'  最后一次错误: {last_err}')
    else:
        dataset, meta = savefmt.unwrap_old(content, runtime, inner_key)
        r.step('内层解密', '成功')

    r.version = meta['version']
    r.inner_key = meta['inner_key']
    r.marker = meta.get('marker') or ''
    r.data = dataset
    # dat.db 里只有 md5(账号)，真正的账号在解密后的 EncryptID 里
    if not r.account:
        r.account = dataset.get('EncryptID') or ''
    # 区服（ServerID）也在明文里 —— 文件名要用它，且 dat.db 本身没存
    if not r.server:
        r.server = savefmt.server_from_json(dataset)
    r.step('版本号', r.version)
    r.step('内层密钥', f"{r.inner_key}  ({meta['tried']} 个候选中命中)")
    r.step('解密结果', f'{len(dataset)} 个字段   角色 = {r.nick!r}')

    r.player = row.get('player') or player_mod.build(dataset, r.version, avatar_field)
    r.step('player 摘要', r.player)

    # 顺手把初版 content 也准备好（方便直接换格式/写别的库）
    if fmt == savefmt.NEW:
        try:
            r.content = savefmt.unwrap_outer(content, r.outer_key)
        except Exception:
            pass
    else:
        r.content = content
    return r
