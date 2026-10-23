# StatusCast 架构说明

## 1. 设计目标

StatusCast 是「状态采集 → 统一模型 → 多端推送」的桥接层，不是某个展示端的插件。

核心约束：

- **采集与展示解耦**：核心不知道 NekoTrak、Discord 或 Web 面板的存在。
- **数据源可切换**：电量与媒体各自有抽象接口；AMS 不可用时可换成 AirPlay/MQTT。
- **失败降级而不是崩溃**：设备未连接、BLE 断开、AMS 订阅失败都只影响对应字段。
- **展示端读取要快**：PowerShell 被周期性调用，不能每次都重新连接 BLE/USB。

## 2. 分层

```text
┌─────────────────────────────────────────────────────────────┐
│ adapters/                                                   │
│   Adapter.push(Status)                                      │
│   ├─ StdoutAdapter       打印一行                            │
│   ├─ SnapshotAdapter     原子写 JSON 快照                    │
│   └─ NekoTrakAdapter     占位，后续接真实传输                 │
└─────────────────────────────▲───────────────────────────────┘
                              │ Status
┌─────────────────────────────┴───────────────────────────────┐
│ core/                                                       │
│   StatusAggregator.build(BatteryStatus, MediaStatus)        │
│   Daemon: 采集循环 + 适配器 fan-out                          │
└──────────────▲──────────────────────────────▲───────────────┘
               │ BatteryStatus                │ MediaStatus
┌──────────────┴──────────────┐  ┌────────────┴───────────────┐
│ core/sources/battery_usb.py │  │ core/sources/media_ams.py  │
│ pymobiledevice3 / USB       │  │ bleak / BLE AMS            │
└─────────────────────────────┘  └────────────────────────────┘
```

PowerShell 只属于展示端：

```text
scripts/get_status.ps1
  ├─ 读 run/status.json（新鲜）→ 直接输出 text
  └─ 过期/缺失 → 调用 get_battery.py（电量降级）
```

WinUI 3 图形界面（`gui/StatusCast.Gui`）也是薄客户端：

- 每秒读取同一份 `run/status.json`；
- 显示电量、媒体、核心状态和诊断路径；
- 启动/停止 daemon 通过 `run_daemon.ps1` / `--stop` 控制文件；
- 不直接访问 USB 或 BLE，不复制核心逻辑。

## 3. 数据契约

`core.models.Status` 是唯一跨层对象：

```text
Status
├─ battery: BatteryStatus
│  ├─ level: int | None
│  ├─ is_charging: bool | None
│  ├─ updated_at: float | None
│  └─ error: str | None
├─ media: MediaStatus
│  ├─ title / artist / album
│  ├─ playback_state: PlaybackState
│  ├─ duration_seconds / elapsed_seconds
│  ├─ player_name
│  ├─ updated_at
│  └─ error
├─ generated_at: float
└─ errors: list[str]
```

- `display_text()` 负责生成展示行，例如
  `🔋85%⚡ | 🎵 Song - Artist`。
- `to_dict()` 增加 `text` 字段，供 PowerShell 直接读取，避免 PowerShell
  重新实现格式化逻辑。
- 适配器只能读 `Status`，不能反向调用数据源。

## 4. 生命周期

### 电量

- daemon 按 `battery.poll_interval`（默认 10s）通过 USB 读取一次。
- 每次读取建立短连接，读取 `BatteryCurrentCapacity` 和 `BatteryIsCharging`，
  然后关闭。
- 失败返回带 `error` 的 `BatteryStatus`，不会让 daemon 退出。

### 媒体

- `AmsMediaSource.run(stop_event)` 是长期任务：
  扫描 → 连接 → 订阅 Entity Update → 注册 Player/Track 属性 → 等待通知。
- 断线或失败后等待 `media.retry_interval`，重新扫描连接。
- daemon 关闭时取消任务并断开 BLE。
- 通知只更新 `AmsMediaState`，`snapshot()` 无阻塞返回最新 `MediaStatus`。

### 适配器

- 每个循环 daemon 调用一次 `push(status)`。
- 单个适配器抛异常只记录日志，不影响其他适配器。
- `SnapshotAdapter` 使用临时文件 + `os.replace` 原子替换，避免读到半个 JSON。

## 5. 配置与路径

- 单一 `config.toml`。
- 相对路径基于配置文件所在目录解析，不依赖当前工作目录。
- 默认快照：`<project>/run/status.json`。
- 默认日志：`<project>/run/statuscast.log`，滚动保留 3 份备份。
- 配置错误在启动时直接失败并给出中文错误，不带着坏配置运行。

## 6. 运行保护

- 单实例：Windows 命名互斥锁 `Local\StatusCastDaemon`，重复启动会退出；
  进程崩溃时内核自动释放，不会留下过期锁。
- 日志：控制台 + 滚动文件；计划任务以隐藏窗口运行时不依赖终端。
- `scripts/install_task.ps1` 注册当前用户的登录自启任务，支持 `-WhatIf`
  和 `-Uninstall`。

## 7. 失败模式

| 失败 | 行为 |
| --- | --- |
| iPhone 未连接 USB | 电量字段为 error，daemon 继续运行 |
| usbmuxd 未运行 | 同上，提示安装 iTunes / 启动服务 |
| BLE 关闭/无 AMS | 媒体字段为 error，按间隔重扫 |
| AMS 未认证 | 媒体字段提示需要配对，daemon 继续运行 |
| BLE 断开 | 设置错误并重连，保留最后一次媒体状态 |
| 适配器异常 | 记录日志，继续推送其他适配器 |
| 快照过期 | PowerShell 回退到电量直连，不影响展示 |
| config.toml 错误 | 启动失败，退出码 2 |

## 8. 扩展点

- 新展示端：实现 `adapters.base.Adapter.push(status)`，在
  `adapters/factory.py` 注册，在 `config.toml` 增加开关。
- 新媒体源：实现 `core.sources.base.MediaSource`，在
  `core/sources/factory.py` 注册，配置 `media.source` 即可切换。
- 新电量源：实现 `BatterySource.read()`，在数据源工厂中注册。
- 备选 AirPlay：`core/sources/media_airplay_mqtt.py` 已提供占位，配置
  `media.source = "airplay_mqtt"` 可验证切换路径；实现 MQTT 订阅后即可用。
- daemon 通过构造函数接收 `battery/media/adapters`，生产用
  `Daemon.from_config`，测试可直接注入 fake 实现。

## 9. 待办

- 完成 AMS BLE 配对并在真机验证通知。
- 用真实 iPhone 验证电量读取。
- NekoTrak 适配器真实传输。
- 实现 AirPlay/MQTT 备选源。
- 真机验证后开启登录自启并观察长时间运行稳定性。
