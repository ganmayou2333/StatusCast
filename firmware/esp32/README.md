# StatusCast ESP32

把 StatusCast 从「Windows 上的数据层」移植成一台**不依赖 PC 也能独立工作**的状态服务器：
ESP32-S3 自己探测局域网主机、读取 BLE 外设，并通过 HTTP 拉取 PC daemon 的快照补齐手机 /
NekoTrak 数据，最后以**与 daemon 字节级兼容**的 schema-3 JSON 对外提供服务，同时驱动一块
128×64 单色 OLED。

设计文档与分票：`.scratch/esp32-port/`（`spec.md` + `issues/`）。

## 定位

| | PC daemon | ESP32 固件 |
| --- | --- | --- |
| iPhone / Android / NekoTrak | ✅ Python collectors（USB/ADB/本地文件） | ❌ 改为拉取 PC daemon 的 `/status/` |
| 局域网主机存活 | ✅ ARP → ping → TCP，带迟滞 | ✅ ping → TCP，带迟滞（**无 ARP**） |
| BLE 外设电量 / 心率 | ✅ bleak / WinRT | ✅ NimBLE central |
| SSH 节点 | ✅ OpenSSH | ❌ v2 |
| HTTP API | `localhost:5099`（loopback only） | `0.0.0.0:5099`（局域网可达） |
| 本机显示 | 无 | 128×64 OLED，总览页 + 轮播 |

两者共用同一份 `config.json` 语义与 `schema/status-contract.json` 契约；PC daemon 退化为
「手机数据源」，NekoTool 只需把 `-StatusUrl` 指向 ESP32。

## 硬件

已在实机确认（`python -m esptool --port COM3 flash-id`）：

```text
Chip:      ESP32-S3 (QFN56) rev v0.2, 双核 240MHz + LP 核
Memory:    Embedded PSRAM 8MB (AP_3v3)
Flash:     16MB (quad, 3.3V)
USB-UART:  CH343 (VID_1A86&PID_55D3) → COM3
MAC:       dc:da:0c:xx:xx:xx
Display:   SSD1315 128×64, I2C, SSD1306 兼容指令集
```

接线（板子原理图 `ESP32-S3-COREBOARD` 确认，模块 4 针丝印顺序为 `GND / VCC / SCL / SDA`）：

| 屏幕针脚 | ESP32-S3 引脚 | 板子位置 |
| --- | --- | --- |
| GND | GND | J1 的 GND（或 J2 的 GND） |
| VCC | **3V3** | **J1 pin 1** |
| SCL | **GPIO9** | **J1 pin 14** |
| SDA | **GPIO8** | **J1 pin 11** |

> 不要接 5V：模块逻辑电平会跟着 VCC 走，5V 上拉对 ESP32 不安全。板载 WS2812 的数据脚是
> **GPIO48**（本版未驱动，留作状态灯）。

> 地址 `0x3C`，驱动会自动探测 `0x3D`。屏不是硬依赖：初始化失败只打日志，采集与 HTTP 照常。

分区表默认 `partitions_16mb.csv`（5MB × 2 OTA 槽 + 4MB LittleFS + coredump）。若换成 8MB
板子，改 `sdkconfig.defaults` 里的 `CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions_8mb.csv"`。

## 构建与烧录

依赖 **ESP-IDF 5.5.6**（离线安装包，自带 embedded Python / cmake / ninja / xtensa 工具链）。
本机安装在 `E:\Espressif\frameworks\esp-idf-v5.5.6`。

```powershell
# 构建
.\tools\build.bat build

# 烧录 + 串口监视
.\tools\flash.ps1 -Port COM3 -Monitor

# 只开串口监视
.\tools\monitor.ps1 -Port COM3
```

> 串口号会在重新插拔后变化（本机实测从 COM3 变成 COM4）。以设备管理器里 CH343 的端口为准，
> 所有命令都接受 `-Port COMx`。

`tools\build.bat` 内部会调用 IDF 的 `export.bat`（并先清掉 `MSYSTEM`，因为 ESP-IDF
拒绝在 MSYS/Mingw 环境里运行），不污染全局环境。手动等价命令：

```powershell
. E:\Espressif\frameworks\esp-idf-v5.5.6\export.ps1
idf.py set-target esp32s3
idf.py build
idf.py -p COM3 -b 460800 flash monitor
```

## host 侧单测（不需要板子）

契约核心（13 键投影、additive 白名单、别名、`\uXXXX` 转义、路由、迟滞、配置解析）
被抽成 **不依赖 ESP-IDF** 的纯 C 模块，在 PC 上直接编译运行，并对照
`../../schema/status-contract.json` 校验键顺序。

```powershell
.\tools\host-test.ps1
```

## 配置

`config.json` 放在 LittleFS 根目录，字段名与 PC 侧一致（PC 侧会忽略它不认识的 `PcFeed`）：

