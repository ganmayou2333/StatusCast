# 安装与运行

StatusCast 有两个可以独立运行的部件，装哪个取决于你要什么：

| 你想做的事 | 装这个 | 需要什么 |
| --- | --- | --- |
| 在 Windows 上采集 iPhone / Android / 主机 / BLE / NekoTrak，对外提供 `/status/` | PC 侧（Python 采集层 + C# daemon） | Windows 10 1809+ / 11、.NET 10 SDK（仅构建）、Python 3.10+ |
| 不要 PC 常开，自己探测局域网主机和 BLE，并读一块小屏 | ESP32-S3 固件 | ESP32-S3 开发板 + ESP-IDF 5.5.6，见 [固件文档](../../firmware/esp32/README.md) |

两者提供**同一个契约**（schema 3、端口 5099、13 键设备行），NekoTool 只需要改一个 URL 就能从前者切到后者。

---

## 1. 环境要求

### 必需

- **Windows 10 1809+ / Windows 11**
- **.NET 10 SDK** — 只在构建 daemon 时需要；运行发布后的 exe 不需要
- **Python 3.10+** 与仓库内 `.venv`
  - 代码里**没有** Python 版本校验；CI 用 3.12，本机开发在 3.14 上跑过

### 按设备类型可选

| 设备 | 需要装 | 缺失时的表现 |
| --- | --- | --- |
| iPhone | iTunes 或「Apple 设备」应用（提供 `Apple Mobile Device Service` / usbmuxd） | 该条目 `error` 为 `iPhone 采集失败：No module named 'pymobiledevice3'` 或 usbmuxd 连接失败 |
| Android | Android Platform Tools，`adb` 在 `PATH` 中 | 该条目 `error` 为 `ADB not found. Install Android Platform Tools and add adb to PATH.` |
| Mac / Linux 节点 | 节点开 sshd 并配置密钥登录；本机 `ssh` 可用（Windows 10+ 自带 OpenSSH 客户端） | 该条目 `error` 为 `ssh 客户端不可用（需要 Windows OpenSSH 客户端）` |
| BLE 外设 | `bleak==3.0.2`（已写进 `requirements.txt`） | 该条目 `error` 为 `缺少 BLE 依赖：bleak` |
| 局域网主机（PS5 / Xbox / NAS） | 无 —— 走 Windows 自带 ARP/ping | 不会报错；探测失败表现为 `console.online: false` |
| NekoTrak | 本机装过并跑过 NekoTrak | 数据根不存在时**不注册**采集器，不会产生一条永远红的条目 |

只缺某一件时，只有对应设备降级，其余设备照常采集。

---

## 2. 安装

```powershell
git clone https://github.com/ganmayou2333/StatusCast.git
cd StatusCast

py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` 只有两个直接依赖：

```text
pymobiledevice3==11.43.1
bleak==3.0.2
```

采集器是**惰性导入**的：没有 iPhone / 没有 BLE 设备时不装对应依赖也不会崩溃，只会让那一条设备降级。

**`config.json` 不会被自动创建。** 仓库里带了一份只有别名的示例；你需要 `Consoles` / `SshNodes` / `BleDevices` 时自己加，键名见 [设备采集配置](collectors.md)。文件位置固定为仓库根目录（可用环境变量 `STATUSCAST_ROOT` 或 `--config` 覆盖）。文件缺失或 JSON 坏掉时**不会**阻断启动，全部按默认值处理，只是没有可选设备。

---

## 3. 构建与运行

```powershell
dotnet build .\src\StatusCast.Daemon\StatusCast.Daemon.csproj -c Debug
dotnet run  --project .\src\StatusCast.Daemon\StatusCast.Daemon.csproj -c Debug
```

构建完成后也可以直接跑 exe：

```powershell
.\src\StatusCast.Daemon\bin\Debug\net10.0\StatusCast.Daemon.exe
```

正常启动会打印（间隔取自 `RefreshIntervalSeconds`）：

