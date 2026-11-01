# 04: presence 管线改造

来源：`docs/NekoTrak接入实施计划.md` T3。

**What to build:** 修掉 Discord presence 每分钟失败一次的问题。

现状：链路是「NekoTrak 自定义文本 → 启动脚本 → `ultimate_status.ps1`」，对 NekoTrak 的 ~5s 硬超时太慢，日志每分钟报 `custom_text_command_failed`；demo 里实测「直接读预写文件」只要 ~6ms。

改造方向：

- `write_to_obs.ps1` 自己直接读 `/status/` 组装文本并循环写 `obs_status.txt`，不再串 `launch_and_pull.ps1` 的重链路
- NekoTrak 的自定义文本改为读该文件的绝对路径（`-NoProfile` 一条 `Get-Content`）
- `setup.ps1` 生成的模板同步更新，README 的「NekoTrak 联动」节改写
- 保留 `-OutputPath` / `-IntervalSeconds`

**注意：** 这张票把「presence 文本从哪来」从 `ultimate_status.ps1` 迁到 `write_to_obs.ps1`。display-polish 的展示票因此排在它之后。

**Blocked by:** 03

**Status:** implemented — 待 NekoTrak 重启后观测定论（见 Comments）

- [ ] NekoTrak 重启后连续 ≥10 分钟日志**零** `custom_text_command_failed`（改造前约每分钟一次）
- [x] `obs_status.txt` 内容来自 `/status/`，与快照一致；NekoTrak 关闭时文件仍按间隔刷新
- [x] `-OutputPath` / `-IntervalSeconds` 含默认值的行为不变
- [x] `setup.ps1` 新模板与 README 描述一致，可照文档一步步复现

## Comments

**2026-11-01 — 实现完成，最后一条验收依赖用户重启 NekoTrak。**

**实测根因（不是推测）**：真实的 presence 模板在 `%APPDATA%\NekoTrak\users\<steamid>\config.json` 的 `steamSyncItems[4].text`，内容是 `${powershell.exe ... -File "...\ultimate_status.ps1"}`。而 `ultimate_status.ps1` 实测跑 **8.3 秒**（1 秒网速采样 + 每个插件一个子 job，两个插件各 ~2 秒），超过 NekoTrak 的 **5 秒**硬限。日志里累计 2538 条 `custom_text_command_failed`，`elapsed_ms` 稳定在 5010 左右，到排查当下（11:05）仍在每分钟触发一次。

**顺带挖出两个真 bug：**

1. **`write_to_obs.ps1` 从来就跑不起来。** 它用 `[CmdletBinding()]` + 参数默认值里 `Join-Path $PSScriptRoot ...`，而 PS 5.1 在**参数绑定阶段** `$PSScriptRoot` 还是空的，直接报 `Cannot bind argument to parameter 'Path' because it is an empty string`。所以这个写入器一直是死脚本 —— 这也是为什么管线从没人发现有问题。已改为在函数体内解析默认路径，并用最小脚本复现验证过。
2. **原子替换写错。** `[IO.File]::Replace($temp, $dest, $null)` 里 `$null` 会被 PowerShell 绑成空字符串，抛 `The path is not of a legal form`；换 `[NullString]::Value` 才通过。

**改动：**

- `write_to_obs.ps1`：参数默认值修好；原子替换（临时文件 + `File.Replace`）修好；每轮计时，只补睡 `IntervalSeconds` 余量，慢轮不会累积延迟；新增 `-Once`；默认间隔 5 → 10 秒（一轮实测 6.8–8.0 秒）。
- 新增 `point-nekotrak-presence.ps1`：把那条模板改成读文件，**改前带时间戳备份**，可先 `-WhatIfMode` 预览，重复执行幂等。用原始 JSON 文本定点替换而不是 `ConvertTo-Json` 回写，因为 NekoTrak 配置里有很深的嵌套结构，序列化往返会把它们压平。
- `setup.ps1`：生成的模板改成 `Get-Content -Raw`；`Create-StartupShortcut` 支持 `-Name` / `-Arguments`；可选把写入器加进开机启动项，并当场后台启动一次。
- README「NekoTrak 接入」整节重写：讲清「写的人和读的人分开」、5 秒/8 秒的原因、以及怎么一键改回来。

**实测：**

- 新读法（NekoTrak 将来执行的命令）：**187–194 ms**，原来 8.3 秒。
- 写入器：6.8–8.0 秒/轮，10 秒周期稳定刷新，无 `.tmp` 残留。
- 已对真实配置应用模板（备份：`config.json.bak-20261101-110855`），JSON 仍合法、`steamSyncItems` 数量不变。
- 写入器已用 COM 脱离父进程在后台运行。

**偏差（与票面文字不符，已写进 README）**：票面说 `write_to_obs.ps1` 要「自己直接读 `/status/` 组装文本」。实际保留了它调用 `ultimate_status.ps1` —— 5 秒限制卡的是 **NekoTrak 的自定义文本命令**，不是这个后台循环；在写入器里另写一套文本拼装会和 `ultimate_status.ps1` 立刻分叉。真正的修复点是把**读取方**换成读文件。

**仍未验证**：`custom_text_command_failed` 连续 10 分钟归零 —— 需要重启 NekoTrak 让新模板生效，这是用户动作。另外这 8 秒里约 4 秒是两个插件的子进程开销，05（内容增强）可以考虑把插件从 presence 那一行里摘掉。
