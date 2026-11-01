# iPhone Wi-Fi 采集预研（v3.4.0）

> 适用版本：StatusCast v3.3.0。v3.4 仍处于预研阶段：USB 失败后会尝试 LAN 探活降级，
> 并提供一个默认关闭的实验开关 `wifiDataChannel`，**不代表 Wi-Fi 采集可用**。

## 结论先行

在当前 Windows + Microsoft Store 版 Apple Devices 环境下，**iPhone Wi-Fi 采集不可用**，
StatusCast 继续只使用 USB 作为 iPhone 数据通道。

- `usbmuxd` 不会列出 `Network` 设备；
- Bonjour/mobdev2 可以发现设备并建立 lockdown 连接；
- 但 `com.apple.mobile.diagnostics_relay` 等业务服务连接会被 iPhone 立即断开；
- 因此无法稳定读取电量，也不能接入 `status_core.py`。

## 测试环境

- Windows 11，Microsoft Store 版 Apple Devices（AMDS 位于 `WindowsApps`）
- iPhone14,3，iOS 27.0
- 电脑：有线以太网；iPhone：同一局域网的 Wi-Fi
- Python：项目 `.venv` + `pymobiledevice3` 11.43.1（临时验证过 11.44.0，结果一致）

## 测试脚本

`collectors/iphone_wireless_test.py` 是独立预研脚本，不会被 `status_core.py` 导入，
也不会注册为采集器。

```powershell
# 同时查看 usbmuxd / Bonjour 两条通道
.\.venv\Scripts\python.exe .\collectors\iphone_wireless_test.py --list

# 仅走 usbmuxd Network
.\.venv\Scripts\python.exe .\collectors\iphone_wireless_test.py --discovery usbmux --verbose

# 仅走 Bonjour/mobdev2
.\.venv\Scripts\python.exe .\collectors\iphone_wireless_test.py --discovery bonjour --verbose
```

脚本会自动尝试 usbmuxd Network，失败后回退 Bonjour/mobdev2；电源服务读取失败时只警告，
不会误报整个 Wi-Fi 不可用。

## 结果

### 1. usbmuxd Network 不可见

```text
=== usbmuxd ===
  （usbmuxd 没有发现设备）
```

Microsoft Store 版 Apple Devices 的 Apple Mobile Device Service **不提供 Wi-Fi 设备发现**。
pymobiledevice3 源码中也注明了这个限制（pymobiledevice3#1968）：Store 版服务不会发现
Network 设备，经典 iTunes 安装的 AMDS 才会。

### 2. Bonjour/mobdev2 可以发现并握手

```text
=== Bonjour/mobdev2 ===
  - 00008110-...  [Bonjour ...]  iPhone14,3
```

`lockdown` 能读取设备名称、型号、系统版本等信息，说明局域网、mDNS 和配对记录都正常。
但发现是间歇性的：iPhone 锁屏/休眠或 Wi-Fi 同步广播暂停时，`--list` 会短暂看不到设备。

### 3. 业务服务连接被断开

```text
[OK] iPhone Wi-Fi 连接成功（Bonjour/mobdev2）
[WARN] 连接成功，但电量服务被设备断开：
       ConnectionTerminatedError: Connection was terminated abruptly.
```

手动验证过：

- IPv4 / IPv6 地址；
- `keep_alive=True/False`；
- `EscrowBag`；
- `pymobiledevice3` 11.44.0；

结果一致：lockdown 能建立，但 `diagnostics_relay` 的请求立即被设备关闭。

### 4. 经典 iTunes 方案也未能恢复

临时安装经典 iTunes + Apple Mobile Device Support + Bonjour 后：

- `usbmuxd` 仍然看不到 Network 设备；
- Store 版 Apple Devices 启动时要求卸载经典 AMDS；
- 两套 Apple 服务无法共存。

因此不采用经典 iTunes 方案。

### 如何确认当前 Apple 服务类型

```powershell
sc.exe qc "Apple Mobile Device Service"
```

- `BINARY_PATH_NAME` 指向 `Common Files\Apple\Mobile Device Support`：经典 iTunes AMDS；
- 指向 `WindowsApps`：Microsoft Store 版 Apple Devices；
- 服务不存在：通常说明只安装了 Store 版 Apple Devices（它使用
  `AppleMobileDeviceProcess`，而不是经典服务）。

不要同时安装两套 Apple 服务，否则 Apple Devices 会要求卸载经典 AMDS。

## 对 StatusCast 的影响

- **USB 是 iPhone 唯一稳定路径**：电量、充电状态、机型都正常。
- **Wi-Fi 预研脚本保留**：作为后续 iOS/Apple 服务变化时的探测工具，不接入主流程。
- **iPhone 媒体元数据暂不支持**：iOS 的 Now Playing / MediaRemote 是私有接口，
  `pymobiledevice3` 没有可用的 lockdown 服务；当前 `IPhoneCollector` 的媒体字段为占位符。
- **Android 媒体**仍通过 `dumpsys media_session` 采集，但需要 ADB。

## v3.4 实验开关：`wifiDataChannel`

v3.4 起 `config.json` 的 `IPhone` 节提供实验开关：

```json
{
  "IPhone": {"ip": "192.168.1.20", "fallback": true, "wifiDataChannel": true}
}
```

打开后，USB 失败时会依次尝试 usbmuxd `Network` 与 Bonjour/mobdev2（复用
`iphone_wireless_test.py` 的发现逻辑）作为 Wi-Fi 数据通道；能读到电量即视为正常条目，
只能握手读不到电量则继续往下走。**结论仍然不变：当前环境不可用**，因此默认 `false`，
本轮也不把它作为 iPhone 的稳定路径。

- `wifiDataChannel` 默认为 `false`，此时完全跳过 Wi-Fi 步骤，行为与旧版一致；
- USB 失败且配置了 `IPhone.ip`（`fallback` 未关闭）时，会降级为 LAN 探活（ARP → ping）：
  条目 `error` 仍为 `null`，`transport.active="lan"`、`transport.degraded=true`，
  电量 / 机型为 `null`；
- 写 `"fallback": false` 则恢复旧行为（USB 失败即整条 `error`）；
- Wi-Fi 数据通道成功但只能握手时，同样落到 LAN 探活，不会误报为「已读数据」。

## 后续可选方向

1. 如果未来切换到经典 iTunes AMDS，重新运行预研脚本确认 Network 是否出现；
2. 用 iOS 快捷指令把当前播放信息 POST 到本地，作为非侵入式桥接；
3. 评估第三方私有 MediaRemote 方案，但不进入主仓库主线。

## 相关文件

- `collectors/iphone_wireless_test.py`
- `collectors/iphone_collector.py`
- `status_core.py`
