# -*- coding: utf-8 -*-
"""统一入口。

════════════════════ 给使用者 ════════════════════
从抓包 / 存档 / 明文 JSON 导出「初版 dat.db」，一步出结果：

    python -m yyjh start                   ★ 向导：选模式 -> 导入 -> 打开网页版
    python -m yyjh capture  <抓包.har>      抓包结果  -> 初版 dat.db
    python -m yyjh local                    去模拟器上取存档 -> 初版 dat.db
    python -m yyjh local    <dat.db>        本地存档  -> 初版 dat.db
    python -m yyjh rebuild  <存档.json>     明文 JSON -> 初版 dat.db
    python -m yyjh auto     <任意文件>      自动判断该用哪个
    python -m yyjh batch    <目录>          批量处理

    （capture / local 会自动去游戏内存里取"外层密钥"，需要游戏正在运行）

════════════════════ 给开发者 ════════════════════
-v 看完整流程（放在命令任意位置都行），--log 存 UTF-8 日志。
逐块调试用下面这些子命令：

    info     自动识别文件类型
    xxtea    XXTEA 加解密 / 爆破
    key      密钥表查询 / 补录
    whale    .whale 容器（游戏 Lua 打包格式）
    res      res_encrypt 资源（明文 Lua 源码）
    apk      APK 解包 / 字符串
    exe      exe/PE 字符串与**内层密钥表**提取
    db       dat.db 读写 / 解密 / 结构对比
    har      抓包 HAR 概览 / 解密
    mem      内存：adb / maps / dump / 采集候选 / 爆破外层密钥
    elf      .so 逆向：符号 / 反汇编 / 调用点 / 字符串引用
"""
import argparse
import os
import sys

# 版本守卫在 yyjh/__init__.py 里（本文件含 f-string，Python 2 下解析期就报错，
# 写在这里的守卫根本跑不到）。

from . import __version__, layer1, layer2, ui, wizard

# -v/-q 允许出现在任意位置：先摘出来，再交给 argparse
_VERBOSE_FLAGS = ('-v', '--verbose')
_QUIET_FLAGS = ('-q', '--quiet')


def _pre_extract(argv):
    """把 -v / -q / --log 从任意位置摘出来，这样放命令前后都能用。"""
    verbose = quiet = False
    logfile = None
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in _VERBOSE_FLAGS:
            verbose = True
        elif a in _QUIET_FLAGS:
            quiet = True
        elif a == '--log' and i + 1 < len(argv):
            logfile = argv[i + 1]
            i += 1
        elif a.startswith('--log='):
            logfile = a.split('=', 1)[1]
        else:
            rest.append(a)
        i += 1
    return rest, verbose, quiet, logfile


def build_parser():
    ap = argparse.ArgumentParser(
        prog='yyjh',
        description='烟雨观星 存档研究工具集 v%s' % __version__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument('-V', '--version', action='version',
                    version=f'yyjh {__version__}')
    ap.add_argument('-v', '--verbose', action='store_true',
                    help='显示完整流程与错误堆栈（放在命令后面也有效）')
    ap.add_argument('-q', '--quiet', action='store_true', help='只输出错误')
    ap.add_argument('--log', metavar='FILE', help='同时写一份 UTF-8 日志到文件')

    sub = ap.add_subparsers(dest='cmd', metavar='<命令>')
    wizard.add_parsers(sub)
    layer2.add_parsers(sub)
    layer1.add_parsers(sub)
    return ap


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    argv, verbose_pre, quiet_pre, logfile_pre = _pre_extract(argv)

    ap = build_parser()
    if not argv:
        ui.setup_console()
        # 交互环境下什么都不给 = 想要向导；管道/脚本里还是照旧打帮助
        if ui.is_tty():
            argv = ['start']
        else:
            ap.print_help()
            return 0
    else:
        # 只给了向导的参数（比如 `run.py --outdir x`）也当成 `start` 的
        argv = wizard.with_default_start(argv, ap)
    args = ap.parse_args(argv)

    verbose = bool(getattr(args, 'verbose', False)) or verbose_pre
    quiet = bool(getattr(args, 'quiet', False)) or quiet_pre
    logfile = logfile_pre or getattr(args, 'log', None)

    ui.setup_console()
    ui.configure(verbose=verbose, quiet=quiet, logfile=logfile)
    if logfile:
        ui.info(f'日志: {os.path.abspath(logfile)}')

    func = getattr(args, 'func', None)
    if func is None:
        ap.print_help()
        return 1

    # 老的单文件 keys.user.json -> 拆成 keys.json（可分享）+ keys.local.json（本机）
    # 只在第一次需要时做一次，原文件改名保留、内容不丢。
    try:
        from .config import keytables
        note = keytables.migrate_legacy()
        if note:
            ui.info(note)
            ui.info()
    except Exception:                                  # noqa: BLE001
        pass

    try:
        rc = func(args)
        return rc if isinstance(rc, int) else 0
    except ui.Cancelled:
        ui.info('已取消，没有生成任何文件。')
        return 0
    except KeyboardInterrupt:
        ui.info()
        ui.warn('已中断')
        return 130
    except ui.UserError as e:
        ui.report_error(e, ui.is_verbose())
        return 2
    except FileNotFoundError as e:
        ui.report_error(ui.UserError(
            f'找不到文件：{e.filename or e}',
            ['检查路径是否正确；路径带空格时记得加引号']), ui.is_verbose())
        return 2
    except Exception as e:                       # noqa: BLE001
        ui.report_error(e, ui.is_verbose())
        return 1


if __name__ == '__main__':
    sys.exit(main())
