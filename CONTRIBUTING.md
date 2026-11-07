# 贡献指南

> **English TL;DR** — Fork, branch, keep changes focused, run the checks in
> [测试矩阵](#测试矩阵) before opening a PR, and describe the user-facing effect.
> Contract changes must update `schema/status-contract.json` and all three sides
> (Python / C# / firmware) in the same PR. Security reports go through GitHub's
> private advisory flow, never a public issue.

## 先看这里

| 我想…… | 去哪 |
| --- | --- |
| 报告「它不工作」 | [开 Bug report](https://github.com/ganmayou2333/StatusCast/issues/new?template=bug_report.yml) |
| 提建议 / 提新设备支持 | [开 Feature request](https://github.com/ganmayou2333/StatusCast/issues/new?template=feature_request.yml) |
| 问「怎么配置 / 为什么」 | 先读 [排障 FAQ](docs/guide/faq.md) 与 [Discussions](https://github.com/ganmayou2333/StatusCast/discussions) |
| 报告安全问题 | [私密漏洞报告](https://github.com/ganmayou2333/StatusCast/security/advisories/new)，**不要**开公开 issue |

开 issue 前扫一眼已有 issue，避免重复。提 Bug 时最有用的三件东西：`status_core.py` 的原始输出、
`/health` 的返回、以及 `config.json` 去掉隐私后的片段。

## 贡献梯度

不需要任何许可就能做的事，按门槛从低到高：

| 层级 | 做什么 | 需要什么 |
| --- | --- | --- |
| **报告者** | 开 issue、在 Discussions 回答问题、补充 FAQ | 不需要写代码 |
| **文档贡献者** | 修文档错字、补一节配置说明、给 FAQ 加一个真实故障 | 一个 PR，无需预先沟通 |
| **代码贡献者** | 修 bug、加采集器、改固件 | 一个 PR；**改动超过约 200 行或触碰契约时先开 issue 对齐** |
| **常规贡献者** | 反复提交、参与 review 他人 PR | 有 2 个以上已合并的 PR |
| **维护者** | 合并、发版、判 triage | 由维护者邀请。`main` 有分支保护规则，维护者保留 bypass |

`main` 受 Ruleset 保护：外部 PR 需要一次 review 和绿的 CI 才能合并。维护者自己的提交走 bypass，
这是刻意的——单人项目上自己审自己没有意义。

## 开发环境

```powershell
git clone https://github.com/ganmayou2333/StatusCast.git
cd StatusCast

py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

dotnet build .\src\StatusCast.Daemon\StatusCast.Daemon.csproj -c Debug
```

固件另外需要 ESP-IDF 5.5.6，见 [firmware/esp32/README.md](firmware/esp32/README.md)。只改 PC 侧
不需要装 IDF。

## 测试矩阵

开 PR 前把你改到的那一层跑一遍，并在 PR 描述里贴结果。**没跑就写「未运行」**，不要打勾。

| 改动范围 | 必跑 |
| --- | --- |
| `collectors/`、`status_core.py`、`tests/` | `.\.venv\Scripts\python.exe -m unittest discover -s tests -t .` |
| `src/`、`tests-csharp/` | `dotnet run --project .\tests-csharp\StatusCast.Daemon.Tests\StatusCast.Daemon.Tests.csproj -c Release` |
| `firmware/esp32/main/core/`、`firmware/esp32/host_test/` | `.\firmware\esp32\tools\host-test.ps1` |
| `firmware/esp32/main/` 任意 C 文件 | 上面那条 **加** `.\firmware\esp32\tools\build.bat build` |
| 只改 `*.md` | 无需跑测试；但请确认引用的路径真实存在 |

`host-test.ps1` 需要一份 Zig（脚本会自己找 `G:\tools\zig\…`、`%LOCALAPPDATA%\zig\zig.exe` 或 `PATH`），
也可以用 `tools/host_test` 的 CMake 工程配任意 C11 编译器。

## 契约改动（最容易踩的一条）

`schema/status-contract.json` 是 schema-3 字段、加法对象与字段顺序的**唯一事实源**。Python 测试、
C# 测试、固件 host 测试**三处**都对照它校验。

所以：

- 加字段 = 改 `schema/status-contract.json` + 三个消费方同改 + 三套测试同绿，**一个 PR 里做完**；
- 加设备行字段会破坏 NekoTool：先开 issue 说明，别直接提 PR；
- 加法对象是**只增不改**的：可以加新对象，不要改已有对象里的字段名或顺序；
- `schema` 版本号目前仍是 `3`。要动它得先有迁移方案。

## 提交规范

用 [Conventional Commits](https://www.conventionalcommits.org/) 的风格，与现有历史一致：

```text
feat(esp32): onboard WS2812 status light and NekoTool mDNS default
fix(tools): bound the serial write timeout so a wedged TX reports instead of hanging
chore(tickets): record the on-device BLE scan result
docs(guide): split the README into per-task guides
```

scope 用受影响的区域（`esp32`、`daemon`、`tools`、`docs`、`repo`）。一个提交做一件事，
不要把无关重构塞进同一个 PR。

## 代码风格

- **Python**：只用标准库（`requirements.txt` 之外不引新依赖，除非有充分理由）。采集器必须
  惰性导入设备库，缺依赖只让该设备降级。
- **C#**：`Nullable` 与 `ImplicitUsings` 都是 enabled。**WinUI 已被移除**，不要引入
  `DispatcherQueue`、Windows App SDK 或任何 UI 依赖——这个项目是无界面的数据层。
- **C（固件）**：`main/core/` 必须保持**不依赖 ESP-IDF**（host 测试直接编译它），这是硬约束。
  `main/core/{sc_core,sc_config,sc_json,sc_snapshot}.c` 进 host 测试，其它不动。
  路由计数改动必须同步 `main/core/sc_route_counts.h`。
- **Markdown**：文档正文中文，`.github/` 模板与代码注释英文。

## 绝对不要提交

- `.venv/`、`bin/`、`obj/`、`run/`、`firmware/esp32/build/`
- `.scratch/`（本地设计笔记，已 gitignore）
- 任何凭据：令牌、私钥、`config.json` 里的 `pairCode`
- 真实设备标识：MAC 地址、局域网 IP、SSID、串口号——文档示例请用
  `AA:BB:CC:DD:EE:FF` / `192.168.1.100` / `<your-ssid>` / `COM3`

## Review 时看什么

1. 契约有没有被悄悄改动（见上）；
2. 新增的失败路径有没有把错误降级成静默成功；
3. 文档里写的命令是否真的能跑（本项目历史上最常出的错就是这个）；
4. 有没有把真机标识符带进仓库。

## 发布

维护者流程见 [docs/posts/update-announcement-v3.4.0.md](docs/posts/update-announcement-v3.4.0.md)
末尾的「发布前检查清单」。要点：升 `<Version>`、把 `[Unreleased]` 改成带日期的版本节、
打 tag，`release` 工作流只取当前版本那一段作为 release 正文。
