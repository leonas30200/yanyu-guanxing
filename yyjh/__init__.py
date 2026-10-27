# -*- coding: utf-8 -*-
"""烟雨观星（com.lanjing.yanyu.*）存档研究工具集  v1.1

目录分层
────────
  config/    所有密钥、常量、路径的唯一真源（要改密钥只改这里）
  core/      基础算法：XXTEA / base64 / zlib / 长度预言机（纯函数，无 IO）
  game/      游戏结构：协议密钥推导、存档格式（初版/新版）、player 摘要
  capture/   抓包：HAR 解析与解密
  localdb/   本地存档：dat.db / .yanyu 解密与写出
  memscan/   内存：adb / maps / dump / 候选提取 / 爆破
  unpack/    解包：.whale 容器 / res_encrypt / APK / PE(exe)
  reverse/   逆向辅助：ELF 符号、反汇编、调用点扫描
  report.py  输出文档（Markdown + JSON）
  model.py   跨层共享的数据类

两层接口
────────
  第一层  yyjh.layer1  ← 分块基础功能，出问题可以逐块单独调
                        python -m yyjh <块> <动作> [参数]
  第二层  yyjh.layer2  ← 整合管道，一步出文档
                        python -m yyjh run <抓包或存档> [--outdir DIR]
  入口层  yyjh.wizard  ← 纯使用向导（选模式 → 导入 → 打开网页版 web/）
                        python run.py
"""
import sys

# 版本守卫必须放在包的 __init__ 里，不能放在 __main__.py：
# `python -m yyjh` 会先执行本文件，再去编译 __main__.py，
# 而 __main__.py 里有 f-string —— 在 Python 2 下**解析期**就是 SyntaxError，
# 写在那里的守卫根本跑不到。本文件只用 2/3 都认的语法。
if sys.version_info < (3, 8):
    if sys.platform == 'win32':
        try:                      # 先把控制台切成 UTF-8，否则中文提示是乱码
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

__version__ = '1.1.0'

__all__ = ['__version__']
