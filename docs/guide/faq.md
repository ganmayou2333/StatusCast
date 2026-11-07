# 排障 FAQ

## iPhone

### iPhone 能通过 Wi-Fi 采集吗？

**不能。** Wi-Fi 采集需要 Apple 的 Wi-Fi 同步 / 发现能力，而这条路上每一环都不通：

- Microsoft Store 版 Apple Devices 的 Apple Mobile Device Service **不提供 Wi-Fi 设备发现**，所以 `usbmuxd` 看不到 `Network` 设备；
- 换经典 iTunes + Apple Mobile Device Support 19.4.0.10 后，在 iOS 27.0 上 `usbmuxd` **仍未**注册 `Network` 设备（本机实测）；
- Bonjour / mobdev2 能建立 lockdown，但 `com.apple.mobile.diagnostics_relay` 等业务服务会被 iPhone 立即断开，读不到稳定电量；
- iPhone 的 Wi-Fi 同步广播本身是间歇性的（锁屏、休眠、切换网络都会暂停），`--list` 时有时无；
- 经典 iTunes 与 Store 版 Apple Devices 无法共存。

所以 StatusCast 只用 USB。预研细节与待复测项见 [docs/iphone-wireless-notes.md](../iphone-wireless-notes.md)。

### 为什么 `mediaTitle` 一直是 `null`？

iOS 的 Now Playing / MediaRemote 是私有接口，`pymobiledevice3` 没有可用的 lockdown 服务。iPhone 采集器只读电量、充电状态和机型，媒体字段是占位符。

Android 侧可以读：`dumpsys media_session` 能拿到媒体信息，所以 Android 行的 `mediaTitle` / `mediaArtist` / `mediaState` 通常有值。

### 报 `Failed to connect to usbmuxd socket`

通常是 Apple Mobile Device Service / usbmuxd 没起来：

- Store 版 Apple Devices：保持「Apple 设备」应用运行；
- 经典 iTunes：确认 `Apple Mobile Device Service` 服务在跑；
- USB 连上 iPhone 并在手机上点「信任」；
- 刚装/卸过 Apple 组件的话，重开应用或重启系统再试。

## Android

### 报 `ADB not found. Install Android Platform Tools and add adb to PATH.`

装 Android Platform Tools，把 `adb.exe` 所在目录加进 `PATH`，然后重跑。验证一下：

```powershell
adb version
```

### 无线调试连不上

Android 11+ 需要在「开发者选项 → 无线调试」里配对一次。`Android.pairPort` 和 `Android.pairCode` **必须同时配置**才会尝试 `adb pair`；配对码只在当轮使用，不落盘。

也可以手动来一遍：

```powershell
adb mdns services
adb pair 192.168.1.101:37123 123456
adb connect 192.168.1.101:5555
```

## 通用

### `/health` 返回 503 是不是挂了？

不是。503 只表示**快照还没写出来**——刚启动的头几秒，或者上一轮还没落盘。守护脚本应当重试而不是当成崩溃。

真正的异常是 `/health` 一直 503：去看 `/status/` 是不是也 503，再看 daemon 日志里有没有 Python 层的报错。

### 所有设备突然都变成 `读取超时`

这是 **Python 整轮失败**的统一表现（超时 / 崩溃 / 非零退出 / 输出不是合法 JSON）。daemon 会把上一轮的设备条目原样复用，并把 `error` 标成 `读取超时`，快照不会变空。

常见原因：

1. `config.json` 结构写错。最典型的是 `"Consoles": 3` 这种把数组写成数字——Python 侧会抛异常、整轮失败。数组里放错类型的对象只会让单条变错误，不会拖垮整轮。
2. `.venv` 里的依赖坏了或没装。先手动跑一次看真实报错：

```powershell
.\.venv\Scripts\python.exe status_core.py --pretty
```

3. 某个采集器卡死超过 30 秒的进程上限。

### 配置写了但没生效

先检查**键名大小写**。Python 采集层严格区分：`Consoles` 不是 `consoles`，`IPhone` 不是 `iphone`，写错会**静默忽略**、不报错。

