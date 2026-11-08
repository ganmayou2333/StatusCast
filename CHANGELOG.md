# Changelog

All notable changes to StatusCast are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

- Planned: AMS media metadata, Discord RPC adapter, MSIX packaging and login autostart.

### Fixed — 稳定性：轮次预算、看门狗、无界等待（`docs/specs/occupancy-and-stability.md`）

先量化再动手：固件 flash 74% 空闲、DIRAM 56%、Python 单轮 0.51 s、daemon private 14 MB
且无跨轮增长、发布体积 0.21 MB。**占用不是瓶颈**。真正的缺口在轮次失控：

- **单轮最坏 ≈84.6 s vs 30 s 周期**（LAN 29.6 + BLE 25 + PC ≤30，串行），而 spec 自己
  写的是「一轮上限 30 秒」。现在每轮计算 `budget = clamp(周期−2s, 3s, 25s)`，
  LAN 在每台主机与每个 TCP 端口前、BLE 在每台设备前、PC feed 在取数前都按剩余预算收手，
  **已完成的结果照常发布**。BLE 阶段预算 20 s → 8 s。
- **看门狗形同虚设**：只查 idle 任务、`PANIC` 未开、没有任何任务订阅——卡死既不记录也不重启，
  `/health` 继续把旧快照报成最新。现在 `sc_sched` 订阅任务看门狗并在阶段边界喂狗，
  `CONFIG_ESP_TASK_WDT_PANIC=y` 写入 `sdkconfig.defaults`。**真机重启行为未验证**。
- **三处无界等待**：BLE 轮次信号量 `portMAX_DELAY` → 20 s；串口 `cfg put` 粘贴循环
  `portMAX_DELAY`（且 `read<=0` 时无限重试）→ 空闲 60 s 后放弃；`/control/*` 请求体读取
  在 `HTTPD_SOCK_ERR_TIMEOUT` 后无限重试 → 总时长 5 s。这三处是「单轮卡死」的真实入口。
- **Python 轮次预算**（`ROUND_BUDGET_SECONDS = 25`，低于 daemon 的 30 s 强杀）：
  在采集器开始前与批次之间检查；未能开始的采集器产出一条
  `采集预算用尽（本轮未完成）` 错误行，已完成的照常返回。
  光有这一层不够——Android 采集器排在第二位、一旦启动就可能连出 ~46 s 的 adb 超时，
  超过 daemon 的 30 s 强杀。所以 `BaseCollector` 新增 `deadline` 属性与
  `budget_timeout()`，Android 的每次 `_run_adb` 都按剩余预算截断超时、无剩余则直接放弃；
  预算耗尽时跳过可选媒体采集。实测：预算 0.001 s 时 0.001 s 返回完整 schema-3 快照，
  而不是进程被杀、所有设备变 stale。
- **`run/console_state.json` 有界**：`failStreak` 钳到 2（`FAILS_TO_OFFLINE`，原为字面量 2）
  且成功时归零；不在 `config.json` 里的主机在写入时被清理（只删本采集器自有字段的记录，
  外来键保留）；存储值非法不再抛异常。
- **PC 侧护栏**（`Services/MemoryGuard.cs`）：每 10 轮记一行
  `[MEM] private=… MB threads=… handles=…`；private > 96 MB、线程 > 64、或最近 5 次采样
  单调上升时告警，闩锁至条件解除后重新武装。不新增端点、不改契约。

证据口径：R1–R5 通过（host 255 checks / 0 failures、固件构建零告警、C# 63 项通过），
**小时~天级长时间退化未证明**。

### Fixed — ESP32 串口工具不再猜端口（issue #5）

- `flash.ps1` / `monitor.ps1` / `serial-command.ps1` 的 `-Port` 默认值从 `COM3` 改成
  `auto`。串口号每次插拔都会变（本机实测 COM3 → COM4），而写死一个数字有两种坏结果：
  命令失败，或者**成功作用在错的端口上**——这些工具打开端口时会拉 DTR/RTS 复位目标板。
- 新增 `tools/ports.ps1`：按 USB VID 找板子（`303A` = Espressif 原生 USB-Serial-JTAG、
  `1A86:55D3` = CH343、`1A86:7523` = CH340）。只有一块板时直接用；多块板**拒绝并列候选**，
  而不是替用户猜；显式指定一个存在但不像板子的端口会**警告**「打开它会复位别的设备」。
  端口不存在时报错并列出本机实际端口，附一句「用 -Port auto」。
