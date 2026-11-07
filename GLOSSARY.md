# StatusCast

一台桌面设备状态服务器：把散落在桌上的手机、主机、蓝牙外设和桌面应用汇总成一份固定形状的
JSON，通过 HTTP 交给消费方。这个词表只收本项目特有的说法，通用编程概念不收。

## 语言

**快照 / snapshot**:
一次采集轮次的完整产物，即 `run/status.json` 与 `/status/` 返回的那份 JSON。
_Avoid_: 状态文件、数据包、报告

**schema 3 / 契约**:
快照的固定形状——顶层三键、设备行 13 键、五个加法对象。字段名、顺序与类型由
`schema/status-contract.json` 唯一确定。
_Avoid_: 格式、协议、接口定义

**设备行 / device entry**:
`devices` 数组里的一项，代表一台设备。固定 13 个键，无法省略。
_Avoid_: 记录、条目、item

**加法对象 / additive object**:
设备行上可选的具名对象（`console` `node` `transport` `ble` `nekotrak`），承载某类设备专属
或某轮专属的信息。缺失时整个键不出现，绝不写 `null`；它们是只增不改的，schema 仍是 3。
_Avoid_: 扩展字段、附加属性、metadata

**displayName**:
设备行里给人看的名字。来源优先级是别名、硬编码机型表、原始 `model`、最后落到 `未知机型`。
_Avoid_: 昵称、label、显示名

**别名 / alias**:
`config.json` 里把原始 `model` 映射成 `displayName` 的一条规则。按轮次重读，改完不必重启。
_Avoid_: 重命名、映射表

**collector**:
一个采集来源的实现，负责「怎么读到这台设备」。采集失败只影响它自己那一条。
_Avoid_: 插件、驱动、provider、adapter

**采集轮次 / round**:
一次「跑完所有 collector、合并、落盘」的完整循环。默认 30 秒一轮，轮与轮不重叠。
_Avoid_: 周期、tick、刷新、cycle

**主通道 / 备用通道**:
一台设备的取数路径是被排序的。主通道失败才试备用通道；`fallback: false` 关掉这条链。
_Avoid_: 优先级、回退方案、backup

**降级 / degraded**:
能证明设备在线，但拿不到电量、媒体或机型这类常规字段。降级**不是错误**，`error` 保持 `null`。
_Avoid_: 失败、异常、部分成功

**stale / 读取超时**:
整轮采集失败时，把上一轮的设备条目原样复用并标记 `error` 为 `读取超时`。此时 `transport`
会被丢弃——它描述的是本轮通道，而本轮没有通道。
_Avoid_: 过期、缓存命中、离线

**迟滞 / hysteresis**:
主机在线状态不是逐轮直读的：连续两次失败才判离线，一次成功立即回在线。用来压掉 ARP/ping 抖动。
_Avoid_: 防抖、阈值、retry

**控制面 / control**:
ESP32 独有的可写接口族 `/control/*`，需要令牌，能改变设备状态（重启、改配置、换令牌）。
它与只读面（`/status/` `/health` `/diag`）是明确的两种权限等级。
_Avoid_: 管理接口、admin API、write API

**只读面**:
`/status/` `/health` `/diag` 三个不带鉴权的端点。它们是 NekoTool 零改动接入的前提。
_Avoid_: 公开 API、read API

**PC feed / feed**:
ESP32 主动去拉 PC 上 daemon 快照的那条通道，用来补齐固件自己采不到的设备（iPhone、
Android、NekoTrak）。因为 daemon 只绑 loopback，实际要经一个中继。
_Avoid_: 同步、上游、proxy

**PC 中继 / relay**:
把 daemon 的只读端点从 loopback 转发到局域网的辅助脚本，只转发 `GET /status/` 与 `GET /health`。
_Avoid_: 代理、网关、bridge
