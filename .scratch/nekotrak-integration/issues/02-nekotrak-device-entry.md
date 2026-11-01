# 02: NekoTrak 作为设备条目出现

来源：`docs/NekoTrak接入实施计划.md` T1。

**What to build:** `devices` 数组里出现 NekoTrak 自己的条目，`ultimate_status.ps1` 与 widget 不改代码就能看到它。

**这是移植，不是重写。** `demo/plan-a` 下的采集器与 Steam 索引已用 44 项单测验证过，把解析逻辑包成 StatusCast 的 `BaseCollector` 子类即可；解析细节不要去重新摸索。

条目要提供：

- `platform:"windows"`、`id:"nekotrak"`、`model="NekoTrak <version>"`
- 在线判定双保险：heartbeat 文件 mtime < 60s **或** 进程存在
- 版本、启动时长、TRACKER 开关、宠物近况
- 当前游戏：`foregroundApp` = 前台进程 exe 名，`foregroundAppName` = 经 Steam 库索引解析出的游戏名
- 新增可选对象 `nekotrak` 承载 uptimeSec / trackerActive / pet / pomodoro / warns / source

健壮性：`NEKOTRAK_HOME` 可覆盖；`users/*` 取最新 steamid；缺文件或格式损坏只让对应字段为 null 并进 `warns`，**绝不抛未捕获异常**；设备未运行时抛 `CollectorError("NekoTrak 未运行")` 走标准 error 条目，其它设备不受影响。

**Blocked by:** 01

**Status:** implemented — real-machine checks pending (see Comments)

- [x] `python status_core.py --pretty` 的 devices 里出现该条目，字段与 demo 页面一致
- [ ] 切换前台游戏后 `foregroundAppName` 在 ≤60s 内正确（Steam 索引缓存 10 分钟不影响正确性）
- [ ] 杀掉 NekoTrak 后该条目 ≤30s 转 error，同轮 iPhone / Android 条目仍正常更新
- [x] 缺文件、日志格式漂移 → 对应字段 null + `warns`，无未捕获异常
- [ ] 实测确认 `ultimate_status.ps1` 与 widget 对新增字段宽容（不宽容则先修消费侧，再继续）
- [x] 离线 unittest 全绿，fixtures 锁当前 NekoTrak 格式，不需要真机或真 Steam 库

## Comments

**2026-11-01 — 实现完成，3 项验收待真机确认。**

真机实测（本机，NekoTrak 1.2.0）：

- `status_core.py --pretty` 出现 `id="nekotrak"`、`platform="windows"`、`model="NekoTrak 1.2.0"`、`error: null`，`uptimeSec`/`trackerActive`/`pet`（46 条事件）/`warns: []` 均有真实值；`foregroundApp="DeepSeek Harness.exe"`，因为当时前台不是游戏所以 `foregroundAppName: null`——这条正是待确认项。
- 单轮刷新 540–630ms（NekoTrak 采集约 100ms）；Steam 索引在本机 23 个 App / 3 个库，构建 7ms，远低于 15s 预算。

**以下 3 项没有做，不要当成已完成：**

1. 「切游戏后 `foregroundAppName` 正确」——当时前台没有 Steam 游戏，无法验证映射；日志路径与 `installdir` 匹配已有单测覆盖。
2. 「杀掉 NekoTrak 转 error」——只能靠用户自己关掉应用验证；`CollectorError` → 标准 error 条目的语义已有单测（`test_failure_becomes_the_standard_error_entry`），真实进程消失未测。
3. 「消费侧宽容性」——`ultimate_status.ps1`/widget 用 `Invoke-RestMethod`/`JSON.parse` 天然忽略未知键，但未做实测；display-polish 01/02 会正面消费这个对象。

实现要点与偏差：

- **契约强制两侧同改**：`nekotrak` 进入 `schema/status-contract.json` 的 `additiveObjects` 后，C# 侧若不解码就会被 `StatusPayloadTests` 判红。所以本票把 C# 的 `NekoTrakStatus` 强类型 + `ParseNekoTrak` + payload 透传一并做完了 —— 原计划的 T2 里那部分工作已提前落地，03 只剩图标/机型名/`/health`。
- **设备可见性用「数据根是否存在」判定**：本机没装过 NekoTrak 就不产生条目，避免给不需要的用户挂一条永远红的 `NekoTrak 未运行`。与「没配 `Consoles` 就没有主机条目」一致。
- **在线判定**：heartbeat 文件 mtime ≤60s **或** `tasklist` 里存在 `NekoTrak.exe`；两者都可注入，测试不依赖本机是否真的在跑。
- **新增 `transport.active="process"`**：本地进程不是网络通道，沿用 `none` 会误导降级判断。这是 schema-3 里的新取值（字符串无枚举约束）。
- **demo 的东西没搬**：`presenceFix` 计时、`window`/`config` 镜像、MOCK 回退、CLI `main()` 都留在 demo；`nekotrak` 对象只暴露 13 个契约字段。
- 单测：新增 25 项（重跑解析 + Steam 索引 + 存活判定 + 设备条目 + dispatcher 接线），全量 195 项离线全绿。

**下一张**：03（daemon 渲染 + `/health`）。
