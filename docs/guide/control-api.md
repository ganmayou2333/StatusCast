# ESP32 控制面（`/control/`）

> **这一段是安全敏感的。** `/control/` 能重启板子、改写配置、轮换令牌。它走**明文 HTTP**，绑 `0.0.0.0`，出厂默认令牌是公开的 `statuscast`。**在你可以接受「同一局域网内任何人只要知道这个默认值就能控制它」之前，不要把它接进不可信网络。** 上线前第一件事是轮换令牌。

固件的其余部分（`/status/`、`/health`、`/diag`）是只读的，不需要令牌，NekoTool 才能零改动接入。

---

## 鉴权

每个 `/control/*` 请求都必须带请求头：

```text
X-StatusCast-Token: <当前令牌>
```

失败（缺失或不匹配）统一返回：

```text
401 Unauthorized
{"error":"unauthorized"}
```

令牌按以下优先级取，**每次请求都重新读 NVS**（不缓存）：

| 优先级 | 来源 | 说明 |
| --- | --- | --- |
| 1 | NVS `sc_state` / `ctl_token` | 只能通过 `POST /control/token` 写入 |
| 2 | 编译期 `CONFIG_SC_CONTROL_TOKEN` | `Kconfig.projbuild` 的默认值 |
| 3 | 硬编码兜底 | 宏未定义时用字面量 |

**默认值是 `statuscast`。** 这不是秘密，`sdkconfig` 和 `Kconfig.projbuild` 里都写着同一个词。

其它已知弱点（都是已知项，不是疏忽）：

- **不恒定时间比较**：用的是 `strcmp`，理论上存在时序侧信道。
- **无速率限制 / 无锁定**：可以无限次猜。
- **无重放防护**：静态共享密钥放在静态头里，抓到一个请求就能永久重放。
- **无 TLS**：令牌和请求体都明文过网。
- 超过 79 字节的头会被截断，按「缺失」处理（→ 401）。

加固手段目前只有两个：轮换令牌、把设备限制在可信 VLAN。TLS / Bearer / IP 白名单都在 v2 待办里。

### 轮换令牌

```powershell
.\firmware\esp32\tools\sc-control.ps1 -Action token -Token <当前令牌> -NewToken <新令牌>
```

新令牌长度必须 **4..63** 个字符。写入 NVS 后立即生效。

> **没有「恢复默认」的路径。** 没有任何代码会删除 NVS 里的 `ctl_token`；要回到编译期默认值只能擦 NVS（`idf.py erase-flash` 或串口侧等效操作），那会一并丢掉 WiFi 凭据和迟滞状态。

---

## 路由表

`GET /diag` 是**不需要令牌**的只读诊断路由。

| 方法 | 路径 | 鉴权 | 成功响应 | 其它状态码 |
| --- | --- | --- | --- | --- |
| `GET` | `/diag` | 否 | `{"deviceCount":N,"generatedAt":G,"roundMillis":M,"i2c":[…],"sensor":{"ok":bool,"temperatureC":f,"humidityPct":f},"led":"…","ledMode":"…","ledAvailable":bool}` | 503（快照未就绪） |
| `GET` | `/control/status` | 是 | 见下 | 401 |
| `GET` | `/control/scan` | 是 | `{"i2c":["0x3C",…],"ble":[{"address":"…","name":"…","rssi":-52},…]}` | 401、500 |
| `POST` | `/control/reboot` | 是 | `{"ok":true,"rebootIn":2}` | 401 |
| `POST` | `/control/config` | 是 | `{"ok":true,"bytes":N,"rollbackInSeconds":90}` | 401、400、500 |
| `POST` | `/control/led` | 是 | `{"ok":true,"led":"green\|amber\|red\|off","ledMode":"auto\|manual"}` | 401、400 |
| `POST` | `/control/token` | 是 | `{"ok":true}` | 401、400、500 |
| `GET` | `/control/log` | 是 | `text/plain; charset=utf-8`，最近 ≤128 行 / 16384 字节 | 401 |
| `GET` | `/control/coredump` | 是 | `application/octet-stream` + `X-Dump-SHA256` / `X-Dump-Size` / `Content-Disposition` | 401、404、500 |
| `DELETE` | `/control/coredump` | 是 | `{"ok":true}` | 401、404、500 |
| `GET` | `/control/audit` | 是 | JSON 数组，≤16 条 `{"at":int,"action":str,"result":str,"peer":str}` | 401 |

> 405 / 404 语义与公开面一致，但注意：`/control/*` 的**方法写错**例如 `GET /control/reboot` 会命中 `/control/*` 的 GET 通配，得到 404 而不是 405。用对方法。

### `GET /control/status`

一次拿到全部运行时状态，字段顺序固定：

```text
uptimeSec, wifi{connected,ip,rssi}, devices, generatedAt, roundMillis,
sensor{ok,temperatureC,humidityPct}, i2c[], led, ledMode, ledAvailable,
config{refreshSeconds,consoles,
bleDevices,aliases,pcFeed,pcUrl}, configRollbackPending
```

