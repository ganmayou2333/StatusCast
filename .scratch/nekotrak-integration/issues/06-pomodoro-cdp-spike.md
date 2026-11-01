# 06: 番茄钟 CDP 桥接（可砍）

来源：`docs/NekoTrak接入实施计划.md` T5。

**What to build:** 限时 0.5 天的 spike，目标是把番茄钟阶段/剩余时间带进快照。

做法：用户手动用带 `--remote-debugging-port=9222` 的快捷方式启动 NekoTrak，移植 demo 的 CDP 探测（≤2s），枚举 `__TAURI_INTERNALS__.invoke` 白名单。验收是取到番茄钟阶段 / 会话 JSON：拿到就写进 `nekotrak.pomodoro` 并让 presence 出现 🍅；任一环节不通就删掉探测代码、把结论写进 `docs/NekoTrak接入实施计划.md`，回落纯 L1。

红线：调试端口不写进任何自动配置、只绑 localhost、不入仓。

**这张票整体可砍**，不影响其它票的验收。

**Blocked by:** 05

**Status:** blocked — probe ready and verified; needs one NekoTrak restart with the debug port

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
