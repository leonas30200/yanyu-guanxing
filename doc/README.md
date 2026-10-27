# 烟雨观星

<p>
<img alt="python" src="https://img.shields.io/badge/python-3.8%20%7C%203.11-blue">
<img alt="platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey">
<img alt="deps" src="https://img.shields.io/badge/deps-%E4%BB%85%E9%9C%80%E6%A0%87%E5%87%86%E5%BA%93-success">
</p>

把存档从 **抓包（HAR） / 设备内存 / 明文 JSON** 中获取，并观星。
可输出成第三方直接读取的格式。全程本地运行，不联网。

- 面向使用者的手册：[`使用说明.md`](使用说明.md)
- 网页版模拟器：[`web/`](web/) —— 基于免费本地版作者的页面，可脱离本工具独立使用。修复了原版在Firefox等浏览器中跨源存档不共享，导致切换页面时无法正确使用的问题，
- 更新后更改了序列生成方式，请使用新版观星页面，旧版工具得到的序列与实际不符
- 仅当前版本可用，开发者为业余爱好者，不定期更新
- 本项目使用Deepseek V4.1 Flash完成，文档均为自动生成

---

## 目录

- [特性](#特性)
- [环境要求](#环境要求)
- [安装](#安装)
- [快速开始](#快速开始)
- [命令行接口](#命令行接口)
- [输出结构](#输出结构)
- [项目结构](#项目结构)
- [配置](#配置)
- [工作原理](#工作原理)
- [游戏更新后怎么办](#游戏更新后怎么办)
- [开发](#开发)
- [已知陷阱](#已知陷阱)
- [免责声明](#免责声明)

---

## 特性

- **多来源输入**：抓包文件、设备上的存档、本地 `dat.db`、明文 JSON，自动识别格式
- **零配置设备发现**：adb 路径、设备、游戏包名（渠道）全部自动探测，结果记入 `keys.user.json`
- **无需渠道知识**：同设备装了多个渠道时，用 `save.id` 反查即可定位，不要求用户知道自己是哪个渠道（此条未实际验证渠道服）
- **内存取密钥**：直接读 `/proc/<pid>/mem` 爆破外层密钥（不依赖 frida，模拟器上更稳定）
- **输出即插即用**：默认输出早期 v1.8 版本加密格式，方便其他工具使用
- **可逐层调试**：内外两层接口，游戏更新后可按层定位到具体失效的一环
- **依赖可选**：核心功能只用标准库，第三方包缺失时自动降级

---

## 环境要求

| 项       | 要求                                                                           |
| -------- | ------------------------------------------------------------------------------ |
| Python   | **最低 3.8**；**建议 3.11**（开发与验证使用的版本）                |
| 第三方包 | 无硬依赖 —— 核心功能只用标准库                                               |
| 系统     | Windows                                                                        |
| 其它     | 从模拟器取档需要 adb（自动发现，见[配置](#配置)）；iOS 必须走抓包路线，无需越狱 |

假设你的 Python 3.11 位于 `D:\py311\python.exe`。下面的命令以此为例：

```powershell
# 方式一：先切环境
cd yanyu-guanxing
# 方式二：直接写全路径（不用切环境）
D:\py311\python.exe run.py
```

---

## 安装

```powershell
git clone <repo-url>
cd yanyu-guanxing
# 依赖全部可选，不装也能跑（缺哪个就降级哪项功能）
pip install -r requirements.txt
```

若使用 conda：

```powershell
conda create -n py311_yanyu python=3.11
conda activate py311_yanyu
pip install -r requirements.txt
```

`requirements.txt` 中每一项都是可选增强：

| 包                            | 作用           | 不装后果                                |
| ----------------------------- | -------------- | --------------------------------------- |
| `tqdm`                      | 进度条         | 降级为每 10% 输出一行日志               |
| `xxtea`                     | XXTEA C 扩展   | 降级为纯 Python 实现（爆破慢约 100 倍） |
| `pyelftools` + `capstone` | `elf` 子命令 | 仅`elf` 子命令不可用                  |

---

## 快速开始

```powershell
# 向导模式：选模式 → 导入存档 → 用浏览器打开网页版
D:\py311\python.exe run.py

# 从模拟器/设备上取档（不给路径 = 自动去设备上找）
D:\py311\python.exe run.py local

# 从抓包文件导出
D:\py311\python.exe run.py capture "抓包结果.har"
```

结果输出到 `output/<平台>-<区服>-<用户名>/<时间戳>/`，详见 [输出结构](#输出结构)。

---

## 命令行接口

统一入口为 `python -m yyjh <命令>`，`run.py` 是免 `cd` 的等价启动器。
不带子命令时进入向导。

### 外层接口（使用者入口）

| 命令                        | 输入                         | 输出   |
| --------------------------- | ---------------------------- | ------ |
| `capture <抓包.har>`      | HAR                          | dat.db |
| `local [dat.db]`          | 本地 /**设备上**的存档 | dat.db |
| `rebuild <存档.json>`     | 明文 JSON                    | dat.db |
| `auto <任意文件>`         | 按扩展名自动选择             | 同上   |
| `start`（别名 `guide`） | 向导                         | —     |

`local` 的四种输入：

```powershell
python -m yyjh local                                                  # 自己去设备上找并拉取
python -m yyjh local "dat.db"                                         # 本地文件
python -m yyjh local "/data/data/…/server12/dat.db"                   # 设备绝对路径
python -m yyjh local "127.0.0.1:7555:/data/data/…/server12/dat.db"    # 指定设备
```

设备路径的识别在 `config/paths.parse_device_path()`，需排除 Windows 盘符
（`C:\x` 视为本地，`/data/x` 视为设备）；`strip_quotes()` 处理拖拽文件产生的包裹引号。

### 日志与详细程度

```powershell
python -m yyjh local "dat.db"          # 默认：只输出结果与输出路径
python -m yyjh local "dat.db" -v       # 详细：打印每一步（DEBUG 级）
python -m yyjh local "dat.db" -q       # 只输出错误
python -m yyjh local "dat.db" --log run.log   # 另写一份 UTF-8 日志
```

`-v` / `-q` / `--log` 放在命令前后都有效（入口会先从 argv 中摘出）。

### 内层接口（分块调试）

游戏更新后按此顺序逐块验证，哪一步对不上就修哪一步：

```powershell
python -m yyjh info    <文件>              # 0. 识别文件类型

python -m yyjh whale   info|dump|find      # 1. 游戏资源（.whale 容器，明文 Lua）
python -m yyjh res     dump|strings        #    res_encrypt 资源
python -m yyjh apk     list|unpack|strings #    APK
python -m yyjh exe     info|strings|needle|tables   # tables = 提取内层密钥表

python -m yyjh key     list|lookup|add|outer        # 2. 密钥表

python -m yyjh xxtea   selftest|check|decrypt|encrypt|crack   # 3. 最底层算法

python -m yyjh db      show|decrypt|pragma|diff|create        # 4. dat.db
python -m yyjh har     info|list|decrypt                      # 5. 抓包

python -m yyjh mem     devices|procs|maps|scan|dump|harvest|crack   # 6. 内存

python -m yyjh elf     info|syms|disasm|calls|strref          # 7. .so 逆向
```

---

## 输出结构

只有一种结构（没有 `--flat` 之类的变体）：两层目录 + 固定文件名。

```
output/
├── ios-s3-示例角色乙/
│   └── 20261001-160805/          ← yyyymmdd-hhmmss
│       ├── dat.db                ← 初版 dat.db（与官方同构；content 默认 v1.8 格式）
│       ├── dat.json              ← 解密后的完整存档
│       ├── dat.meta.json         ← 元信息（账号 / 版本 / 密钥 / 格式 / 来源 / snapshot_dir）
│       └── dat.report.md         ← 可选，加 --report 才有
├── android-s12-示例角色/
│   └── 20261001-165334/
│       └── ...
└── _cache/                       中间产物（内存候选 heap_keys.bin），不是结果
```

路径规则见 `report.snapshot_dir()`：

| 参数           | 目录                                          |
| -------------- | --------------------------------------------- |
| （默认）       | `<outdir>/<平台>-<区服>-<用户名>/<时间戳>/` |
| `--outdir D` | `D/<平台>-<区服>-<用户名>/<时间戳>/`        |
| `--name X`   | `<outdir>/X/<时间戳>/`（只换第一层目录名）  |

- 第一层：平台（`android` / `ios`）+ 区服（`report.server_tag()` → `s12`，读不到则为 `s0`）
  + 角色名（`MainRole.Name`，为空则为 `未知`）
- 第二层：`%Y%m%d-%H%M%S`；同名冲突时在**目录名**上加 `-2` / `-3`，内层文件名保持不变
- 快照目录内文件名固定，永远是 `dat.db`
- **账号不进任何文件名**（邮箱 / QQ / 微信号等均属敏感信息），只写在 `dat.meta.json` 中

### `--save-format`

| 值              | 内层密钥                        | 可读者                             |
| --------------- | ------------------------------- | ---------------------------------- |
| `v18`（默认） | 平台固定密钥（按 runtime 选择） | **解析网站**、本工具         |
| `current`     | 按存档版本的内层密钥            | 仅本工具（工具需自身升级后才能读） |

两个方向的读写都支持：`savefmt.inner_candidates()` 中同样包含平台密钥，
因此从工具 / 网站拿回的 v1.8 文件（即使版本号不在表内）本工具也能读取。

### capture / local 的「内存步骤」

外层密钥**每账号一把、随机、只存在于游戏进程内存中**，无法推导。

| 命令        | 是否需要                                                                                   |
| ----------- | ------------------------------------------------------------------------------------------ |
| `local`   | **必需** —— 解新版 `dat.db` 必须先拿到它（扫内存爆破）                           |
| `capture` | 非必需 —— 抓包已能顺带挖出账号的外层密钥并记入`keys.user.json`，之后解本地存档无需再扫 |
| `rebuild` | 不需要 —— 重新加密用的是「版本 + 渠道」对应的固定内层密钥                                |

```
--mem auto    默认：keys.user.json 中无记录时才扫
--mem force   强制重扫
--mem off     跳过
```

`capture` 找不到设备 / 进程时只警告，不影响输出；`local` 会给出三种替代方案。

---

## 项目结构

```
yanyu-guanxing/
├── run.py                    便捷启动器（不带子命令 = 向导）
├── README.md                 本文（开发者文档）
├── 使用说明.md                使用者手册
├── requirements.txt          可选依赖
├── keys.user.json            自动生成：挖到的密钥、用过的设备
├── output/                   所有生成物（_cache/ 为中间产物）
├── web/                      网页版模拟器（独立可用）
├── dev/                      自检 / 校验脚本
└── yyjh/
    ├── __main__.py           统一入口（含 Python 版本守卫）
    ├── wizard.py             向导层：选模式 → 导入 → 打开网页版
    ├── layer2.py             外层接口：capture / local / rebuild / auto / batch
    ├── layer1.py             内层接口：分块基础功能
    ├── ui.py                 控制台输出（logging + tqdm）
    ├── model.py              跨层数据类（SaveResult）
    ├── report.py             输出生成（json / meta / db / 可选 report.md）
    │
    ├── config/               【配置】密钥与常量的唯一真源
    │   ├── constants.py        算法常量（XXTEA / 签名 / 标记 / 容器格式）
    │   ├── keytables.py        内层密钥表 + 渠道后缀 + 设备记忆
    │   ├── channels.py         渠道 ↔ 包名表 + 自动探测
    │   └── paths.py            路径解析（adb / serial 仅作可选覆盖）
    │
    ├── core/                 【基础算法】纯函数、无 IO
    │   ├── xxtea.py            XXTEA 加解密 + 长度字预言机
    │   ├── codecs.py           base64 / zlib / ROL3 / 循环 XOR
    │   └── oracle.py           爆破引擎
    │
    ├── game/                 【游戏结构】
    │   ├── protocol.py         抓包协议密钥推导（key0 / innerK / realKey）
    │   ├── savefmt.py          存档各格式的识别 / 解封 / 构造
    │   └── player.py           save.player 明文摘要
    │
    ├── capture/              【抓包】HAR 解析与解密
    ├── localdb/              【本地存档】dat.db 读写 / 解密 / 从设备取档
    ├── memscan/              【内存】adb 发现 / 设备选择 / maps / dump / 爆破
    ├── unpack/               【解包】.whale / res_encrypt / APK / PE(exe)
    └── reverse/              【逆向】ELF 符号 / 反汇编 / 调用点 / 字符串引用
```

**分层规则**：上层可调用下层，**下层绝不反向 import 上层**；跨层只传递 `model.py` 中的数据类。
哪一层出问题就单独调那一层。

---

## 配置

### 环境变量

全部可选，不设置也能正常运行：

| 变量               | 用途                   | 不设置时                      |
| ------------------ | ---------------------- | ----------------------------- |
| `YYJH_WORKSPACE` | 数据目录所在的工作区根 | `yanyu-guanxing` 的上级目录 |
| `YYJH_ADB`       | 强制指定 adb 路径      | 自动发现（见下）              |
| `YYJH_SERIAL`    | 强制指定设备 serial    | 自动选择装了游戏的那台        |

**编码**：入口会自动把 Windows 控制台切到 UTF-8（65001）并把 stdout 重配为 utf-8。
`--log` 写出的日志固定为 **UTF-8 无 BOM**（PowerShell 5.1 的 `>` 重定向会写成 UTF-16，不要用它重定向）。

### `keys.user.json`

工具自动维护，也可以手工编辑（无需改代码）：

```json
{
  "inner_keys": [
    {"runtime": "android", "version": "00074000", "key": "abcdefghijklmnop"}
  ],
  "outer_keys": {
    "0123456789ABCDEF0123456789ABCDEF": "0123456789abcdef"
  },
  "outer_keys_by_id": {
    "0123456789abcdef0123456789abcdef": "0123456789abcdef"
  },
  "adb": "D:\\platform-tools\\adb.exe",
  "serial": "127.0.0.1:7555",
  "pkg": "com.lanjing.yanyu.lanjing"
}
```

| 键                             | 作用                                          |
| ------------------------------ | --------------------------------------------- |
| `inner_keys`                 | 补充新版本的内层密钥                          |
| `outer_keys`                 | 外层密钥，按**账号**索引                |
| `outer_keys_by_id`           | 外层密钥，按 **save_id（md5 账号）**索引      |
| `adb` / `serial` / `pkg` | 上次使用的环境，自动带上，省去每次敲`--pkg` |

存在两套外层密钥索引的原因：`local` 流程一开始**只知道 save_id**
（`dat.db` 中只存 `md5(账号)`），解密成功后才会补上账号。

---

## 工作原理

### 存档格式

```
初版(old)： content = base64( 版本号(8) + XXTEA( zlib(json), 内层密钥 ) )
新版(new)： content = marker(8) + base64( XXTEA( X, 外层密钥 ) )
         其中 X = base64( 版本号(8) + XXTEA( zlib(json), 内层密钥 ) )
v1.8     ： content = base64( 版本号(8) + XXTEA( zlib(json), 平台固定密钥 ) )
         与初版同形状，区别仅在于内层密钥是固定的那几把
```

抓包解开外层后得到的正是 `X`，即初版 content，无需重新加密
（网络外层用的是服务器下发的 `realKey`，恰好替代了每账号随机的外层密钥）。

### v1.8：默认输出格式

`web/`（同时是 APK 中 `assets/web/` 那套）的判定条件**只看文件名**：

```js
// save_decrypt.js
if (/\.db$/i.test(name) && !/-v\d+(?:\.\d+)?\.(?:db|yanyu)$/i.test(name)
    && /* 文件名中不含且仅含一对 @8位@ */) return '1.8';
```

随后依次尝试**固定的平台密钥**：

```js
for (platform of _getV18PlatformOrder(version))          // 按版本号决定尝试顺序
    compressed = decryptXXTEA(cipher, SAVE_V18_PLATFORM_KEYS[platform])
```

平台密钥表位于 `config/constants.V18_PLATFORM_KEYS`（ios = `3598gjjdtekelaw9`，
android = `3598gjjdtekelad9` 等）。**密钥不由版本号推导**，因此版本号头写什么都行，
工具会试到正确的那把（以 zlib + JSON 双重校验）。

因此本工具的输出只需满足三条即可被直接读取：

1. `content` 用平台固定密钥加密（`savefmt.build_v18()`）
2. 文件名是 `dat.db`（匹配 `/\.db$/`，无 `-vX.Y` 后缀，无 `@8位@`）
3. 是合法 SQLite 文件（本来就是）

### 三把钥匙

| 名字               | 形态                             | 作用                   | 来源                                 |
| ------------------ | -------------------------------- | ---------------------- | ------------------------------------ |
| **realKey**  | `acc[:5] + "eakec" + 种子[:6]` | 解开抓包请求体         | 服务器在`k` 响应头中下发           |
| **内层密钥** | `psw(8) + suffix(8)`           | 解开存档正文           | **固定表**，按 版本 + 渠道 查  |
| **外层密钥** | 16 字符随机                      | 解开本地 dat.db 最外层 | **每账号一把**，只能从内存中挖 |

### 容易混淆的几套「码」

| 名字     | 示例                                                      | 用途                                                    |
| -------- | --------------------------------------------------------- | ------------------------------------------------------- |
| 渠道后缀 | `po06ewkx` / `cgkmi4cq` / `yoqkmrtj` / `meeo0wmk` | **内层密钥的后 8 位**                             |
| 解析码   | `e082ac1d92b6c07f`                                      | `.yanyu` 旧版容器的外层密钥（**与本工具无关**） |
| 外层标记 | `dnkemiea`                                              | 新版 content 开头的固定 8 字符                          |

**不支持 `.yanyu` 旧版导出容器**（`yanyu-@<解析码>@-@<渠道>@-…-v2.1.0.yanyu`）：

### 渠道与包名（此块未实际渠道服验证）

各渠道服只是**包名不同**（`com.lanjing.yanyu.lanjing` / `.huawei` / `.m4399` …），
存档格式与密钥算法完全一致。映射表在 `config/channels.py`（17 个渠道），
运行时直接扫描设备上**已安装的包**，因此：

- 未收录的渠道同样可用 —— 只要设备上装了且包名可被识别
- 自动探测依据包名特征（`lanjing` / `yanyu` / `smqy` …），并排除第三方工具包
  （`exporter` / `yystars` / `tool` / `assist` …，见 `_TOOL_HINTS`）
- 识别失败时用 `--pkg <渠道名 | 完整包名>` 手动指定

### 同设备多渠道的选择顺序（`devices.choose_game()`）

模拟器上同时装多个渠道是常见情况，而用户通常不知道自己在玩哪个渠道。选择顺序：

1. `--pkg huawei` 显式指定
2. **按 `save.id` 反查** —— `localdb.device.find_save_by_id()` 把 `md5(账号)`
   （`capture` 从账号算得，`local` 从 `dat.db` 的 `id` 列取得）交给设备 `grep -l`，
   哪个包的 `dat.db` 中含有该 id 即为目标包。**用户无需提供任何渠道信息**，是首选手段。

   ```
   grep -l <md5(账号)> "/data/data/*/files/*/server*/dat.db"
   ```

   判据是 `grep -l` 有无输出（它输出的是文件名，不是命中的字符串）。
3. `keys.user.json` 中上次用过的（`pkg` 键），选定后即记住
4. 只有一个正在运行的 → 用它
5. 多个候选 + 交互式终端 → 列出编号菜单：

   ```
   这台设备上装了 3 个渠道的游戏，请选一个（你正在玩的那个）：
     [1] 官服      com.lanjing.yanyu.lanjing    运行中(17695)  服 12/20  2 个存档
     [2] 华为      com.lanjing.yanyu.huawei                     服 12     1 个存档
     [3] B站       com.lanjing.yanyu.bilibili                   服 7      1 个存档
   ```

   `Game.line()` 会带上**服号**与**存档数**，便于仅凭包名认不出时判断。
6. 兜底：取第一个；非交互环境（`sys.stdin` 非 tty）或 `--no-pick` 时直接走这条

### 交互菜单约定（`ui.choose()`）

选渠道与选存档共用同一套菜单：

| 输入                        | 行为                                                                   |
| --------------------------- | ---------------------------------------------------------------------- |
| 直接回车                    | 使用默认项（存档选择中默认 =**最近改动的那个**）                 |
| 编号                        | 使用该项                                                               |
| `q` / `quit` / `exit` | 抛`ui.Cancelled`，入口接住并退出，**不生成任何文件**，退出码 0 |
| 其它字符                    | 提示后回退到默认项                                                     |
| `Ctrl+C`                  | 原样抛出`KeyboardInterrupt`，入口返回 130                            |

`Ctrl+C` 一律中止、不替用户做决定：早期版本将其 catch 后返回默认项，
会导致用户以为已取消、实际文件已经生成。

存档候选会显示**最后修改时间**（`stat -c %Y`，同时检查 `dat.db-wal`，取两者中较新的），
并按新到旧排序 —— 同一模拟器上常堆积多个旧存档，时间是最可靠的判断依据。

再上一层，`pick_game_device()` 负责在**多台设备**中选择：逐台 `list_games()`，
若有 `save_id` 则在每台上尝试反查，命中即返回；都未命中则回到第一台装了游戏的设备。

### adb 自动发现（`find_adb()`）

按以下顺序查找：

1. `--adb` 参数
2. `YYJH_ADB` 环境变量
3. `keys.user.json` 中的 `adb`（上次使用过的）
4. `shutil.which('adb')`（系统 PATH）
5. 19 个常见安装路径（各模拟器自带 + platform-tools + Android SDK，见 `_ADB_CANDIDATES`）
6. `_search_platform_tools()`：在 `C:\`、`D:\`、桌面等有限深度内浅层搜索

`Adb.online()` 在配置的 serial 不在线时会自动回退到 `rank()` 的第一台，不会直接报错。

不使用 frida 的原因：在 MuMu 上直接读 `/proc/<pid>/mem` 更稳定，
且外层密钥是明文 16 字符，爆破无需注入（详见 `memscan/crack.py`）。

## 游戏更新后怎么办

| 步骤 | 命令                            | 说明                                  |
| ---- | ------------------------------- | ------------------------------------- |
| 1    | `yyjh mem crack --db <db>`    | 外层密钥变更时（约 3 分钟，带进度条） |
| 2    | `yyjh whale dump` → 查看 Lua | 仅当存档格式整体变更时才需要          |

---

## 开发

### 自检脚本

`dev/` 下的脚本均为自包含，退出码非 0 即失败：

| 脚本                       | 验证内容                                                                                                                                                                        |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `check_xxtea.py`         | 纯 Python 与 C 扩展的 XXTEA**逐字节一致**                                                                                                                                 |
| `check_v18.py`           | 把存档工具`save_decrypt.js` 的三个函数照抄成 Python，验证 `output/` 下的 `dat.db` 能被认成 v1.8 并解开（同时验证 `--save-format current` 的产物**故意**不被识得） |
| `check_channels.py`      | 多渠道选择逻辑（`--pkg` / save.id 反查 / 运行中 / 非交互兜底）                                                                                                                |
| `check_tool_compat.py`   | 从工具源码提取版本表，逐档诊断兼容性                                                                                                                                            |
| `check_version_field.py` | 版本号字段                                                                                                                                                                      |
| `check_wizard.py`        | 向导层各分支                                                                                                                                                                    |
| `verify_outputs.py`      | 生成物与初版格式比对                                                                                                                                                            |

运行：

```powershell
python dev\check_xxtea.py
python dev\verify_outputs.py
```

`verify_outputs.py` 的输出示例：

```
结构 vs 官方: ✅   表定义 / 自动索引 / page_size(1024) / encoding / 文件头 全一致
回读解密:    ✅   223 / 221 字段，角色名正确
```

| 接口        | 实测输入                | 结果                             |
| ----------- | ----------------------- | -------------------------------- |
| `capture` | iOS 抓包                | 223 字段                         |
| `capture` | 安卓抓包                | 221 字段                         |
| `local`   | `data/dat.db`（新版） | 外层密钥自动匹配                 |
| `rebuild` | 明文 JSON               | 往返自检：解密结果与输入完全一致 |

---

## 已知陷阱

1. **XXTEA 加密侧携带 `z`，解密侧携带 `y`** —— 两侧不对称，写错会「自己解不开自己」
   （`dev/check_xxtea.py` 即为此而写）。
2. **`sys.exit(元组)` 会打印整个对象且退出码变为 1** —— 入口统一把非 int 返回值视为 0。
3. **输出路径相对 CWD 解析，输入路径才去猜工作区** —— 分别使用
   `paths.resolve_out()` 与 `paths.resolve()`。
4. **设备端 shell 是 32 位有符号算术** —— `dd` 的 `skip` / `count` 必须在 PC 端算好后以十进制传入。
5. **PowerShell 5.1 的 `>` 重定向写出 UTF-16LE** —— 日志一律使用工具自带的 `--log`。
6. **读 `/proc/<pid>/mem` 需 `bs=1` 或按页对齐**，否则 scudo 堆会读到错位数据。
7. **纯字符串不能以反斜杠结尾** —— `r'C:\'` 是语法错误（`r'...\'` 会吞掉收尾引号），
   表示盘根要用 `'C:\\'`（见 `adb.py` 的 `_ADB_SEARCH_ROOTS`）。
8. **不要假设用户的 adb 路径 / serial** —— 早期版本硬编码了开发机路径，他人无法直接运行；
   现在全部自动发现 + 由 `keys.user.json` 记忆。

---

## 免责声明

本项目仅用于个人存档的备份、迁移与学习研究，不修改游戏数据、不提供任何游戏内优势。
请勿用于商业用途或对外传播他人存档。使用产生的一切后果由使用者自行承担。

## License

未附带许可证文件。如需转载或二次分发，请先联系作者。
