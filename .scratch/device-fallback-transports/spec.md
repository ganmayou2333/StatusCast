# Spec: 设备备用连接通道（Fallback Transports）

状态：已实现（2026-10-31）。实现记录见文末「实现备注」。
日期：2026-10-31
关联：C1/C2b 实施（`docs/C1-C2b实施提示词.md`）、`docs/iphone-wireless-notes.md`

## 背景

当前每台设备只有一条数据通道，主通道断掉整条设备就变成 `error`：

| 设备 | 主通道 | 断掉时 |
| --- | --- | --- |
| iPhone | `pymobiledevice3` USB | 整条 error；Wi-Fi 已预研判定不可用 |
| Android | `adb`（USB 或已手动连接的 TCP） | 整条 error |
| 主机（console） | ARP → ping | `console.online=false` |
| SSH 节点 | SSH | 整条 error，电量/机型全丢 |

目标：主通道不可用时按顺序尝试备用通道，**能拿到多少报多少**，并把"走的是哪条通道、是否降级"显式暴露给消费者。

## 目标

- 每台设备有一条**有序通道链**，主通道失败后自动尝试备用通道。
- 降级结果不是错误：`error` 保持 `null`，用新增 `transport` 对象表达在线状态与完整性。
- 默认开启自动 fallback，配置里可逐设备关闭（`"fallback": false`）。
- 零新增第三方依赖；schema 3 只增不改；未配置时行为与现在完全一致。

## 非目标

- 不改 PS1 / widget / dashboard 的展示布局（降级信息先只进 JSON，展示留 C3）。
- 不做 iPhone 媒体元数据、不做 WOL 触发。
- 不引入后台常驻进程或监听端口。

## 设计

### 1. 统一 `transport` 对象（可选，加法）

每台设备条目可带一个可选对象；无对象时键缺失（与 `console` / `node` 规则一致）：

```json
"transport": {
  "active": "usb | usb-wifi | adb | adb-wifi | ssh | lan | tcp | none",
  "degraded": false,
  "online": true,
  "lastSeenAt": 1793458293,
  "detail": "arp"
}
```

- `active`：本次实际取数的通道。
- `degraded`：true 表示只拿到存活信息，设备类别正常能提供的字段（电量/媒体/机型）缺失或部分缺失。
- `online`：该通道下判定是否在线。
- `detail`：自由文本细节（`arp` / `ping` / `tcp:9295` / `wifi:bonjour` / `adb:192.168.1.21:5555`）。
- 主机（console）正常只提供存活信息，因此 `degraded` 恒为 false；只有"本应更丰富"的通道降级时才为 true。

### 2. 各设备通道链

**iPhone**（`IPhone` 配置节，可选）

1. USB：`pymobiledevice3` `create_using_usbmux(connection_type="USB")`。
2. Wi-Fi 数据通道（实验性，`"wifiDataChannel": true` 才启用）：
   a. usbmuxd `Network`；
   b. Bonjour/mobdev2 lockdown（复用 `iphone_wireless_test.py` 的发现逻辑）。
   成功读到电量 → 正常条目；只能握手读不到电量 → 视为第 3 步。
3. 局域网探活（需配置 `ip`，`fallback` 未关闭）：ARP → ping。
4. 全部失败 → 整条 `error`（与现在一致）。

**Android**（`Android` 配置节，可选）

1. `adb devices` 已有在线设备（USB 或用户自己 connect 的 TCP）。
2. `adb mdns services` 自动发现 `_adb-tls-connect._tcp` → `adb connect <addr>`。
3. 配置了 `ip` 时 `adb connect <ip>:<port>`；若给了 `pairPort` + `pairCode` 先 `adb pair`。
4. 局域网探活（需 `ip`）：ARP → ping。
5. 全部失败 → 整条 `error`。

**主机 console**（`Consoles` 条目，新增可选键）

1. ARP（`iphlpapi.SendARP`，顺带取 MAC）。
2. ICMP `ping -n 1 -w 1000`。
3. TCP 端口探活：对 `tcpPorts`（默认 `[9295, 3074, 80, 443]`，单端口 connect 超时 300ms）依次尝试，命中即在线。
4. 全失败 → `console.online=false`（离线不是错误，与现在一致）。

**SSH 节点**（`SshNodes` 条目，新增可选键）

1. SSH（现有 8s 上限）。
2. 局域网探活：对 `host` 做 `socket.gethostbyname` 解析后 ARP → ping。
3. 探活成功 → 降级在线（`error=null`、`node.ssh=false`、`node.online=true`、电量/机型 null）。
4. 探活也失败 → 降级离线（`error=null`、`node.online=false`、`node.statusText="离线"`）。
5. `fallback: false` 时维持现状（整条 error）。

### 3. 配置键（全部可选，缺省即现有行为）

