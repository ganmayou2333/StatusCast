# Spec: BLE 外设采集（BleDevices）

状态：已实现（2026-10-31）。实现备注见文末。
日期：2026-10-31
关联：`docs/C1-C2b实施提示词.md`、`.scratch/device-fallback-transports/spec.md`

## 背景与目标

StatusCast 现在能采手机（USB/adb）、主机（ARP/ping/TCP）、SSH 节点。蓝牙外设（耳机、手环、心率带）目前完全没有入口，虽然 venv 里已装有 `bleak 3.0.2`（未被 requirements 声明）。

目标：新增一类采集器，按**显式白名单**扫描并连接 BLE 外设，读取：

- GATT Battery Service（`0x180F` / `0x2A19`）→ `batteryLevel`
- GATT Heart Rate Service（`0x180D` / `0x2A37`）→ `ble.heartRate`

结果为独立 device（`platform="ble"`），进 `run/status.json` 与 `/status/`；PS1/OBS 本轮不变。

## 非目标

- 不做 BLE 配对/绑定管理、不写 Windows 配对表。
- 不给 iPhone/Android/主机加"BLE 在附近"探活（iPhone 锁屏广播随机地址、无稳定名称，不可靠；单独议题）。
- 不显示到 OBS 行（展示留 C3）。
- 不做经典蓝牙（RFCOMM/A2DP）状态。

## 设计

### 1. 配置（新顶层键 `BleDevices`，可选）

```json
"BleDevices": [
  {"id": "airpods", "name": "AirPods Pro", "address": "AA:BB:CC:DD:EE:FF",
   "services": ["battery"]},
  {"id": "hrm", "name": "心率带", "nameFilter": "Polar", "services": ["heart_rate"]}
]
```

| 键 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `id` | 是 | — | 设备 id，缺失即配置错误（invalid 条目） |
| `name` | 否 | `id` | 显示名，写入 `model` |
| `address` | 二选一 | — | BLE 地址（大小写不敏感，推荐 `AA:BB:...`） |
| `nameFilter` | 二选一 | — | 对 advertised `local_name`（退回 `BLEDevice.name`）做大小写不敏感子串匹配 |
| `services` | 否 | `["battery"]` | 子集：`battery` / `heart_rate`；非法值忽略，全非法则用默认 |
| `heartRateWaitSeconds` | 否 | `3` | 等待第一条心率通知的上限（1..8） |

`address` 与 `nameFilter` 都缺失 → 配置错误。同一轮里多个 BLE 设备**共享一次扫描**。

### 2. 扫描与连接

- 扫描：`BleakScanner.discover(timeout=4.0, return_adv=True)`（必须用 `return_adv` 才有 `rssi` 与 `local_name`）。模块级缓存，TTL 10s；同一轮内多个 BLE 采集器只扫一次。
- 由于 bleak/WinRT 在同一进程里并发跑多个事件循环不可靠，**扫描与连接都用模块级锁串行化**。
- 匹配：先 `address`（大写规范化后精确匹配），再 `nameFilter`（`local_name` 优先，退回 `name`）。
- 连接：`BleakClient(address, timeout=5)` → `connect()` → 按 `services` 读/订阅 → `disconnect()`。
- 单设备最坏耗时：扫描 4s（共享）+ connect 5s + 读 1s + 心率等待 ≤3s；配置 ≤2 个 BLE 设备时单轮 <15s 目标仍成立，README 写明该建议。

### 3. 输出（schema 3，只增不改）

```json
{
  "id": "airpods", "platform": "ble", "model": "AirPods Pro",
  "batteryLevel": 85, "isCharging": null,
  "mediaTitle": null, "mediaArtist": null, "mediaState": "unknown",
  "foregroundApp": null, "foregroundAppName": null,
  "ble": {
    "address": "AA:BB:CC:DD:EE:FF", "name": "AirPods Pro", "rssi": -61,
    "services": ["battery"], "connected": true,
    "heartRate": null, "statusText": null, "lastSeenAt": 1793458293
  },
  "transport": {"active": "ble", "degraded": false, "online": true,
                "lastSeenAt": 1793458293, "detail": "AA:BB:CC:DD:EE:FF"},
  "error": null
}
```

语义：

