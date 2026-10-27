# -*- coding: utf-8 -*-
"""save.player 列 —— 明文摘要（角色选择界面用）。

字段顺序固定，必须照抄，否则字节不一致。
映射关系由 3 份"同一时刻"的样本逐项核对得到（见 docs/01_抓包解密.md §9.2）:

    energy   <- 顶层 Energy
    menpai   <- MainRole.MenPai
    sex      <- MainRole.Sex
    money    <- 顶层 Money
    lev      <- MainRole.Level
    prestige <- globalVariable.repute
    doubt    <- 恒为 0（未找到来源）
    saveCode <- 顶层 SavCode   ← 游戏自己拼错了，少个 e
    nick     <- MainRole.Name
    job      <- MainRole.Job
    version  <- 版本号数字
    avatar   <- MainRole.Cloth（实测与 Shoe 恒等）
    yue      <- 顶层 Yue
"""
import json

FIELD_ORDER = ['energy', 'menpai', 'sex', 'money', 'lev', 'prestige', 'doubt',
               'saveCode', 'nick', 'job', 'version', 'avatar', 'yue']


def build(data, version, avatar_field='auto'):
    """生成 player 摘要字符串（紧凑 JSON，键顺序与游戏一致）。"""
    mr = data.get('MainRole') or {}
    gv = data.get('globalVariable') or {}

    if avatar_field and avatar_field != 'auto':
        avatar = mr.get(avatar_field, 0)
    else:
        avatar = mr.get('Cloth')
        if avatar is None:
            avatar = mr.get('Shoe')
        if avatar is None:
            avatar = mr.get('Face', 0)

    try:
        ver_num = int(version)
    except (TypeError, ValueError):
        ver_num = 0

    out = {
        'energy': data.get('Energy', 0),
        'menpai': mr.get('MenPai', 0),
        'sex': mr.get('Sex', 0),
        'money': data.get('Money', 0),
        'lev': mr.get('Level', 0),
        'prestige': gv.get('repute', 0),
        'doubt': 0,
        'saveCode': data.get('SavCode', 0),
        'nick': mr.get('Name', ''),
        'job': mr.get('Job', 0),
        'version': ver_num,
        'avatar': avatar,
        'yue': data.get('Yue', 0),
    }
    # 严格按游戏顺序输出
    ordered = {k: out[k] for k in FIELD_ORDER}
    return json.dumps(ordered, ensure_ascii=False, separators=(',', ':'))


def summary(data):
    """给报告用的可读摘要。"""
    mr = data.get('MainRole') or {}
    return {
        '角色名': mr.get('Name'),
        '门派': mr.get('MenPai'),
        '性别': mr.get('Sex'),
        '等级': mr.get('Level'),
        '职业': mr.get('Job'),
        '银两': data.get('Money'),
        '体力': data.get('Energy'),
        '月卡': data.get('Yue'),
        '声望': (data.get('globalVariable') or {}).get('repute'),
    }
