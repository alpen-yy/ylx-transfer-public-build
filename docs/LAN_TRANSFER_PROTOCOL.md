# 局域网数据传输功能设计文档

> 状态：v1 已实现，本文保留原始设计决策和协议背景。权威 wire contract 以
> `RP-YLX/capture/docs/transfer-api/v1/openapi.json` 及同目录 schemas 为准；
> PC production 默认使用真实设备数据，模拟器仅由显式 `demo` feature 启用。
>
> 本文档横跨两个仓库：Pi 端协议规格（第四节）落地在 `RP-YLX` 仓库的
> `capture/src/ylx_capture/`（下文引用的文件路径均相对该仓库根目录）；
> PC 客户端实现（第五节起）落地在本仓库 `ylx-transfer`。PC 运行时的
> source of truth、启动/恢复顺序、状态机和 CI 命令以
> [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) 与
> [`docs/adr/ADR-PC-001-persistence.md`](adr/ADR-PC-001-persistence.md) 为准。

## 一、背景

项目原本对"采集数据怎么从设备上取出来"没有任何产品化方案。树莓派上的
`ylx-capture` 只负责本地录制（双目 MJPEG + IMU，落盘在 SD 卡或外接存储），
`capture/src/ylx_capture/networking.py` 里的联网能力也只覆盖设备本身连
Wi-Fi/热点，不涉及录制数据的搬运。早期取数依赖拔卡或运维手工复制，
这两种方式都不是面向日常使用的产品能力。

## 二、需求与目的

需求来自这样一句话："可以增加一个局域网数据传输功能吗？会写一个 PC 端的
app"，随后逐步细化为：

- 局域网内可能同时存在**多台**树莓派采集设备，PC 端要能分别发现、分别管理。
- PC 端用 Tauri 写桌面应用，要**兼顾性能**、**能自动发现设备**、**传输稳定**。
- 连接必须由 PC 主动发起，但**树莓派本机要弹出确认按钮**，人工点击确认后才能
  建立连接——这是显式提出的安全要求，不接受纯软件层面的 token/密码鉴权。
- PC 端不仅要能下载，还要能**操控删除**树莓派上的数据。

目的：把"从树莓派取数据"从一个运维动作，变成这个采集系统自带的产品能力，
同时不能牺牲现有采集链路的稳定性、不能让局域网内任意设备未经物理确认就能
拿到可能包含隐私内容的录制数据（双目视频 + IMU）。

## 三、设计过程与决策依据

以下按讨论顺序记录每个关键分支点：讨论过的选项、最终决定、以及为什么。

### 3.1 传输方式：为什么不做录制中的实时流式传输

**选项**：录制过程中实时流式传输，还是录制完成后批量传输。

**决定**：只做录制完成后的批量传输。

**原因**：根目录 `README.md`"设备工作方式"一节写得很明确——当前固件只在
UVC 视频流运行时刷新 XU IMU，`IMUDevice`/`CaptureSession` 必须独占打开相机，
且"不能再由 OpenCV、FFmpeg 或另一个进程同时打开同一台相机"。如果在录制的
同时再跑一条实时传输链路，等于给本来就脆弱的单进程采集链路增加一个新的
资源竞争者和故障点，收益（更快看到数据）远小于风险（可能拖垮正在进行的
标定级录制）。批量传输在时间上和采集完全解耦，不存在这个风险。

### 3.2 传输协议：为什么是 HTTP + Range，不是自定义协议

**选项**：自定义二进制传输协议，还是 HTTP + Range 请求。

**决定**：HTTP + Range 请求，PC 端做并发分片下载。

**原因**：用户明确会用 Tauri（Rust 后端）写 PC 端，且要求"性能高、稳定"。
局域网内 HTTP/1.1 配合 Range 请求的吞吐基本能跑满千兆网络，Rust 的
`reqwest` 天然支持并发分片下载和断点续传，不需要额外造轮子；自定义协议
虽然理论上能再压榨一点性能，但开发和维护成本高得多，稳定性也更依赖自己
的实现质量。对于"局域网内搬运几百 MB 到几 GB 文件"这个场景，HTTP 已经
是性价比最高的选择。

### 3.3 进程架构：为什么独立出 `transfer-daemon`