- 顺带修正一处硬件认知错误：参考板实际走的是 **ESP32-S3 原生 USB-Serial-JTAG
  (VID_303A&PID_1001)**，不是文档原先写的 CH343；CH343 是同一块板上另一个 USB 口。
  只认 CH343/CH340 会让 `auto` 在真板上直接找不到设备。
- 新增 `tools/test-ports.ps1`：注入候选设备的自检，覆盖八种情况（单板、CH343、无板、
  多板、显式端口不存在、显式外设端口、小写 com4、非 COM 值），`8 checks, 0 failures`。

### Fixed — ESP32 灯带的上报与恢复（issue #7）

- **手动覆盖期间上报的颜色是假的**：`POST /control/led` 把状态机标成 `OFF`，于是
  `/diag` 与 `/control/status` 一边显示像素亮着、一边报 `"led":"off"`。
  现在 `led` 报的是**实际显示的颜色**，另加 `ledMode`（`auto` / `manual`）与
  `ledAvailable`（灯带是否初始化成功）两个字段——「像素是什么颜色」和「谁在做主」
  不再挤在同一个值里。
- **关闭覆盖可能让像素永久卡住**：`{"auto":true}` 只清了标志位，没有重绘；而
  `status_led_set()` 在状态未变时会跳过重绘，所以只要自动状态当时也是 `off`，
  像素就一直停在手动颜色上。现在两个 setter 都返回「是否需要重绘」，关闭覆盖会
  **当场按当前自动状态重绘**。
- 顺带把调色板与状态判定从 `ui/status_led.c` 抽到不依赖 ESP-IDF 的 `core/sc_led.c`：
  颜色表、名字解析、auto/manual 策略现在都能在 host 上测。`POST /control/led` 的四个
  颜色名也由同一张表解析，不再在 handler 里重复写 RGB 常量。
- `/control/status` 与 `/diag` 的响应缓冲加了余量（768→1024、448→512）。

### Fixed — ESP32 固件（由 bug hunt 发现，见 `docs/test-reports/v3.4.0-bug-hunt.md`）

- **快照容量**：渲染缓冲、`/status/` 响应副本与路由表槽位原本是三个互不知情的数字，
  8192 只装得下 8 台带 `nekotrak` 的设备；第 9 台起 `sc_snapshot_render()` 返回 0，
  整轮**不发布**——板子永远返回上一轮快照，`/health` 却报旧计数为最新。
  现在三处统一到 `core/sc_limits.h` 的 `SC_SNAPSHOT_JSON_BUDGET`（73728），
  `app_main.c` 与 `http_server.c` 各加 `_Static_assert`，host 测试用**填满全部字段的
  16 行**验证预算真的够（实测 65,985 字节）。响应副本改从 PSRAM 分配。
- **透传子树静默丢弃**：`nekotrak.pet` / `pomodoro` 序列化后超过 447 字节时整个字段消失，
  无日志无错误字段；参考机真实 `pet` 已达 309 字节。缓冲提到 512，放不下时改为向
  `nekotrak.warns` 追加一条说明。
- **UTF-8 截断产生替换符**：95 字节截断会劈开多字节序列，转义层随后为每个残留字节吐一个
  `\uFFFD`（中文 2 个、emoji 3 个）。改为按字符边界回退。
- **`.NET-style Trim()` 名不副实**：只剪 ASCII 空白，NBSP / U+3000 在固件里保留而在 daemon
  里被剪掉，两侧字节不一致。改用 .NET `char.IsWhiteSpace` 的完整空白集合。
- **`/health` 就绪判定与 daemon 不一致**：cJSON 容忍尾部垃圾而 `System.Text.Json` 不容忍，
  同一份 body 固件 200、daemon 503。改为校验解析后只剩空白。
- **`/diag` 在无 I2C 设备时输出非法 JSON**：`app_state.c` 里 `out[0] = '\0'` 写成了
  多字符常量 `'\\0'`，实际写入 ASCII `'0'`（0x30）；而调用方是未初始化栈缓冲，
  没接屏的板子上 `/control/status` 同样受影响。修完后 `main/` 构建告警归零。

## [3.4.0] - 2026-11-07

### Added — ESP32 固件（`firmware/esp32/`，experimental）

- 新增独立状态服务器移植：ESP32-S3（N16R8，16MB Flash / 8MB PSRAM）跑 ESP-IDF 5.5.6，
  自己探测局域网主机、读取 BLE 外设，并通过 HTTP 拉取 PC daemon 的 `/status/` 补齐
  iPhone / Android / NekoTrak 条目。
