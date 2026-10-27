# -*- coding: utf-8 -*-
"""自检：我们生成的 dat.db 能不能被「存档工具」认成 v1.8 并解开。

这里把工具里 `save_decrypt.js` 的三个关键函数**照抄**成 Python：
    detectSaveFileVersion   —— 靠**文件名**判断版本
    _getV18PlatformOrder    —— 按版本号决定试哪几把平台密钥
    decryptV18SaveContent   —— base64 -> 版本(8) + XXTEA(平台固定密钥) -> zlib -> JSON

只要这三步在本项目产物上全部通过，产物就能直接拖进工具/解析网站。
跑法: python -u dev\\check_v18.py
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
UP = os.path.dirname(ROOT)

# ── 照抄 save_decrypt.js ──────────────────────────────────
V18_DEVICE_MARKER = re.compile(r'@([A-Za-z0-9]{8})@')
V19_FILE_PATTERN = re.compile(
    r'^yanyu-@([A-Za-z0-9]{16})@-(\d{8})-(\d{6})-dat-v1\.9\.db$')
V20_SHAPE = re.compile(r'^yanyu-@([^@]*)@-@([^@]+)@-(\d{8})-(\d{6})-v2\.0\.db$')
V21_SHAPE = re.compile(r'^yanyu-@([^@]*)@-@([^@]+)@-(\d{8})-(\d{6})-v2\.1(?:\.0)?\.yanyu$')
HAS_VER_SUFFIX = re.compile(r'-v\d+(?:\.\d+)*\.(?:db|yanyu)$', re.I)


def detect_save_file_version(name):
    """工具的 detectSaveFileVersion()。"""
    if V21_SHAPE.match(name):
        return '2.1'
    if V20_SHAPE.match(name):
        return '2.0'
    if V19_FILE_PATTERN.match(name):
        return '1.9'
    if name.lower().endswith('.db') and not HAS_VER_SUFFIX.search(name):
        if len(V18_DEVICE_MARKER.findall(name)) == 1:
            return '1.8.3'
    if re.search(r'\.db$', name, re.I):
        return '1.8'
    return None


def platform_order(version):
    """工具的 _getV18PlatformOrder()。"""
    n = int(version or 0)
    if n > 40318:
        return ['ios', 'android', 'pre_ios', 'pre_android', 'test',
                'oversea', 'korea', 'japan', 'older', 'windows']
    if n > 40098:
        return ['oversea', 'ios', 'android', 'older', 'windows']
    if n > 5503:
        return ['older', 'ios', 'android', 'windows']
    if n == 0:
        return ['windows']
    return ['ios', 'android', 'pre_ios', 'pre_android', 'test',
            'oversea', 'korea', 'japan', 'older', 'windows']


def tool_read(content):
    """工具的 decryptV18SaveContent()。返回 (data, platform, version) 或 None。"""
    try:
        ct = ''.join(content.split())
        env = base64.b64decode(ct + '=' * (-len(ct) % 4))
    except Exception:
        return None
    if len(env) < 9:
        return None
    ver = env[:8].decode('ascii', 'ignore')
    if not re.match(r'^\d{8}$', ver):
        return None
    for plat in platform_order(ver):
        key = constants.V18_PLATFORM_KEYS[plat]
        try:
            comp = xxtea.decrypt_raw(env[8:], key)
            data = json.loads(zlib.decompress(comp).decode('utf-8'))
        except Exception:
            continue
        if isinstance(data, dict):
            return data, plat, ver
    return None


# ── 检查 ──────────────────────────────────────────────────
def read_content(path):
    con = __import__('sqlite3').connect(path)
    try:
        row = con.execute('SELECT content FROM save LIMIT 1').fetchone()
    finally:
        con.close()
    return row[0] if row else ''


ok = bad = 0
skipped = []
print('=' * 74)
print('① 我们生成的 dat.db（output/ 下每个）')
print('=' * 74)
mine = sorted(glob.glob(os.path.join(ROOT, 'output', '**', 'dat.db'),
                        recursive=True))
if not mine:
    skipped.append('①（自己的生成物）')
    print('⚠ 没有样本：output/ 下没有 dat.db（清过 output？）')
    print('  这一段什么都没验证 —— 跑一次 `python -m yyjh local` 生成样本再来。')
for p in mine:
    name = os.path.basename(p)
    # 看旁边的 dat.meta.json 用的是哪种格式（没写就按 v18 期望）
    meta_path = os.path.join(os.path.dirname(p), 'dat.meta.json')
    want = 'v18'
    if os.path.exists(meta_path):
        with open(meta_path, encoding='utf-8') as f:
            want = (json.load(f).get('save_format') or 'v18')

    ver_kind = detect_save_file_version(name)
    res = tool_read(read_content(p))
    if want == 'v18':
        good = (ver_kind == '1.8') and bool(res)
    else:
        # --save-format current：故意用版本密钥 -> 工具**应该**读不了。
        # 两边分得开，才能证明我们真的写的是两种东西。
        good = bool(res) is False
    print(f'{"✅" if good else "❌"} {os.path.relpath(p, ROOT)}   save_format={want}')
    print(f'     文件名判定 = {ver_kind!r}（v18 才要求是 1.8）')
    if res:
        data, plat, ver = res
        print(f'     工具解得开 = v{ver} 平台 {plat} '
              f'{len(data)} 字段 角色 {(data.get("MainRole") or {}).get("Name")!r}')
    else:
        print('     工具解不开')
    ok, bad = (ok + 1, bad) if good else (ok, bad + 1)

print()
print('=' * 74)
print('② 工具老的参照档（data/formattter/*.db，确认我们读得也对）')
print('=' * 74)
refs = sorted(glob.glob(os.path.join(ROOT, '..', 'data', 'formattter', '*.db')))
if not refs:
    skipped.append('②（参照档）')
    print('⚠ 没有参照档：data/formattter/*.db 不在 —— 这一段跳过。')
for p in refs:
    name = os.path.basename(p)
    res = tool_read(read_content(p))
    good = bool(res) and detect_save_file_version(name) == '1.8'
    print(f'{"✅" if good else "❌"} {name}  判定={detect_save_file_version(name)!r}')
    if res:
        data, plat, ver = res
        print(f'     v{ver} 平台 {plat} {len(data)} 字段 '
              f'角色 {(data.get("MainRole") or {}).get("Name")!r}')
    ok, bad = (ok + 1, bad) if good else (ok, bad + 1)

print(f'\n通过 {ok} / 失败 {bad}')
if skipped:
    print('⚠ 以下段落这次没样本、没实际验证：' + '、'.join(skipped))
    print('  （“通过 0”不等于“没问题”，只是手头没东西可测）')
sys.exit(0 if bad == 0 else 1)