**决定**：新增一个独立的 snap app `transfer-daemon`，不把网络传输功能塞进
现有的 `capture-daemon`。

**原因**：这是基于现有 `snap/snapcraft.yaml` 权限模型做的架构判断——
`capture-daemon` 目前持有 `camera`、`raw-usb` 等高权限 plug，而网络传输
服务是这个项目里**第一个真正对局域网开放端口**的组件，天然是新增攻击面。
把它拆成独立进程后：

- 只需要 `network`、`network-bind`、`removable-media`，完全不需要碰摄像头
  相关权限，实现最小权限。
- 传输服务如果出 bug 崩溃，不会连带影响正在进行的录制——`capture-daemon`
  是这个设备最核心、最不能挂的进程，不应该被一个新功能拖累稳定性。

### 3.4 设备发现：为什么是 mDNS，以及为什么要留手动 IP 兜底

**决定**：默认用 mDNS/Bonjour 自动发现，同时提供手动输入 IP 的兜底入口。

**原因**：mDNS 是明确问过的选项，用户直接选择"需要，用 mDNS/Bonjour 自动
发现（推荐）"——因为局域网里"可能有多个树莓派设备"，靠用户记 IP 不现实。
手动 IP 兜底不是最初讨论的一部分，是在 PC 端 UI 设计阶段、系统性检查功能
缺口时补上的：mDNS 组播在一些路由器/VLAN 环境下会被屏蔽，纯自动发现在这
类网络里会直接变成"什么都找不到"，必须有一条不依赖组播的路径。

### 3.5 鉴权方式：为什么是"物理确认"而不是 token/密码

**决定**：不做免确认的纯 token/密码鉴权；PC 发起连接请求后，树莓派本机
弹出确认按钮，必须有人在设备旁边物理点击"允许"才能建立连接。

**原因**：这是用户直接提出的需求（"树莓派上弹出按钮，需要在树莓派上点击
确认"），不是我推导出来的。背后的逻辑是：mDNS 让局域网内任何设备都能
*发现*这台 Pi，但发现不等于应该能*访问*——采集的是双目视频 + IMU，属于
可能涉及隐私的数据，纯软件层面的 token/密码在局域网场景下几乎形同虚设
（同一网络里的人本来就能看到彼此），物理确认把信任锚点放在"谁能碰到这台
设备"上，是这类家用/实验室场景下更合理的边界。

### 3.6 权限粒度：为什么配对即完整权限，不做二次删除确认

**选项**：配对时只给"浏览/下载"权限、删除需要单独再授权或再确认一次，
还是配对即拿到包含删除在内的完整权限。

**决定**：配对即完整权限（浏览、下载、删除都可以），不做二次删除确认、
不做读写分级 token。

**原因**：直接问过这个问题，用户回答"不需要二次确认"。推理是 §3.5 里
"物理点击确认"这一步本身已经是唯一需要的信任锚点——一个人愿意在设备上
点"允许"，就已经代表了对这台 PC 的完全信任，再叠加一层"删除时还要再确认
一次"只是重复同一个信任判断，没有实际增加安全性，只会增加操作阻力。

### 3.7 信任的有效期：为什么是会话级，不做持久化配对记忆

**选项**：配对一次后长期记住这台 PC（类似"信任的设备"列表），还是每次
连接都要重新走一遍确认。

**决定**：不持久化信任关系，只在**连接存续期间**有效；断开后重新连接必须
重新在设备上点击确认，即使是同一台 PC。

**原因**：用户明确要求"连接后再断开二次连接需要再次确认"。这避免了信任
关系被长期滥用的风险——比如 PC 丢失或被盗、或者局域网环境发生变化后，
不会有一个"曾经被信任过就一直有效"的后门，每次访问都要求一次当下的、
主动的物理确认。

### 3.8 会话保活机制：为什么是心跳轮询，不是 WebSocket

**决定**：PC 端配对成功后定期发心跳（`POST /api/v1/session/heartbeat`）
维持会话，超时判定为异常断开；不引入 WebSocket 长连接。

