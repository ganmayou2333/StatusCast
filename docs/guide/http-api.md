# HTTP API 与契约

这是 StatusCast 对外的全部契约。**PC daemon 和 ESP32 固件实现的是同一份**，所以 NekoTool 只需要改 URL。

端口固定 **5099**。PC daemon 绑 `localhost` 与 `127.0.0.1` 两个前缀（各自独立注册，一条被 URL ACL 拒绝不会拖垮服务器）；ESP32 绑 `0.0.0.0`。

## 路由表

### PC daemon

| 方法 | 路径 | 结果 |
| --- | --- | --- |
| `GET` | `/status/`（**必须带尾斜杠**） | 200 快照原文，或 503 |
| `GET` | `/health` | 200 或 503 |
| 任意非 GET | **任意路径** | 405 |
| `GET` | 其余路径（`/status` `/health/` `/Health` …） | 404 |

方法检查在路径检查之**前**，所以 `POST /nope` 得到的是 405 而不是 404。路径用 `Url.AbsolutePath` 做 `Ordinal` 比较：尾斜杠与大小写都是精确匹配。查询串被忽略，`/status/?x=1` 仍命中 `/status/`。

### ESP32 固件

在上面基础上多了 `GET /diag` 和十个需要鉴权的 `/control/*` 路由。控制面单独成篇，见 [ESP32 控制面](control-api.md)。

---

## 状态码与精确响应体

响应体都是**单行、无尾换行**。

| 场景 | 状态码 | 体 |
| --- | --- | --- |
| `/status/` 快照可读 | 200 | 快照原文（**不做 JSON 校验**） |
| `/status/` 快照读不出 | 503 | `{"error":"status snapshot not ready"}` |
| `/health` 就绪 | 200 | `{"ok":true,"deviceCount":N,"generatedAt":G,"schema":S}` |
| `/health` 未就绪 | 503 | `{"ok":false,"deviceCount":null,"generatedAt":null,"schema":null}` |
| 非 GET | 405 | `{"error":"method not allowed"}` |
| 未知路径 | 404 | `{"error":"not found"}` |

注意一处**保留的不一致**：快照文件能读但内容不是合法 JSON 时，`/status/` 仍然 200 原样返回，而 `/health` 返回 503。这是刻意的——`/status/` 是裸通道，`/health` 才是判定器。

**`/health` 就绪判定**：快照能解析成 JSON 对象**且** `devices` 是数组。`generatedAt` / `schema` 缺失不阻塞 200（此时它们的值是 `null`）。没有新鲜度窗口——不拿 `generatedAt` 跟当前时钟比较。

### 响应头

每个响应都带：

```text
Access-Control-Allow-Origin: *
Cache-Control: no-store
Content-Type: application/json; charset=utf-8
Content-Length: <精确字节数>
```

没有鉴权、Cookie、ETag、gzip，不用 chunked。（固件的 `/control/log` 和 `/control/coredump` 是例外，见控制面文档。）

---

## 快照结构

顶层三个键，顺序固定：`schema` → `generatedAt` → `devices`。`schema` 恒为 `3`，`generatedAt` 是 unix 秒。输出带缩进、camelCase。

```json
{
  "schema": 3,
  "generatedAt": 1792851926,
  "devices": [
    {
      "id": "iphone",
      "platform": "ios",
      "model": "iPhone15,3",
      "displayName": "主力机",
      "batteryLevel": 87,
      "isCharging": true,
      "mediaTitle": null,
      "mediaArtist": null,
      "mediaState": "unknown",
      "foregroundApp": null,
      "foregroundAppName": null,
      "updatedAt": 1792851926,
      "error": null
    }
  ]
}
```

## 设备行：13 个键，顺序固定

```text
id, platform, model, displayName, batteryLevel, isCharging, mediaTitle, mediaArtist,
mediaState, foregroundApp, foregroundAppName, updatedAt, error
```

**全部键无条件写出**，`null` 也写。语义要点：

- `platform` 取值：`ios` `android` `console` `macos` `linux` `ble` `windows`。未知时为 `unknown`。
- `mediaState` 缺失或不是字符串 → `"unknown"`。
- `updatedAt` **等于** `generatedAt`：它表示「本轮发布」，不是「设备最近采集时间」。想看设备层面的新鲜度请看 `transport.lastSeenAt` / `console.lastSeenAt`。
- `batteryLevel` 必须是 JSON 整数（`87.0` 和 `"87"` 都算非法 → `null`）；`isCharging` 必须是 JSON bool。
- 字符串先 trim，空白 → `null`。
- `displayName` 顺序：`Aliases[model]` → `iPhone15,3 → 14 Pro Max` / `iPhone14,3 → 13 Pro Max` → `model` → `未知机型`。

## 非 ASCII 转义

固件与 daemon 都把非 ASCII 一律转义成 `\uXXXX`（`JavaScriptEncoder.Default`）：

```json
"displayName": "\u672A\u77E5\u673A\u578B"
"generatedAtLabel": "2026-11-06T23:17:48.147\u002B08:00"
```

原因：PowerShell 5.1 的 `Invoke-RestMethod` 对 charset 处理不可靠。`"` `\` 控制字符按 JSON 常规转义，`<` `>` `&` `'` 也会被编码。

> Python 采集层自身的输出是**原始非 ASCII**（`ensure_ascii=False`），转义发生在 daemon 落盘/响应时。所以直接调 `status_core.py` 和调 `/status/` 看到的字节不同，这是预期的。

---

## 加法对象

