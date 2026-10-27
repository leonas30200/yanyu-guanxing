# -*- coding: utf-8 -*-
"""爆破新版存档的外层密钥：acc[:5] + 'eakec' + 6 位数字（100 万个候选）。

用法: brute_outer6.py <dat.db路径> [账号前5位] [进程数]
命中后自动把密钥写进 keys.local.json（账号从 player 列的 nick / db 文件名推不出来时手动补）。
"""
import sys
import os
import json
import sqlite3
import multiprocessing as mp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from yyjh.core import xxtea, codecs          # noqa: E402

HEAD = b'MDAwNzM0OTI'      # base64("00073492") 的前 11 个字符


def load(path):
    db = sqlite3.connect(path)
    row = db.execute('select id, content, player from save').fetchone()
    db.close()
    return row


def worker(args):
    pre, raw, lo, hi = args
    out = []
    buf = bytearray(pre.encode())
    tail = bytearray(b'000000')
    for n in range(lo, hi):
        # 手搓数字尾，避免每次 format
        s = '%06d' % n
        k = bytes(buf) + s.encode()
        p = xxtea.decrypt(raw, k)
        if p and p[:11] == HEAD:
            out.append((k.decode(), bytes(p[:16])))
    return out


def main():
    path = sys.argv[1]
    _id, content, player = load(path)
    print('save.id =', _id)
    print('player  =', player[:120])
    raw = codecs.b64d(content[8:])
    print('marker  =', content[:8], ' 外层密文', len(raw), '字节')
    pre = sys.argv[2] if len(sys.argv) > 2 else None
    if not pre:
        raise SystemExit('需要账号前 5 位')
    nproc = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    total = 1000000
    chunk = total // nproc
    jobs = [(pre, raw, i * chunk, total if i == nproc - 1 else (i + 1) * chunk)
            for i in range(nproc)]
    print('爆破 %s + 000000..999999，%d 进程…' % (pre + 'eakec', nproc))
    with mp.Pool(nproc) as pool:
        res = pool.map(worker, jobs)
    hits = [h for r in res for h in r]
    if not hits:
        print('❌ 没命中（外层密钥可能不是这个形状）')
        return
    for k, head in hits:
        print('✅ 命中外层密钥 =', k, ' 明文头 =', head)
    # 记盘
    lp = os.path.join(ROOT, 'keys.local.json')
    d = json.load(open(lp, encoding='utf-8')) if os.path.exists(lp) else {}
    d.setdefault('outer_keys_by_id', {})[_id] = hits[0][0]
    json.dump(d, open(lp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print('已写入 keys.local.json -> outer_keys_by_id[%s]' % _id)


if __name__ == '__main__':
    main()