**原因**：这是我基于现有代码风格提出的技术选型，不是用户直接指定的。
项目目前的 IPC 实现（`capture/src/ylx_capture/ipc.py`）全部是最朴素的
"JSON over Unix socket"请求/响应模式，`RemoteCaptureController` 已经有
"每 0.25 秒轮询一次 snapshot"的先例；整个项目也只依赖 stdlib +
`gpiod`/`ylx-imu-sdk`，没有引入过任何长连接类依赖。心跳轮询完全能表达
"连接中 / 已断开"这个二元语义，没有必要为了这一个功能引入 WebSocket 库
和相应的复杂度，符合项目一贯"能用 stdlib 解决就不加依赖"的风格。

### 3.9 多设备 / 多 PC 并发：为什么不需要额外协议改动

**背景**：PC 端 UI 设计到后期，重新审视"一台 PC 同时连接多台 Pi"这个
最初就提出的场景时，发现早期原型里被简化成了"同一时间只能连一台"的模型
（连新设备会自动断开旧的）。

**决定**：改为允许同一台 PC 同时保持多个已连接设备；同时明确协议层面
本来就支持一台 Pi 被多台 PC 同时连接。

**原因**：重新看 §3.7 定下的 token 模型就会发现，token 是"按每次配对"
独立签发的，`{token: {...}}` 这张表天然支持多条并存——"一台 PC 只能连
一台设备"从来都不是协议层面的限制，只是 PC 端 UI 原型早期实现偷懒引入的
人为限制。既然最初的需求就明确提到"可能有多个树莓派设备"，把这个限制
去掉、让 UI 反映协议本来就支持的能力，是更准确的设计。

## 四、Pi 端协议规格

### 4.1 进程与权限划分

对应 §3.3 的决定，新增一个独立的 snap app：`transfer-daemon`，与现有
`capture-daemon` 分离：

| App               | 新增/现有 | plugs                                                                                  |
| ----------------- | --------- | -------------------------------------------------------------------------------------- |
| `capture-daemon`  | 现有      | `camera`, `gpu-2604`, `hardware-observe`, `network-bind`, `raw-usb`, `removable-media` |
| `transfer-daemon` | **新增**  | `network`, `network-bind`, `removable-media`                                           |

`transfer-daemon` 不直接持有采集状态。它作为 IPC **客户端**连接
`capture-daemon` 现有的 Unix socket（`ipc.py` 的 `CaptureIpcServer`），与
`ylx-capture` CLI、`frame-gui` 处于同等地位，只用于：

- 读取 `scan_session_catalog()` 产出的录制清单（只读文件系统访问也可以
  直接读，不一定要经 IPC，具体见 §4.4）。
- 转发配对请求、等待触屏确认结果（见 §4.3）。

### 4.2 设备发现：mDNS

对应 §3.4：

- 使用纯 Python `zeroconf` 库（vendor 进 snap，参照现有 `ylx-imu-sdk` 的
  打包方式），不依赖宿主 avahi-daemon。
- Service type：`_ylx-capture._tcp.local.`
- Instance name：复用 `device_identity.py` 中已用于热点 SSID 的 8 位设备
  ID，形如 `YLX-30D5872D`。它是由完整 TLS 指纹派生的展示 ID，不是 PC
  设备 actor 的身份键；PC 端使用完整指纹索引设备。
- TXT record：

  | Key         | 说明                                               |
  | ----------- | -------------------------------------------------- |
  | `api`       | 协议版本，当前为 `1`                               |
  | `device_id` | 8 位设备 ID，与 instance name 一致，便于客户端解析 |

`device_id` 和 instance name 都是未受信的发现/展示元数据，不能作为 TLS pin、
PC fleet key 或持久化新记录的身份。PC 从完整 TLS SHA-256 指纹统一派生：

```text
canonical identity / path / RPC key: ylx-<64 lowercase hex>
TLS pin:                             sha256:<64 lowercase hex>
display label / legacy alias:        YLX-<first 8 uppercase hex>
```

指纹输入只接受可选的大小写不敏感 `sha256:` 前缀加恰好 64 个 ASCII hex，
内部规范化为无前缀小写。两台设备可以拥有相同的 8 位展示标签，PC 仍必须按
完整 canonical identity 保持独立 endpoint、client/handle、session 和操作。

兼容策略是 dual-read/canonical-write：新设备、job 和 library row 写完整 ID；
旧 job、自然键/request digest、library path/entry key、delete intent、lease 与
S3 key 不盲目改写。历史 `YLX-<8 hex>` alias 仅在当前完整身份集合中恰好匹配
一台设备时可解析；0 个匹配视为 unknown，多个匹配明确报 ambiguous 并
fail closed。

