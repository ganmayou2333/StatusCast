# 06: 番茄钟 CDP 桥接（可砍）

来源：`docs/NekoTrak接入实施计划.md` T5。

**What to build:** 限时 0.5 天的 spike，目标是把番茄钟阶段/剩余时间带进快照。

做法：用户手动用带 `--remote-debugging-port=9222` 的快捷方式启动 NekoTrak，移植 demo 的 CDP 探测（≤2s），枚举 `__TAURI_INTERNALS__.invoke` 白名单。验收是取到番茄钟阶段 / 会话 JSON：拿到就写进 `nekotrak.pomodoro` 并让 presence 出现 🍅；任一环节不通就删掉探测代码、把结论写进 `docs/NekoTrak接入实施计划.md`，回落纯 L1。

红线：调试端口不写进任何自动配置、只绑 localhost、不入仓。

**这张票整体可砍**，不影响其它票的验收。

**Blocked by:** 05

**Status:** ready-for-agent

- [ ] 二选一成立：🍅 上屏，或在计划文档留下明确不可行结论（写明卡在哪一步）
- [ ] 失败时探测代码已删除，无残留开关或死代码
- [ ] 调试端口相关配置未进入仓内任何文件
