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

它通过 **USB** 读取 iPhone、通过 **ADB** 读取 Android 的电量、充电状态、机型和媒体状态，然后：

- 每 30 秒刷新一次；
- 原子写入 `run/status.json`；
- 通过 `http://localhost:5099/status/` 提供纯 JSON API。

v3.2 已移除 WinUI 3 GUI，StatusCast 只保留一个轻量的控制台 daemon。所有界面、聚合、插件和展示都由 [NekoTool](https://github.com/ganmayou2333/NekoTool) 负责。daemon 启动时还会在后台检查 GitHub Release，发现新版本时打印彩色 `[UPDATE]` 提示（不会自动下载覆盖）。

## 架构

```text
iPhone --USB--> status_core.py ─┐
Android --ADB--> status_core_android.py ─┤
                                         ▼
                            StatusCast.Daemon
                            ├─ StatusCoordinator (Task.WhenAll)
                            ├─ run/status.json (schema 3)
                            └─ HTTP API :5099
                                         │
                                         ▼
                              NekoTool（展示 / 插件）
                              ├─ NekoTrak
                              ├─ OBS 文本源
                              └─ OBS 浏览器控制台
```

StatusCast 不再包含：

- WinUI 3 / Windows App SDK
- `IStatusPlugin`
- 动态插件 DLL
- 顶层 `battery` / `media` / `plugins` / `text`

## 目录结构

```text
StatusCast/
├─ src/
│  └─ StatusCast.Daemon/
│     ├─ StatusCast.Daemon.csproj    # <Version>3.2.0</Version>
│     ├─ Program.cs                  # 控制台入口、30 秒轮询、优雅退出
│     └─ Services/
│        ├─ ProjectLocator.cs
│        ├─ StatusService.cs         # iPhone + Android 采集
│        ├─ StatusCoordinator.cs     # Task.WhenAll + 快照
│        ├─ StatusSnapshot.cs
│        ├─ StatusCastConfig.cs
│        ├─ StatusServer.cs          # HttpListener :5099
│        └─ UpdateChecker.cs         # GitHub Release 启动检查
├─ status_core.py                    # iPhone USB 采集
├─ status_core_android.py            # Android ADB 采集
├─ config.json                       # 自定义别名
├─ docs/
│  └─ ble-ams-notes.md               # BLE/AMS 调研记录
├─ requirements.txt
├─ .github/
├─ CHANGELOG.md
├─ README.md
└─ run/                              # 运行时快照（不提交）
```

## 环境要求

- Windows 10 1809+ / Windows 11
- .NET 10 SDK（仅构建时需要）
- Python 3.10+ 与 `.venv`
- iPhone：iTunes / Apple Devices + `Apple Mobile Device Service`
- Android：Android Platform Tools，`adb` 在 `PATH` 中

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
[SYSTEM] StatusCast Core v3.2.0 启动
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
[UPDATE] 发现新版本 v3.2.1！请前往 GitHub Release 下载最新 Daemon。
[UPDATE] 更新内容：
[UPDATE]   - 修复 Android 前台应用识别
[UPDATE]   - ...
```

按 `Ctrl+C` 优雅退出。

## 版本检查

daemon 启动时非阻塞请求：

```text
https://api.github.com/repos/ganmayou2333/StatusCast/releases/latest
```

解析 `tag_name` 与当前程序集版本（`<Version>3.2.0</Version>`）比较，若远端更新则打印 `[UPDATE]` 提示，并从 `body` 中取前 3 行更新内容。检查失败或没有新版本时完全静默，不影响采集与 API。**不会自动下载或覆盖程序文件。**

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
      "mediaTitle": "Yesterday",
      "mediaArtist": "The Beatles",
      "mediaState": "playing",
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
- 快照未生成时返回 `503`；
- 非 GET 返回 `405`，未知路径返回 `404`；
- 返回 `application/json; charset=utf-8`；
- 端口被占用时 daemon 会打印错误并退出。

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
