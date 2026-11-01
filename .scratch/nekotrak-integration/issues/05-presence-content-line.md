# 05: presence 一行内容增强

来源：`docs/NekoTrak接入实施计划.md` T4。

**What to build:** presence 那一行带上有用的信息：`🎮 <游戏名 / 挂台中> · 🔋 <别名 电量%[⚡]> · 🍅 <剩余（06 做成才有）>`。

电量取 primary 设备；多设备时取「有电量的第一台」。这条规则要写进 README，否则以后改 primary 逻辑的人会推翻它。PC 本机播放（SMTC）这一段本期不做，不要顺手加。

**Blocked by:** 04

**Status:** implemented — 最后一条随 04 一起待 NekoTrak 重启后观测

- [x] 有游戏时显示游戏名，无游戏显示「挂台中」
- [x] 电量取 primary 且充电时带 ⚡；无电量设备不抢 primary
- [x] primary / 电量选取规则写进 README
- [ ] 增强后 NekoTrak 日志仍零 `custom_text_command_failed`（不得把耗时拉回超时线）

## Comments

**2026-11-01 — 完成（提交 `6a1bbfb` 之后）。**

`write_to_obs.ps1` 新增 `-Style presence|full`，默认 `presence`：不再调用 `ultimate_status.ps1`，直接读 `/status/` 拼三段。这也把 A‑04 里那 8.3 秒的一轮降到 **0.1 秒**（插件子进程根本不参与 presence 行）。`-Style full` 保留原长行给 OBS 用。

三组合成快照实测（仓库外临时 harness，mock `/status/`）：

| 输入 | 输出 |
| --- | --- |
| `currentGame="ELDEN RING"` + iPhone 87% 充电 | `🎮 ELDEN RING · 🔋 主力机 87%⚡` |
| `currentGame=null` + iPhone 无电量 | `🎮 挂台中 · 🔋 主力机 未知` |
| `pomodoro.remainingSeconds=1513` | `🎮 ELDEN RING · 🔋 主力机 64% · 🍅 25:13` |

规则（已写进 README 表格）：

- 🎮 只认 `nekotrak.currentGame`。**我曾让它在前台不是游戏时退回 `foregroundAppName`，结果 presence 显示成 `chrome.exe`** —— 与票面「无游戏显示挂台中」冲突，已移除该回退。
- 🔋 只认手机：`ios 且有电量` → `android 且有电量` → 任一手机。MacBook / 主机 / BLE 不抢这一格。daemon 在机型未知时会把 `displayName` 填成占位符「未知机型」，已过滤，否则会出现 `🔋 未知机型 未知`。
- 🍅 只在快照带 `nekotrak.pomodoro.remainingSeconds` 时出现（06 落地后自动生效），现在整段省略、不留占位。

真机跑一轮（daemon 在跑、手机未连）：`🎮 挂台中 · 🔋 未知`，耗时 388ms（含 PowerShell 启动）。

**未验证**：最后一条（日志零 `custom_text_command_failed`）需要用户重启 NekoTrak。从结构上说不会回归——NekoTrak 读的仍是同一个文件，读路径耗时没变（187–194ms）。

后台写入器已用新代码重启（默认 presence 风格）。

**2026-11-01 11:34 — 追加**：真实 daemon 下跑一轮 presence（`write_to_obs.ps1 -Once`）耗时 **0.1 秒**，输出 `🎮 挂台中 · 🔋 未知`（当时没连手机、前台不是 Steam 游戏，两段都落在预期分支）。
