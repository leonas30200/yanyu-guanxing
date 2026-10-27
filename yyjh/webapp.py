# -*- coding: utf-8 -*-
"""网页版（web/）构建辅助。

目前只做一件事：把「自动洗脉」的**后台计算线程**打成单文件包，
让 `file://` 下也能跑。

为什么必须打这个包
──────────────────
`file://` 页面的源是 opaque origin；Firefox 默认 `privacy.file_unique_origin=true`
时每个文件还各自独立。于是浏览器会把这两件事都当成**跨源**而拒绝：

    new Worker('…/shared/wash_route_worker.js')          // Chrome: cannot be accessed from origin 'null'
    importScripts('…/shared/wash_source_optimizer.js')   // 在 worker 里同样被拒

把 worker 连同它 importScripts 的 4 个依赖整体拼成一个字符串、再用 **Blob URL**
起线程就绕开了 —— Blob 与创建者同源，整包里也没有任何需要再加载的外部脚本
（实测：真实 Chrome / Firefox 下均可用）。

产物 `web/shared/wash_route_worker_bundle.js`：
    1) `self.WASH_ROUTE_WORKER_BUNDLE = {hash, files, src}`
    2) 就地安装 Worker 垫片：`file://` 下 `new Worker('…wash_route_worker.js')`
       自动改用 Blob（`http` 下不动，照旧加载原文件，改完即时生效）

改了 `SOURCES` 里任一源文件后要重新生成：跑一次 `python run.py`
向导会自动刷新，也可以手动跑 `python dev\\gen_worker_bundle.py`。
"""
import argparse
import hashlib
import json
import os
import re
import sys

# 拼接顺序 = worker 里 importScripts 的顺序，最后才是 worker 本体
SOURCES = ('sequence_rules.js', 'data.js', 'wash_route_engine.js',
           'wash_source_optimizer.js')
BODY = 'wash_route_worker.js'
OUT_NAME = 'wash_route_worker_bundle.js'

# 整包里没有别的脚本可加载，把 worker 的 importScripts 那行删掉
_IMPORT_RE = re.compile(r'^[ \t]*importScripts\s*\([^;]*\)\s*;?[ \t]*\r?\n', re.M)

# 垫片：只在 file:// 下生效
SHIM = """
/* ---- Worker 垫片：file:// 下改用内联 Blob ---- */
(function () {
    'use strict';
    var bundle = self.WASH_ROUTE_WORKER_BUNDLE;
    if (!bundle || !bundle.src || typeof self.Worker !== 'function') return;
    if (location.protocol !== 'file:') return;
    if (self.__yyjhWashWorkerShim) return;
    self.__yyjhWashWorkerShim = true;

    var NativeWorker = self.Worker;
    var blobUrl = null;

    function bundled() {
        if (!blobUrl) {
            blobUrl = URL.createObjectURL(new Blob([bundle.src], { type: 'text/javascript' }));
        }
        return blobUrl;
    }

    function Bridged(url, options) {
        if (String(url || '').indexOf('wash_route_worker.js') >= 0) {
            try { return new NativeWorker(bundled(), options); } catch (e) { /* 掉回原生 */ }
        }
        return new NativeWorker(url, options);
    }
    Bridged.prototype = NativeWorker.prototype;
    try { self.Worker = Bridged; } catch (e) { }
})();
"""


def build_source(shared_dir):
    """拼出整包源码。返回 (指纹, 源码, 参与拼接的文件名列表)。"""
    chunks = []
    used = []
    for name in SOURCES + (BODY,):
        path = os.path.join(shared_dir, name)
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        with open(path, encoding='utf-8') as f:
            text = f.read()
        if name == BODY:
            text = _IMPORT_RE.sub('', text)
        chunks.append('/*** %s ***/\n%s\n;\n' % (name, text.rstrip()))
        used.append(name)
    src = ''.join(chunks)
    return hashlib.md5(src.encode('utf-8')).hexdigest(), src, used


def write_bundle(shared_dir, force=False):
    """按需重新生成整包。返回 (产物路径, 是否真的重写了)。

    产物刻意写成**纯 ASCII**（非 ASCII 走 \\uXXXX 转义），
    这样 file:// 下不用担心脚本的编码嗅探。
    """
    digest, src, used = build_source(shared_dir)
    out = os.path.join(shared_dir, OUT_NAME)
    header = (
        '/* Generated file - do not edit by hand.\n'
        ' * Producer: yyjh/webapp.py  (or dev/gen_worker_bundle.py)\n'
        ' * Sources : %s\n'
        ' * Digest  : %s\n'
        ' */\n' % (' + '.join(used), digest)
    )
    payload = json.dumps({'hash': digest, 'files': used, 'src': src},
                         ensure_ascii=True, separators=(',', ':'))
    body = header + 'self.WASH_ROUTE_WORKER_BUNDLE = ' + payload + ';\n' + SHIM

    if not force and os.path.exists(out):
        try:
            with open(out, encoding='utf-8') as f:
                if f.read() == body:
                    return out, False
        except OSError:
            pass
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        f.write(body)
    return out, True


def default_web_dir():
    from .config import paths
    return os.path.join(paths.TOOL_DIR, 'web')


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='python -m yyjh.webapp',
        description='生成 web/shared/wash_route_worker_bundle.js（file:// 下自动洗脉用）')
    ap.add_argument('--web', help='网页版目录（默认 <工具目录>/web）')
    ap.add_argument('--force', action='store_true', help='无条件重写')
    args = ap.parse_args(sys.argv[1:] if argv is None else list(argv))

    web = args.web or default_web_dir()
    shared = os.path.join(web, 'shared')
    if not os.path.isdir(shared):
        print('找不到 %s —— 网页版目录不对？' % shared)
        return 1
    path, changed = write_bundle(shared, force=args.force)
    size = os.path.getsize(path)
    print('%s %s（%s 字节）' % ('已重新生成' if changed else '内容没变，跳过', path, format(size, ',')))
    if changed:
        print('  注意：web/auto/index.html 与 web/wash/index.html 需要引入这个文件，否则垫片不生效。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
