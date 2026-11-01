<div align="center">

# StatusCast

**纯后台的 iPhone / Android 状态采集引擎。**

[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D4?logo=windows)](https://www.microsoft.com/windows)
[![.NET](https://img.shields.io/badge/.NET-10-512BD4?logo=dotnet)](https://dotnet.microsoft.com/)
[![API](https://img.shields.io/badge/API-localhost%3A5099-22C55E)](http://localhost:5099/status/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## 项目简介

StatusCast 是 Windows 上的**底层设备数据层**。

它通过 **USB** 读取 iPhone 的电量、充电状态和机型，通过 **ADB** 读取 Android 的电量、充电状态、机型和媒体状态，然后：

- 每 30 秒刷新一次；
- 原子写入 `run/status.json`；
- 通过 `http://localhost:5099/status/` 提供纯 JSON API。

v3.3 在 v3.2 的纯 daemon 基础上，把 Python 采集层重构为**插件化 collector**：`status_core.py` 负责调度与 JSON 合并，iPhone / Android 分别封装在 `collectors/` 下。所有界面、聚合、插件和展示仍由 [NekoTool](https://github.com/ganmayou2333/NekoTool) 负责。daemon 启动时还会在后台检查 GitHub Release，发现新版本时打印彩色 `[UPDATE]` 提示（不会自动下载覆盖）。

## 架构

```text
iPhone --USB--> collectors/iphone_collector.py ─┐
Android --ADB--> collectors/android_collector.py ─┤
                                                   ▼
                                  status_core.py（调度 + JSON 合并）
                                                   │  标准 schema-3 JSON
                                                   ▼
                                       StatusCast.Daemon
                                       ├─ StatusService（JSON 解析）
                                       ├─ StatusCoordinator（别名 + 快照）
                                       ├─ run/status.json (schema 3)
                                       └─ HTTP API :5099
                                                   │
                                                   ▼
                                         NekoTool（展示 / 插件）
                                         ├─ NekoTrak
                                         ├─ OBS 文本源
                                         └─ OBS 浏览器控制台
```

> 当前只启用 USB / ADB 数据路径；iPhone Wi-Fi 预研结论见 [Roadmap](#roadmap)。

StatusCast 不再包含：

- WinUI 3 / Windows App SDK
- `IStatusPlugin`
- 动态插件 DLL
- 顶层 `battery` / `media` / `plugins` / `text`

## 目录结构

```text
StatusCast/
├─ collectors/                       # 插件化采集源
│  ├─ base.py                        # BaseCollector / CollectorError
│  ├─ iphone_collector.py            # iPhone USB 采集
│  ├─ iphone_wireless_test.py        # v3.4 Wi-Fi 预研脚本（不接入主流程）
│  ├─ android_collector.py           # Android ADB 采集
│  ├─ console_collector.py           # C1 主机 L0 局域网探测（ARP/ping）
│  └─ ssh_node_collector.py          # C2b SSH 节点档（Mac / Linux）
├─ src/
│  └─ StatusCast.Daemon/
│     ├─ StatusCast.Daemon.csproj    # <Version>3.3.0</Version>
│     ├─ Program.cs                  # 控制台入口、30 秒轮询、优雅退出
│     └─ Services/
│        ├─ ProjectLocator.cs
│        ├─ StatusService.cs         # 运行 status_core.py 并解析 JSON
│        ├─ StatusCoordinator.cs     # 别名 + 快照
│        ├─ StatusSnapshot.cs
│        ├─ StatusCastConfig.cs
│        ├─ StatusServer.cs          # HttpListener :5099
│        └─ UpdateChecker.cs         # GitHub Release 启动检查
├─ tests/                            # 标准库 unittest + fixtures（离线可跑）
├─ status_core.py                    # 主调度器：合并 collector JSON
├─ status_core_android.py            # 旧 STATUS 行格式兼容 CLI
├─ config.json                       # 自定义别名
├─ docs/
│  ├─ ble-ams-notes.md               # BLE/AMS 调研记录
│  └─ iphone-wireless-notes.md       # iPhone Wi-Fi 预研记录
├─ requirements.txt
├─ .github/
├─ CHANGELOG.md
├─ README.md
└─ run/                              # 运行时快照（不提交）
```

## 插件化采集源（面向开发者）

StatusCast 的 Python 采集层现在是插件式的：每个设备只需继承 `collectors.base.BaseCollector`，实现 `get_data() -> dict`，然后在 `status_core.py` 的 `collect_all()` 里注册即可。采集成功返回 schema-3 设备字典；设备不存在时抛 `CollectorError`，调度器会把错误写进该设备的 `error` 字段，**不会影响其它采集器**。

```python
from collectors.base import BaseCollector, CollectorError


class MyDeviceCollector(BaseCollector):
    platform = "my_device"
    device_id = "my_device_1"

    def get_data(self) -> dict:
        try:
            battery = read_my_device_battery()
        except Exception as exc:
            raise CollectorError(f"设备未连接：{exc}") from exc

        return {
            "id": self.device_id,
            "platform": self.platform,
            "model": "My Device",
            "batteryLevel": battery,
            "isCharging": None,
            "mediaTitle": None,
            "mediaArtist": None,
            "mediaState": "unknown",
            "foregroundApp": None,
            "foregroundAppName": None,
            "error": None,
        }
```

再在 `status_core.collect_all()` 的 `collectors` 列表中加入 `MyDeviceCollector()` 即可。daemon 端不需要新增代码：`StatusService` 会解析新的 `devices` 数组并按平台渲染。

## 局域网主机与节点（可选）

`config.json` 可声明家用主机（C1 · L0 局域网探测）、Mac / Linux 节点（C2b · SSH 节点档），以及 iPhone / Android 的备用通道参数（全部可选）：

```json
{
  "IPhone":  {"ip": "192.168.1.20", "fallback": true, "wifiDataChannel": false},
  "Android": {"ip": "192.168.1.21", "port": 5555, "pairPort": 0, "pairCode": "", "fallback": true},
  "Consoles": [
    {"id": "ps5", "name": "PS5", "type": "ps5", "ip": "192.168.1.50",
     "offlineText": "已关机", "tcpPorts": [9295, 3074], "fallback": true}
  ],
  "SshNodes": [
    {"id": "macbook", "name": "MacBook", "platform": "macos",
     "user": "fly", "host": "192.168.1.60",
     "sshKey": "~/.ssh/id_ed25519", "fallback": true}
  ]
}
```

**通道链（主通道 → 备用通道）**

| 设备 | 主通道 | 备用通道 |
| --- | --- | --- |
| iPhone | USB（`pymobiledevice3`） | （实验）Wi-Fi 数据通道 → LAN 探活（ARP → ping） |
| Android | `adb`（USB 或已手动连接的 TCP） | `adb mdns services` 发现 → `adb connect` → LAN 探活 |
| 主机（console） | ARP | ICMP ping → TCP 端口探活 |
| SSH 节点 | SSH | LAN 探活（`host` 解析 → ARP → ping） |

**配置键**（全部可选，缺省即旧行为）：

- `IPhone.ip`：iPhone 的局域网地址，只用于 LAN 探活；不配置则 iPhone 没有 LAN 备用通道。
- `IPhone.wifiDataChannel`（默认 `false`）：实验性 Wi-Fi 数据通道（usbmuxd `Network` → Bonjour/mobdev2）；当前 Apple 服务环境下**预期仍不可用**，保持关闭。
- `Android` 的 `ip` / `port`（端口默认 `5555`）：配置后主通道失败时会 `adb connect <ip>:<port>`。
- `Android.pairPort` / `Android.pairCode`（默认 `0` / 空）：Android 11+ 无线调试首次配对用；`pairCode` 只在当轮 `adb pair` 使用，**不落盘、不进日志、不进快照**。
- `Consoles[].tcpPorts`（默认 `[9295, 3074, 80, 443]`）：ARP / ping 都不通时依次做 TCP 端口探活，单端口 connect 超时 300ms，命中即在线；PS5 常用 9295，Xbox 常用 3074。
- `fallback`（`IPhone` / `Android` / `Consoles[]` / `SshNodes[]` 均支持，默认 `true`）：主通道失败后是否尝试备用通道。写 `false` 恢复旧行为（主通道失败即整条 `error`，主机仍只是 `console.online=false`）；未知或非法值会忽略该项并退回默认，不影响整轮采集。
- **降级语义**：备用通道能拿到多少报多少。降级在线 / 离线都**不是错误**，`error` 保持 `null`，状态由新增的条目级可选对象 `transport` 表达（`active` / `degraded` / `online` / `lastSeenAt` / `detail`，取值见 [HTTP API](#http-api)）。降级时电量 / 机型为 `null`；主机正常只提供存活信息，`transport.degraded` 恒为 `false`。
- **展示边界**：PS1 / OBS 行本轮**不变**，降级信息目前只出现在 JSON（`transport` 对象）里，展示接入留到 C3。

- 主机探测：先 ARP（`iphlpapi.SendARP`，同网段、免管理员），不通退 ICMP ping，
  再不通按 `tcpPorts` 依次 TCP 探活；连续 2 次失败才判离线，1 次成功立即回在线，
  迟滞状态存 `run/console_state.json`
  （daemon 每轮新起 Python 进程，状态必须落盘）。**主机离线不再算错误**：快照的
  `error` 保持 `null`，离线状态看 `console.online=false`，展示文案写进
  `console.statusText`（取自每台主机的配置键 `offlineText`，默认 `离线`）。
  L0 拿不到游戏名、也区分不了「挂机 / 在玩」，条目恒带 `console.idleHint: true`，
  更细的展示留到 C3。
- SSH 节点：走 Windows 自带 OpenSSH 客户端（`BatchMode=yes` 密钥认证、
  `IdentitiesOnly=yes` 只用指定密钥、`ConnectTimeout=2`、单节点 8 秒封顶），
  远端只需开 sshd / macOS「远程登录」，无常驻软件；电量与机型映射进标准
  schema-3 字段（台式机无电池为 null，不算错误）。Linux 多电池只取第一块；
  `node.uptimeSeconds` 给出节点开机秒数。SSH 失败且未关闭 `fallback` 时，对
  `host` 做 LAN 探活（`socket.gethostbyname` 解析后 ARP → ping）：探活成功为降级
  在线（`node.ssh=false`、`node.online=true`，电量 / 机型为 `null`），探活也失败为
  降级离线（`node.online=false`、`node.statusText="离线"`）；两种降级下 `error` 均为
  `null`，只有 `fallback: false` 时才写 `error`。
- 首次连接按 TOFU 处理：`StrictHostKeyChecking=accept-new`，首次自动信任并写入
  `known_hosts`，之后指纹变化会被直接拒绝（安全边界见 [SECURITY.md](SECURITY.md)）。
- `sshKey` 支持 `~` 与 `%VAR%` 展开（如 `~/.ssh/id_ed25519`、
  `%USERPROFILE%\.ssh\id_ed25519`）；路径不存在按配置错误处理，不会静默回退到
  默认密钥。
- 采集器经 `ThreadPoolExecutor` 分批并行执行（每批并发上限 8），多节点超时不叠加；
  一轮设备不超过 8 台时，含 SSH 超时最坏总耗时 < 15 秒，超过则按批串行；
  未配置或文件损坏时行为与纯手机采集完全一致。
- Python 整体失败（超时 / 崩溃 / 非零退出）时，daemon 保留上一轮设备条目并把
  `error` 标为 `读取超时`，快照不会变空。
- Python 输出为设备条目附加可选对象 `console` / `node` / `transport`，daemon 原样
  透传到 `/status/`；字段表见 [HTTP API](#http-api)。`Aliases` 对主机同样生效，key
  填条目的 `name`（即采集到的 `model`）。
- 测试：`.\.venv\Scripts\python.exe -m unittest discover -s tests -t .`（全离线）。

**Android 无线调试（可选）**

Android 11+ 可在「开发者选项 → 无线调试」里配对；StatusCast 会在 `adb devices` 没有在线设备时自动执行 `adb mdns services`，发现 `_adb-tls-connect._tcp` 后 `adb connect`。也可以手动确认：

```powershell
# 列出 mDNS 发现的无线调试服务
adb mdns services

# Android 11+ 首次配对（配对端口与配对码以手机屏幕为准）
adb pair 192.168.1.21:37123 123456

# 配对后连接（端口见手机「无线调试」页面）
adb connect 192.168.1.21:5555
```

`Android.pairPort` + `Android.pairCode` 只用于当轮配对；配对码不写入任何文件或日志，也不进 `run/status.json` / `/status/`。

## 蓝牙外设（BLE，可选）

`config.json` 可声明 `BleDevices`，每项一台 BLE 外设（耳机 / 手环 / 心率带）：

```json
"BleDevices": [
  {"id": "airpods", "name": "AirPods Pro", "address": "AA:BB:CC:DD:EE:FF"},
  {"id": "hrm", "name": "心率带", "nameFilter": "Polar",
   "services": ["heart_rate"], "heartRateWaitSeconds": 3}
]
```

- `address` 与 `nameFilter` **二选一**（都缺即配置错误）；`nameFilter` 对广播里的 `local_name` 做大小写不敏感子串匹配（没有时退回设备名）。
- `services` 默认 `["battery"]`，可选 `battery`（GATT `0x180F` / `0x2A19`）与 `heart_rate`（`0x180D` / `0x2A37`）；`heartRateWaitSeconds` 默认 3 秒（1..8）。
- 扫描用 `BleakScanner.discover(timeout=4, return_adv=True)`；同一轮多个 BLE 设备**共享一次扫描**；扫描与连接串行执行（bleak / WinRT 在同一进程并发事件循环不可靠）。
- 状态语义：读取成功为正常条目；扫描到但连不上为降级在线（`transport.online=true`、`transport.degraded=true`、`ble.statusText="已发现未连接"`）；扫描不到为 `transport.online=false`、`ble.statusText="未发现"`。这两种情况 `error` 都是 `null`；只有配置错误或缺少 bleak 才写 `error`。
- 依赖：`requirements.txt` 已声明 `bleak==3.0.2`（Windows 下它会带上 `winrt-Windows.Devices.Bluetooth*`）。缺依赖只影响 BLE 条目，不拖垮其它设备。
- 性能：配置 ≤2 个 BLE 设备时单轮 < 15 秒；更多设备会串行叠加。
- 隐私：BLE 地址与广播名只存在本机 `config.json`，不联网、不配对、不向设备写入任何数据。
- BLE 结果只进 JSON；PS1 / OBS 行本轮不变（展示留 C3）。

## 环境要求

- Windows 10 1809+ / Windows 11
- .NET 10 SDK（仅构建时需要）
- Python 3.10+ 与 `.venv`
- iPhone：iTunes / Apple Devices + `Apple Mobile Device Service`
  - Microsoft Store 版 Apple Devices 需保持「Apple 设备」应用运行，否则可能没有 usbmuxd socket；
  - Wi-Fi 采集受 Store 版 Apple Devices 限制，当前 StatusCast 只使用 USB。
- Android：Android Platform Tools，`adb` 在 `PATH` 中
- Mac / Linux 节点（可选）：节点开启 sshd（macOS 为「系统设置 → 通用 → 共享 → 远程登录」）并配置密钥登录；本机 `ssh` 可用（Windows 10+ 自带 OpenSSH 客户端）

安装 Python 依赖：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 构建与运行

```powershell
dotnet build .\src\StatusCast.Daemon\StatusCast.Daemon.csproj -c Debug
dotnet run --project .\src\StatusCast.Daemon\StatusCast.Daemon.csproj -c Debug
```

构建完成后可直接运行：

```powershell
.\src\StatusCast.Daemon\bin\Debug\net10.0\StatusCast.Daemon.exe
```

启动日志：

```text
[SYSTEM] StatusCast Core v3.3.0 启动
[SYSTEM] 纯后台采集引擎 · WinUI 已移除
[API] 监听 http://localhost:5099/status/
[USB] 等待 iPhone 连接...
[ADB] 等待 Android 连接...
[IOS] 未检测到 iPhone
[ANDROID] ADB not found. Install Android Platform Tools and add adb to PATH.
[SNAPSHOT] run/status.json 已更新 · 1792851926
```

发现新版本时，后台还会追加（彩色）：

```text
[UPDATE] 发现新版本 v3.3.1！请前往 GitHub Release 下载最新 Daemon。
[UPDATE] 更新内容：
[UPDATE]   - 修复 Android 前台应用识别
[UPDATE]   - ...
```

按 `Ctrl+C` 优雅退出。

## 测试

Python 采集层（全离线、零网络、零真机）：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

C# daemon（xunit 断言 + 自带反射运行器；VSTest 在部分受限 shell 下无法启动，因此不依赖它）：

```powershell
dotnet run --project .\tests-csharp\StatusCast.Daemon.Tests\StatusCast.Daemon.Tests.csproj -c Debug
```

- 默认套件不打开真实端口；设置 `STATUSCAST_NETWORK_TESTS=1` 后会额外跑 `/health`、`/status/` 的 loopback 集成测试（部分沙箱环境禁止 HttpListener，默认关闭）。
- `schema/status-contract.json` 是 schema-3 字段的**唯一事实源**：Python 与 C# 两侧测试都对照它校验输出形状，新增字段必须两边同时改，否则测试直接失败。

## 版本检查

daemon 启动时非阻塞请求：

```text
https://api.github.com/repos/ganmayou2333/StatusCast/releases/latest
```

解析 `tag_name` 与当前程序集版本（`<Version>3.3.0</Version>`）比较，若远端更新则打印 `[UPDATE]` 提示，并从 `body` 中取前 3 行更新内容。检查失败或没有新版本时完全静默，不影响采集与 API。**不会自动下载或覆盖程序文件。**

## HTTP API

```powershell
Invoke-RestMethod -Uri 'http://localhost:5099/status/' -UseBasicParsing
```

返回结构 `schema: 3`，只包含 `devices`：

```json
{
  "schema": 3,
  "generatedAt": 1792851926,
  "devices": [
    {
      "id": "iphone",
      "platform": "ios",
      "model": "iPhone15,3",
      "displayName": "主力机",
      "batteryLevel": 87,
      "isCharging": true,
      "mediaTitle": null,
      "mediaArtist": null,
      "mediaState": "unknown",
      "foregroundApp": null,
      "foregroundAppName": null,
      "updatedAt": 1792851926,
      "error": null
    },
    {
      "id": "android",
      "platform": "android",
      "model": "Pixel 7",
      "displayName": "备用机",
      "batteryLevel": 42,
      "isCharging": false,
      "mediaTitle": "晴天",
      "mediaArtist": "周杰伦",
      "mediaState": "playing",
      "foregroundApp": "com.tencent.mm",
      "foregroundAppName": "微信",
      "updatedAt": 1792851926,
      "error": null
    }
  ]
}
```

API 说明：

- 只监听 `localhost`；
- `GET /status/` 返回快照；快照未生成时返回 `503`；
- `GET /health` 返回 `{"status":"ok","snapshotReady":true|false}`，用于探活与判断快照是否就绪；
- 非 GET 返回 `405`，未知路径返回 `404`；
- 返回 `application/json; charset=utf-8`；
- 端口被占用时 daemon 会打印错误并退出。
- `devices` 条目可带四个**加法可选对象**：`console`（局域网主机）、`node`
  （SSH 节点）、`ble`（蓝牙外设）与条目级 `transport`（本轮实际通道与降级状态）；
  daemon 会把它们原样透传进 `run/status.json` 和 `/status/`，缺失时整个键不出现。
  除此之外的未知字段仍被安全忽略。
- `console.online=false` 的主机 `error` 仍为 `null`；iPhone / Android / 节点的备用
  通道降级（在线或离线）同样保持 `error: null`。只有主通道失败且 `fallback: false`，
  或整条通道链全部失败时，才写 `error`（形如 `ssh 连接失败: Permission denied`）。

**扩展对象字段**

| `console` 字段 | 类型 | 说明 |
| --- | --- | --- |
| `online` | bool | 迟滞后的在线结果 |
| `method` | str | 探测方式：`arp` / `ping` / `tcp`（端口命中时）/ `lan` |
| `lastSeenAt` | int / null | 最近一次在线的时间戳 |
| `mac` | str / null | ARP 回填的 MAC（可作 WOL 凭据） |
| `idleHint` | true | L0 分不清「在玩 / 挂机」，恒为 `true` |
| `name` | str | 配置里的主机名 |
| `type` | str | 配置里的主机类型（如 `ps5`） |
| `statusText` | str / null | 离线文案，来自 `offlineText`，默认 `离线` |

| `node` 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ssh` | bool | 本轮 SSH 是否成功；降级通道下为 `false` |
| `method` | str | 采集方式：`ssh`（SSH 成功）或 `lan`（降级） |
| `source` | str | 数据来源：`ssh` 或 `lan` |
| `hostname` | str / null | 远端 hostname |
| `lastSeenAt` | int / null | 最近一次成功的时间戳 |
| `uptimeSeconds` | int / null | 远端 uptime（秒） |
| `online` | bool / null | 仅降级路径有值：LAN 探活命中为 `true`，降级离线为 `false`；SSH 成功时为 `null` |
| `statusText` | str / null | 仅降级路径有值：降级离线为 `离线`，降级在线为 `null`；SSH 成功时为 `null` |

| `transport` 字段 | 类型 | 说明 |
| --- | --- | --- |
| `active` | str | 本轮实际取数通道：`usb` / `usb-wifi` / `adb` / `adb-wifi` / `ssh` / `lan` / `tcp` / `none` |
| `degraded` | bool | `true` 表示只拿到存活信息，电量 / 媒体 / 机型等常规字段缺失或部分缺失；主机恒为 `false` |
| `online` | bool | 该通道下判定的在线状态 |
| `lastSeenAt` | int / null | 最近一次在该通道上看到设备的时间戳 |
| `detail` | str | 自由文本细节：`arp` / `ping` / `tcp:9295` / `wifi:bonjour` / `adb:192.168.1.21:5555` |

| `ble` 字段 | 类型 | 说明 |
| --- | --- | --- |
| `address` | str / null | 命中的 BLE 地址（大写规范格式） |
| `name` | str / null | 广播名或配置里的 `name` |
| `rssi` | int / null | 扫描到时的信号强度（dBm） |
| `services` | str[] | 本轮尝试读取的服务：`battery` / `heart_rate` |
| `connected` | bool | 是否成功连接并读取 |
| `heartRate` | int / null | 心率（BPM），仅请求 `heart_rate` 且收到通知时有值 |
| `statusText` | str / null | 降级文案：`已发现未连接` / `未发现`；正常时为 `null` |
| `lastSeenAt` | int / null | 扫描到该设备的时间戳；未发现时为 `null` |

> `transport` 是条目级对象：Python 产出的条目都会带上它（没有任何通道产出时 `active="none"`、`online=false`）；仅当 daemon 因 Python 整轮失败而复用上一轮条目时该键缺失。四个对象都是只增不改的加法字段，schema 仍为 `3`；老消费者忽略它们即可。

## 自定义别名

`config.json`：

```json
{
  "Aliases": {
    "iPhone15,3": "主力机",
    "Pixel 7": "备用机"
  }
}
```

`Aliases` 的 key 必须与采集到的原始 `model` 完全一致。修改后重启 daemon。

## 静默运行

### 方式一：隐藏控制台窗口

```powershell
Start-Process `
  -FilePath '.\src\StatusCast.Daemon\bin\Debug\net10.0\StatusCast.Daemon.exe' `
  -WorkingDirectory (Get-Location) `
  -WindowStyle Hidden
```

### 方式二：Windows 服务

先发布单文件 exe：

```powershell
dotnet publish .\src\StatusCast.Daemon\StatusCast.Daemon.csproj `
  -c Release -r win-x64 --self-contained false `
  -p:PublishSingleFile=true -o .\dist
```

以管理员身份注册服务：

```powershell
sc.exe create StatusCast binPath= "G:\StatusCast\StatusCast\dist\StatusCast.Daemon.exe" start= auto
sc.exe start StatusCast
```

如果 `HttpListener` 在服务账户下提示拒绝访问，为服务账户授予 URL ACL：

```powershell
netsh http add urlacl url=http://localhost:5099/ user=SYSTEM
```

### 方式三：启动文件夹 / 计划任务

把发布后的 `StatusCast.Daemon.exe` 做成快捷方式放入 `shell:startup`，或使用任务计划程序在登录时启动。

## NekoTool 联动

NekoTool 的 `launch_and_pull.ps1` 会检测 API，未运行时启动 `StatusCast.Daemon.exe`，然后调用 `ultimate_status.ps1`。

NekoTrak 自定义文本：

```text
${(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "G:\StatusCast\NekoTool\scripts\launch_and_pull.ps1")}
```

首次配置：

```powershell
cd G:\StatusCast\NekoTool
.\scripts\setup.ps1
```

## Roadmap

- [x] **v3.3.0** — Python 采集器插件化、schema-3 JSON 标准输出、C# 解析重构
- [ ] **v3.4.0** — 探索 iPhone Wi-Fi 无线采集（预研完成，暂缓）
  - 预研脚本：`collectors/iphone_wireless_test.py`（不接入 `status_core.py`）
  - 结论：Microsoft Store 版 Apple Devices 的 AMDS 不提供 Wi-Fi 发现；换成经典
    iTunes + Apple Mobile Device Support 19.4.0.10 后，在 iOS 27.0 上 `usbmuxd`
    仍未注册 `Network` 设备。Bonjour 能建立 lockdown，但 `diagnostics_relay`
    服务会被 iPhone 断开。
  - 当前仍以 **USB 为 iPhone 唯一稳定路径**，详见
    [docs/iphone-wireless-notes.md](docs/iphone-wireless-notes.md)。
- [ ] **后续** — iPhone 媒体 Now Playing 元数据（受 iOS 私有 MediaRemote 接口限制，待评估）

## FAQ

### iPhone 能通过 Wi-Fi 采集吗？

当前**不能**。Wi-Fi 采集需要 Apple 的 Wi-Fi 同步/发现能力：

- Microsoft Store 版 Apple Devices 的 Apple Mobile Device Service **不提供 Wi-Fi 设备发现**，
  所以 `usbmuxd` 看不到 `Network` 设备；
- 通过 Bonjour/mobdev2 可以建立 lockdown 连接，但 `com.apple.mobile.diagnostics_relay`
  等业务服务会被 iPhone 立即断开，无法稳定读电量；
- iPhone 的 Wi-Fi 同步广播本身是间歇性的（锁屏、休眠、切换网络时会暂停），
  `--list` 可能时有时无，这不是 StatusCast 的采集逻辑能修复的；
- 经典 iTunes + AMDS 与 Store 版 Apple Devices 无法共存；本机实测经典
  AMDS 19.4.0.10 + iOS 27.0 仍然没有注册 `Network`，因此不作为推荐方案。

StatusCast 目前只使用 USB。预研细节和待复测项见
[docs/iphone-wireless-notes.md](docs/iphone-wireless-notes.md)。

### 为什么 iPhone 的 `mediaTitle` 一直是 `null`？

iOS 的 Now Playing / MediaRemote 是私有接口，`pymobiledevice3` 没有可用的 lockdown 服务；
当前 `IPhoneCollector` 只采集电量、充电状态和机型，媒体字段是占位符。
Android 端可以通过 `dumpsys media_session` 采集媒体信息，但需要先安装 ADB。

### 为什么报 `Failed to connect to usbmuxd socket`？

通常是 Apple Mobile Device Service / usbmuxd 没有运行：

- 使用 Microsoft Store 版 Apple Devices 时，保持「Apple 设备」应用运行；
- 使用经典 iTunes 时，确认 `Apple Mobile Device Service` 正在运行；
- 用 USB 连接 iPhone 并在手机上点「信任」；
- 如果刚安装/卸载过 Apple 组件，重新打开应用或重启系统后再试。

### Android 报 `ADB not found` 怎么办？

安装 Android Platform Tools，并把 `adb.exe` 所在目录加入 `PATH`，然后重新运行
`status_core.py`。

## 相关仓库

- [NekoTool](https://github.com/ganmayou2333/NekoTool) — 聚合、插件、OBS 控制台
- [StatusCast Releases](https://github.com/ganmayou2333/StatusCast/releases)

## License

MIT License，详见 [LICENSE](LICENSE)。

---

<div align="center">

**StatusCast** · Headless device status engine.

[![](https://img.shields.io/badge/powered_by-dsh-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/deepseek-ai/deepseek-harness)

</div>