- 对外提供与 daemon 字节级兼容的 `GET /status/` 与 `GET /health`：端口 5099、schema 3、
  13 键固定顺序、加法对象按 `console → node → transport → ble → nekotrak` 追加、非 ASCII
  一律 `\uXXXX` 转义（PS 5.1 零乱码）、404/405/503 精确字节。
- 契约核心（投影 / 别名 / 转义 / 路由 / 就绪判定 / 迟滞 / 配置解析）抽成不依赖 ESP-IDF 的
  `main/core/`，host 侧 193 项检查对照 `schema/status-contract.json` 全绿。
- SSD1315 128×64 单色屏：总览页 + 设备轮播（全 ASCII 字库）；屏缺失时只打日志、业务不受影响。
- 配置：LittleFS `config.json`（与 PC 侧字段名一致，另有 `PcFeed` 键）+ 编译期默认镜像 +
  串口 `cfg put` 粘贴模式；WiFi 凭据只进 NVS；串口命令 `cfg` / `net` / `json` / `stats` /
  `heap` / `reboot`。
- 真机已确认：芯片/Flash/PSRAM 识别、LittleFS 挂载、HTTP 监听 `0.0.0.0:5099`、NimBLE 初始化、
  30 秒轮询与快照渲染、配置写入与别名生效。
- 真机端到端已验证（2026-11-06，COM3）：WiFi 2.4G 接入并拿到 `192.168.1.100`（5G SSID 不可见属硬件限制）、
  `GET /status/` 与 `GET /health` 在局域网逐字节符合契约、404/405 精确、网关探测 `online:true` +
  `method:"ping"`、不可达主机两轮后 `statusText` 走 `offlineText`、BLE 扫描 16 台设备并能连接、
  经 `tools/pc_feed_relay.py` 合并 PC 的 iphone/android/nekotrak（含 `currentGame` 与 `pet` 透传）、
  停 daemon 后按 daemon 语义降级为 `读取超时`、NekoTool `ultimate_status.ps1` 输出
  `🎮 Home Router 在线 | 🎮 Ghost 已关机 | 📶 Mijia Scale 已连接`。
- 屏幕已在真机验证（2026-11-06，`SSD1315 ready at 0x3C (sda=8 scl=9)`，总览页与轮播已上屏）。
  仍未验证：心率带 `0x180D` 通知读取。

### Fixed

- **固件 HTTP handler 表槽位不足**：`max_uri_handlers` 只按公开路由的数量分配（10），
  实际注册 20 个，导致 `GET`/`DELETE /control/coredump`、`GET /control/audit` 以及七个
  通配 405 处理器在启动时被静默丢弃——「任何非 GET 返回 405」实际变成了 404。
  路由计数收进 `core/sc_route_counts.h`，两张路由表各加 `_Static_assert`，host 测试断言总数。
- daemon 启动横幅不再硬编码版本号，改为读程序集版本。此前 `<Version>` 升到 3.4.0 后
  横幅仍打 `v3.3.0`，而 `UpdateChecker` 比较的正是程序集版本。
- `firmware/esp32/tools/host-test.ps1` 之前根本跑不起来：契约路径多退了一级目录，且
  `-DSC_CONTRACT_PATH` 的引号被 PowerShell 吃掉，编译器把 `G:/…` 当成标识符。同时补上
  `-DSC_FIXTURE_DIR`，daemon 快照往返测试不再被静默跳过。
- 六处文档与实现不符：`/health` 形状（真实是 `{ok, deviceCount, generatedAt, schema}`）、
  固件「WS2812 本版未驱动」（早已驱动）、`firmware/esp32/README.md` 里不存在的 `tools/build.ps1`、
  固件 API 列表缺 `/diag` 与 `/control/*`、固件「局域网内无鉴权」（`/control/*` 需要令牌）、
  固件里 `.\scripts\ultimate_status.ps1` 指向不存在的目录。

### Changed

- **文档结构重写**：README 缩成门面（是什么 / 截图 / 快速开始 / 文档索引），细节下沉到
  `docs/guide/` 的安装、设备采集、HTTP 契约、控制面与排障五篇；新增 `docs/README.md` 索引、
  `GLOSSARY.md` 与 `docs/adr/0001-control-surface-static-token.md`。
