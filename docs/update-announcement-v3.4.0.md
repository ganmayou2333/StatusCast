# StatusCast v3.4.0 更新公告

> 发布日期：2026-11-01 01:10（Asia/Shanghai）
> 适用仓库：StatusCast（daemon / 采集层）、NekoTool（展示层）
> 摘要：这一版把 StatusCast 从「只看两台手机」扩展成「手机 + 游戏主机 + Mac/Linux 节点 + 蓝牙外设」的局域网状态引擎，并为每条链路加上可降级的备用通道；同时补上了 C# 测试、契约与 CI。

---

## 一句话

**现在，你的游戏主机、Mac、Linux 小主机和蓝牙外设也能出现在状态栏里了；某条连接断掉时，它会自动换一条路继续报"在线/离线"，而不是整台设备消失。**

## 新增设备

### 🎮 游戏主机（局域网探测，零凭据）

在 `config.json` 的 `Consoles` 里配置即可：

```json
"Consoles": [
  {"id": "ps5", "name": "PS5", "type": "ps5", "ip": "192.168.1.50",
   "offlineText": "已关机"}
]
```

- 探测顺序：ARP → ICMP ping → TCP 端口（默认 `[9295, 3074, 80, 443]`，单端口 300ms）。
- 连续 2 次失败才判离线，1 次成功立即回在线（迟滞状态落盘 `run/console_state.json`）。
- 顺手回填 MAC，为后续 WOL 唤醒留好凭据。
- 离线**不是错误**：`error` 为 `null`，状态看 `console.online`，文案可自定义（`offlineText`，默认「离线」）。

### 💻🐧 Mac / Linux 节点（SSH，按键免密）

```json
"SshNodes": [
  {"id": "macbook", "name": "MacBook", "platform": "macos",
   "user": "fly", "host": "192.168.1.60", "sshKey": "~/.ssh/id_ed25519"}
]
```

- 一条 SSH 命令拉回电量、充电状态、机型、hostname、uptime。
- 仅密钥认证、`IdentitiesOnly=yes`、首次连接 TOFU（写 `known_hosts`）、单节点 8 秒封顶。
- 台式机无电池、多电池机器、`pmset` 各种充电状态都有对应处理。

### 🎧 蓝牙外设（BLE，白名单）

```json
"BleDevices": [
  {"id": "airpods", "name": "AirPods Pro", "address": "AA:BB:CC:DD:EE:FF"},
  {"id": "hrm", "name": "心率带", "nameFilter": "Polar",
   "services": ["heart_rate"], "heartRateWaitSeconds": 3}
]
```

- 只读公开 GATT：电量（`0x180F`/`0x2A19`）、心率（`0x180D`/`0x2A37`）。
- 每轮 4 秒扫描一次，多台 BLE 设备共享；不配对、不写入设备、地址只存本机。
- 扫描不到是「离线」，连不上是「已发现未连接」，两者都不算错误。

## 备用通道（自动降级）

每条链路现在都是**有序通道链**，主通道失败自动尝试备用通道；配置里写 `"fallback": false` 可关闭，恢复旧行为。

| 设备 | 通道链 |
| --- | --- |
| iPhone | USB →（实验）Wi-Fi 数据通道 → 局域网探活 |
| Android | adb → `adb mdns services` 自动发现 / `adb pair` + `adb connect` → 局域网探活 |
| 主机 | ARP → ping → TCP 端口 |
| 节点 | SSH → 局域网探活 |

结果以新增的 `transport` 对象表达：

```json
"transport": {"active": "lan", "degraded": true, "online": true,
              "lastSeenAt": 1793458293, "detail": "arp"}
```

`degraded=true` 表示「只能证明它在，但拿不到电量/媒体」。主机正常只提供存活信息，所以恒为 `false`。

## 需要你注意的行为变化

1. **主机离线不再写 `error`**：`error` 保持 `null`，离线状态看 `console.online=false`，文案在 `console.statusText`。自己写消费者的话请改读这两个字段；NekoTool 已适配。
2. **`GET /health` 现在真的存在**：返回 `{"status":"ok","snapshotReady":true|false}`，可用于探活（此前启动日志里写了但没注册）。
3. **`STATUSCAST_ROOT` 变严格**：显式设置的根目录里没有 `status_core.py` 时，daemon 会明确报「找不到项目根目录」，不再悄悄爬到别的目录去采错配置。
4. **schema 仍是 3**：所有改动均为加法字段，旧消费者忽略未知字段即可，配置文件向后兼容。

