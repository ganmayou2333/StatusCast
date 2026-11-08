# 设备采集配置

所有可选设备都在仓库根目录的 `config.json` 里声明。**全部键都可选**：文件不存在、JSON 坏掉、或某个键没写，都退回默认行为，不会阻断启动。

采集层是 Python（`status_core.py` + `collectors/`），daemon 只负责调度、加别名、写快照和提供 HTTP。

## 键名大小写（踩过的坑）

| 侧 | 读取的键 | 大小写 |
| --- | --- | --- |
| Python 采集层 | `IPhone` `Android` `Consoles` `SshNodes` `BleDevices` | **严格区分**。写 `"consoles"` 或 `"iphone"` 会被**静默忽略**，不报错 |
| C# daemon | `RefreshIntervalSeconds` `Aliases` | 不区分（`PropertyNameCaseInsensitive`） |

所以统一按本文档的大驼峰写，两边都不会出问题。

## 容错半径不一致（务必知道）

两侧遇到坏配置的行为完全不同：

- **C# 侧**：`RefreshIntervalSeconds` 或 `Aliases` 里出现类型错误 → 整个 config 被丢弃，回到全默认（连别名一起失效）。
- **Python 侧**：单个条目坏掉 → 只产生一条带 `error` 的错误设备行，其它设备照常采集。
- **例外**：`Consoles` 被写成数字（`"Consoles": 3`）会让 Python 侧抛异常、整轮失败，daemon 表现为所有设备变成上一轮的 `读取超时`。数组里放错类型的对象则只是单条错误。
- Python 的 `json.load` **不接受**注释和尾逗号（C# 侧容忍）。共用一份文件时按标准 JSON 写。

---

## 完整示例

```json
{
  "RefreshIntervalSeconds": 30,
  "Aliases": {
    "iPhone15,3": "主力机",
    "Pixel 7": "备用机"
  },
  "IPhone":  {"ip": "192.168.1.100", "fallback": true, "wifiDataChannel": false},
  "Android": {"ip": "192.168.1.101", "port": 5555, "pairPort": 0, "pairCode": "", "fallback": true},
  "Consoles": [
    {"id": "ps5", "name": "PS5", "type": "ps5", "ip": "192.168.1.102",
     "offlineText": "已关机", "tcpPorts": [9295, 3074], "fallback": true}
  ],
  "SshNodes": [
    {"id": "macbook", "name": "MacBook", "platform": "macos",
     "user": "you", "host": "192.168.1.103",
     "sshKey": "~/.ssh/id_ed25519", "fallback": true}
  ],
  "BleDevices": [
    {"id": "buds", "name": "蓝牙耳机", "address": "AA:BB:CC:DD:EE:FF"},
    {"id": "hrm", "name": "心率带", "nameFilter": "Polar",
     "services": ["heart_rate"], "heartRateWaitSeconds": 3}
  ]
}
```

---

## 刷新间隔

| 键 | 类型 | 默认 | 行为 |
| --- | --- | --- | --- |
| `RefreshIntervalSeconds` | int | `30` | 小于 5 或大于 3600 时**重置为 30**，不是钳到边界；非整数（`"30"`、`30.5`）会让整份 config 失效 |

## 别名

`Aliases` 的 key 必须与采集到的原始 `model` **完全一致**（trim 后、`OrdinalIgnoreCase` 比较）。value 写成空白会被忽略。

`displayName` 的取值顺序是：`Aliases[model]` → 硬编码的 `iPhone15,3 → 14 Pro Max` / `iPhone14,3 → 13 Pro Max` → 原始 `model` → `未知机型`。

> 别名**每轮重新读**，改完不用重启 daemon。

---

## iPhone

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `ip` | 无 | iPhone 的局域网地址，只用于 LAN 探活。不配就没有 LAN 备用通道 |
| `fallback` | `true` | 主通道失败后是否尝试备用通道 |
| `wifiDataChannel` | `false` | 实验性 Wi-Fi 数据通道。当前 Apple 服务环境下预期仍不可用，保持关闭 |

