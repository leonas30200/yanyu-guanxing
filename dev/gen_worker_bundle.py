# -*- coding: utf-8 -*-
"""重新生成 web/shared/wash_route_worker_bundle.js。

file:// 下浏览器把 worker 脚本当跨源，`new Worker('…wash_route_worker.js')`
会被拒；这个整包把 worker 连同它的依赖拼成字符串、改用 Blob 起线程。

改过 web/shared/ 里的优化器（wash_source_optimizer.js 等）后跑一次即可。
等价于 `python -m yyjh.webapp`。

    python dev\\gen_worker_bundle.py
    python dev\\gen_worker_bundle.py --force
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yyjh.webapp import main            # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
