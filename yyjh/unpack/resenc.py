# -*- coding: utf-8 -*-
"""`res_encrypt/` 资源文件解密。

格式: `BIGDA`(5) + 密文   或   `KEIIY`(5) + 密文
     -> 循环XOR( JJyyt973 / NanShuGo ) -> XXTEA("LanJingNo1")
解出来是**明文 Lua 源码**（`src/app/**.lua`）—— 换版本后想看游戏逻辑就看这里。
"""
import os

from ..config.constants import RES_SIGN_XOR
from ..core import codecs
from . import signed


def is_encrypted(data):
    return signed.sign_of(data) is not None


def decrypt_res(data, shrink_words=0):
    """单文件解密。返回明文 bytes 或 None。"""
    pt, _sign = signed.decrypt_file(data, shrink_words=shrink_words)
    return pt


def decrypt_tree(target, outdir=None, shrink_words=0, on_progress=None):
    return signed.decrypt_tree(target, outdir=outdir, shrink_words=shrink_words,
                               on_progress=on_progress)


def looks_like_lua(data):
    """解出来的到底是不是 Lua（有 function/local/return 之类的词）。"""
    probe = data[:4096]
    return any(w in probe for w in (b'\nlocal ', b'\nfunction ', b'\nreturn ',
                                    b'local function'))


def scan_for_lua(target, outdir=None, on_progress=None):
    """只解出"看起来是 Lua 源码"的文件（有些资源解出来是图片/图集）。"""
    return decrypt_tree(target, outdir=outdir, on_progress=on_progress)