主通道是 USB（`pymobiledevice3` + usbmuxd）。`ip` 非法时报 `iPhone IP 非法：<ip>`。

**Wi-Fi 为什么不行**：Store 版 Apple Devices 的 AMDS 不提供 Wi-Fi 发现，`usbmuxd` 看不到 `Network` 设备；经典 iTunes + AMDS 19.4.0.10 + iOS 27.0 实测同样没注册 `Network`；Bonjour 能建立 lockdown，但 `diagnostics_relay` 会被 iPhone 立刻断开。详见 [docs/iphone-wireless-notes.md](../iphone-wireless-notes.md)。

## Android

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `ip` | 无 | 配置后主通道失败时会 `adb connect <ip>:<port>` |
| `port` | `5555` | 非法或越界回落到 5555 |
| `pairPort` | `0` | Android 11+ 无线调试首次配对端口 |
| `pairCode` | 空 | 配对码，**只在当轮 `adb pair` 用**，不落盘、不进日志、不进快照 |
| `fallback` | `true` | 主通道失败后是否尝试备用通道 |

主通道是 `adb`（USB 或已手动连上的 TCP）。没有在线设备时会自动跑 `adb mdns services` 找 `_adb-tls-connect._tcp` 再 `adb connect`。

只有 `pairPort` 和 `pairCode` 同时存在才会尝试配对。

**手动确认无线调试**：

```powershell
# 列出 mDNS 发现的无线调试服务
adb mdns services

# Android 11+ 首次配对（端口与配对码以手机屏幕为准）
adb pair 192.168.1.101:37123 123456

# 配对后连接（端口见手机「无线调试」页面）
adb connect 192.168.1.101:5555
```

## 局域网主机（`Consoles`）

不装任何东西，用 Windows 自带能力探测 PS5 / Xbox / NAS 是否在线。

```json
{"id": "ps5", "name": "PS5", "type": "ps5", "ip": "192.168.1.102",
 "offlineText": "已关机", "tcpPorts": [9295, 3074], "fallback": true}
```

| 键 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `id` | ✅ | — | 设备 `id` |
| `ip` | ✅ | — | 局域网地址 |
| `name` | | `id` | 名字 |
| `type` | | `console` | 写进 `console.type`，只用于展示 |
| `offlineText` | | `离线` | 离线文案，写进 `console.statusText` |
| `tcpPorts` | | `[9295, 3074, 80, 443]` | ARP / ping 都不通时依次做 TCP 探活；单端口 300ms 超时；去重、越界丢弃、**最多 8 个**；写 `[]` 表示不做 TCP 阶段 |
| `fallback` | | `true` | ARP 不通后是否退 ICMP ping（写 `false` 就只剩 ARP） |

探测链：**ARP → ICMP ping → TCP 端口**。迟滞规则：连续 2 次失败才判离线，1 次成功立即回在线；状态落 `run/console_state.json`（daemon 每轮新起 Python 进程，状态必须落盘）。

> 主机离线**不算错误**：`error` 保持 `null`，离线看 `console.online=false`，文案看 `console.statusText`。
> L0 拿不到游戏名、也分不清「在玩 / 挂机」，所以 `console.idleHint` 恒为 `true`。

## SSH 节点（`SshNodes`）

只支持 `linux` / `macos`。

| 键 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `id` `user` `host` | ✅ | — | 连接三元组 |
| `platform` | ✅ | — | 只能是 `linux` 或 `macos` |
| `sshKey` | | 无 | 私钥路径，支持 `~` 与 `%VAR%`；路径不存在按配置错误处理，不静默回退默认密钥 |
| `name` | | `id` | 名字 |
| `model` | | 无 | 覆盖 `model` |
| `fallback` | | `true` | SSH 失败后是否对 `host` 做 LAN 探活 |

行为细节：

