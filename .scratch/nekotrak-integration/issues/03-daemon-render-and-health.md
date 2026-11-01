# 03: daemon 渲染适配 + `/health` 升级

来源：`docs/NekoTrak接入实施计划.md` T2。

**What to build:** daemon 认识 `windows` 平台，并把 `/health` 从探活升级成可用的自检端点。

现状要点（省一次探索）：

- `/health` 路由**已存在**，当前返回 `{status, snapshotReady}` —— 本票是扩展，不是新建
- `PlatformIcon` 目前**没有** `windows` 分支
- `ModelDisplay` 仍是 iPhone 硬编码映射；本票顺手把它挪进静态字典并让 windows 直通机型名，现有行为不得改变

扩展后的 `/health` 返回 `{ok, deviceCount, generatedAt, schema}`，读快照、未就绪回 503，保持 ACAO `*`；`http://127.0.0.1:5099/health` 与 `http://localhost:5099/health` 都要通。版本不 bump，随下一次 release。

**Blocked by:** 02

**Status:** implemented — verified against the real daemon (see Comments)

- [x] 两种 host 拼写 curl `/health` 均 200，且四个字段齐全、取值与快照一致
- [ ] 快照未生成时 `/health` 回 503；`/status/` 行为与现在逐字段一致
- [x] `windows` 设备拿到 🖥 图标与直通机型名；原 iPhone 映射结果不变
- [x] C# 测试项目覆盖新路由与新图标映射，`dotnet build` 零警告

## Comments

**2026-11-01 — 完成（含 02 提前带来的契约/透传部分）。**

真机验证（真实 daemon + 真实 NekoTrak 1.2.0）：

- `GET http://localhost:5099/health` 与 `GET http://127.0.0.1:5099/health` 均 **200**，body 为 `{"ok":true,"deviceCount":3,"generatedAt":1793501597,"schema":3}`，与 `/status/` 的 `schema`/`generatedAt` 一致，`deviceCount` 与 devices 长度一致。
- daemon 日志出现 `[WINDOWS] NekoTrak 1.2.0 | 🔋 未知 | 🎵 暂无播放` —— 🖥 图标与机型直通都生效。
- `listen` 前缀改为逐条添加：`localhost` 与 `127.0.0.1` 各自独立 try，单条被 URL ACL 拒绝不会拖垮整个服务器。

**未验证的一项**：503「快照未生成」这条路径没有在真实 daemon 上观测到 —— daemon 首轮采集约 600ms 就写出快照，探针来不及命中窗口。该分支由 `HealthAndStatusOverLoopback` 覆盖，但这个测试在本机沙箱里跑不了（`HttpListener` 报「句柄无效」，见该测试注释），只能在 CI 的 `STATUSCAST_NETWORK_TESTS=1` 下证明。**不要把这条当成已真机验证。**

C# 侧改动：`PlatformIcon` 加 `windows => 🖥`；`ModelDisplay` 的 iPhone 映射搬进 `DeviceModelAliases` 静态字典，其余机型（含 `NekoTrak x.y.z`）直通。新增测试：图标映射、机型直通、`WithAlias` 保留 `nekotrak`、`/health` 路由。

**测试**：C# 43 项通过、两个 build 零警告；Python 195 项离线全绿。

## Comments

**2026-11-01 — 范围已缩小，开工前先读这条。**

02 落地时，`nekotrak` 进入共享契约（`schema/status-contract.json` 的 `additiveObjects`）后，C# 侧不同步解码就会被 `StatusPayloadTests` 判红。因此 02 已顺带完成：

- `NekoTrakStatus` 强类型 + `DeviceStatus.NekoTrak`
- `ParseNekoTrak`（含 `pet`/`pomodoro` 原样透传）
- `StatusCoordinator.BuildDevicePayload` 的 `nekotrak` 键
- `WithAlias` / `ToStaleDevice` 的复制
- C# 测试：契约字段集合、payload 附加、快照信封、解析往返

**本票剩下**：`PlatformIcon` 的 `windows` 分支、`ModelDisplay` 的 iPhone 映射搬进静态字典并让 windows 直通、`/health` 从 `{status,snapshotReady}` 扩到 `{ok,deviceCount,generatedAt,schema}` 并支持 503、`127.0.0.1` 与 `localhost` 双拼写，以及对应测试。契约与透传部分不必重做。
