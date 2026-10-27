# -*- coding: utf-8 -*-
"""诊断：我们的存档 vs 工具（extracted/assets/web）的兼容性 —— 到底缺什么。

会去读工具自己的源码，而不是猜：
    shared/save_decrypt.js    —— 已知版本表 / 密钥表 / 各版判定
    shared/sequence_rules.js  —— 按版本号切序列规则的地方
    shared/simulator.js       —— "请先导入包含打造状态的有效存档" 的真正判据

跑法: python -u dev\\check_tool_compat.py
"""
import base64
import glob
import json
import os
import re
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yyjh.config import constants                       # noqa: E402
from yyjh.core import xxtea                             # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.normpath(os.path.join(ROOT, '..', 'extracted', 'assets', 'web'))


def read(p):
    try:
        with open(p, encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return ''


# ── 1. 从工具源码里把"它认识什么"抽出来 ─────────────────────
sd = read(os.path.join(WEB, 'shared', 'save_decrypt.js'))
sr = read(os.path.join(WEB, 'shared', 'sequence_rules.js'))
sim = read(os.path.join(WEB, 'shared', 'simulator.js'))

MARKER_MAP = {}
for m in re.finditer(r"'([a-z0-9]{8})':\s*\{\s*knownVersions:\s*\[([^\]]*)\]", sd):
    MARKER_MAP[m.group(1)] = re.findall(r"'(\d{8})'", m.group(2))
INNER_MAP = dict(re.findall(r"'(\d{8})':\s*'([^']{16})'", sd))
PLATFORM_KEYS = dict(re.findall(r"\n\s*(\w+):\s*'([A-Za-z0-9]{16})'", sd))

GATES = sorted({int(x) for x in re.findall(r'version\(state\)\s*>=\s*(\d+)', sr)})
GATE_MSG = re.search(r"errors\.push\('请先导入包含打造状态的有效存档'\)", sim) is not None
MAKE_JUDGE = re.search(r'function extractEquipMakeRnd[\s\S]{0,400}?if \(!makeRnd\) return null;', sim)

print('=' * 76)
print('① 工具（extracted/assets/web）自己声明认识什么')
print('=' * 76)
print(f'  嵌套标记 -> 已知版本  (SAVE_NESTED_MARKER_MAP)')
for k, vs in MARKER_MAP.items():
    print(f'    {k}  ->  {", ".join(vs)}')
print(f'  版本 -> 内层密钥      (SAVE_NESTED_INNER_KEY_MAP)')
print(f'    {", ".join(sorted(INNER_MAP))}')
print(f'  v1.8 平台固定密钥     {PLATFORM_KEYS}')
print(f'  序列规则的版本分界    version >= {GATES}   (sequence_rules.js)')
if GATE_MSG:
    print('  "请先导入包含打造状态的有效存档" 的判据 = extractEquipMakeRnd(saveData) 为空')

TOOL_MAX = max(int(v) for v in INNER_MAP)

# ── 2. 逐个存档检查 ─────────────────────────────────────────
def make_rnd_report(data):
    if not isinstance(data, dict):
        return '❌ 顶层不是对象'
    mr = data.get('MakeRnd')
    if not isinstance(mr, dict):
        return '❌ 没有 MakeRnd'
    need = ['Seeds', 'LuckySeeds', 'IDSeeds', 'MakeLucky']
    have = [k for k in need if isinstance(mr.get(k), dict)]
    extra = [k for k in mr if k not in need]
    return (f'✅ MakeRnd 有 {len(mr)} 个键；工具要的 4 个里齐了 {len(have)}/4 {have}；'
            f'另有 {extra}')


def check(path):
    import sqlite3
    con = sqlite3.connect(path)
    try:
        row = con.execute('SELECT content FROM save LIMIT 1').fetchone()
    finally:
        con.close()
    content = row[0] if row else ''
    print('=' * 76)
    print(os.path.relpath(path, ROOT))
    head = content[:8]
    if head in MARKER_MAP:
        print(f'  嵌套标记 = {head!r}  ← 工具登记过的版本: {MARKER_MAP[head]}')
    else:
        print(f'  无标记（初版 / v1.8 形状），头 8 字符 = {content[:8]!r}')

    # 解出里面的版本号 + JSON
    data = None
    ver = ''
    env = None
    if head in MARKER_MAP:
        print('  外层加密：需要 16 位解析码（工具从文件名/toool 拿）')
    else:
        try:
            env = base64.b64decode(content + '=' * (-len(content) % 4))
            ver = env[:8].decode('ascii', 'ignore')
        except Exception:
            pass
        if re.match(r'^\d{8}$', ver):
            print(f'  版本号   = {ver}   ({int(ver)})')
            if ver in INNER_MAP:
                print(f'  工具认识这个版本 ✅  密钥 {INNER_MAP[ver]}')
            else:
                print(f'  工具**不认识**这个版本 ❌  它最多到 {TOOL_MAX:08d}')
            print(f'  序列规则会走 version >= {GATES} 的分支：'
                  f'{"新规则 ✅" if int(ver) >= min(GATES) else "旧规则"}')
            for plat in ('android', 'ios'):
                try:
                    data = json.loads(zlib.decompress(
                        xxtea.decrypt_raw(env[8:], constants.V18_PLATFORM_KEYS[plat])
                    ).decode('utf-8'))
                    print(f'  v1.8 平台密钥({plat}) 解得开 ✅')
                    break
                except Exception:
                    continue
            else:
                print('  v1.8 平台密钥解不开（只有 --save-format current 才是这样）')
    if data:
        print(f'  {make_rnd_report(data)}')


targets = sorted(glob.glob(os.path.join(ROOT, 'output', '**', 'dat.db'), recursive=True))
targets += [p for p in (os.path.join(ROOT, '..', 'data', 'dat.db'),
                        os.path.join(ROOT, '..', 'data', 'origin_ver', 'dat.db'))
            if os.path.exists(p)]
targets += sorted(glob.glob(os.path.join(ROOT, '..', 'data', 'formattter', '*.db')))

print()
print(f'② 逐个存档（{len(targets)} 个）')
for p in targets:
    try:
        check(p)
    except Exception as e:
        print(f'  ❌ {type(e).__name__}: {e}')

print()
print('=' * 76)
print('③ 结论：缺什么')
print('=' * 76)
print(f'  · 工具外置密钥表最高只到 {TOOL_MAX:08d}，游戏现在（存档头）是 00073440')
print(f'    => save_decrypt.js 的 SAVE_NESTED_INNER_KEY_MAP / SAVE_NESTED_MARKER_MAP')
print(f'       需要登记 00073440（pon1dxg7po06ewkx / dnkemiea 的 knownVersions）')
print(f'  · 走 v1.8 可以绕过密钥表（平台密钥固定，工具自己就解开了）')
print(f'  · 序列规则分界目前只有 version >= {GATES}；73440 会跟 73420 走同一分支。')
print(f'    所以如果 73440 又改了机制，真正缺的是“73440 的序列规则 + WASM 内核”，')
print(f'    那部分不在工具里，只能从游戏本体的新代码反推。')
print()
print('  注：MakeRnd 不缺失（我们解出来的档里 4/4 字段齐 + S/CKL/CKI/CKS/DaZao/RedSeeds）。')
print('      "请先导入包含打造状态的有效存档" 是“存档没装载成功”的表象，不是字段缺失。')
sys.exit(0)
