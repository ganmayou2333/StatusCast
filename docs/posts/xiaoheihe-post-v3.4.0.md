# 【软件】StatusCast v3.4.0 正式版：主机、手机、Mac、蓝牙设备，一行字全在状态栏

> 小黑盒发布稿 · 正式版
> 发布日期：2026-11-01 01:10（Asia/Shanghai）

> **归档说明**：这是发布时写的社区宣传稿，不是使用文档。正文里提到的
> `docs/screenshots/*.png`（五张）从未被提交进仓库，那几行只保留了当时的配图文件名。
> 用户文档见 [docs/guide/](../guide/)。

---

如果你在 OBS 里直播或录屏，可能见过这种小细节：画面角落有一行字，实时显示「手机电量、正在播放的歌、CPU 占用」。

StatusCast 就是做这件事的 Windows 纯后台小工具。它每 30 秒采集一次设备状态，通过本地接口输出，配合 NekoTool 在 OBS 里显示成一行文字。全部在本机运行，不需要账号，不上传任何数据。

这次 v3.4.0 是它发布以来最大的一次更新：**从「只看两台手机」，扩展成「手机 + 游戏主机 + Mac/Linux 小主机 + 蓝牙外设」，并且给每条连接都加了一条备用通道。**

---

## 这次更新了什么

### 一、游戏主机正式上线（PS5 / Xbox / Switch 等）

在配置里填上主机的局域网 IP 就行，不需要账号、不需要 token、不用在主机上装任何东西。

- 探测顺序：ARP → ping → TCP 端口，三级兜底
- 连续 2 次探测失败才判离线，1 次成功立即回在线，避免网络抖动误报
- 离线文案可以自己改，比如"已关机""待机中"
- 顺手记录 MAC 地址，为以后的远程唤醒留好接口

### 二、Mac / Linux 小主机

家里有 MacBook、Mac mini、软路由或者 Linux 小主机，可以走 SSH 通道：

- 一条命令拉回：电量、充电状态、机型、开机时长
- 只用密钥登录，首次连接自动记录指纹，之后指纹变化会被直接拒绝
- 主机睡眠或 SSH 不可达时，自动降级成局域网探活，仍然能显示在线/离线

### 三、蓝牙外设

耳机、手环、心率带这类 BLE 设备也能接进来：

- 读取公开的电量（0x180F）与心率（0x180D）服务
- 不配对、不写入设备，地址和名称只保存在本机配置里
- 设备名或地址二选一匹配；扫描不到、连不上都会如实标注，不会影响其它设备

### 四、备用通道（这次的重点）

每台设备现在都有一条「有序通道链」，主通道断了会自动试下一条，而不是整台设备从状态栏消失：

| 设备 | 通道链 |
| --- | --- |
| iPhone | USB →（实验）Wi-Fi → 局域网探活 |
| Android | adb → 无线调试自动发现/配对 → 局域网探活 |
| 主机 | ARP → ping → TCP 端口 |
| Mac / Linux | SSH → 局域网探活 |

降级时会明确告诉你「现在只能确认它在不在，拿不到电量」——状态栏如实显示，不装懂。

### 五、其它改进

- 新增 `/health` 探活接口，方便判断服务是否正常
- 修复了「日志里说有健康检查、实际却没有」的老问题
- 自动化测试从 0 补到 211 项（Python 170 + C# 41），新增两条 CI 流水线
- 建立 `schema/status-contract.json` 字段契约，Python 与 C# 两侧同时校验，避免字段各写各的
- 数据格式仍是 schema 3，旧配置直接兼容

---

## 效果预览

【配图 1】OBS 单行状态（真实运行 `ultimate_status.ps1`，末尾出现 🎮 主机在线/离线）
`docs/screenshots/v340-ultimate-line.png`

【配图 2】后台日志节选（`[API]` / `[CONSOLE]` / `[BLE]` / `[SNAPSHOT]`）
`docs/screenshots/v340-daemon-log.png`

【配图 3】`/status/` 响应节选（主机与蓝牙设备块，以及各自的 `transport`）
`docs/screenshots/v340-status-json.png`

【配图 4】v3.4.0 新增配置示例（`Consoles` / `SshNodes` / `BleDevices`）
`docs/screenshots/v340-config.png`

【可选】OBS 网页挂件（旧版截图，UI 未变）
`docs/screenshots/ui-widget.png`

---

## 怎么用

1. 准备：Windows 10/11，Python 3.10+（项目自带 `.venv` 说明），构建需要 .NET 10 SDK
2. 克隆仓库，按 README 建虚拟环境、安装依赖
3. 构建并启动 daemon
4. 配合 NekoTool 的 `ultimate_status.ps1`，在 OBS 里显示成一行文字

最简配置示例（`config.json`）：

```json
"Consoles": [
  {"id": "ps5", "name": "PS5", "type": "ps5", "ip": "192.168.1.50", "offlineText": "已关机"}
],
"SshNodes": [
  {"id": "macbook", "name": "MacBook", "platform": "macos",
   "user": "fly", "host": "192.168.1.60", "sshKey": "~/.ssh/id_ed25519"}
],
"BleDevices": [
  {"id": "airpods", "name": "AirPods Pro", "address": "AA:BB:CC:DD:EE:FF"}
]
```

不配置这些键也完全没问题，行为与上一个版本一致。

**升级注意**：更新后记得执行一次 `pip install -r requirements.txt`（新增了声明的 `bleak`）；如果你自己写了读取 `/status/` 的程序，主机离线现在不再写 `error`，改读 `console.online` 与 `console.statusText`。

---

## 已知限制（说在前面）

- 主机只能判断「在线 / 离线」，分不清「在玩」还是「挂机」，更细的展示留到下个版本
- iPhone 的 Wi-Fi 采集在当前 Apple 服务环境下仍不可用（实测诊断服务会被设备断开），继续用 USB
- 蓝牙与降级信息目前只写进 JSON，OBS 那行还没展示，下一版补
- 部分真机项目仍待验证（ARP 缓存时间、真实 MAC/唤醒、Linux 多电池、macOS 充电状态、BLE 真实读数等），清单公开在仓库里

---

## 下载与反馈

- StatusCast：GitHub 搜索 `ganmayou2333/StatusCast`
- NekoTool（OBS 展示层）：GitHub 搜索 `ganmayou2333/NekoTool`
- 遇到问题或有想接的设备，欢迎在 Issues 里提；能描述清楚「设备型号 + 系统版本 + 日志」最好

如果这篇对你有用，点个赞让更多做直播/录屏的朋友看到。下一版主要做两件事：把主机/蓝牙的信息真正显示到 OBS 行里，以及补完真机联调。