局域网中可能同时存在多台 Pi，PC 端按 mDNS 枚举出的多个 instance 分别
处理，互不影响。

若 mDNS 发现不到设备（路由器/VLAN 屏蔽了组播），PC 端提供手动输入 IP 的
兜底入口。手动地址仍先获取/校验完整 TLS 指纹，并通过同一 HTTPS 配对流程；
它只跳过 mDNS 候选发现，不是另一套鉴权路径，也不存在明文 HTTP fallback。

### 4.3 配对与会话生命周期

对应 §3.5～§3.9。

#### 4.3.1 流程

```
PC                                          Pi
│  1. mDNS 发现设备列表                        │
│  2. POST /api/v1/pairing                    │
│     {"client_name": "..."}  ────────────────>│
│                                              │  transfer-daemon 经 IPC 向
│                                              │  capture-daemon 提交配对请求
│                                              │
│                                              │  frame-gui 轮询 snapshot（沿用
│                                              │  RemoteCaptureController 现有的
│                                              │  0.25s 轮询机制）发现待处理请求，
│                                              │  弹出确认框："PC「xxx」请求连接"
│                                              │
│                                              │  用户在触屏上点击"允许"
│  3. HTTP 响应返回 token（请求期间挂起，       │
│     直到有结果或超时）        <───────────────│
│  4. 之后的请求携带 token                      │
│     GET/DELETE ... + Authorization: Bearer  │
│     <token>                  ───────────────>│
```

- 配对请求默认等待 60 秒；超时或用户点"拒绝"，PC 端收到明确的失败响应。
- 同一时间只弹出一个确认框；若有多个待处理请求，按到达顺序排队展示。

#### 4.3.2 权限范围

配对一旦被允许，即授予**完整权限**（浏览、下载、删除），不做二次删除
确认、不做读写分级 token（§3.6）。

#### 4.3.3 会话与断开

不做持久化配对，信任只在**连接存续期间**有效（§3.7）：

- 配对成功后 PC 需要定期发心跳维持会话：`POST /api/v1/session/heartbeat`
  （建议每 5s 一次），`transfer-daemon` 记录每个 token 的 `last_seen_at`。
- 满足以下任一条件即判定断开，token 立即失效：
  - PC 主动调用 `DELETE /api/v1/session`（正常退出/用户手动断开）。
  - 心跳超时（建议 15s 未收到视为异常断开，如网络中断、PC 应用崩溃）。
- **重新连接必须重新走一遍 §4.3.1 的配对确认流程**，即使是同一台 PC、
  同一个 `client_name`，树莓派上也要重新弹窗确认。
- `transfer-daemon` 只在内存中维护 `{token: {client_name, last_seen_at}}`，
  daemon 重启即清空，不落盘。
- 触屏 GUI 可顺带显示当前连接状态（"当前已连接：xxx"）并提供手动断开
  按钮，效果等同于 PC 端主动断开。

不采用 WebSocket 做长连接，原因见 §3.8。

#### 4.3.4 多设备 / 多 PC 并发

token 按"每次配对"独立签发，`{token: {...}}` 表天然支持多条并存，因此
以下两种并发**不需要额外协议改动**（§3.9）：

- **同一台 PC 同时连接多台 Pi**：每台 Pi 各自独立走一遍 §4.3.1 配对流程、
  各自持有独立 token，互不影响。PC 端 UI 已按"可同时保持多个已连接设备"
  设计。
- **同一台 Pi 同时被多台 PC 连接**：多个 token 可以同时有效；若多个配对
  请求同时到达，§4.3.1 已说明按到达顺序排队弹窗，不需要互斥。

### 4.4 HTTP API

均为 JSON，鉴权通过 `Authorization: Bearer <token>` header（只有创建配对
请求在 token 签发前；heartbeat 必须携带已签发 token）。

