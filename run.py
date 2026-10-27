#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tool_1.1 便捷启动器 —— 等价于 `python -m yyjh`，但不用先 cd。

**不带子命令就是向导**（选模式 → 导入存档 → 打开网页版）：

    python run.py
    python run.py --outdir ..\\out          只给向导的参数也行

带子命令时原样透传：

    python run.py local ..\\data\\dat.db
    python run.py capture ..\\data\\x.har
    python run.py key list
"""
import os
import sys

# 版本守卫放在最前面：下面这几行只用 Python 2 也认的语法，
# 所以万一 python 指向 2.7，用户看到的是人话而不是一堆 SyntaxError。
if sys.version_info < (3, 8):
    if os.name == 'nt':
        try:                      # 控制台切成 UTF-8，否则中文提示是乱码
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
    sys.exit('需要 Python 3.8 或更高版本（推荐 3.11），当前是 %d.%d。\n'
             '请用 py311 环境跑：\n'
             '  conda activate py311   然后  python run.py\n'
             '或者直接写全路径：\n'
             '  D:\\Anaconda\\envs\\py311\\python.exe run.py'
             % sys.version_info[:2])

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from yyjh.__main__ import main       # noqa: E402

if __name__ == '__main__':
    # main() 会自己处理：没子命令就补 start，非交互终端则打帮助
    sys.exit(main())