- 采集器注册点在文档里改为 `build_collectors()`（原文写的是 `collect_all()`）。
- 加法对象数量在文档里改为五个（原文写四个，漏了 `nekotrak`）。
- `docs/*-v3.4.0.md` 两篇社区发帖稿移到 `docs/posts/`，并标注其中五张截图未被仓库收录。
- `.scratch/` 停止被 git 跟踪（本地保留），因为里面的真机笔记带有设备 MAC、局域网地址与 SSID。
- 新增固件 CI：host 单测每次跑；ESP-IDF 全量构建只在打 tag 与手动触发时跑。
- `release` 工作流不再把整份 `CHANGELOG.md` 当 release 正文，只取当前版本那一节。
- README 顶部改用 `docs/images/` 下的实际截图；此前引用的 `docs/screenshots/*` 从未提交过。

### Added — PC 侧（daemon / collectors）

- `collectors/nekotrak_collector.py`: NekoTrak on this PC becomes a device of
  its own (`platform: "windows"`, `id: "nekotrak"`) with an additive
  `nekotrak` object — version, uptime, TRACKER state, pet, current game and
  `warns`. Liveness is a double check (heartbeat file rewritten within 60 s or
  a live `NekoTrak.exe`); anything unreadable degrades one field plus a
  warning, and only "not running" becomes the standard error entry. The
  collector is registered only when the NekoTrak data root exists, so a
  machine that never ran it keeps byte-identical output.
- `schema/status-contract.json`: new `nekotrak` additive object with its field
  list, asserted on both the Python and the C# side. The daemon decodes and
  passes it through (`NekoTrakStatus`), keeps it in `WithAlias` /
  `ToStaleDevice` clones, and renders `platform: "windows"` with 🖥.
- `GET /health` now answers `{ok, deviceCount, generatedAt, schema}` read from
  the snapshot, with **503** while no usable snapshot exists, and both
  `localhost` and `127.0.0.1` prefixes are registered independently.

- `collectors/iphone_wireless_test.py`: independent iPhone Wi-Fi discovery probe
  (usbmuxd `Network` first, Bonjour/mobdev2 fallback). It is deliberately not
  imported by `status_core.py` and is not a registered collector.
- `collectors/console_collector.py`: C1 LAN (L0) console probe — ARP via
  `iphlpapi.SendARP` with ICMP `ping` fallback, hysteresis persisted to
  `run/console_state.json` (offline only after 2 consecutive failures, one
  success is online again). Reports an additive `console` object (`online` /
  `method` / `lastSeenAt` / `mac` / `idleHint` / `name` / `type` /
  `statusText`); an offline console is a normal `error: null` entry whose
  message comes from the per-console `offlineText` config key (default
  `离线`).
- `collectors/ssh_node_collector.py`: C2b SSH node collector for Mac / Linux
  machines (stdlib `subprocess` + Windows OpenSSH, `BatchMode` key-only,
  `IdentitiesOnly=yes`, `StrictHostKeyChecking=accept-new` TOFU,
  `ConnectTimeout=2`, 8 s cap per node); `sshKey` expands `~` and `%VAR%` and
  a missing path is a config error. Battery and model map onto the standard
  schema-3 fields (Linux multi-battery takes the first battery), desktops
  without batteries are a normal null, not an error. A `node` optional object
  (`ssh` / `method` / `source` / `hostname` / `lastSeenAt` / `uptimeSeconds`)
  rides along in the Python payload.
- `status_core.py` now runs collectors in a `ThreadPoolExecutor` (max 8
  workers) so node timeouts never stack, loads `config.json` (`Consoles` /
  `SshNodes` lists; missing or broken config keeps the v3.3 behaviour), and
  accepts `--config`.
- `tests/`: offline unittest suite with fixtures covering ARP/ping hysteresis,
  SSH output parsing, per-device fault isolation and config loading.
- C# daemon now passes the additive `console` / `node` objects through to
  `run/status.json` and `/status/` instead of dropping them.
- When the Python collection as a whole fails (timeout, crash or non-zero
  exit), the daemon keeps the previous round's devices and marks them
  `error: 读取超时` instead of emptying the snapshot. The runner reports that
  failure explicitly, so a normally disconnected phone (both phone collectors
  erroring) is never mistaken for a stale round.
- Daemon `PlatformIcon` renders `console`/`steam` as 🎮, `macos` as 💻 and
  `linux` as 🐧; hard fallback entries remain ios/android only.
- `docs/iphone-wireless-notes.md`: v3.4 research record, test commands, observed
  failures and the decision to keep USB as the stable iPhone transport.
- Shared LAN probe layer (`lan_probe`, ARP → ICMP ping) reused by the iPhone,
  Android, console and SSH node collectors.