| Method   | Path                                 | 说明                                                                                                       |
| -------- | ------------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| `POST`   | `/api/v1/pairing`                    | 发起配对请求，挂起直到触屏确认/拒绝/超时，成功返回 token                                                   |
| `POST`   | `/api/v1/session/heartbeat`          | 维持会话存活                                                                                               |
| `DELETE` | `/api/v1/session`                    | 主动断开，token 立即失效                                                                                   |
| `GET`    | `/api/v1/device`                     | 设备 ID、hostname、存储剩余空间                                                                            |
| `GET`    | `/api/v1/sessions`                   | 录制清单，基于 `session_catalog.scan_session_catalog()`                                                    |
| `GET`    | `/api/v1/sessions/{id}`              | 单个录制详情，含文件清单                                                                                   |
| `GET`    | `/api/v1/sessions/{id}/files/{path}` | 文件下载，支持 `Range` 头（stdlib `http.server` 不原生支持，需要自行实现），供 PC 端并发分片下载和断点续传 |
| `DELETE` | `/api/v1/sessions/{id}`              | 删除该录制（`video/`、`raw/`、`preview/`、`events.jsonl`、`session.json` 整体删除）                        |

`GET /api/v1/sessions` 只返回 `state == "complete"` 且 `integrity_ok ==
True` 的录制（沿用 `session_summary.SessionSummary` 中已有字段），进行中
的录制目录不通过此接口暴露，避免读到写入中的半截文件。

单条录制的示例响应字段（基于 `SessionSummary`）：

```json
{
  "id": "20260731-142233",
  "state": "complete",
  "duration_seconds": 121.4,
  "video_bytes": 483920112,
  "integrity_ok": true,
  "imu_samples": 14520,
  "files": ["video/segment_000.mp4", "raw/...", "preview/imu.jsonl", "events.jsonl", "session.json"]
}
```

批量/多选操作（PC 端一次删除或下载多条录制）不引入新接口，由 PC 端循环
调用 `GET`/`DELETE /api/v1/sessions/{id}` 实现，见 §5.4。

### 4.5 需要扩展的现有模块

| 文件                                    | 改动                                                                                                                                                                           |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `capture/src/ylx_capture/controller.py` | 增加待处理配对请求队列（`PairingRequest{client_name, requested_at}`）、`request_pairing()` / `decide_pairing()`、超时自动拒绝                                                  |
| `capture/src/ylx_capture/ipc.py`        | `CaptureCommandDispatcher` 增加 `pairing_request` / `pairing_decide` 命令；`snapshot_to_dict` 携带待处理配对请求列表                                                           |
| `capture/src/ylx_capture/ui.py`         | 新增配对确认弹窗；顶部状态条显示当前连接的 PC 与手动断开入口                                                                                                                   |
| `capture/src/ylx_capture/transfer/`     | **新增**：`discovery.py`（zeroconf 广播）、`http_server.py`（HTTP handler + Range 支持）、`session.py`（token/心跳状态管理）、`daemon.py`（新 app 入口，对应 `ylx-transferd`） |
| `snap/snapcraft.yaml`                   | 新增 `transfer-daemon` app 及 plugs；`parts.ylx-python` 需要 vendor `zeroconf` 依赖                                                                                            |

## 五、PC 客户端 UI 设计

### 5.1 为什么是交互原型，不是 claude.ai/design

用户最初要求用 `https://claude.ai/design` 设计界面，但那是网页端独立
产品，不在 Claude Code 的工具范围内。给出的两个选项——"在这里直接做一个
可交互的 HTML 原型"或"写一份设计需求文档供你自己去那边用"——用户选了
前者，于是设计过程变成了在这个仓库里迭代一个可交互的 HTML/CSS/JS 原型，
而不是产出静态设计稿。

### 5.2 视觉方向的演变

视觉设计经历了三轮方向调整，每一轮都是对上一轮明确反馈的直接回应：