- 用 Windows 自带 OpenSSH 客户端，`BatchMode=yes`、`IdentitiesOnly=yes`、`ConnectTimeout=2`、单节点 8 秒封顶；远端只需开 sshd（macOS 是「系统设置 → 通用 → 共享 → 远程登录」），不需要常驻软件。
- 首次连接按 TOFU：`StrictHostKeyChecking=accept-new`，首次自动信任并写 `known_hosts`，之后指纹变化会被拒绝。安全边界见 [SECURITY.md](../../SECURITY.md)。
- 电量与机型映射进标准字段：台式机没有电池就是 `null`，不算错误。Linux 多电池只取第一块。`node.uptimeSeconds` 给出开机秒数。
- SSH 失败且 `fallback` 未关时对 `host` 做 LAN 探活（`gethostbyname` 解析 → ARP → ping）：探活成功是降级在线（`node.ssh=false`、`node.online=true`、电量与机型 `null`），探活失败是降级离线（`node.online=false`、`node.statusText="离线"`）。两种降级下 `error` 都是 `null`，只有 `fallback: false` 才写 `error`。

## BLE 外设（`BleDevices`）

耳机 / 手环 / 心率带。

| 键 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `id` | ✅ | — | 设备 `id` |
| `name` | | `id` | 名字 |
| `address` | 二选一 | — | BLE 地址（内部统一大写） |
| `nameFilter` | 二选一 | — | 对广播 `local_name` 做大小写不敏感子串匹配，没有时退回设备名 |
| `services` | | `["battery"]` | 可选 `battery`（GATT `0x180F`/`0x2A19`）与 `heart_rate`（`0x180D`/`0x2A37`）；全非法时回落默认 |
| `heartRateWaitSeconds` | | `3` | 钳制 1..8 |

- `address` 与 `nameFilter` **都缺就是配置错误**（`BLE 配置缺少 address 或 nameFilter`）。
- 扫描用 `BleakScanner.discover(timeout=4, return_adv=True)`；同一轮多个 BLE 设备**共享一次扫描**；扫描与连接串行（bleak / WinRT 同进程并发事件循环不可靠）。
- 状态语义：读到值 = 正常条目；扫到但连不上 = 降级在线（`transport.online=true`、`transport.degraded=true`、`ble.statusText="已发现未连接"`）；扫不到 = `transport.online=false`、`ble.statusText="未发现"`。这三种 `error` 都是 `null`，只有配置错误或缺 bleak 才写 `error`。
- **Apple 设备（AirPods 等）不暴露标准 GATT 电量**，读不到属预期，不是 bug。
- 性能：≤2 个 BLE 设备时单轮 < 15 秒；更多会串行叠加。
- 隐私：地址与广播名只存在本机 `config.json`，不联网、不配对、不向设备写任何数据。

## NekoTrak

**没有配置键。** 装了并跑过 NekoTrak 的机器上它会自动出现，作为一台独立设备（`platform: "windows"`，`id: "nekotrak"`）。

- 判定依据：`NEKOTRAK_HOME` → `%APPDATA%\NekoTrak` → `~/AppData/Roaming/NekoTrak`，目录存在才注册采集器。
- 数据源是明文产物：`startup.log`（版本 / 启动时长）、`NekoTrak.log`（TRACKER 状态、`custom_text_command_failed` 计数）、`users/<steamid>/pet-tales.txt`；当前游戏由「前台进程 × Steam 库索引」解析。
- 存活双保险：heartbeat 文件 60 秒内被改写，**或**进程表里有 `NekoTrak.exe`。
- `pomodoro` 恒为 `null`：NekoTrak 的数据库是加密的（无 SQLite 头），`tracker-heartbeat.json` 是 `NKSC` 密文，没有明文番茄钟会话可读。
- 从没跑过 NekoTrak 的机器不会产生这一条——不会挂一条永远红的 `NekoTrak 未运行`。

---

## 通道与降级

