# -*- coding: utf-8 -*-
"""批量验证生成物：结构同构 + 能否解回来。"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yyjh.localdb import sqlitedb
from yyjh.game import savefmt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFICIAL = os.path.join(ROOT, '..', 'data', 'origin_ver', 'dat.db')
OFFICIAL = os.path.normpath(OFFICIAL)

print(f'官方样板: {OFFICIAL}')
print(f'  {sqlitedb.pragmas(OFFICIAL)}')
print()

ok = bad = 0
dbs = glob.glob(os.path.join(ROOT, 'output', '**', '*.db'), recursive=True)
if not dbs:
    print('⚠ 没有样本：output/ 下没有 dat.db（清过 output？）')
    print('  跑一次 `python -m yyjh local`（或 capture）生成样本再来。')
for p in sorted(dbs):
    name = os.path.relpath(p, ROOT)
    try:
        diffs = sqlitedb.structure_diff(OFFICIAL, p)
        row = sqlitedb.read_save(p)
        fmt = savefmt.detect_format(row['content'])
        data, meta = savefmt.unwrap_any(
            row['content'], 'android',
            outer_key=os.environ.get('YYJH_OUTER_KEY') or None)
        struct_ok = '✅' if not diffs else '❌ ' + str(diffs)
        print(f'{name}')
        print(f'   id={row["id"]}  content={len(row["content"]):,}  格式={fmt}')
        print(f'   结构 vs 官方: {struct_ok}')
        print(f'   回读解密: {len(data)} 字段  角色={(data.get("MainRole") or {}).get("Name")!r}  '
              f'版本={meta["version"]}  内层密钥={meta["inner_key"]}')
        if not diffs:
            ok += 1
        else:
            bad += 1
    except Exception as e:
        bad += 1
        print(f'{name}\n  ❌ {type(e).__name__}: {e}')
    print()

print(f'同构通过 {ok} / 失败 {bad}')
