# 06: 番茄钟 CDP 桥接（可砍）

来源：`docs/NekoTrak接入实施计划.md` T5。

**What to build:** 限时 0.5 天的 spike，目标是把番茄钟阶段/剩余时间带进快照。

做法：用户手动用带 `--remote-debugging-port=9222` 的快捷方式启动 NekoTrak，移植 demo 的 CDP 探测（≤2s），枚举 `__TAURI_INTERNALS__.invoke` 白名单。验收是取到番茄钟阶段 / 会话 JSON：拿到就写进 `nekotrak.pomodoro` 并让 presence 出现 🍅；任一环节不通就删掉探测代码、把结论写进 `docs/NekoTrak接入实施计划.md`，回落纯 L1。

红线：调试端口不写进任何自动配置、只绑 localhost、不入仓。

**这张票整体可砍**，不影响其它票的验收。

**Blocked by:** 05

**Status:** blocked — CDP ruled out on this build; UIA probe needs one running pomodoro

- [ ] 二选一成立：🍅 上屏，或在计划文档留下明确不可行结论（写明卡在哪一步）
- [ ] 失败时探测代码已删除，无残留开关或死代码
- [x] 调试端口相关配置未进入仓内任何文件
- [ ] 失败时探测代码已删除，无残留开关或死代码

## Comments

**2026-11-01 — 探针已就绪并验证，卡在一次用户动作上（尚未得出可行性结论）。**

**先排除了更省事的路**：番茄钟的「剩余时间」确实不在明文里。`%APPDATA%\NekoTrak` 下所有 `.db`（含 `backups/`、`users/<id>/cloud*/`）都没有 SQLite 魔数，`tracker-heartbeat.json` 是 `NKSC` 密文。所以只能从运行中的应用里拿 —— CDP 是唯一路，计划里的判断成立。

**已交付 `scripts/probe_nekotrak_cdp.py`**（纯读，`websocket-client` 是仓库已有依赖，`hr_collector` 在用）：

- HTTP 发现 `/json/list`、`/json`，2 秒上限；
- 迷你 CDP 客户端发 `Runtime.evaluate`，读回值；
- 不猜 Tauri 命令名 —— 报告**页面实际暴露了什么**：target 列表、`__TAURI_INTERNALS__` 的键、名字匹配 `pomodoro|timer|focus|session` 的全局变量、localStorage 键、以及页面上可见的 `mm:ss` 倒计时。

顺手修了一个真实缺陷：`targets[0]` 会选中 Chromium 的**首启同步弹窗**（`edge://sync-confirmation-dialog`），而不是应用窗口。现在按 `tauri.localhost` > `http(s)` > `file` > 其他 排序挑选，跳过 `devtools://`。

**验证方式（不是空跑）**：用 Edge headless 带 `--remote-debugging-port=9222` 打开一个含 `25:13` / `04:59` 和 `window.__TAURI_INTERNALS__` 的测试页，探针实测返回：

- 正确选中 `file:///…/page.html`（不是弹窗）
- `countdowns: ["25:13", "04:59"]`
- `tauriKeys: ["invoke", "metadata"]`
- `globals` 含 `__pomodoroState`
- 全程 19.6 ms；端口关闭时 2.0 s 内返回 `exit=2` 并给出下一步提示。

**当前真实状态**：NekoTrak（`G:\SteamLibrary\steamapps\common\NekoTrak\NekoTrak.exe`，PID 20884）是**正常启动**的，9222 未开放，探针如实返回 `CDP port is not open`（exit 2）。所以**还没有结论**，不能说可行也不能说不可行。

**下一步（需要用户做一次）**：

```powershell
# 1. 退出 NekoTrak（托盘退出，不要只点关闭）
# 2. 带调试端口重新启动
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = '--remote-debugging-port=9222'
& 'G:\SteamLibrary\steamapps\common\NekoTrak\NekoTrak.exe'
# 3. 跑探针
python G:\StatusCast\NekoTool\scripts\probe_nekotrak_cdp.py
```

三种结果对应三条路：出现 `Visible countdown(s) found` → 有戏，直接接 `nekotrak.pomodoro.remainingSeconds`（`write_to_obs.ps1` 的 🍅 段已经预留好）；只有 tauriKeys 没有倒计时 → 需要枚举 IPC 命令名，再评估值不值；两者都没有 → 按票面失败分支**删掉探针**、把结论写进计划文档、回落纯 L1。

**红线遵守**：探针只反向读 `127.0.0.1:9222`、2 秒上限、不进采集器默认路径、不改任何启动配置、`setup.ps1` 里没有它。调试端口只出现在本票与探针的默认参数里，不会被任何自动配置写入。

**2026-11-01 11:52 — CDP 路线宣告不可行（有证据），换 UIA。**

用户按说明带 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222` 重启了（新 PID 9456，11:50:51 起，父进程就是那个 PowerShell，日志 `[Startup] pid=9456 ... entered Ready` 正常）。结果：

- `netstat` 无 9222 监听；探针仍 `CDP port is not open`
- 遍历所有 `msedgewebview2.exe`，**没有任何一个**带 `--remote-debugging-port`（grep 计数 0）
- NekoTrak 的 webview 数据目录 `%APPDATA%\NekoTrak\com.bocchithedev.nekotrak\EBWebView\EBWebView` 下**没有** `DevToolsActivePort` 文件
- 应用自带 **FixedVersionRuntime 151.0.4129.107**，且窗口类名是 `Tauri Window` → Tauri 自己给 WebView2 传了 additionalBrowserArgs，环境变量被忽略

**结论：环境变量这条 CDP 路线在这个打包版上不成立。**要 CDP 只能改应用自身的构建配置或注入，超出本 spike 的红线（不该为了看个番茄钟去改别人的应用）。

**替代路线：Windows UI Automation。** 已实测 WebView2 的 DOM 文本通过 UIA 可读（`Add-Type -AssemblyName UIAutomationClient`，按 PID 找窗口，第一次查询会唤醒 Chromium 的无障碍树，需要重试几次）：

- 90 多个带标签元素：`番茄钟`、`开始`、`停止`、`编辑: 专注分钟数`、`上一周期`、`总专注时长`、`69.1%` ……
- Document 元素的 Value 是 `http://tauri.localhost/`，确认是 Tauri 应用

新增 `scripts/probe_nekotrak_uia.ps1`：只读，报告窗口数、元素数、以及所有形如 `mm:ss` 的文本。当前空闲状态下**没有倒计时文本**（界面上只有「开始/停止」按钮），所以还需要一次「番茄钟正在跑」的观测才能定论。

**下一步**：在 NekoTrak 里点一次「开始」（25 分钟那个），然后重跑 `scripts/probe_nekotrak_uia.ps1`。出现 `countdown: 24:59` 这类文本 → UIA 可用，接着把读取接进采集器；仍然没有 → UIA 也看不到（定时器可能是画布绘制），按票面失败分支收尾。
