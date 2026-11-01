# 01: 工作区清理与分组提交

来源：`docs/NekoTrak接入实施计划.md` T0。

**What to build:** 把两个仓库当前未提交的改动审一遍、分组提交，让后续每张票的 diff 只包含自己的改动。这是本 feature 的前置整理，不产出功能。

分组建议：文档 + 预研一组、daemon 小改一组、widget 单改一组。清单**必须先给用户过目**，用户自己的实验性改动不代提交。

**Blocked by:** None（可立即开始）

**Status:** ready-for-agent

- [ ] 逐条审完 StatusCast 的 modified / untracked 与 NekoTool 的 modified，产出「文件 → 分组 → 是否用户实验」清单交用户确认
- [ ] 用户确认后按组提交，提交信息沿用仓库风格，结尾附 `Co-Authored-By` 署名
- [ ] 未获确认的改动保持未提交，不被顺手带走
- [ ] 两仓 `git status` 干净，或只剩明确标注为「用户实验、暂不提交」的文件