| 设备 | 主通道 | 备用通道 |
| --- | --- | --- |
| iPhone | USB（`pymobiledevice3`） | （实验）Wi-Fi 数据通道 → LAN 探活 |
| Android | `adb` | `adb mdns` 发现 → `adb connect` → LAN 探活 |
| 主机 | ARP | ICMP ping → TCP 端口 |
| SSH 节点 | SSH | LAN 探活 |
| BLE | GATT 读取 | 扫到但连不上算降级在线 |

**降级不写 `error`。** 备用通道能拿到多少报多少：降级在线/离线都保持 `error: null`，状态由条目级 `transport` 对象表达；降级时电量 / 机型为 `null`。只有主通道失败且 `fallback: false`，或整条通道链全部失败，才写 `error`（形如 `ssh 连接失败: Permission denied`）。

**Python 整轮失败**（超时 / 崩溃 / 非零退出 / 坏 JSON）时，daemon 保留上一轮条目并把 `error` 标为 `读取超时`，快照不会变空。注意此时 `transport` 键会被**丢弃**——它描述的是「本轮通道」，本轮没有通道。

并发：采集器按 `ThreadPoolExecutor` 分批发执行，每批上限 8，同一批内不重排。

**轮次预算.** 一轮采集有 **25 秒**预算（低于 daemon 的 30 秒强杀上限），在采集器开始前与批次之间检查。来不及开始的采集器各自产出一条 `error` 为 `采集预算用尽（本轮未完成）` 的正常设备行，已完成的结果照常返回——所以预算吃紧时你看到的是「部分设备有数据 + 其余带明确原因」，而不是整轮被杀、所有设备变成 `读取超时`。

采集器如果自己会长时间运行（Android 要连续起多个 `adb`），会拿到同一个 deadline 并按剩余预算截断每次调用的超时，剩余不足时直接放弃剩余步骤。写自己的采集器时继承 `BaseCollector` 即可用 `self.out_of_budget()` 与 `self.budget_timeout(n)` 复用这套逻辑。

---

## 写自己的采集器

采集层没有注册表、没有 entry point、没有自动发现——采集器是**静态注册**的，只有四步。

### 1. 继承 `BaseCollector`

```python
from collectors.base import BaseCollector, CollectorError


class MyDeviceCollector(BaseCollector):
    platform = "my_device"      # 写进设备行的 platform
    device_id = "my_device_1"   # 写进设备行的 id

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

`get_data()` 是唯一的抽象方法，返回一个普通 dict。可选覆盖 `error_payload(message)` 来自定义错误条目（默认 `error_entry(platform, device_id, message or "采集失败")`）。

### 2. 在 `build_collectors()` 里注册

注意是 **`build_collectors()`**（`status_core.py:93`），不是 `collect_all()`——后者只是调用它。

```python
from collectors.my_device_collector import MyDeviceCollector

# 在 build_collectors() 的 collectors 列表里追加
collectors.append(MyDeviceCollector())
```

注册顺序就是设备行顺序：`IPhone → Android → Consoles[] → SshNodes[] → BleDevices[] → NekoTrak`，无效条目统一追加在最后。

### 3. 错误怎么冒泡

抛 `CollectorError`（`RuntimeError` 的子类）就会变成该设备的 `error` 字段；**任何**异常都会被捕获，消息取 `str(exc)` 或类名。一个采集器炸掉只影响它自己那一条。

### 4. 只有契约内的键会被保留

daemon 只认 13 个快照键 + 5 个具名加法对象（`console` `node` `transport` `ble` `nekotrak`）。**采集器返回的任何其它键会被静默丢弃**。

`transport` 不需要你写：派发层会给每个条目补默认值 `{"active":"none","degraded":false,"online":false,"lastSeenAt":null,"detail":null}`，你要做的就是覆盖成实际值。

daemon 端不需要改代码：`StatusService` 会解析新的 `devices` 数组并按 `platform` 渲染。

字段全集见 [HTTP API 与契约](http-api.md)，以及 [`schema/status-contract.json`](../../schema/status-contract.json)。