- iPhone: LAN fallback via `IPhone.ip` after a USB failure, plus an experimental
  Wi-Fi data channel behind `wifiDataChannel` (usbmuxd `Network` →
  Bonjour/mobdev2). The Wi-Fi channel defaults to off and is expected to remain
  unavailable with the current Apple services.
- Android: wireless debugging auto-discovery via `adb mdns services` followed by
  `adb connect`, with Android 11+ `adb pair` pairing (`pairPort` / `pairCode`).
  The pairing code is only used for that pairing round and is never logged,
  persisted or written into the snapshot.
- Consoles: TCP port probing (`tcpPorts`, default `[9295, 3074, 80, 443]`,
  300 ms connect timeout per port) after ARP and ICMP both fail.
- SSH nodes: LAN fallback after an SSH failure (`SshNodes[].fallback`) reporting
  degraded online/offline with `error: null` and `node.ssh=false`.
- New optional device-level `transport` object (`active` / `degraded` /
  `online` / `lastSeenAt` / `detail`) and new optional config keys
  `IPhone.{ip,fallback,wifiDataChannel}`,
  `Android.{ip,port,pairPort,pairCode,fallback}`,
  `Consoles[].{tcpPorts,fallback}` and `SshNodes[].fallback`. `fallback`
  defaults to `true`; `false` restores the previous "primary channel failure is
  an error" behaviour.
- `collectors/ble_collector.py`: BLE peripheral collection behind the new
  `BleDevices` config section (`id` / `name` / `address` or `nameFilter` /
  `services` / `heartRateWaitSeconds`). One shared 4 s `BleakScanner` scan per
  round, read-only GATT reads of Battery (`0x180F` / `0x2A19`) and Heart Rate
  (`0x180D` / `0x2A37`), additive `ble` object (`address` / `name` / `rssi` /
  `services` / `connected` / `heartRate` / `statusText` / `lastSeenAt`) and
  `transport.active="ble"`. Scan misses and "found but not connectable" are
  degraded states, not errors.
- `requirements.txt` now declares `bleak==3.0.2` for BLE devices (already
  present in the development venv).
- `tests-csharp/StatusCast.Daemon.Tests`: xunit-based test suite for the daemon
  (device parsing incl. malformed additive objects, `status.json` projection,
  `WithAlias`, `IsAvailable`, stale-row fallback, `ProjectLocator`, request
  routing). Ships a tiny reflection runner (`dotnet run`) because the VSTest
  testhost aborts in restricted shells; the tests are ordinary xunit facts.
- `schema/status-contract.json`: single source of truth for the schema-3 device
  shape (`collectorDeviceFields`, `snapshotDeviceFields`, `additiveObjects`,
  `additiveFields`). Both the Python suite (`tests/test_contract.py`) and the C#
  suite assert their output against it.
- CI: the daemon workflow now builds and runs the C# suite (including the
  loopback `/health` + `/status/` test), and a new `python-tests` workflow runs
  the offline unittest suite on Linux.
- `GET /health` on the daemon's loopback server, returning
  `{"status":"ok","snapshotReady":true|false}`.
- `collectors/base.py` exposes `error_entry()`; the dispatcher's invalid-config
  rows and `BaseCollector.error_payload()` now share one implementation.

### Changed

- `/health` is now actually served (it was logged as `[API] 健康检查 …` before
  but never registered). The server listens on `http://localhost:{port}/` and
  routes `/status/` and `/health`.
- `STATUSCAST_ROOT` is now authoritative: an explicit root without
  `status_core.py` makes the daemon report "找不到项目根目录" instead of silently
  walking up into an unrelated parent checkout and collecting the wrong config.
- Console devices no longer carry an offline `error`: `error` stays `null`
  and the offline state is `console.online=false` with the message in
  `console.statusText`. Consumers that keyed on `error` for consoles must
  switch to `console.online`.
- Documented that iPhone Wi-Fi collection is deferred on Windows: the
  Microsoft Store "Apple Devices" AMDS does not discover Network devices, and
  a classic iTunes + Apple Mobile Device Support 19.4.0.10 installation also
  failed to register a `Network` device on iOS 27.0. Bonjour/mobdev2 can only
  establish lockdown before the `diagnostics_relay` service connection is
  terminated by the device.
- Documented that iPhone Now Playing/media metadata is not available through
  `pymobiledevice3`; the iPhone collector keeps `mediaTitle`/`mediaArtist`/
  `mediaState` as placeholders, while Android media collection is unchanged.