设备行可以带五个**可选**对象：`console`（局域网主机）、`node`（SSH 节点）、`transport`（本轮通道与降级）、`ble`（蓝牙外设）、`nekotrak`（桌面应用）。

规则：

- **缺失时整个键不出现**，绝不写 `null`。
- 附加顺序固定：`console` → `node` → `transport` → `ble` → `nekotrak`。
- 各自内部字段顺序也固定。
- 它们都是**只增不改**的加法字段，`schema` 仍是 `3`；老消费者忽略即可。
- 未知字段被安全忽略。

### `console`

`online, method, lastSeenAt, mac, idleHint, name, type, statusText`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `online` | bool | 迟滞后的在线结果 |
| `method` | str | 探测方式：`arp` / `ping` / `tcp`（端口命中）/ `lan` |
| `lastSeenAt` | int/null | 最近一次在线的时间戳 |
| `mac` | str/null | ARP 回填的 MAC（可作 WOL 凭据） |
| `idleHint` | true | L0 分不清「在玩 / 挂机」，恒为 `true` |
| `name` | str | 配置里的主机名 |
| `type` | str | 配置里的主机类型（如 `ps5`） |
| `statusText` | str/null | 离线文案，来自 `offlineText`，默认 `离线` |

> 固件侧**永远不会有** `method: "arp"`——lwIP 没有 `SendARP` 等价能力，探测链只有 `ping → tcpPorts`。

### `node`

`ssh, online, statusText, method, source, hostname, lastSeenAt, uptimeSeconds`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ssh` | bool | 本轮 SSH 是否成功；降级通道下为 `false` |
| `online` | bool/null | 仅降级路径有值：LAN 探活命中 `true`，降级离线 `false`；SSH 成功时为 `null` |
| `statusText` | str/null | 仅降级路径有值：降级离线为 `离线`，降级在线为 `null` |
| `method` | str | 采集方式：`ssh` 或 `lan` |
| `source` | str | 数据来源：`ssh` 或 `lan` |
| `hostname` | str/null | 远端 hostname |
| `lastSeenAt` | int/null | 最近一次成功的时间戳 |
| `uptimeSeconds` | int/null | 远端 uptime（秒） |

### `transport`

`active, degraded, online, lastSeenAt, detail`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `active` | str | 本轮实际取数通道：`usb` `usb-wifi` `adb` `adb-wifi` `ssh` `lan` `tcp` `none` |
| `degraded` | bool | `true` 表示只拿到存活信息；主机恒为 `false` |
| `online` | bool | 该通道下判定的在线状态 |
| `lastSeenAt` | int/null | 最近一次在该通道上看到设备的时间戳 |
| `detail` | str | 自由文本：`arp` / `ping` / `tcp:9295` / `wifi:bonjour` / `adb:192.168.1.101:5555` |

`transport` 是条目级对象，Python 产出的条目都会带（无通道时 `active="none"`、`online=false`）。**只有** daemon 因整轮失败而复用上一轮条目时该键缺失——它描述「本轮通道」，本轮没有通道。

### `ble`

`address, name, rssi, services, connected, heartRate, statusText, lastSeenAt`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `address` | str/null | 命中的 BLE 地址（大写规范格式） |
| `name` | str/null | 广播名或配置里的 `name` |
| `rssi` | int/null | 信号强度（dBm） |
| `services` | str[] | 本轮尝试读取的服务：`battery` / `heart_rate` |
| `connected` | bool | 是否成功连接并读取 |
| `heartRate` | int/null | 心率（BPM），仅请求 `heart_rate` 且收到通知时有值 |
| `statusText` | str/null | 降级文案：`已发现未连接` / `未发现`；正常为 `null` |
| `lastSeenAt` | int/null | 扫描到该设备的时间戳；未发现为 `null` |

### `nekotrak`

`version, source, uptimeSec, startedAt, lastLogAt, trackerActive, trackerStartedAt, trackerStoppedAt, currentGame, currentGameAppId, pet, pomodoro, warns`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `version` | str/null | NekoTrak 版本 |
| `source` | str | 数据来源 |
| `uptimeSec` | int/null | 运行时长（秒） |
| `startedAt` | **str**/null | ISO 时间字符串，**不是 epoch 整数** |
| `lastLogAt` | **str**/null | 同上 |
| `trackerActive` | bool | TRACKER 是否在记录 |
| `trackerStartedAt` | **str**/null | 同上 |
| `trackerStoppedAt` | **str**/null | 同上 |
| `currentGame` | str/null | 当前游戏名 |
| `currentGameAppId` | int/null | Steam AppID |
| `pet` | str/null | 宠物近况 |
| `pomodoro` | null | 当前恒为 `null`（数据源是密文） |
| `warns` | str[] | 告警列表 |

> 那四个时间字段是 **ISO 字符串**不是整数，这是真机快照回归测试盯着的一条。

---

## 什么时候写 `error`

只有这三种情况：

1. 主通道失败**且** `fallback: false`；
2. 整条通道链全部失败；
3. 配置错误 / 缺依赖（例如 `ADB not found…`、`缺少 BLE 依赖：bleak`、`SSH 私钥不存在：…`）。

**主机离线、降级在线、降级离线、BLE 扫到但连不上——这些都不写 `error`**，状态看 `online` / `statusText`。

---

## 一致性的唯一事实源

[`schema/status-contract.json`](../../schema/status-contract.json) 是 schema-3 字段、加法对象与字段顺序的唯一事实源。Python 测试、C# 测试、固件 host 测试**三处**都对照它校验；改字段必须三边同时改，否则测试直接红。
