# -*- coding: utf-8 -*-
"""对比 C 扩展与纯 Python 的 XXTEA 行为，找出真正一致的那一组。"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yyjh.core import xxtea as X

try:
    import xxtea as C
    print('C 扩展 dir:', [d for d in dir(C) if not d.startswith('_')])
except Exception as e:
    C = None
    print('无 C 扩展', e)


def show(tag, b):
    if b is None:
        print(f'  {tag}: None')
        return
    lw = struct.unpack_from('<I', b, len(b) - 4)[0] if len(b) >= 4 else -1
    print(f'  {tag}: len={len(b)} tail={lw} head={b[:16]!r}')


k = 'LanJingNo1'
for n in (1, 5, 16, 100):
    pt = bytes(range(n))
    print(f'\n=== 明文 {n} 字节 {pt[:16]!r} ===')
    buf = pt + b'\x00' * ((-n) % 4) + struct.pack('<I', n)
    print(f'  构造缓冲 {len(buf)}B  {buf.hex()}')

    e_py = X._encrypt_py(buf, k)
    show('纯py加密', e_py)
    d_py = X._decrypt_py(e_py, k)
    show('  -> 纯py解密', d_py)
    print(f'     往返OK: {d_py == buf}')
    if C:
        try:
            d_c = C.decrypt(e_py, X.norm_key(k), padding=False)
            show('  -> C解密', d_c)
            print(f'     C解出来 == 缓冲: {d_c == buf}')
        except Exception as ex:
            print('  C解密异常:', ex)
        try:
            e_c = C.encrypt(buf, X.norm_key(k), padding=False)
            show('C加密', e_c)
            print(f'     C加密 == 纯py加密: {e_c == e_py}')
            d_c2 = X._decrypt_py(e_c, k)
            show('  -> 纯py解密', d_c2)
            print(f'     往返OK: {d_c2 == buf}')
        except Exception as ex:
            print('  C加密异常:', ex)
