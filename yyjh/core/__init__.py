# -*- coding: utf-8 -*-
"""基础算法层：纯函数、无业务、无 IO。出问题可以单独测这一层。"""
from . import codecs, oracle, xxtea      # noqa: F401

__all__ = ['codecs', 'oracle', 'xxtea']