## [3.3.0] - 2026-10-25

### Added

- Pluggable Python collector architecture under `collectors/`:
  `BaseCollector` (`get_data() -> dict`), `CollectorError`,
  `IPhoneCollector` and `AndroidCollector`.
- `status_core.py` is now the single dispatcher: it runs every collector,
  isolates failures per device and prints one standard schema-3 JSON snapshot.
- `status_core_android.py` kept as a legacy STATUS-line compatibility CLI for
  manual diagnostics.

### Changed

- C# `StatusService` now executes one `status_core.py` process and parses its
  JSON `devices` array instead of running two Python scripts and matching text
  with a regex.
- `StatusCoordinator` consumes the parsed device list, applies aliases and
  writes the snapshot; collection errors no longer abort the whole refresh.
- Bumped the daemon to `<Version>3.3.0</Version>`; startup log and update
  checker user agent follow the assembly version.

### Removed

- Duplicate per-device Python entry points from the daemon call path; all device
  collection now flows through the collector plugins.

## [3.2.0] - 2026-10-25

### Added

- Background GitHub Release check at startup: compares `tag_name` with the
  assembly version, prints a colored `[UPDATE]` notice and the first three
  lines of the release `body`. It never blocks collection and never downloads
  or overwrites the running daemon.
- Android foreground app lookup now cross-checks two `dumpsys` views:
  `dumpsys activity activities` (`topResumedActivity` / `mResumedActivity`)
  first, then `dumpsys window displays` (`mCurrentFocus`) as a fallback.
- `<Version>3.2.0</Version>` in `StatusCast.Daemon.csproj`; startup log now
  reports `v3.2.0`.

### Changed

- Android foreground app parsing is more tolerant of OEM/Android-version
  output differences and ignores transient system packages.

### Removed

- Legacy Python daemon architecture: `core/`, `adapters/`, `cli/`, `legacy/`,
  `integrations/`, `tests/`, `scripts/`.
- Stale build/config artifacts: `build/`, `dist/`, `run/`, `pyproject.toml`,
  `config.toml`, `statuscast-cli.spec`, `requirements-build.txt` and
  `docs/architecture.md`.
- WinUI 3 remnants and any GUI/frontend code; StatusCast is now strictly the
  C# daemon plus the two Python collectors.

## [3.1.0] - 2026-10-24

### Changed

- Removed the WinUI 3 GUI and all Windows App SDK dependencies.
- Added the headless `StatusCast.Daemon` console application.
- Moved `StatusService`, `StatusCoordinator` and `StatusServer` into
  `src/StatusCast.Daemon/Services`.
- Added colored startup/collection logs and graceful Ctrl+C shutdown.
- NekoTool now targets `StatusCast.Daemon.exe` for watchdog startup.

## [3.0.0] - 2026-10-24

### Changed

- StatusCast is now a pure data layer: removed `StatusCast.Core`,
  `IStatusPlugin` and dynamic DLL loading.
- Snapshot schema is now `3`; `status.json` contains only `schema`,
  `generatedAt` and the `devices` array.
- Removed top-level `battery`, `media`, `plugins`, `errors` and `text` fields.
- `config.json` now only contains `Aliases`.
- Plugin and aggregation logic moved to the separate
  [NekoTool](https://github.com/ganmayou2333/NekoTool) repository.

## [2.0.0] - 2026-10-24

### Added

- Android collection via ADB (`status_core_android.py`): battery, model,
  foreground app and best-effort media session.
- Parallel iPhone + Android collection with `Task.WhenAll` and a v2 snapshot
  containing a `devices` array.
- `config.json` with `Aliases` and `EnabledPlugins`.
- WinUI multi-device dashboard, aliases and a plugin status chip.

### Known issues

- Realtek Bluetooth adapter cannot complete BLE pairing with the test iPhone;
  AMS returns `Insufficient Authentication`.
- Android media metadata is best-effort and depends on the media app exposing
  a MediaSession.

## [1.0.0] - 2026-10-23

### Added

- First public stable release.
- USB battery, charging and iPhone model collection via `pymobiledevice3`.
- WinUI 3 desktop app with a dynamic battery arc and 30-second refresh.
- Local HTTP API at `http://localhost:5099/status/` and NekoTrak integration.
- MIT license.

## [0.2.0] - 2026-10-23

### Added

- Initial USB collector and WinUI 3 prototype.
- `run/status.json` snapshot and the local HTTP API.