再检查 JSON 是否标准：Python 的 `json.load` 不接受注释和尾逗号（C# 侧容忍）。

### 别名改了要重启 daemon 吗？

不用。配置**每轮重读**，下一轮（默认 30 秒内）就生效。

### 端口 5099 被占用

daemon 会打印错误并退出。找出占用者：

```powershell
Get-NetTCPConnection -LocalPort 5099 | Select-Object OwningProcess
```

### 服务方式启动后 `HttpListener` 拒绝访问

服务账户没有 URL ACL。以管理员身份补一条：

```powershell
netsh http add urlacl url=http://localhost:5099/ user=SYSTEM
```

## 设备采集

### BLE 耳机的电量读不到

Apple 设备（AirPods 等）**不暴露标准 GATT `0x180F` 电量特征**，读不到属预期，不是 bug。`ble.statusText` 会显示 `已发现未连接`，`error` 保持 `null`，`transport.degraded` 为 `true`。

愿意的话可以用 `address` 直接指定设备试试，但只要设备本身不实现 `0x180F/0x2A19`，结果一样。

### 心率带没有 `heartRate`

三个前提缺一不可：

1. `services` 里包含 `heart_rate`；
2. 设备真的会发 `0x180D/0x2A37` 通知；
3. `heartRateWaitSeconds`（默认 3，范围 1..8）够长到收到第一帧。

`ble.connected: true` 但 `heartRate: null` 就是「连上了但没等到通知」，`error` 仍是 `null`。

### 主机离线没有 `error`

设计如此。主机离线看 `console.online: false` 和 `console.statusText`（默认 `离线`，可用 `offlineText` 覆盖），`error` 保持 `null`。

### `console.idleHint` 为什么恒为 `true`？

L0 层只能判断「在不在线」，分不清「正在玩」和「挂着不动」。所以它恒为 `true`，表示「这是初步提示，别当成确定结论」。

## NekoTrak

### 有条目但 `pomodoro` 恒为 `null`

NekoTrak 的数据库是加密的（没有 SQLite 头），`tracker-heartbeat.json` 是 `NKSC` 密文，**没有明文番茄钟会话可读**。所以 `pomodoro` 恒为 `null`，除非 CDP 预案落地。

### 为什么我这台机器根本没有 NekoTrak 条目

数据根不存在就不注册采集器——这是刻意的，避免挂一条永远红的 `NekoTrak 未运行`。数据根按顺序找：`NEKOTRAK_HOME` → `%APPDATA%\NekoTrak` → `~/AppData/Roaming/NekoTrak`。

## ESP32

### 固件拿不到手机 / NekoTrak 的数据

PC daemon **只绑 loopback**，ESP32 拉不到 `localhost:5099`。用仓库自带的中转把 PC 快照暴露到局域网：

```powershell
python .\firmware\esp32\tools\pc_feed_relay.py --listen-port 5100
```

然后在固件配置里把 `PcFeed.url` 指向 `http://<PC 的局域网 IP>:5100/status/`。

### 屏幕上的中文变成 `?`

固件只内嵌 ASCII 点阵字库，**没有中文字库**。`displayName` 含非 ASCII 时逐字符替换成 `?`；整串没有可显示 ASCII 时依次回退 `model` → `id`。

API 和 NekoTool 都不受影响。想要中文屏显示需要额外嵌一个点阵子集字库（约 +20–60KB），目前不在计划内。

### 固件的 `/control/audit`、`/control/coredump` 返回 404

旧固件（v3.4.0 之前）的 HTTP handler 表只按公开路由的数量分配槽位，导致最后注册的几条路由在启动时被静默丢弃。已在 [`f889d92`](https://github.com/ganmayou2333/StatusCast/commit/f889d92) 修复。重新烧录即可。

### 串口号每次插拔都变

正常现象。CH343 的 COM 号由 Windows 重新枚举，本机实测从 COM3 变成过 COM4。所有脚本都接受 `-Port`，以设备管理器里的实际端口为准。
