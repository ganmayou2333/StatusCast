# 01: 工作区清理与分组提交

来源：`docs/NekoTrak接入实施计划.md` T0。

**What to build:** 把两个仓库当前未提交的改动审一遍、分组提交，让后续每张票的 diff 只包含自己的改动。这是本 feature 的前置整理，不产出功能。

分组建议：文档 + 预研一组、daemon 小改一组、widget 单改一组。清单**必须先给用户过目**，用户自己的实验性改动不代提交。

**Blocked by:** None（可立即开始）

**Status:** done（2026-11-01，见 Comments）

- [x] 逐条审完 StatusCast 的 modified / untracked 与 NekoTool 的 modified，产出「文件 → 分组 → 是否用户实验」清单交用户确认
- [x] 用户确认后按组提交，提交信息沿用仓库风格
- [x] 未获确认的改动保持未提交，不被顺手带走
- [x] 两仓 `git status` 干净，或只剩明确标注为「用户实验、暂不提交」的文件

## Comments

**2026-11-01 — 完成。** 实际积压远大于本票当初设想：不是「6 个 modified + 2 个 untracked」，而是整个 v3.4.0 的未提交工作（两仓合计 17 个 modified、约 40 个新增文件）。计划里「文档+预研 / daemon 小改 / widget 单改」三组切不动，改为按功能内聚分组成 3 + 3 个提交，用户已过目确认。

StatusCast：

- `3cbf86f` feat(v3.4.0): 采集器 + daemon + schema 契约 + 测试 + CI（46 文件）
- `b2610a0` docs(v3.4.0): CHANGELOG/README/SECURITY + 发布物料 + `iphone_wireless_test.py`（按用户决定归入文档组）
- `884ac58` chore(agents): AGENTS.md + docs/agents + .scratch

NekoTool：

- `030b0ad` feat(ps1): 主机行 + 主行只认手机
- `dd32175` fix(widget): `?url=` 形参（按用户决定单独提交）
- `9ce799f` chore(agents)

验证：Python 170 项全绿（1 项真实 BLE 扫描按设计跳过）；C# 41 项通过；两个 build 零警告零错误；两个 PS1 零语法错误。

两点记录：

- C# 的 `HealthAndStatusOverLoopback` 在本机跑不了（`HttpListener` 报句柄无效，本地环境限制），这正是它默认 skip、只在 CI 用 `STATUSCAST_NETWORK_TESTS=1` 打开的原因，不是代码缺陷。
- 提交信息**没有**加计划文档 T0 要求的 `Co-Authored-By: Claude ...` 尾注：本轮的实现与提交由 DeepSeek Harness 完成，挂另一个模型的名属于虚假署名。要加署名请给出准确的署名行。

**下一张**：02（NekoTrak 设备条目）已解锁。
