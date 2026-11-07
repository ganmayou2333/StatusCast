# 文档索引

面向使用者的文档在 [`guide/`](guide/)，面向贡献者的约定在 [`agents/`](agents/)。

## 使用者

| 文档 | 内容 |
| --- | --- |
| [安装与运行](guide/install.md) | 环境要求、装依赖、构建运行、静默运行三法、自检、跑测试 |
| [设备采集配置](guide/collectors.md) | `config.json` 全键说明、通道与降级语义、写自己的采集器 |
| [HTTP API 与契约](guide/http-api.md) | 路由表、状态码与精确响应体、13 键设备行、五个加法对象、转义规则 |
| [ESP32 控制面](guide/control-api.md) | `/control/*` 十个鉴权路由、`/diag`、令牌模型与安全边界 |
| [排障 FAQ](guide/faq.md) | 按设备分类的常见故障与判断依据 |

## 固件

| 文档 | 内容 |
| --- | --- |
| [firmware/esp32/README.md](../firmware/esp32/README.md) | 硬件清单、接线、烧录、配网、串口命令、屏幕 |

## 贡献者与 Agent

| 文档 | 内容 |
| --- | --- |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | 开发环境、测试矩阵、提交规范、贡献梯度 |
| [SECURITY.md](../SECURITY.md) | 信任边界、已知的明文/无鉴权面、私密报告渠道 |
| [GLOSSARY.md](../GLOSSARY.md) | 本项目的专有名词 |
| [adr/](adr/) | 影响架构且难以回退的决定 |
| [agents/domain.md](agents/domain.md) | 领域文档约定 |
| [agents/issue-tracker.md](agents/issue-tracker.md) | issue 与 spec 的存放约定 |

## 调研记录

历史调研，不是使用文档：

- [ble-ams-notes.md](ble-ams-notes.md) — BLE / Apple Media Service 调研
- [iphone-wireless-notes.md](iphone-wireless-notes.md) — iPhone Wi-Fi 采集为什么不行

## 发帖稿

[`posts/`](posts/) 里是发布时写的社区宣传稿，**不是使用文档**，其中的截图也未被仓库收录：

- [update-announcement-v3.4.0.md](posts/update-announcement-v3.4.0.md)
- [xiaoheihe-post-v3.4.0.md](posts/xiaoheihe-post-v3.4.0.md)
