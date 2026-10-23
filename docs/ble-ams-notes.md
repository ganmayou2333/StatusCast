# Windows + bleak 实现 Apple Media Service (AMS) 的关键步骤与坑

> 目标：Windows 作为 BLE Central，连接 iPhone（Peripheral），订阅 AMS 的
> Entity Update 通知，拿到歌名/艺术家/播放状态。
>
> 本文记录方案、风险与本机实测；实际实现见 `core/sources/media_ams.py`，
> 诊断入口是 `scripts/probe_ams.py`。所有协议字段来自 Apple 官方
> Apple Media Service Reference；Windows 行为部分需要在目标机器上实测确认。

## 0. 本机实测记录（2026-10-23）

环境：Windows 11、Python 3.14.6、bleak 3.0.2、WinRT BLE。

- `scripts/probe_ams.py` 能在扫描结果中找到一台 **iPhone15,3**，连接后
  `client.services` 中确实存在 AMS 服务，三个特征齐全。
- 未配对时 `pair=False` 可以建立连接并枚举服务，但
  `start_notify(Entity Update)` 返回：
  `BleakGATTProtocolError: (5, 'GATT Protocol Error: Insufficient Authentication')`
- 使用 `--pair`（bleak 的 WinRT 配对流程）返回
  `BleakError: Could not pair with device: FAILED`。
- 进一步直接用 WinRT `DeviceInformationPairing` 尝试
  `CONFIRM_ONLY` / `DISPLAY_PIN` / `CONFIRM_PIN_MATCH`，均立即返回
  `DevicePairingResultStatus.FAILED (19)`，且没有触发 `pairing_requested`
  事件。这说明当前不是「iPhone 上没人点确认」那么简单，可能是 iPhone
  锁屏/已绑定状态或 Windows BLE 栈拒绝了配对流程，需要继续排查。
- 结论：**AMS 需要已认证的 BLE 链路**；当前剩余阻塞是完成 Windows/iPhone
  的 BLE 配对。需要在 iPhone 解锁状态下重试，并在 iPhone 上确认配对弹窗，
  或先在 Windows「设置 → 蓝牙和其他设备」中完成配对。若仍失败，AMS 在
  该 Windows 蓝牙栈上可能不可行，需要评估 AirPlay/MQTT 或 Linux BLE 桥接。

复现命令：

```powershell
.\.venv\Scripts\python.exe .\scripts\probe_ams.py --pair --debug-raw --listen 30
```

## 1. 先记住结论

1. AMS 是一个 **有状态的长连接订阅**，不适合每次 PowerShell 调用都重新连一遍。
   最终应由常驻 Python 核心持有 BLE 连接，PowerShell 只读最新快照。
2. AMS 服务 **不保证一直存在**。iPhone 锁屏、切后台、没有媒体会话、蓝牙关闭、
   iOS 版本差异，都可能让 AMS 暂时消失。必须做「重扫 → 重连 → 重新订阅」。
3. Windows 上最大的不确定性不是解析协议，而是 **发现/配对/随机地址/服务缓存**。
   建议先写一个独立探针脚本，把原始通知打出来，再谈聚合逻辑。
4. 不要让 PowerShell 直接做 BLE 操作。PowerShell 只负责启动/读取 Python 的输出。

## 2. AMS 的 GATT 结构

Apple 官方 UUID（128 位）：

| 角色 | UUID | 属性 |
| --- | --- | --- |
| AMS Service | `89D3502B-0F36-433A-8EF4-C502AD55F8DC` | Primary Service |
| Remote Command | `9B3C81D8-57B1-4A8A-B8DF-0E56F7CA51C2` | Write, Notify |
| Entity Update | `2F7CABCE-808D-411F-9A0C-BB92BA96C102` | Write with response, Notify |
| Entity Attribute | `C6B2F38C-23AB-46D8-A6AB-A3A870BBD5D7` | Read, Write |

注意：Entity Update 的 UUID 是 `2F7CABCE-...`，不是网上一些旧代码里的
`2F7B60A5-...`。以 Apple 官方文档为准。

### Entity 与 Attribute ID

| Entity | ID | 关注的 Attribute | ID |
| --- | --- | --- | --- |
| Player | `0` | Name | `0` |
| Player | `0` | PlaybackInfo | `1` |
| Player | `0` | Volume | `2` |
| Queue | `1` | Index / Count / ShuffleMode / RepeatMode | `0` / `1` / `2` / `3` |
| Track | `2` | Artist | `0` |
| Track | `2` | Album | `1` |
| Track | `2` | Title | `2` |
| Track | `2` | Duration | `3` |

### 通知格式

Entity Update 通知：

```text
byte 0      EntityID
byte 1      AttributeID
byte 2      EntityUpdateFlags (bit0 = Truncated)
byte 3..    Value (UTF-8 字符串)
```