| 轮次   | 方向                                                                                                                                                             | 触发原因                                                                                                                                                                                                                                                                     |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 第一版 | 精密仪器/相机设备美学：DIN 风格窄体大写标签、青蓝（镜头镀膜）+ 铜色（VU 表指针）双强调色、深石墨背景、胶囊按钮                                                   | 基于项目本身"精密硬件/双目相机/IMU 标定"的属性做的第一版方向判断，把设备 ID、字节数、时长等数据全部用等宽字体强调"仪表读数"感                                                                                                                                                |
| 第二版 | 原生桌面应用观感：更大的圆角、毛玻璃材质侧栏、macOS 风格红黄绿信号灯标题栏，字体改用 `ui-rounded` 调用系统圆体                                                   | 用户反馈"不是web页面，是一个Tauri软件，需要精致圆润的画风"——第一版的做法更像一个网页管理后台，没有传达"这是一个悬浮的原生桌面窗口"的观感                                                                                                                                     |
| 第三版 | Claude 品牌配色：暖炭灰/暖米白基调 + 赤陶橙单一强调色，圆角收窄到 8～11px（只有标签/徽章保留胶囊形），去掉渐变按钮和彩色光晕投影，字体换回克制的系统无衬线字体栈 | 用户反馈"UI太丑了，换个配色，可以适当大的重构一下"，随后明确"使用claude的配色，现在看起来非常不精致，可以再精致一点"——"精致"在这里对应的实际改动主要是**克制**：双强调色改单强调色、胶囊按钮改克制圆角、渐变光晕改纯色平涂、阴影从厚重改成几乎只剩一条描边，而不是加更多装饰 |

### 5.3 原型链接与覆盖范围

<https://claude.ai/code/artifact/beff60a0-dc4d-4728-b051-b6217d2a17de>

原型是私有链接，仅本人可见；作为 Tauri 前端实现的界面/交互参考，不是
最终视觉规范。覆盖的界面：

- **设备发现与配对**：左侧栏列出局域网内多台 Pi（在线/已连接/离线状态用
  LED 圆点区分），支持 mDNS 发现不到时手动输入 IP；点击未连接设备弹出
  等待确认面板，对应 §4.3.1 的配对挂起态。
- **会话浏览**：按设备展示 §4.4 中 `GET /api/v1/sessions` 返回的录制
  清单，支持搜索、按状态筛选、按时间排序、多选批量下载或删除、"下载全部
  新数据"一键批量。
- **本地数据管理**：下载到本机的录制单独成一个视图，与"设备"视图解耦
  （不需要保持连接也能看本地文件），显示来源设备、本地大小、上传状态。
- **对象存储上传**：**纯 PC 端本地能力，不经过 Pi、不在 §4.4 的 API 范围
  内**。Tauri 应用本地保存 endpoint/bucket/密钥配置，直接从本地磁盘上传
  到用户自建的对象存储，Pi 侧协议对此无感知。
- **传输队列**：下载（Pi→PC）与上传（PC→云）共用一个队列面板，用方向
  箭头区分；本地设置了并发上限，超出的排队等待——这是纯客户端节流，
  Pi 侧 HTTP 服务只要能扛住几个并发的 `Range` 请求即可。
- **失败与重试**：下载失败会带出对应设备"连接已中断"的状态（呼应
  §4.3.3 的心跳超时语义）；重试复用 `Range` 请求从失败点续传，不是整个
  文件重新下载——这依赖 §4.4 中 `GET /api/v1/sessions/{id}/files/{path}`
  已经要求的 `Range` 支持。

### 5.4 功能缺口的发现过程

原型搭好主流程后，被两次问到"还有什么值得优化的"，每一次都是先做完整
审查再给建议，用户两次都要求"全部做进去"。理由摘要：

**第一轮（5 项，聚焦"能不能用起来"）**

| 缺口                   | 为什么算缺口                                                                                                       |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------ |
| 对象存储配置界面       | 上传功能存在但没有任何地方能填 endpoint/bucket/密钥，功能等于摆设                                                  |
| 失败态设计             | 原型里所有传输默认必定成功，不反映真实网络环境；设备异常断开（心跳超时）和用户主动断开在体验上应该是两种状态       |
| 设备发现空状态         | 局域网里一台都发现不到时没有任何提示，也没有兜底手段（见 §3.4 的手动 IP）                                          |
| "已备份"状态回流       | 下载状态在"设备"页、上传状态在"本地数据"页，用户想知道"这条录制是否已安全上云、可以从 Pi 删了腾空间"时要跨页拼信息 |
| 会话列表搜索/筛选/排序 | 设备持续录制，列表会随时间变长，纯线性滚动不可持续                                                                 |

**第二轮（6 项，聚焦"功能正确性和真实性"）**