```json
{
  "IPhone":  {"ip": "", "fallback": true, "wifiDataChannel": false},
  "Android": {"ip": "", "port": 5555, "pairPort": 0, "pairCode": "", "fallback": true},
  "Consoles": [
    {"id": "ps5", "name": "PS5", "type": "ps5", "ip": "192.168.1.50",
     "offlineText": "已关机", "tcpPorts": [9295, 3074], "fallback": true}
  ],
  "SshNodes": [
    {"id": "macbook", "name": "MacBook", "platform": "macos",
     "user": "fly", "host": "192.168.1.60", "sshKey": "", "fallback": true}
  ]
}
```

- `fallback` 默认 `true`；写 `false` 即恢复"主通道失败就 error"的旧行为。
- `pairCode` 只在本次运行用于 `adb pair`，不写日志、不进快照、不进错误信息。
- 未知/非法值容错：忽略该项，退回默认；不让整轮失败。

### 4. 语义

- **降级在线/离线都不是错误**：`error` 保持 `null`。
- `console` / `node` 对象保留原有字段；node 增加 `online` / `statusText` / `transport`。
- 配置错误（缺 id/ip、IP 非法、key 不存在）仍是 `error`（invalid entry），不走 fallback。
- 通道链最坏耗时：iPhone 3s（Wi-Fi 握手）+ 2s（探活）；Android connect 2s + pair 2s + 探活 2s；SSH 8s + 探活 2s。并发上限 8 不变，单轮 <15s 目标仍成立（设备数不超过 8 时）。

### 5. C# daemon

- 新增 `TransportStatus` 强类型 + `DeviceStatus.Transport`；`WithAlias` 复制；`status.json` 透传（无对象则键缺失）。
- `IsAvailable` 不变：`transport.online=false` 不影响现有判定（现有判定已排除 error 与离线 console）。

### 6. 兼容性

- schema 保持 3；只增加可选字段。
- 不配置 `IPhone` / `Android` / `tcpPorts` 时，Python 输出与现在逐字节一致（除新增 `transport` 对象）。
- 零新增第三方依赖（`socket` / `subprocess` / `ctypes` 均为 stdlib）。
- 不新增监听端口、不常驻进程。

## 测试（全部离线，零网络零真机）

- 通道链顺序与降级：iPhone USB 失败 → LAN 探活（mock ARP/ping）；节点 SSH 失败 → 探活；console ARP/ping 失败 → TCP 探活。
- 配置开关：`fallback: false` 时维持旧行为；无配置节时行为不变。
- `adb mdns services` 输出解析（合成文本）；`adb pair` / `connect` 参数构造；pairCode 不出现在 error / 快照。
- TCP 探活：mock `socket.create_connection` 命中/超时/全部失败。
- `transport` 形状：degraded / active / online / lastSeenAt 在各路径上的取值。
- iPhone Wi-Fi 步骤在 `wifiDataChannel=false` 时完全跳过。
- C#：无测试项目，沿用手工清单（`status.json` 中 `transport` 键形状 + 无对象时缺失）。

## C0 / 验证边界

以下只能真机验证，写入 `docs/C1-C2b-C0-checklist.md`：

- iPhone `IPhone.ip` 与真实 LAN 探活；Wi-Fi 数据通道在本机 Apple 服务下预期仍不可用（默认关）。
- Android `adb mdns services` 真实输出与 `adb connect` / `adb pair` 行为（Android 11+ 配对码）。
- 主机 TCP 端口是否真实开放（PS5 9295 / Xbox 3074 因固件与省电策略而异）。
- SSH 节点睡眠/sshd 停止时，LAN 探活的在线/离线判定与 ≤60s 时延。

## 实现备注（2026-10-31）

- 新建 `collectors/lan_probe.py`：ARP/ping 从 `console_collector` 原样迁出（模块级名字保留，测试仍可 patch），新增 `probe_lan` / `tcp_probe` / `make_transport` / `as_bool` / `sanitize_ports`。
- `transport` 对象**所有条目都带**（含 error / invalid 条目，取 `active="none", online=false`），消费者不必处理"有时缺键"。
- SSH 降级条目在 `node` 里保留 `source="lan"`，并新增 `online` / `statusText`；C# `NodeStatus` 已同步这两个字段（验收时发现 C# 漏解析，已修）。
- iPhone Wi-Fi 的 3s 是 usbmuxd Network + Bonjour/mobdev2 的**共享总预算**（按"最坏 3s"收紧，而非各 3s）。
- `status_core.py` 对非法 `IPhone` / `Android` 配置生成 invalid 条目，不再让整轮失败。
- 单测：119 项全绿（原 52 项 + 新增 67 项），全离线。
- 未验证项见 `docs/C1-C2b-C0-checklist.md` 的 C1-8…C1-11、C2b-8。