`PlayerAttributeIDPlaybackInfo` 的值不是单个整数，而是逗号分隔字符串：

```text
PlaybackState,PlaybackRate,ElapsedTime
```

PlaybackState：`0=Paused, 1=Playing, 2=Rewinding, 3=FastForwarding`。  
例如 `"1,1.000000,42.5"` 表示正在播放，速率 1.0，已播放 42.5 秒。

## 3. bleak 侧的实现步骤

### Step 1：安装与扫描

```powershell
.\.venv\Scripts\python.exe -m pip install "bleak>=3.0,<4"
```

先用 `BleakScanner.discover(return_adv=True, timeout=10)` 打印附近设备的
name/address/RSSI/manufacturer_data/service_uuids，确认能否看到 iPhone。

不要一上来就用 `service_uuids=[AMS_UUID]` 过滤：AMS UUID 不一定出现在广告包里。
更稳的做法是先按设备名/已配对设备筛选，连接后再枚举 GATT 服务。

### Step 2：连接并确认服务存在

```python
from bleak import BleakClient

AMS_SERVICE = "89d3502b-0f36-433a-8ef4-c502ad55f8dc"
AMS_ENTITY_UPDATE = "2f7cabce-808d-411f-9a0c-bb92ba96c102"

async with BleakClient(bledevice, timeout=20, pair=True) as client:
    services = client.services
    ams = services.get_service(AMS_SERVICE)
    if ams is None:
        # AMS 当前不存在：解锁 iPhone、打开音乐 App、播放一下，再重试
        ...
```

`pair=True` 会让 bleak 在连接时尝试配对。Windows 上这是可用的，但可能弹出
系统配对流程；建议把超时调大（例如 30 秒）。如果配对失败，先在
Windows「设置 → 蓝牙和其他设备」里删除该 iPhone 的旧配对记录，再重试。
是否需要绑定因 iOS/蓝牙栈而异，必须实测；不要假设「不配对也能订阅」。

### Step 3：先订阅通知，再写注册命令

顺序很重要：

1. `await client.start_notify(ENTITY_UPDATE, callback)`
2. 再向 Entity Update 写要监听的 `(EntityID, AttributeID...)`

如果先写再订阅，AMS 可能返回 `Invalid State (0xA0)`。

注册 Player 的 PlaybackInfo：

```python
await client.write_gatt_char(
    AMS_ENTITY_UPDATE,
    bytes([0x00, 0x01]),   # EntityID=Player(0), AttributeID=PlaybackInfo(1)
    response=True,
)
```

注册 Track 的 Artist/Album/Title：

```python
await client.write_gatt_char(
    AMS_ENTITY_UPDATE,
    bytes([0x02, 0x00, 0x01, 0x02]),  # EntityID=Track(2)
    response=True,
)
```

Entity Update 是 write-with-response，显式传 `response=True`。

### Step 4：回调里解析

```python
def on_entity_update(_char, data: bytearray):
    entity = data[0]
    attribute = data[1]
    flags = data[2]
    value = bytes(data[3:]).decode("utf-8", errors="replace")
    truncated = bool(flags & 0x01)
    ...
```

建议维护一个 `dict[(entity, attribute)] = value`，每次通知只更新一个字段；
展示层读取完整快照，而不是假设通知一定成组到达。

### Step 5：截断值用 Entity Attribute 补读

如果 `truncated` 为真，向 Entity Attribute 写入 `bytes([entity, attribute])`，
然后 `read_gatt_char(ENTITY_ATTRIBUTE)` 读取完整值。Windows 协商的 MTU 通常
比 BLE 最小值大，但歌名/艺术家仍可能超长。

### Step 6：断线重连

监听 `disconnected_callback`，用指数退避重连。重连后必须重新：
枚举服务 → start_notify → 写注册。不要复用旧 `BleakClient` 的 services 缓存。

## 4. Windows 上最容易踩的坑

### 4.1 随机地址，不要硬编码 MAC

iPhone 使用随机可解析私有地址，`BLEDevice.address` 可能变化。不要保存
`AA:BB:...` 然后下次直接连接；每次通过扫描得到的 `BLEDevice` 连接。若要做
「只认我的 iPhone」，用设备名 + 配对记录/系统设备 ID 组合筛选，并允许人工指定。

### 4.2 AMS 不一定在广告包里

`BleakScanner(service_uuids=[AMS_SERVICE])` 可能什么都扫不到。先扫全部设备，
看 Apple 厂商数据（manufacturer ID `0x004C`）和名称，再连接后枚举服务。
如果枚举不到 AMS：解锁 iPhone、打开音乐 App、播放/暂停一次、确认蓝牙开启，
再重连。仍失败就退避重试，不要崩溃。