| 缺口           | 为什么算缺口                                                                                                              |
| -------------- | ------------------------------------------------------------------------------------------------------------------------- |
| 批量多选       | 之前只有"全选新数据"这种全量批量，没有"就选这几个"的能力                                                                  |
| 多设备并发连接 | 见 §3.9——最初需求就提到"可能有多个树莓派设备"，但早期原型把并发连接限制成了 1，属于实现偷懒引入的限制，不是有意的产品决策 |
| 真断点续传     | §4.4 的协议本来就要求 `Range` 支持，但原型里"重试"最初是进度清零重新来，没有真正利用这个能力                              |
| 清理已备份数据 | "☁ 已备份"标签只是信息展示，没有对应的批量清理动作，没有真正帮用户完成"下载→上传→腾空间"这个动机闭环                      |
| 传输并发/排队  | 一次点"下载全部新数据"可能同时触发几十个并发连接，没有节流会打满局域网带宽和本机磁盘 I/O                                  |
| 系统通知       | 大文件传输时用户切到其他应用，回来才知道传没传完，只有窗口内 toast 不够                                                   |

实现过程中还发现并修正了一个真实 bug：并发上限判断把"正在启动的这一个
传输"也计入了自己的配额检查，导致实际同时只能跑 `MAX-1` 个而不是
`MAX` 个。

### 5.5 当前 PC 实现边界

本节只说明 PC 如何消费协议，不改变第四节的 Pi wire contract。当前默认
composition 使用真实 mDNS/HTTPS、完整指纹身份和持久化 transfer authority；
`demo.rs`/`sim.rs` 及 `DemoTransferState` 只在显式 `demo` feature 中存在，
production 失败不会回退到模拟器。

- `TransferStore` 当前 schema 为 v19。下载和上传共享 tagged job、CAS、retry
  lineage 和 terminal outbox；上传另有 durable activity/dismissal、multipart
  URL style、immutable object namespace 和 version-bound verified receipt ledger。
- 单文件下载只发布所选文件并写 `.ylx-selected` subset marker，不会把会话
  标成完整；只有整会话 seal/rename 写出的 `.ylx-revision` 才表示完整发布。
- `pending-downloads.json` / `pending-uploads.json` 只保留一次性、带 marker 和
  byte backup 的兼容导入；`.part.journal` 仍是当前下载文件恢复证据，历史
  migration DDL 仍需保留。
- PC batch RPC 把 `item` 与 tagged `success`/`failure` 放在同一对象中；创建
  job 的 success 同时携带该 item 的 durable `jobId`。稳定错误是
  `{code,message,retryable,details?}`，未知 code/status、malformed envelope、
  missing/duplicate/unexpected item 和旧 parallel arrays 都 fail closed。上传
  start 使用 `LibraryKey`，之后的 `Transfer.key`、retry/cancel/dismiss 统一使用
  durable `UploadJobId`，cancel/dismiss payload 为 `{jobId}`。
- 对象存储是 PC 本地 saga。`ObjectStorePort` 不提供 version-safe completed
  object delete 或 provider orphan listing；没有 exact durable receipt 的
  `UnknownUpload` 必须保持 `aborting` 并阻塞相关 cleanup，不能宣称已回滚。

应用/RPC、启动恢复和持久化的当前权威说明见 `ARCHITECTURE.md` 与
`ADR-PC-001-persistence.md`；本文早期原型叙述不应被当作当前 runtime 状态。

## 六、有意搁置的问题

以下问题当前按最简单方式处理，后续如有需要再调整：

- 不支持"记住信任的 PC"，每次重连都要求重新触屏确认（§3.7）。
- 不区分只读/删除权限，配对即完整权限（§3.6）。
- 不做录制中数据的实时传输（§3.1）。
- 心跳超时时间（15s）、配对等待时间（60s）为初始建议值，未经实机验证。

## 七、参考

- PC 端 UI 交互原型：<https://claude.ai/code/artifact/beff60a0-dc4d-4728-b051-b6217d2a17de>
- 现有 IPC 实现：`capture/src/ylx_capture/ipc.py`
- 现有录制清单实现：`capture/src/ylx_capture/session_catalog.py`、`session_summary.py`
- 现有设备身份实现：`capture/src/ylx_capture/device_identity.py`
- 现有 snap 权限模型：`snap/snapcraft.yaml`