## 工程与可维护性

- **测试**：Python 170 项（全离线）、C# 41 项（解析/投影/路由/`/health`/回退），CI 双流水线分别跑。
- **契约**：`schema/status-contract.json` 成为 schema-3 字段的唯一事实源，Python 与 C# 两侧测试同时校验，字段漂移会直接失败。
- **可测性**：`error_entry()` 统一了错误条目形状；路由逻辑抽成纯函数；`InternalsVisibleTo` 让 daemon 内部逻辑可被测试。
- **文档**：新增 C0 真机联调台账，明确标注哪些项目尚未在真实设备上验证。

## 升级须知

1. 拉取代码后更新 Python 依赖（新增 `bleak` 声明）：

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

2. `config.json` 无需迁移；不配置新键时行为与 v3.3.0 一致。
3. 重新构建 daemon：

   ```powershell
   dotnet build .\src\StatusCast.Daemon\StatusCast.Daemon.csproj -c Release
   ```

4. 想启用新设备，按上面示例添加 `Consoles` / `SshNodes` / `BleDevices` / `IPhone` / `Android` 配置后重启 daemon。

## 已知限制（诚实版）

- **iPhone Wi-Fi 采集仍不可用**：当前 Apple 服务下 `diagnostics_relay` 会被设备断开；实验开关默认关闭，仅保留代码与研究记录。
- **主机只分"在线/离线"**：L0 无法区分「在玩」和「挂机」，`console.idleHint` 恒为 `true`，更细的展示留到下一阶段（C3）。
- **PS1 / OBS 展示本轮只加了主机行**：蓝牙与降级信息目前只进 JSON，展示打磨在 C3。
- **真机联调项未全部验证**：ARP 缓存消失时间、真实 MAC/WOL、known_hosts 首次连接、Linux 多电池、macOS `pmset` 各状态、BLE 真实读数等，均记录在 `docs/C1-C2b-C0-checklist.md`，完成 C0 后逐项勾选。

## 下一步

- **C0**：真实设备清单、静态 IP 绑定、密钥分发，逐项验证台账。
- **C3**：NekoTool 展示打磨（在玩段、节点行、蓝牙/降级标记）。
- 媒体元数据（AMS / Discord RPC）仍在计划中。

---

## GitHub Release 摘要（可直接粘贴）

```markdown
## StatusCast v3.4.0

局域网设备状态引擎正式扩展：游戏主机、Mac/Linux 节点、蓝牙外设现在都能进状态栏。

### 新增
- 🎮 主机：ARP → ping → TCP 端口探测，迟滞防抖，离线文案可配
- 💻🐧 节点：SSH 拉电量/机型/uptime，仅密钥认证，失败自动降级局域网探活
- 🎧 蓝牙：`BleDevices` 白名单，读取电量与心率，不配对不写入
- 🔀 备用通道：iPhone / Android / 主机 / 节点统一通道链，新增 `transport` 对象
- 🩺 `GET /health` 探活端点

### 变化
- 主机离线不再写 `error`（改看 `console.online` / `console.statusText`）
- `STATUSCAST_ROOT` 严格化，避免采错目录
- schema 仍为 3，配置向后兼容

### 质量
- Python 170 项离线测试 + C# 41 项测试，CI 双流水线
- `schema/status-contract.json` 作为字段唯一事实源

### 注意
- 升级后执行 `pip install -r requirements.txt`（已声明 `bleak`）
- iPhone Wi-Fi 采集仍不可用，实验开关默认关闭
- 真机项见 `docs/C1-C2b-C0-checklist.md`
```

---

## 发布前检查清单（维护者）

- [ ] 将 `src/StatusCast.Daemon/StatusCast.Daemon.csproj` 的 `<Version>` 从 `3.3.0` 升到 `3.4.0`
- [ ] `CHANGELOG.md` 的 `[Unreleased]` 改为 `[3.4.0] - 2026-11-01`
- [ ] NekoTool 的 `CHANGELOG.md` 同步版本号（若一起发版）
- [ ] 打 tag `v3.4.0`（release 工作流会以 `CHANGELOG.md` 作为 release body）
- [ ] 在装有真实手机/主机/节点的机器上跑一遍 C0 清单，勾掉已验证项