### 4.3 配对/绑定状态可能卡住

Windows 可能残留旧的配对记录，导致 `pair=True` 一直失败或连接后立刻断开。
处理顺序：

1. Windows 设置里删除该 iPhone 的配对；
2. iPhone 上忽略此电脑（如需要）；
3. 重新扫描连接；
4. 如果系统弹配对框，确认；
5. 仍失败则记录 Windows 版本、蓝牙适配器型号、iOS 版本，换适配器/重启蓝牙服务再试。

### 4.4 服务缓存与「服务消失」

Windows 的 GATT 服务缓存、以及 iPhone 动态增删 AMS，都会让旧句柄失效。
每次重连都重新发现服务；如果连接成功但 AMS 不存在，按 4.2 重试。
Apple 官方还建议订阅 GATT 的 Service Changed 特征（`0x2A05`）来感知服务
发布/取消发布；bleak 对 Service Changed 的支持有限，实践上更可靠的是
「连接后枚举 + 定时重连」。

### 4.5 锁屏/后台/低电量模式

iPhone 锁屏、媒体 App 被系统挂起、没有正在播放的会话时，AMS 可能没有数据或
直接消失。此时应把媒体状态标为 unknown/stale，而不是抛异常。展示端可以显示
「未在播放」。

### 4.6 Windows 服务 / Session 0

不要把这个核心做成 Windows Service 跑在 Session 0；WinRT 蓝牙 API 需要
交互式用户会话。推荐「用户登录时启动的计划任务」，或托盘程序。

### 4.7 2.4 GHz 干扰

BLE 与 Wi-Fi 2.4 GHz、USB 3.0 接口可能互相干扰。RSSI 很低、频繁断线时，
换 USB 口/远离路由器/用延长线，先排除环境问题。

### 4.8 不要把 BLE 循环放在 PowerShell 里

PowerShell 每次执行都是新进程，无法保持 BLE 通知订阅；反复启停 Python 还会
让 Windows 蓝牙栈来不及清理连接。常驻核心 + 快照读取是更稳的形态。

## 5. 建议的验证顺序

在写正式 `media_ams.py` 前，按这个顺序做独立探针：

1. `bleak` 扫描：确认 iPhone 可见，记录 address/name/RSSI/广告字段。
2. 连接：只打印 `client.services`，确认 AMS 三个特征都存在。
3. 订阅 Entity Update，写 Player PlaybackInfo，打印原始 bytes。
4. 播放/暂停/切歌，观察通知变化，确认 PlaybackState 和曲目字段。
5. 注册 Track Artist/Album/Title，处理截断值。
6. 锁屏、断开蓝牙、走出范围，观察断线回调与重连行为。
7. 连续运行 30 分钟，记录内存/句柄/重连次数。

只有 1–5 都稳定后，才把它接到常驻核心和适配器上。

## 6. 备选路径：AirPlay + Shairport Sync + MQTT

如果 AMS 在目标 Windows 机器上不可行（蓝牙适配器/驱动/配对限制），保留
`MediaSource` 的可切换实现：

```text
iPhone --AirPlay--> Shairport Sync --metadata--> MQTT --> Python MediaSource
```

注意：

- Shairport Sync 原生主要支持 Linux/macOS，**Windows 不是官方主目标**。
  现实方案是 WSL2（mirrored networking）、Docker，或一台树莓派/小型 Linux 盒子
  作为桥接器；Windows 只跑 Python + MQTT 订阅。
- 需要开启 Shairport Sync 的 MQTT/metadata 输出，并确认 AirPlay 版本和发送端
  （Apple Music / 第三方 App）是否真的提供曲目元数据。
- 这条路径的优点是绕开 BLE 配对和随机地址；缺点是额外硬件/网络依赖、
  延迟和元数据完整性问题。
- 在配置层表现为 `media_source = "ams" | "airplay_mqtt"`，聚合器只依赖
  `MediaSource` 接口，不关心具体实现。

## 7. 参考

- [Apple Media Service Reference — Specification](https://developer.apple.com/library/archive/documentation/CoreBluetooth/Reference/AppleMediaService_Reference/Specification/Specification.html)
- [Apple Media Service Reference — Appendix（ID/Flag 表）](https://developer.apple.com/library/archive/documentation/CoreBluetooth/Reference/AppleMediaService_Reference/Appendix/Appendix.html)
- [bleak BleakClient 文档（pair / start_notify / read/write）](https://bleak.readthedocs.io/en/develop/api/client.html)
- [pymobiledevice3 安装与 Windows 说明](https://doronz88.github.io/pymobiledevice3/installation/)
- [SiFli AMS 示例（旁证：AMS 方向与配对确认）](http://docs.sifli.com/projects/sdk/latest/en/sf32lb52x/example/ble/ams/README.html)