```json
{
  "RefreshIntervalSeconds": 30,
  "Aliases": {"PS5": "PS5", "iPhone15,3": "主力机"},
  "Consoles": [
    {"id": "ps5", "name": "PS5", "type": "ps5", "ip": "192.168.1.50",
     "offlineText": "离线", "tcpPorts": [9295, 3074]}
  ],
  "BleDevices": [
    {"id": "hrm", "name": "HRM", "nameFilter": "Polar",
     "services": ["heart_rate"], "heartRateWaitSeconds": 3}
  ],
  "PcFeed": {"enabled": true, "url": "http://192.168.1.10:5099/status/",
             "timeoutSeconds": 5}
}
```

首次启动会写入一份编译期默认配置；之后用串口命令改写：

| 命令 | 作用 |
| --- | --- |
| `help` | 列出命令 |
| `cfg show` | 打印当前配置（WiFi 密码不回显） |
| `cfg put <json>` | 整份替换并落盘到 LittleFS |
| `cfg reset` | 恢复编译期默认 |
| `net ssid <ssid>` / `net pass <pass>` | 写入 NVS 的 WiFi 凭据 |
| `net status` | 打印连接状态 / IP / RSSI |
| `heap` | 打印空闲堆与最小水位 |
| `reboot` | 软重启 |
| `net scan` | 扫描 2.4GHz 热点（ESP32-S3 无 5GHz 射频） |
| `cfg dump` | 打印 LittleFS 里原始的 `config.json` |
| `ble scan` | 扫描附近 BLE 广播（含地址/RSSI/名称） |

**WiFi 凭据只存 NVS**，不写 LittleFS、不进仓库、不进日志。

首次配网（串口命令行，`COM3` 为例）：

```powershell
.\tools\serial-command.ps1 -Port COM3 -Lines 'net scan'      # 先确认能看到 2.4G 热点
.\tools\serial-command.ps1 -Port COM3 -Lines 'net ssid <ssid>','net pass <password>'
```

> ESP32-S3 只有 2.4GHz 射频。名字带 `-5g` 的 SSID 不会出现在 `net scan` 里，也无法连接；
> 请使用路由器的 2.4GHz 同名热点。

## HTTP API（与 daemon 兼容）

- `GET /status/` → 200 快照；未就绪 → 503 `{"error":"status snapshot not ready"}`
- `GET /health` → 200 `{"ok":true,"deviceCount":N,"generatedAt":G,"schema":3}`；未就绪 → 503 四键全 null
- 非 GET（含未知路径）→ 405 `{"error":"method not allowed"}`；其余路径 → 404 `{"error":"not found"}`
- 响应头固定带 `Access-Control-Allow-Origin: *`、`Cache-Control: no-store`、
  `Content-Type: application/json; charset=utf-8`
- 设备行 13 键顺序固定，加法对象按 `console → node → transport → ble → nekotrak` 追加，
  缺失时整键省略；非 ASCII 一律 `\uXXXX` 转义（PS 5.1 零乱码）

### PC 数据源（iPhone / Android / NekoTrak）

daemon 的 `HttpListener` 只注册 `localhost` / `127.0.0.1`，局域网里的 ESP32 拉不到它。在 daemon
增加 opt-in LAN 绑定之前，用仓库自带的中继把只读端点转发到局域网：

```powershell
python .\tools\pc_feed_relay.py --listen-port 5100
```

然后让 `config.json` 指向它：

```json
"PcFeed": {"enabled": true, "url": "http://<PC-IP>:5100/status/", "timeoutSeconds": 5}
```

中继只转发 `GET /status/` 与 `GET /health`，其余方法返回 405；daemon 停掉时 ESP32 会按
daemon 的 stale 语义把 PC 条目标成 `读取超时`（电量清空、`transport` 丢弃、`nekotrak` 保留）。

NekoTool 接入：

```powershell
.\scripts\ultimate_status.ps1 -StatusUrl http://<esp32-ip>:5099/status/
```

## 与 daemon 的有意差异

1. **绑 `0.0.0.0`**（daemon 只绑 loopback）——这是固件存在的意义；局域网内无鉴权，见 `SECURITY.md`。
2. **快照是紧凑 JSON**（daemon 是 2 空格缩进 + CRLF + 尾无换行）。只影响空白字节，不影响语义。
3. **探测链没有 ARP**（lwIP 无 `SendARP` 等价 API），`console.method` 只会是 `ping` / `tcp`。
4. **没有 freshness 窗口**（与 daemon 一致：`/health` 不比对时钟）。
5. **不做 GitHub 版本检查、不做 Windows 服务部署、不跑 Python**。
6. `nekotrak.pet` / `nekotrak.pomodoro` 是原样透传的 JSON 子树（与 daemon 的 clone 行为一致），
   但重新序列化后空白可能不同。

## 排查

| 现象 | 处理 |
| --- | --- |
| `idf.py: command not found` | 先跑 `.\tools\build.ps1`，它会 source `export.ps1` |
| 串口打不开 / `Failed to connect` | 确认设备管理器里是 CH343 的 COM 口，烧录时按一下 BOOT 再试 |
| 屏全黑 | 检查 SDA/SCL 与地址（`0x3C`/`0x3D`），看串口是否有 I2C 初始化日志 |
| `/status/` 一直 503 | 看串口是否 WiFi 未连上 / 第一轮采集未完成 |
| 手机条目全空 | `PcFeed.enabled` 是否为 true、URL 是否指向 PC daemon |