`configRollbackPending` 为 `true` 表示上一轮 `POST /control/config` 还在 90 秒观察窗口里。

### `POST /control/config`

体是**完整的新 `config.json`**（JSON 对象，≤8191 字节）。

| 情况 | 响应 |
| --- | --- |
| 成功 | `{"ok":true,"bytes":N,"rollbackInSeconds":90}` |
| 体为空或超过 8192 字节 | 400 `{"error":"bad request"}` |
| 体不是 JSON 对象 | 400 `{"error":"body is not a JSON object"}` |
| 写入失败 | 500 `{"error":"write failed"}` |

**自带 90 秒自动回滚**：写入前把当前文件备份成 `/littlefs/config.prev`，然后原子替换、重载、把 NVS 的 `cfg_pending` 置 1 并起 90 秒计时。如果 90 秒内板子没被确认健康（正常跑完一轮并清标记），就自动恢复 `config.prev`。开机时若发现标记还在，说明上次写入导致崩溃循环，同样立刻回滚。

所以「改坏了连不上板子」不会变砖——等 90 秒它自己回来。

### `POST /control/led`

体：`{"color":"green"}` / `"amber"` / `"red"` / `"off"`，或 `{"auto":true}` 交还给自动状态机。

| 情况 | 响应 |
| --- | --- |
| 成功 | `{"ok":true,"led":"<当前显示的颜色>","ledMode":"auto\|manual"}` |
| 体为空 | 400 `{"error":"bad request"}` |
| 未知颜色 | 400 `{"error":"unknown color"}` |
| 既没 color 也没 auto | 400 `{"error":"color or auto required"}` |

三个字段含义不同，别混着读：

| 字段 | 含义 |
| --- | --- |
| `led` | 像素**此刻实际显示**的颜色：`green` / `amber` / `red` / `off`。手动覆盖期间报的就是覆盖色 |
| `ledMode` | 谁在做主：`auto`（自动状态机）或 `manual`（`POST /control/led` 覆盖中） |
| `ledAvailable` | 灯带是否初始化成功；`false` 时像素恒暗，但 `led` 仍反映状态机算出的值 |

`off` 只表示「像素是暗的」，**不表示**「自动控制被挂起」——后者看 `ledMode`。

`{"auto":true}` 会把控制权立刻交还自动状态机，并**当场按当前自动状态重绘**（包括自动状态本身就是 `off` 的情况）。

> 手动颜色**只存在 RAM**，重启即恢复自动控制。

### `GET /control/log` 与 `GET /control/coredump`

这两条是**唯二用 chunked** 的响应（公开面不用 chunked）。`/control/log` 返回最近 ≤128 行、≤16384 字节的循环缓冲。

`GET /control/coredump` 只在崩溃分区里有内容时返回 200，否则 404 `{"error":"no coredump"}`；`DELETE` 用于擦除，否则 404 `{"error":"no coredump"}`。

---

## Windows 侧脚本

```powershell
.\firmware\esp32\tools\sc-control.ps1 -Action status
```

十个动作：`status` `scan` `audit` `log` `coredump` `coredump-clear` `reboot` `config` `led` `token`。默认 `-Base http://statuscast.local:5099`（mDNS）。

### 令牌怎么存——以及风险

- 不带 `-Token` 时，脚本从 `%APPDATA%\StatusCast\control-token` 读（明文 ASCII，`.Trim()`）。
- 带 `-Token` 时，脚本**把这个值写进上面那个文件**，然后使用它。
- 两者都没有就直接报错：`no control token stored; run: .\sc-control.ps1 -Action status -Token <token>`。

> **`-Token <值>` 会留在 PowerShell 命令历史里**（PSReadLine 的 `ConsoleHost_history.txt` 是明文）。要在共享机器上用，先把令牌写进那个文件再不带 `-Token` 调用，或改用环境变量在一次性会话里传入。

`401` 时脚本直接抛 `board rejected the token (401)`，不会静默重试；其它失败重试 2 次、间隔 400 ms。`coredump` 会校验响应里的 `X-Dump-SHA256`。

---

## 审计

只有 `/control/*` 的动作会被记录，进 16 条的内存环（掉电即失），**令牌本身永不记录**。每条是 `{"at":int,"action":str,"result":str,"peer":str}`，失败鉴权也会记一条 `unauthorized`。用 `GET /control/audit` 读。

---

## 与公开面的差异汇总

| 方面 | 公开面（`/status/` `/health` `/diag`） | 控制面（`/control/*`） |
| --- | --- | --- |
| 鉴权 | 无 | `X-StatusCast-Token` |
| 绑定 | `0.0.0.0:5099` | 同 |
| 传输 | 明文 | 明文（**无 TLS**） |
| chunked | 不用 | `/control/log`、`/control/coredump` 用 |
| 副作用 | 无 | 重启、改配置、换令牌、擦 coredump |
