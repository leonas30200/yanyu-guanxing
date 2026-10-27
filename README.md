# 简易使用说明

观星工具，已适配游戏版本 **1.125.73492**（2026-10-23 更新），旧版本（2026-10-03 及更早）同样可用。

> 本项目基于 [yanyu-amateur/yanyu-guanxing](https://github.com/yanyu-amateur/yanyu-guanxing) 修改而来（衍生作品），
> 73492 的适配改动见 [`本次更新内容.md`](./本次更新内容.md)，版权与许可说明见 [`NOTICE.md`](./NOTICE.md)。

## 安装python

[python.org](https://www.python.org) 官网选择任一大于等于的3.8版本。

开发版本为3.11.11，可选 [https://www.python.org/downloads/release/python-3119](https://www.python.org/downloads/release/python-3119/)。

安装时记得勾选 "Add Python to PATH"，方便直接在命令行调用。

## 安装依赖

可选安装依赖，优化视图和爆破速度。
如果第三方库下载较慢，请自行查询如何修改channels使用镜像下载，或者直接跳过此步。

win + R —— cmd —— 回车，打开命令行

```bash
# 假设你的项目文件夹在D:/yanyu-guanxing
cd /d D:/yanyu-guanxing
pip install -r requirements.txt
```

## 使用方法

### 快速启动

```bash
cd /d D:/yanyu-guanxing
python run.py
```

后续按照指引进行操作即可

### 获取存档

仅限安卓，确定可用版本为 MuMu==6.8.7 ，其他版本不保证能跑通。需要十分钟左右破解密钥。

### 抓包

安卓和iOS均可

- 模拟器请安装MT管理器，并授予管理所有文件权限
- 安装免费开源抓包工具 ProxyPin ，iOS直接去应用商店即可，安卓最好访问 [github官方仓库](https://github.com/wanghongenpin/proxypin) 的 releases 下载

抓包流程

1. 进入游戏，回到主界面，在主界面退出登录
2. 打开proxyin，开启抓包（无需开启HTTPS代理）
3. 打开游戏，登录（qq/微信/邮箱），进入区服，上传一次存档，**再点一次「下载存档」**
   （73492 起必须要有这一步 —— 上传请求已换成 RSA 封装、抓包解不开，程序只能从服务端下发的存档里取数）
4. 回到proxypin，停止抓包
5. proxypin右上角三点 —— 视图导出 —— （新版本要选择HAR格式）
   模拟器需要配合MT管理器，将结果导出至文件系统，并在文件系统中复制到MuMu共享文件夹
   即，选中右侧界面，点左上角三横 —— MuMu共享文件，长按左侧ProxyPinMM-DD_HH_MM_SS.har文件，复制过去
   打开电脑的MuMu共享文件夹，一般在`C:\Users\xxx\Documents\MuMu共享文件夹`
6. 将导出的结果传输到电脑，然后启动程序`python run.py`进行解析

## 其他说明

1. 游戏本体版本更新后，密钥需要重新挖取，。
2. 密钥至少官服互通，iOS可以在电脑上装一个模拟器，创一个号然后使用本工具挖密钥，当然挖不出来也可以来仓库看看作者更了没有。[`keys.json`](./keys.json)
3. 本项目本质自用工具，web网页部分使用的是免费本地版作者的某个版本，一般不会更新，想使用更新功能，可以将本工具导出的存档导入他的网页使用
4. 本项目不涉及代码和游戏文件修改，免费不联网，仅供免费学习交流，倒狗biss。
5. 本项目完全由deepseek V4.1 flash完成，很多冗余和混乱的设计和架构，后续考虑手动优化整体的架构和使用方式。
6. 在assets文件夹中放入未绑定账号版本的shute存档，[`烟雨江湖shute(第六版).zjs`](assets/烟雨江湖shute(第六版)_未加密.zjs)，可直接导入自动精灵中使用。

## 更新

1. v1.1.261003，修复了一些已知bug，保证在版本更新时能自动挖取新的密钥，并更新了新的密钥。
2. v1.1.261023，**适配游戏 73492**：抓包路线改走"下载存档"响应兜底（73492 起 `upload3` 换成 RSA-2048 封装，抓包解不开），
   新增 iOS / 安卓 73492、73460 共 4 条内层密钥，打通安卓本地存档 73492，并修复平台误判、
   上传请求识别过宽、密钥混记、adb 设备发现共 4 个 bug。详见 [`本次更新内容.md`](./本次更新内容.md)。
