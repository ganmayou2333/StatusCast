# 05: presence 一行内容增强

来源：`docs/NekoTrak接入实施计划.md` T4。

**What to build:** presence 那一行带上有用的信息：`🎮 <游戏名 / 挂台中> · 🔋 <别名 电量%[⚡]> · 🍅 <剩余（06 做成才有）>`。

电量取 primary 设备；多设备时取「有电量的第一台」。这条规则要写进 README，否则以后改 primary 逻辑的人会推翻它。PC 本机播放（SMTC）这一段本期不做，不要顺手加。

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] 有游戏时显示游戏名，无游戏显示「挂台中」
- [ ] 电量取 primary 且充电时带 ⚡；无电量设备不抢 primary
- [ ] primary / 电量选取规则写进 README
- [ ] 增强后 NekoTrak 日志仍零 `custom_text_command_failed`（不得把耗时拉回超时线）
