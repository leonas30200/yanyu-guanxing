# 烟雨江湖 · 网页版工具（独立副本）

这是从游戏 APK 内置网页应用（`extracted/assets/web/`）**整份复制出来的独立副本**，
目的是脱离 APK 单独运行、便于后续自行魔改。

原始代码是压缩过的一体化产物，作者在 `shared/core_glue.js` 里明确写过
"The bundled kernel has no source/build project" —— 也就是说这份代码本身
就是"源码"，我们直接在上面改。

---

## 目录结构

```
yyjh_web/
├─ index.html            首页：导入存档、模块入口
├─ home.js / home.css    首页逻辑（含 handleImport）
├─ serve.py              本地静态服务器（推荐用这个启动）
├─ 启动.cmd              Windows 一键启动（调用 serve.py）
├─ mobile-bridge.js      APK 原生桥接；纯浏览器下第一行就 return
├─ mobile-capture.js     免 root 抓包导入；纯浏览器下不启用
├─ fonts/                内置字体（约 10.9 MB，目录体积主要来自这里）
├─ auto/                 自动洗练
├─ equip/                装备模拟（MakeRnd 打造序列）
├─ role/                 角色 / 天赋
├─ role-status/          角色属性
├─ wash/                 洗练
└─ shared/               全部核心逻辑
   ├─ save_decrypt.js        存档解密 + 页间共享存储（本次修复重点）
   ├─ sequence_rules.js      按版本号分派规则 + WASM 二进制补丁
   ├─ wasm_data.js           RNG 内核（base64 内嵌 WASM）
   ├─ core_glue.js           WASM 装载与导出包装
   ├─ equip_data.js          装备配置数据
   ├─ simulator.js / equip_auto_optimizer.js / ...
   └─ wash_route_worker.js   Web Worker（file:// 下不可用）
```

---

## 如何启动

### 方式一：直接双击 `index.html`（推荐）

可以用了。跨页传递存档由 `shared/page_bridge.js` 负责，详见下文《跨页传递存档》。

### 方式二：本地静态服务器（可选）

双击 **`启动.cmd`**，或者：

```cmd
cd yyjh_web
python serve.py
```

```cmd
python serve.py --port 9000     :: 换端口
python serve.py --no-browser    :: 不自动开浏览器
```

> `启动.cmd` 用 `py -3` / `python` 去找解释器。如果本机 `python` 指向 Python 2，
> 它就会找错版本 —— 直接用完整路径调用即可，例如
> `D:\Anaconda\envs\py311\python.exe serve.py`。

---

## 跨页传递存档：为什么需要 `page_bridge.js`

模块页面是 6 个独立的 html，靠跳转切换，而**存档（解出来 3.4 MB 的 JSON）必须跨页面传递**。
不同浏览器对 `file://` 的存储策略差别很大：

| 通道 | Chrome / Edge 的 `file://` | Firefox 默认设置的 `file://` |
| --- | --- | --- |
| `localStorage` / `sessionStorage` / `IndexedDB` | 所有 file:// 页面**同一个源**，可用 | **每个 html 文件独立源**（`privacy.file_unique_origin=true`），子页面完全看不见 |
| `cookie` | 禁用 | 禁用 |
| `window.name` | 跨源导航后**保留** | 跨源导航后被**清空** |
| URL `#hash` | 可用，但有长度限制 | 存档压完 **805 KB**，塞不进 URL |
| **`postMessage`** | 可用 | **可用** ← 唯一活着的通道 |

> 表里 Firefox 那两行是实测结论：同一个测试里 Chrome 跨源导航后 `window.name` 仍是
> `__MARK__`，Firefox 变成空字符串。而 `window.name` 被清空、`localStorage` 又被按文件隔离，
> 结果就是 —— **首页导入成功，点进洗练/装备页却提示「请先导入包含打造状态的有效存档」**。

所以最终方案是用 `postMessage` 搭一座桥，见下节。

另外两个 `file://` 老问题也一并处理了：