| 状态 | `ble.connected` | `transport.online` | `transport.degraded` | `statusText` | `error` |
| --- | --- | --- | --- | --- | --- |
| 扫描到 + 读取成功 | true | true | false | null | null |
| 扫描到 + 连接/读取失败 | false | true | true | `已发现未连接` | null |
| 扫描不到 | false | false | false | `未发现` | null |
| 配置错误（缺 id/地址/名称过滤） | — | — | — | — | 配置错误文本（invalid 条目） |
| 缺 bleak 依赖 | — | — | — | — | `缺少 BLE 依赖：bleak`（不拖垮其它设备） |

- `degraded` 定义：请求了 `battery` 但没读到，或请求了 `heart_rate` 但等待期无通知，或未连接。
- `ble.lastSeenAt`：扫描到的时间；扫描不到为 `null`。
- 心率只在连接成功后读取，读不到只是 `heartRate=null`，不是错误。
- 电量取 `0x2A19` 首字节（0-100）。

### 4. 依赖

`requirements.txt` 增加 `bleak==3.0.2`（本机已装，仅声明以便复现）。bleak 与全部 `winrt-Windows.Devices.Bluetooth*` 已是 `bleak` 的依赖，无需另装。

### 5. `status_core.py`

- `build_collectors` 读取 `config["BleDevices"]`，每项一个 `BleDeviceCollector(entry)`；构造期 `CollectorError` → invalid 条目（platform `ble`）。
- 其它并发/分批/顺序逻辑不变。

### 6. C# daemon

- 新增 `BleStatus` 强类型 + `DeviceStatus.Ble`；`WithAlias` 复制；`status.json` 透传（无对象则键缺失，规则同 `console`/`node`/`transport`）。
- `PlatformIcon` 不需要为 `ble` 加图标（PS1 不变）；`IsAvailable` 不需要改。

## 测试（stdlib unittest，全离线，mock bleak）

- 配置校验：缺 `id`、既无 `address` 也无 `nameFilter`、`services` 非法、`heartRateWaitSeconds` 越界。
- 匹配：address 命中（大小写不同）、nameFilter 命中 advertised `local_name`、退回 `BLEDevice.name`、不匹配。
- 扫描缓存：同一轮两个采集器只调用一次 `scan_ble`。
- 电量解析：`0x2A19` 单字节 → `batteryLevel`；越界/空 → null。
- 心率：订阅回调收到值 → `heartRate`；等待超时 → null 且不报错。
- 状态语义：读取成功 / 连接失败（online true + degraded true + statusText）/ 扫描不到（online false + `未发现`）/ 缺 bleak（error 条目）。
- `status_core`：`BleDevices` 实例化、非法配置隔离、空配置行为不变。
- 真实 BLE 不参与单测（`BleakScanner`/`BleakClient` 全部 mock）。

## C0 / 验证边界（写入 `docs/C1-C2b-C0-checklist.md`）

- 真实 BLE 外设（耳机/手环/心率带）的地址、广播名与 RSSI。
- Windows 蓝牙无线电关闭/飞行模式时的行为与错误文案。
- 2 个 BLE 设备时的单轮总耗时（目标 <15s）。
- 电量/心率读数与厂商 App 对照。
- bleak 在目标机 `.venv` 的安装验证（`pip install -r requirements.txt` 后）。

## 实现备注（2026-10-31）

- 新建 `collectors/ble_collector.py`；`scan_ble` / `read_ble_services` 为可 mock 的模块级函数，`_SCAN_LOCK` / `_BLE_LOCK` 串行化，扫描缓存 TTL 10s。
- bleak 3.0.2 实际 API：`discover(timeout=4.0, return_adv=True)` 返回 `{address: (BLEDevice, AdvertisementData)}`；`AdvertisementData.local_name` / `rssi` 可用；`BleakClient(address, timeout=5)` + async `connect/read_gatt_char/start_notify`。
- 偏差（spec 未钉死处）：`heartRateWaitSeconds` 越界钳制到 1..8 而非配置错误；扫描不到时 `ble.address` 回填配置地址（`nameFilter` 配置则为 null）；已连接但读数缺失时 `degraded=true`、`statusText=null`（只有未连接才写 `已发现未连接`）；`nameFilter` 仅在 `local_name` 为空时退回设备名。
- C# 新增 `BleStatus` 并在 `status.json` 透传（无对象键缺失）；`WithAlias` 复制。
- 单测：164 项（1 项真实扫描集成默认跳过），全离线；`requirements.txt` 增加 `bleak==3.0.2`。
- 端到端：真实扫描路径 + 未发现语义 + `/status/` 透传已验证；真机 BLE 外设读数见 `docs/C1-C2b-C0-checklist.md` C1-12…C1-16。
