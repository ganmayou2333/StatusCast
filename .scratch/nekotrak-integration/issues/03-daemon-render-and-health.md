# 03: daemon 渲染适配 + `/health` 升级

来源：`docs/NekoTrak接入实施计划.md` T2。

**What to build:** daemon 认识 `windows` 平台，并把 `/health` 从探活升级成可用的自检端点。

现状要点（省一次探索）：

- `/health` 路由**已存在**，当前返回 `{status, snapshotReady}` —— 本票是扩展，不是新建
- `PlatformIcon` 目前**没有** `windows` 分支
- `ModelDisplay` 仍是 iPhone 硬编码映射；本票顺手把它挪进静态字典并让 windows 直通机型名，现有行为不得改变

扩展后的 `/health` 返回 `{ok, deviceCount, generatedAt, schema}`，读快照、未就绪回 503，保持 ACAO `*`；`http://127.0.0.1:5099/health` 与 `http://localhost:5099/health` 都要通。版本不 bump，随下一次 release。

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] 两种 host 拼写 curl `/health` 均 200，且四个字段齐全、取值与快照一致
- [ ] 快照未生成时 `/health` 回 503；`/status/` 行为与现在逐字段一致
- [ ] `windows` 设备拿到 🖥 图标与直通机型名；原 iPhone 映射结果不变
- [ ] C# 测试项目覆盖新路由与新图标映射，`dotnet build` 零警告
