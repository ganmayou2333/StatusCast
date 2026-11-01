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

**Status:** ready-for-agent

- [ ] `python status_core.py --pretty` 的 devices 里出现该条目，字段与 demo 页面一致
- [ ] 切换前台游戏后 `foregroundAppName` 在 ≤60s 内正确（Steam 索引缓存 10 分钟不影响正确性）
- [ ] 杀掉 NekoTrak 后该条目 ≤30s 转 error，同轮 iPhone / Android 条目仍正常更新
- [ ] 缺文件、日志格式漂移 → 对应字段 null + `warns`，无未捕获异常
- [ ] 实测确认 `ultimate_status.ps1` 与 widget 对新增字段宽容（不宽容则先修消费侧，再继续）
- [ ] 离线 unittest 全绿，fixtures 锁当前 NekoTrak 格式，不需要真机或真 Steam 库
