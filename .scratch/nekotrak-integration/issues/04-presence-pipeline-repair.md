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

**Status:** ready-for-agent

- [ ] NekoTrak 重启后连续 ≥10 分钟日志**零** `custom_text_command_failed`（改造前约每分钟一次）
- [ ] `obs_status.txt` 内容来自 `/status/`，与快照一致；NekoTrak 关闭时文件仍按间隔刷新
- [ ] `-OutputPath` / `-IntervalSeconds` 含默认值的行为不变
- [ ] `setup.ps1` 新模板与 README 描述一致，可照文档一步步复现
