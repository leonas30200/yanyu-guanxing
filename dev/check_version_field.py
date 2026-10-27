# -*- coding: utf-8 -*-
"""查存档 JSON 里有没有版本号字段（C 接口重新加密时需要）。"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for name in (r'data\dat_db_decrypted.json', r'data\ios_from_har.json'):
    p = os.path.join(ROOT, name)
    if not os.path.exists(p):
        continue
    d = json.load(open(p, encoding='utf-8'))
    print('=' * 60)
    print(name)
    hits = [k for k in d if re.search(r'ver', k, re.I)]
    print('  顶层含 ver 的键:', hits)
    for k in hits[:10]:
        print(f'    {k} = {d[k]!r}')
    mr = d.get('MainRole') or {}
    mh = [k for k in mr if re.search(r'ver', k, re.I)]
    print('  MainRole 含 ver 的键:', mh)
    for k in mh[:10]:
        print(f'    MainRole.{k} = {mr[k]!r}')
    # 全部顶层键里有没有正好是 8 位数字字符串的
    nums = [k for k, v in d.items() if isinstance(v, str) and re.fullmatch(r'\d{8}', v)]
    print('  值为 8 位数字串的键:', nums)
    for k in nums:
        print(f'    {k} = {d[k]}')