- 绝对路径 `/fonts/…` 会解析成 `file:///C:/fonts/…` → 403（已改成相对路径）；
- `new Worker()` 在 `file://` 下被拒，`wash_route_worker.js` 用不了（这个只能靠本地服务器）。

---

## 本副本相对原版修补了什么

### 1. 页间共享存档不再丢失（核心修复）

`shared/save_decrypt.js`：

- **`meta` 不再携带整行 save**。原来 `extra.row = ext.row` 会把 SQLite 的
  `content` 字段（整份存档正文，通常 1 MB 以上）塞进 meta，而 `data` 里
  已经有同样内容。一份存档因此在存储里占两份 —— 很容易撞上浏览器 5 MB 配额，
  写入失败后所有子页面都读不到。现在只保留 `{ id, player }`
  （真正用到 row 的 `buildV183OuterKeys()` 只读 `row.id`）。
- **meta 也会被压缩**，不再只压 `data`。
- **存储后端做成多级兜底**：`localStorage` → `sessionStorage` → `window.name`。
  写入时三者都写，读取时取第一个有值的；只要有一个成功就返回 `true`。
  这层在 Chrome / Edge 的 `file://` 下就够了（它们所有 file:// 页面同源），
  但**在 Firefox 默认设置下救不了** —— 所以才需要下面的 `page_bridge.js`。
- `clearShareStore()` 会同时清掉三个后端。
- 另外提供 `shareStoreRaw()` / `shareStoreApplyRaw()` 两个原始读写口，
  专供桥接层搬运字符串，避免把 3.4 MB 存档再 `JSON.parse` / `stringify` 一遍。

### 2. 新增 `shared/page_bridge.js`（Firefox 也能跨页）

- `index.html` 当**宿主**，常驻持有存档；
- 点击站内 `.html` 链接时不再跳转，改为在全屏 iframe 叠加层里承载子页面；
- 子页面每次加载完，宿主把存档 `postMessage` 推过去；
- 子页面把存档写进**自己源下**的存储，然后自动重载一次，让页面原有逻辑照常读到；
- 叠加层顶部有「← 返回存档页」；子页面里点「首页」也会自动关掉叠加层，
  回到那个从未被卸载、状态还在的首页。

为避免“先闪一下未导入再重载”，子页面在等宿主推送时会先隐藏文档，
最多等 1.2 秒；**没有存档时本脚本完全不介入**，链接照常跳转，行为与原版一致。

### 3. 绝对路径改为相对路径

6 个页面里的 `/fonts/google-fonts.css`、`/web/mobile-capture.js`、
`/web/mobile-bridge.js` 全部改成相对路径（子页面用 `../`），
`file://` 与本地服务器下都能正确加载。

### 4. 本地服务器（可选）

新增 `serve.py` + `启动.cmd`，见上文。

---

## 已知注意事项

- `role-status/` 页面**没有**引入 `shared/sql-wasm-embed.js`，因此它无法直接
  解析 `.db` 文件 —— 它只从页间共享存储读取数据（原版即是如此）。
- `mobile-bridge.js` / `mobile-capture.js` 是给 APK WebView 用的，
  纯浏览器下第一行就退出了，不会产生影响。
- `shared/core_glue.js` 在非 `file:` 协议下会先 `fetch('pkg/wasm_core_bg.wasm')`，
  本副本没有 `pkg/` 目录 → 404 后自动降级使用 `wasm_data.js` 里内嵌的
  base64 WASM。功能完全一致，只是控制台会有一条 warn。
- `fonts/` 约 10.9 MB，是目录体积的主要来源。
- 桥接层在 `file://` 下已可在 Chrome / Edge / Firefox 工作。若想在 HTTP 环境下
  复现 Firefox 的「子页面跨源」情形来测试，可在控制台设
  `window.YYJH_HOST_BASE = 'http://localhost:8765/'` 再点模块卡片 ——
  子页面会被加载到另一个源上，只有 `postMessage` 能救。

---

## 相关文档

- [`docs/序列生成机制.md`](docs/序列生成机制.md) —— 新版存档格式与随机序列的
  三层结构，以及要适配新游戏版本该改哪里。