```text
[SYSTEM] StatusCast Core v3.4.0 启动
[SYSTEM] 纯后台采集引擎 · 轮询间隔 30 秒
[API] 监听 http://localhost:5099/status/
[API] 健康检查 http://localhost:5099/health
[USB] 等待 iPhone 连接...
[ADB] 等待 Android 连接...
[SNAPSHOT] run/status.json 已更新 · 1792851926
```

`Ctrl+C` 优雅退出。

启动时还会非阻塞地问一次 GitHub Releases，发现新版本时追加彩色 `[UPDATE]` 提示——只提示，**不会下载或覆盖任何文件**。

> 版本号出现在启动横幅里，是从程序集读的；发布新版本请先改 `StatusCast.Daemon.csproj` 的 `<Version>`，`UpdateChecker` 也用它做比较。

---

## 4. 第一次自检

服务起来后，先确认契约面正常：

```powershell
curl.exe http://localhost:5099/health
# {"ok":true,"deviceCount":4,"generatedAt":1793502540,"schema":3}
```

`deviceCount` 大于 0 就说明一轮采集已经落盘。`/health` 返回 503 时不要当成崩溃——那是「快照还没写出来」的正常中间态，重试即可。字段语义见 [HTTP API 与契约](http-api.md)。

然后看完整设备行：

```powershell
Invoke-RestMethod -Uri 'http://localhost:5099/status/' -UseBasicParsing
```

---

## 5. 静默运行

三种方式，按侵入性从低到高。

### 方式一：隐藏控制台窗口

```powershell
Start-Process `
  -FilePath '.\src\StatusCast.Daemon\bin\Debug\net10.0\StatusCast.Daemon.exe' `
  -WorkingDirectory (Get-Location) `
  -WindowStyle Hidden
```

退出登录即结束，适合临时用。

### 方式二：Windows 服务

先发布单文件 exe：

```powershell
dotnet publish .\src\StatusCast.Daemon\StatusCast.Daemon.csproj `
  -c Release -r win-x64 --self-contained false `
  -p:PublishSingleFile=true -o .\dist
```

以管理员身份注册并启动：

```powershell
sc.exe create StatusCast binPath= "<仓库绝对路径>\dist\StatusCast.Daemon.exe" start= auto
sc.exe start StatusCast
```

服务账户下 `HttpListener` 可能报拒绝访问，为服务账户授予 URL ACL：

```powershell
netsh http add urlacl url=http://localhost:5099/ user=SYSTEM
```

> 删服务用 `sc.exe delete StatusCast`。

### 方式三：启动文件夹 / 计划任务

把发布后的 `StatusCast.Daemon.exe` 做成快捷方式放进 `shell:startup`，或用任务计划程序在登录时启动。这是最省事的一种，代价是登录后才起来。

---

## 6. 提交前跑测试

```powershell
# Python 采集层：全离线、零网络、零真机
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .

# C# daemon：xunit 断言 + 自带反射运行器（不依赖 VSTest）
dotnet run --project .\tests-csharp\StatusCast.Daemon.Tests\StatusCast.Daemon.Tests.csproj -c Release
```

- C# 套件默认不打开真实端口。设 `STATUSCAST_NETWORK_TESTS=1` 才额外跑 `/health`、`/status/` 的 loopback 集成测试（部分沙箱禁止 `HttpListener`，所以默认关）。
- `schema/status-contract.json` 是 schema-3 字段的**唯一事实源**。Python 与 C# 两侧测试都对照它校验输出形状；加字段必须两边同时改，否则直接红。

改到 `firmware/esp32/` 时另外跑：

```powershell
.\firmware\esp32\tools\host-test.ps1
```

---

## 7. 接 NekoTool

NekoTool 的 `launch_and_pull.ps1` 会检测 API，没起来就拉起 `StatusCast.Daemon.exe`，然后调用 `ultimate_status.ps1`。

NekoTrak 自定义文本可以直接嵌：

```text
${(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "G:\StatusCast\NekoTool\scripts\launch_and_pull.ps1")}
```

首次配置：

```powershell
cd G:\StatusCast\NekoTool
.\scripts\setup.ps1
```

指向 ESP32 而不是本机 daemon 时，只需给 `ultimate_status.ps1` 加 `-StatusUrl http://<esp32-ip>:5099/status/`。
