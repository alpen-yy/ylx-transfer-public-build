# YLX 录制可靠性与端到端传输并行开发计划

> 文档状态：历史实施合同（不作为当前 PC 代码或持久化 source of truth）
> 制定日期：2026-08-01（Asia/Shanghai）
> 协调分支：`feature/recording-transfer-integration`
> 参考文档：`RP-YLX/capture/docs/RECORDING_STATE_MACHINE_REVIEW.md`，仅作线索来源，本文结论均基于最新代码重新验证
> 实施总顺序：录制停止可靠性 -> Pi `transfer-daemon` -> PC 真实后端/前端 -> 跨仓库集成与发布
>
> 当前 PC runtime 的模块所有权、启动/恢复、状态机、生产/一次性迁移/测试边界和 CI
> 命令以 [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) 与
> [`docs/adr/ADR-PC-001-persistence.md`](adr/ADR-PC-001-persistence.md) 为准；
> 本计划中的 prototype、未开始、任务卡和 Agent/worktree 说明只保留作
> 历史背景，不是当前操作指令，也不得据此判断现有实现状态。

## 1. 文档目的和使用方式

本文制定时不是问题清单或粗粒度路线图，而是多 Agent 并行开发的实施合同，
当时用于固定：

1. 两个仓库的准确基线、范围和非目标。
2. 最新代码重新评审后的事实，明确哪些旧结论被确认、推翻或改变。
3. 所有跨线程、跨进程、跨仓库的生命周期边界和不可破坏的不变量。
4. 推荐模块接口、协议契约和必须先完成的 ADR。
5. 每个 Agent 的启动门禁、文件所有权、禁止修改范围、交付物、测试和合并门禁。
6. 波次、依赖 DAG、接口冻结点、集成顺序、硬件验收和发布标准。

实施期间，任何与本文冲突的新证据都应先形成 ADR 或计划修订，不能由单个
Agent 在自己的任务分支中静默改变跨模块契约。该协调流程已经结束；当前
维护者应以源码、测试、`ARCHITECTURE.md` 和 ADR 为准。

## 2. 制定时仓库基线与反馈状态

以下 SHA、测试数量和环境结论仅表示 2026-08-01 制定计划时观察到的基线，
不是当前 checkout 的验证结果。

### 2.1 制定时基线

| 仓库代号 | 路径                           | 远端                                    | 基线                                       | 分支                                     | 说明                                                                 |
| -------- | ------------------------------ | --------------------------------------- | ------------------------------------------ | ---------------------------------------- | -------------------------------------------------------------------- |
| `RP`     | `/home/alpen/DEV/RP-YLX`       | `git@github.com:mirrorbloom/RP-YLX.git` | `7d32feef5c7aa6de0d34b039b86debd83952da7e` | `feature/recording-transfer-integration` | `origin/main` 与 tag `v0.9.0`，已从旧本地基线 `b196561` fast-forward |
| `PC`     | `/home/alpen/DEV/ylx-transfer` | 未配置 remote                           | `884f8a6706096dad190ee7e14680d1ea40c1f7a4` | `feature/recording-transfer-integration` | 当前本地仓库即唯一可用基线，不能声称已从远端更新                     |

`RP` 从旧评审基线到当前版本新增 5 个提交：`8921272`、`2ea3ac4`、`a4740a6`、`34b256a`、`7d32fee`。这些提交主要涉及 ARM64 产物固定、GUI 启动等待、编码完成 UI 清理和发布产物，没有修改旧评审涉及的 `controller.py`、`recorder.py`、`encoding_queue.py` 核心停止路径。

### 2.2 制定时工作区保护

- 本计划制定期间未修改任何 Python、C、Rust、TypeScript、CSS、Snap、配置或现有文档。
- `RP` 原有未跟踪文件 `capture/docs/RECORDING_STATE_MACHINE_REVIEW.md` 必须原样保留，禁止加入任务提交或清理。
- 并行只读评审期间 `RP` 出现未跟踪 `uv.lock`。所有已返回的评审 Agent 均声明不是其产物；在来源未确认前不得提交、删除或用它改变依赖解析。实施 Wave 0 前由协调者单独解决其来源。
- 本轮唯一允许新增的文件是本计划文档。
- 不在本轮创建 worktree、任务分支、提交、tag、PR 或部署产物；这些动作从计划获批后的 Wave 0 才开始。

### 2.3 制定时反馈基线

| 范围                              | 结果                               | 解释                                                                                  |
| --------------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------- |
| `RP` 录制相关选择性测试           | `70 passed in 1.47s`               | controller、recorder、encoding、IPC、daemon、UI helper 当前绿色，但未覆盖原始挂死形状 |
| `RP` capture 全套测试             | `198 passed in 1.68s`              | 并行只读审查重新运行，仍无进程卡死、资源抢占和硬件故障注入                            |
| `RP` SDK 非硬件测试               | `39 passed, 2 deselected in 0.70s` | 不包含相机 HITL                                                                       |
| `RP` encoding queue               | `17 passed in 0.26s`               | 现有测试反而固化了“失败任务从全局进度消失”的错误语义                                  |
| `RP` session/catalog/storage 相关 | `27 passed in 0.04s`               | 未覆盖 symlink 逃逸和网络安全边界                                                     |
| `PC` 前端                         | format、typecheck、lint 均通过     | 没有 Vitest、DOM 行为测试或 E2E                                                       |
| `PC` Rust                         | `cargo fmt --check` 通过           | 当前本地没有完成 Rust 类型/行为验证；CI 有三平台 clippy/build，但没有 `cargo test`    |

已建立三类确定性红灯/证据，实施时必须先固化为正式测试：

1. fake native `capture.wait()` 永不返回时，finalizer 线程在 200 ms 后仍存活。
2. stop 路径的元数据 `fsync` 被阻塞时，`_stop_signal_sent` 已被置位，但相机从未收到 stop。
3. session 目录和视频文件使用 symlink 时，现有 catalog/summary 可跟随到 recording root 之外。

另有确定性证据表明：编码失败持久化为 `encoding_failed` 后，全局进度会变成 `idle / queue empty`，UI 可能随后显示“录制已保存”。

### 2.4 当前 PC 落地映射（2026-08-04）

以下只用于把历史任务语言映射到当前 `ylx-transfer` 实现；不宣称 RP-YLX、
硬件 HITL 或本文全部跨仓发布门禁已经完成：

- PC runtime 已统一为一个 Cargo workspace、`TransferApplication` facade、
  真实 production composition 和显式 `demo` feature 边界。source gate 防止
  已删除的 sidecar/重复状态/旧 orchestration 和未 gated demo 状态回流。
- `TransferStore` 当前 schema 是 v19。它持有 tagged download/upload jobs、
  immutable specs、per-file ledger、desired run state、retry lineage、terminal
  outbox、durable upload activity/dismissal、multipart URL style、object namespace
  和 verified receipt ledger。
- 当前 batch RPC 使用逐项 tagged outcome；dispatch success 把 `item` 和 durable
  `jobId` 放在同一对象，failure 携带稳定 `{code,message,retryable,details?}`。
  frontend 对每个去重请求项要求恰好一个结果，并拒绝 missing/duplicate/
  unexpected、未知 code/status、malformed envelope 和旧 parallel arrays。上传
  start 输入是 `LibraryKey`，后续 activity/retry/cancel/dismiss 使用 durable
  `UploadJobId`，cancel/dismiss payload 是 `{jobId}`。
- revision 由 Rust 分配，frontend 不生成 synthetic fallback。`read_snapshot`
  原子返回 outer revision 和 devices/library/transfers/storage 各自的
  `{revision,value}`，这四类 read 只读 published cache。`list_sessions` 是唯一
  device-scoped effectful refresh：网络在锁外，发布 exact value 后 response/
  event 复用同一 revision。同设备的 list/delete/cleanup/background refresh
  由独立 async gate 串行，不同设备可并行。`add_manual_device` 返回 revisioned
  `Device`，并与完整 `devices:update` 共用 revision。mutation response 与对应
  event 也复用同一 revision，event 投递失败不回滚 durable write；启动 replay
  只比较 snapshot 实际覆盖的 resource revision。cache 在投递前解锁，因此
  并发 publisher 不承诺 event FIFO；frontend 按 server revision 丢弃迟到
  事件，并以 revisioned command response 作为收敛路径。production 未 manage
  application 时返回 `application_unavailable`；固定 revision fallback 仅供
  `cfg(test)` mock app。
- selected-file publication 使用 `.ylx-selected`，只合并请求的已验证文件；
  `.ylx-revision` 仍是整会话完整性的唯一 seal。`.part.journal` 继续作为当前
  文件级恢复证据。
- 设备身份已统一为完整 TLS 指纹：canonical `ylx-<64 lowercase hex>`、TLS
  pin `sha256:<64 lowercase hex>`、display-only `YLX-<first 8 uppercase hex>`。
  新记录 canonical-write；legacy short alias 仅在完整身份中 1 个匹配时
  dual-read，0 个 unknown、多个 ambiguous，且旧 paths/keys/jobs 不盲改。
- `pending-downloads.json` / `pending-uploads.json` 只保留 marker-backed one-shot
  importer、byte-for-byte backup、损坏诊断和 legacy tests；历史 migration DDL
  也必须保留。它们不是 runtime source of truth。
- `ObjectStorePort` 没有 version-safe completed-object delete 或 provider orphan
  listing。没有 exact durable receipt 的 `UnknownUpload` 保持 `aborting` 并
  阻塞 cleanup；provider create-response/DB-persist 窗口是已记录的 saga 限制。
- 2026-08-04 对 C72 commit `0f98097` 的 PC 本地审计观察到：frontend
  281/281、typecheck、lint、build、format 全部通过；core 305 个 unit tests
  及全部 enabled integration suites 通过；adapters 110 个测试通过，8 个
  real-service/manual 测试按标注明确 ignored。Tauri application 的 source
  check/strict clippy 仅通过临时 `pkg-config` shim；本机缺 GTK/WebKitGTK/DBus
  开发库，无法链接运行 desktop tests。Windows、真实 MinIO 和 hosted CI
  未在本地执行，不能计为通过。

当前实现细节和验证边界分别以 `ARCHITECTURE.md` 与
`adr/ADR-PC-001-persistence.md` 为准。下文 Wave、task card、建议接口和勾选项
均按历史原文保留，不代表这些动作仍待按原顺序执行。

## 3. 目标、范围与非目标

### 3.1 目标

最终系统必须实现以下端到端行为：

1. 用户发出 stop 后，停止意图立即、幂等、可观察地送达采集所有者，不被日志、manifest、存储扫描或 UI 轮询阻塞。
2. native、mux、encoder、ffprobe 等所有子进程或硬件 owner 从创建到确认 reap 始终有唯一所有者；未确认释放前不得清空引用或开始新录制。
3. 采集优先于编码、发布哈希、目录扫描、删除清理和大文件下载；新录制开始前必须撤销并回收冲突后台工作。
4. 录制数据只有在采集完整、编码完整、权限就绪、文件哈希完成，且具备可信来源的 publication manifest 原子持久化后才能被网络发布。
5. Pi 提供独立、最小权限的 `transfer-daemon`，支持可信配对、短期连接凭证、mDNS 候选发现、完成会话浏览、Range 下载和安全删除。
6. PC 从模拟器改为真实多设备客户端，支持可靠配对/心跳、可恢复下载、本地完整性验证、持久任务、S3 兼容上传、OS credential store 和结构化错误。
7. Pi、PC、协议 golden fixtures、故障注入和真实硬件形成可重复的跨仓库验收链路。

### 3.2 明确非目标

- 不做录制中的实时视频/IMU 网络流。
- 默认不增加 PC 远程 start/stop 录制能力。PC 只读取权威 `capture_activity`，用于资源仲裁、状态解释和暂停传输。
- 不允许 `transfer-daemon` 通过现有全功能 capture IPC 间接获得 start、stop 或切换存储权限。
- 不在第一版记住长期受信 PC；连接断开、daemon epoch 改变或应用重启后重新物理配对。
- 不在第一版实现多 Range 响应、文件内随机并行写或自定义传输协议；先做单 Range 续传和跨文件/跨任务有限并发。
- 不以降低 durability、跳过 `fsync`、忽略 hash 或清空未 reap owner 的方式换取“看起来停止更快”。
- 不把 S3、真实网络或真实文件系统硬编码进领域核心；它们必须是 production adapter，并有 deterministic test adapter。

### 3.3 Definition of Done

只有同时满足以下条件，整个项目才算完成：

- 录制停止的确定性测试、进程故障注入、Pi 4/Pi 5 HITL 和 90 分钟录制门禁全部通过。
- 任一 stop 绝不触发新的 start；旧 generation 的回调、进程退出和 IPC 响应不影响新 generation。
- native、mux、encoding 和 transfer 重 I/O 在 capture start 前均已 quiesced；硬件录制 `frame_sequence_gaps == 0`。
- 只有 durable `published` 会话可被列出；任何中间状态、symlink、越界路径、损坏文件或不匹配 hash 都不可下载。
- Pi/PC 对同一 OpenAPI/JSON Schema/golden fixtures 通过契约测试。
- 下载支持断网、token 失效、capture 抢占和 app 重启后的 Range 恢复；最终本地文件逐个 hash 验证并原子发布。
- S3 上传通过 MinIO 集成测试，远端验证 receipt 可追溯；secret 不进入 JSON、日志、事件或 WebView。
- 前端不把不可信 LAN 字符串解释成 HTML；CSP 启用；所有任务状态互斥且错误来源准确。
- Windows、macOS、Linux CI 运行 frontend test/build、workspace cargo test/clippy/fmt；Pi ARM64 snap 构建和离线安装验证通过。
- 两个 integration 分支 tip 绿色、无未知生成文件、无 debug bypass、无 simulation 默认 composition，并有可执行回滚说明。

## 4. 最新代码重新评审

### 4.1 旧评审逐条判定

| 旧结论                                 | 最新判定                                     | 当前事实                                                                                                      | 计划处理                                                                                     |
| -------------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `STOPPING` 中 `toggle()` 静默返回      | `confirmed / changed`                        | 代码形状存在；重复 stop 不应启动第二个 worker，更不能转成 start                                               | 定义 `already_stopping` 幂等结果、不可变状态事件和 HITL 反馈，不重复副作用                   |
| 物理按键完全没有防抖                   | `refuted`                                    | `button.py` 已将配置的 `debounce_ms` 传给 gpiod，默认 250 ms；Core 26 当前生产原型甚至没有可用 GPIO gadget    | 不立“新增 GPIO 防抖”任务；只测跨过窗口的语义双击和按键 re-arm                                |
| `STOPPING` 计时仍增长                  | `confirmed`                                  | controller 在整个 STOPPING 按起始 monotonic 计算 elapsed，UI 又将 STOPPING 归类为 recording                   | stop accepted 时冻结录制指标，展示 finalization stage；native 结束后用权威最终 duration 校正 |
| `_notify()` 可能跳过 STOPPING          | `confirmed shape / needs deterministic test` | 状态修改后重新 snapshot，快速后继状态可让旧通知携带新快照                                                     | 锁内生成带 revision/generation 的不可变 event，锁外发布；测试完整顺序                        |
| `capture.wait()`/encoder wait 无限等待 | `confirmed / old fix rejected`               | native 同进程 C thread 不能被 Python wait timeout 取消；多个 ffmpeg/ffprobe 也无 supervision                  | native 移入受监督进程；统一 TERM/KILL/reap；timeout 只作 deadline，不冒充回收                |
| stop 中同步 fsync 编码积压             | `confirmed effect / cause changed`           | stop 确实同步 fsync 本次所有 source segment；本 session 在 source complete 前根本不编码，不是 encoder backlog | 分阶段进度和进程隔离；保留 durability，不虚构“等编码完成”                                    |
| timeout 后保留 recorder 导致不可恢复   | `confirmed symptom / old remedy rejected`    | 保留 recorder 是资源 ownership fence；直接清空会让旧相机 owner 与新录制并存                                   | 先完成 terminate/kill/reap 或进入明确 stuck-resource 终态，绝不先清 owner                    |
| IPC poll/命令共锁导致 stop 延迟        | `confirmed`                                  | poll 与 stop 共 `_request_lock`，最长先等 5 秒；server dispatcher 锁还覆盖无 timeout 的 `lsblk` 扫描          | 控制优先通道、request deadline/id、存储扫描移出锁并设限、拒绝迟到执行                        |

### 4.2 新增高风险发现

| ID       | 严重度 | 新发现                                                                                                   | 直接后果                                                       | 必须修复的边界                                                                                 |
| -------- | ------ | -------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `NEW-01` | P0     | `recorder.stop()` 先标记 signal sent，再同步写 events/manifest，最后才 `capture.request_stop()`          | metadata fsync 卡住时相机永远没收到 stop，重复 stop 又不会补发 | hardware stop delivery 必须先于非关键持久化；拆分 claimed/delivered/observed                   |
| `NEW-02` | P0     | native FIFO writer 使用阻塞 `fwrite`，主线程无界 join；recorder 等 native 后才结束 mux                   | ffmpeg 活着但不读 FIFO 时形成循环等待                          | cancel-aware nonblocking writer + supervisor 破环 + 全部 owner reap                            |
| `NEW-03` | P0     | native C 扩展运行在 capture-daemon 的 Python thread 中                                                   | wait timeout 无法回收 USB、C thread 或 FD                      | 受监督 worker process；in-process 方案仅可作为经过证明的备选                                   |
| `NEW-04` | P0     | 编码与下一次采集没有互斥，违背 Pi 4 已实测的不并发要求                                                   | 下一录制可出现 UVC sequence gap                                | capture-priority media admission，encoder 完全 reap 后才打开相机                               |
| `NEW-05` | P0     | transfer 未来也会与下一次采集争 CPU/盘/网                                                                | “只传 complete”仍不能保证与采集解耦                            | 可撤销 background lease；capture intent 先关 admission                                         |
| `NEW-06` | P1     | `_finish_mux()` 第二次 wait 仍超时后无条件丢 `_mux_process`                                              | `active=False` 但子进程仍活，controller 可错误释放 owner       | 只有 poll/wait 确认 reap 后才清引用                                                            |
| `NEW-07` | P1     | encoding failure 从全局 progress 删除                                                                    | UI 显示 queue empty/已保存，失败重启后不可操作                 | durable failed registry、retry/ack、错误持续可见                                               |
| `NEW-08` | P1     | `session.json=complete` 早于权限归一化，且没有文件 hash publication marker                               | 网络可能观察伪完成；Range 后无法端到端校验                     | 最后原子写 publication manifest，repository 只消费 marker                                      |
| `NEW-09` | P0     | catalog 和 summary 会跟随 session/file symlink                                                           | HTTP 复用时可越出 recording root 读取任意文件                  | opaque file ID + dirfd/openat + O_NOFOLLOW + cached allowlist                                  |
| `NEW-10` | P0     | 明文 HTTP bearer、可伪造 mDNS、只展示 `client_name`                                                      | 同 LAN 窃听、重放、MITM 可获得下载/删除权限                    | TLS、transcript-bound SAS、会话凭证和限流 ADR                                                  |
| `NEW-11` | P0     | transfer daemon 若复用现 capture IPC，可调用 start/stop/select target                                    | 网络进程被攻破后扩大为录制控制能力                             | 独立只读 activity/admission socket，配对归 transfer broker                                     |
| `NEW-12` | P0     | PC secret 明文落 `store.json` 并回传 WebView                                                             | 本地凭据泄漏                                                   | OS CredentialVault，配置 DTO 只返回 `secret_configured`                                        |
| `NEW-13` | P0     | LAN 字符串直接拼 `innerHTML`、CSP 为 null、`withGlobalTauri=true`，且未使用的 opener plugin 仍获默认权限 | 恶意设备/路径/错误可注入 WebView 并扩大到 Tauri capability     | textContent/可靠 escaping、CSP、关闭 global API、删除未用 plugin/permission、恶意 fixture 测试 |
| `NEW-14` | P1     | 手动添加设备会从 Rust 和前端各启动一次 pairing                                                           | 真实 Pi 出现两个物理确认请求                                   | endpoint registration 与 pairing command 分离，attempt ID fencing                              |
| `NEW-15` | P1     | PC transfer 多布尔状态、无真实落盘、无 job/partial 持久化                                                | 非法状态、假完成、重启丢任务                                   | tagged enum + durable coordinator + LocalLibrary                                               |
| `NEW-16` | P1     | protocol、Rust、TS DTO 不一致                                                                            | 两端并行后无法互通或进度错误                                   | OpenAPI 3.1、wire DTO 与 UI DTO 分层、golden fixtures                                          |
| `NEW-17` | P1     | IPC 超时请求仍可能在 server 锁释放后迟到执行                                                             | UI 报失败后相机才意外 start                                    | client deadline、request ID、服务端执行前取消/过期检查                                         |
| `NEW-18` | P1     | prepare 后 native thread 启动前失败没有 abort/close                                                      | stale FIFO 和准备态资源残留                                    | 明确 `abort_prepared`，启动 crash matrix                                                       |

## 5. 统一生命周期与术语

### 5.1 不允许再混用的完成点

| Milestone               | 权威 owner             | 定义                                                                        | 明确不代表                   |
| ----------------------- | ---------------------- | --------------------------------------------------------------------------- | ---------------------------- |
| `stop_intent_accepted`  | Controller             | 当前 generation 进入 STOPPING，幂等 stop worker 已登记                      | stop 已送相机                |
| `stop_signal_delivered` | Capture supervisor     | stop fd/control message 已成功交付给 native owner                           | native 已观察或相机已停      |
| `acquisition_quiesced`  | Native outcome         | UVC streaming stop 已返回，不再接收新帧                                     | writer、USB、文件已释放      |
| `native_owner_reaped`   | Process supervisor     | native worker 终止且 waitpid/reap 成功                                      | mux/source durable           |
| `mux_owner_reaped`      | Recorder finalizer     | ffmpeg 已退出并被 wait/reap                                                 | 全部 source segment 已 fsync |
| `capture_durable`       | Recorder finalizer     | source inventory、capture outcome、commit marker 和相关目录已 durable       | H.264 编码完成               |
| `encoding_complete`     | Encoding queue         | 双目编码、cleanup、完整解码和编码 manifest 验证通过                         | 网络已可见                   |
| `published`             | Publication repository | 权限、完整性、全部文件 hash/inventory 完成，publication marker 原子 durable | PC 已下载                    |
| `locally_verified`      | PC LocalLibrary        | 全部文件 size/hash 验证、fsync、目录原子提交完成                            | 已上传对象存储               |
| `object_store_verified` | PC ObjectStore         | 全部对象与最终 manifest 上传并经 HEAD/metadata/version 验证                 | Pi 数据已删除                |
| `remote_deleted`        | Pi repository          | revision 匹配、无 lease、原子移入 trash 且删除 receipt durable              | trash 空间已后台回收         |

Controller 的 `IDLE` 只能在资源 ownership 契约满足后出现。PC 的“可下载”只对应 `published`，不能由 `capture_durable`、recorder callback 或 `encoding_complete` 推断。

### 5.2 顶层录制状态与收尾阶段

保留对外稳定顶层状态：

```text
IDLE -> STARTING -> RECORDING -> STOPPING -> IDLE
  \         \          \           \
   +---------+----------+-------------> ERROR
```

`STOPPING` 内增加正交 `finalization_stage`，而不是继续膨胀顶层枚举：

```text
delivering_stop
waiting_for_camera
draining_mux
reaping_native
syncing_capture
committing_capture
queueing_encoding
resource_stuck
```

事件必须携带 `controller_revision`、`recording_generation`、`stage_started_at`、`stop_reason` 和结构化 error。UI 接受 stop 后冻结“正在录制”的 elapsed/frame 指标；native outcome 到达后显示最终 duration。

### 5.3 编码状态

```text
pending -> waiting_for_resource -> running(encode|cleanup|decode|publish)
        -> paused_capture_priority -> pending
        -> failed_retryable | failed_terminal
        -> complete -> published
```

失败必须 durable、重启可见且不阻塞其他 session。`retry` 和 `acknowledge` 是显式操作；不能通过从 `_sessions` 删除来制造 queue empty。

### 5.4 PC 设备和任务状态

设备的三个状态域必须正交：

- discovery：`online | stale | offline`
- connection：`disconnected | pairing(attempt_id, phase) | connected(connection_id, epoch) | expired(reason)`
- capture activity：`idle | starting | recording | stopping | error | unknown`

任务使用单一 tagged enum：

```text
queued
waiting_for_device
waiting_for_pairing
paused_capture_active
preparing
transferring
verifying
committing
retry_wait
cancelling
succeeded
failed(code, retryable)
cancelled
```

网络失败、磁盘满、hash mismatch、对象存储拒绝和设备心跳失败是不同 error scope，不能统一改成 `DeviceState::Error`。

## 6. 不变量与时间约束

### 6.1 正确性不变量

1. 一次 stop intent 永远不能产生 start intent。
2. 同一 generation 的重复 stop 只返回幂等状态，不创建重复 worker、不重复回调、不改 stop reason。
3. stop hardware delivery 不得等待任何非关键 metadata write、catalog scan 或网络请求。
4. 未确认 native/mux/encoder/transfer 重 I/O owner quiesced/reaped 时，不得清 owner，也不得打开新相机。
5. 旧 generation/epoch/revision 的 callback、heartbeat、pairing decision 和 transfer event 不得影响新 generation。
6. capture admission 关闭后不再发出 background lease；capture lease 优先于 encoding、publish、delete cleanup、download 和 deep scan。
7. durability 不得为了响应时间被跳过；无法中断的 kernel D-state 必须进入明确的 `resource_stuck`，不能伪报成功。
8. `complete + integrity_ok` 只是 publication 的必要条件；没有 durable 且通过来源真实性验证的 publication manifest，目录永不进入网络 catalog。hash 与 manifest 同盘时只证明内部一致，不能独立证明未被整体篡改。
9. published 文件清单不可变；任何文件变化必须形成新 revision 或隔离该 session。
10. URL、mDNS、manifest 和 WebView 传入的所有标识均不可信；真实路径只能从可信 publication inventory 映射。
11. downloading read lease 与 delete exclusive lease 互斥；删除和下载不能发生 TOCTOU。
12. PC 只在本地全部 hash 验证并原子提交后标记 downloaded；S3 只在远端验证后标记 backed up。
13. token、poll secret、S3 secret 不落日志、不进 URL、不发前端、不进入普通 JSON。
14. simulation adapter 不得成为 release/default composition。

### 6.2 初始 SLO 与验证方式

| 项目                                          | 初始目标                             | 验证方式                                | 说明                                          |
| --------------------------------------------- | ------------------------------------ | --------------------------------------- | --------------------------------------------- |
| stop accepted -> delivery attempt             | 100 ms 内                            | fake storage hang + monotonic timestamp | 这是控制面目标，不包含相机真实停止            |
| STOPPING event 可见                           | 同一命令响应前生成                   | revision event test                     | 允许远端 poll 合并快照，但审计 event 不得丢   |
| capture intent -> background admission closed | 原子状态转换                         | scheduler barrier test                  | 不能先 check 后启动                           |
| 活跃 transfer stream 撤销                     | 1 s 目标                             | Pi 实机 3 路 Range 中启动录制           | 未达标时 capture start 失败安全，不冒险开相机 |
| TERM/KILL/reap                                | 每阶段使用绝对 deadline              | ignoring-TERM child process test        | 具体 grace 在 ADR prototype 后冻结            |
| 配对 ticket                                   | 60 s 过期建议值                      | fake monotonic clock                    | HTTP 请求本身不得挂 60 s                      |
| heartbeat                                     | 5 s interval、15 s idle timeout 初值 | fake clock + jitter                     | 必须另有 absolute TTL                         |

上述值在 Wave 0 经 fake、Pi 4/Pi 5 prototype 校准后写入 config 和 ADR。任何测试都必须使用 monotonic absolute deadline，避免逐层相对 timeout 累加。

## 7. 架构方案比较与决策

### 7.1 四个设计取向

| 方案             | 核心形状                                                    | 优点                           | 缺点                                   | 结论                                               |
| ---------------- | ----------------------------------------------------------- | ------------------------------ | -------------------------------------- | -------------------------------------------------- |
| A 最小深接口     | 每个进程一个 Application，外部仅 `query/execute/subscribe`  | 调用面最小，易测试             | 若内部没有明确 owner，可能演化成大模块 | 保留为最外层 facade                                |
| B 最大扩展性     | event sourcing、通用 plugin bus、所有资源统一 command/event | 扩展 transport/provider 容易   | 当前规模下概念和迁移成本过高           | 不采用 event-sourcing；只保留 versioned event/port |
| C 调用方最简单   | Controller/HTTP/Tauri 各有贴近用例的 facade                 | 常见调用清楚，错误可领域化     | facade 间仍需底层一致性                | 用于 controller、Pi HTTP、Tauri command            |
| D Ports/Adapters | domain core + production/test adapter + per-device actor    | 外部依赖可替换，跨仓库契约清晰 | 初始文件数和 composition 增加          | 作为总体架构，配合 A/C，拒绝过度通用化             |

最终选择是 A + C + D 的混合：外层 facade 小而深，内部按资源 ownership 拆模块，所有真正外部依赖通过 port；不引入通用事件溯源框架。

### 7.2 Native 隔离方案

| 方案                                       | 能否结束 C/USB hang   | 能否证明 owner 释放  | 风险                                          | 决策                                  |
| ------------------------------------------ | --------------------- | -------------------- | --------------------------------------------- | ------------------------------------- |
| 仅给 `wait()` timeout                      | 否                    | 否                   | 最低代码改动但只停止等待                      | 拒绝                                  |
| 修完所有 native 阻塞点并留在 daemon thread | 部分                  | 取决于 libuvc/kernel | 容易漏掉新阻塞点，daemon 仍共享故障域         | 仅作为补强，不作最终隔离              |
| 独立 native worker process + supervisor    | 是，除 kernel D-state | waitpid 后可证明     | 需设计 IPC、startup rollback 和 snap 生命周期 | 推荐，Wave 0 prototype 后冻结         |
| 整个 capture-daemon 自杀由 snap 重启       | 多数情况              | 粗粒度               | UI/queue/control 一并中断，恢复复杂           | 最后 recovery fallback，不作正常 stop |

推荐流程：

```text
Controller
  -> MediaResourceScheduler.begin_capture()
  -> CaptureProcessSupervisor.start(spec, generation)
       -> worker process owns libuvc/native C/USB
  -> RecorderFinalizer owns mux and capture commit

stop:
  accept + immutable STOPPING event
  -> deliver native stop before metadata
  -> grace drain
  -> if FIFO cycle suspected, terminate/reap mux to close reader
  -> TERM/KILL native worker process
  -> waitpid/reap both owners
  -> durability + capture commit
  -> enqueue encoding
```

如果 worker 处于不可杀的 D-state，系统必须保持 resource fence 并报告 `resource_stuck`；不能清指针或声称相机可重开。可以在确认 UVC 已释放后允许切换到不同存储目标，但这个例外必须由单独 ADR 和硬件证据证明。

### 7.3 推荐模块图

```text
RP capture-daemon
  CaptureController
    -> MediaResourceScheduler
    -> CaptureProcessSupervisor
    -> RecorderFinalizer
    -> EncodingQueue -> ChildProcessSupervisor
    -> PublicationService

RP transfer-daemon
  HTTPS / mDNS adapters
    -> TransferApplication
       -> PairingBroker + ConnectionRegistry
       -> CompletedSessionRepository
       -> CaptureActivityPort
       -> MediaAdmissionPort
  GUI admin Unix socket -> PairingBroker

PC Tauri
  commands/events
    -> Application facade
       -> DeviceManager -> one actor per Pi
       -> DurableTransferCoordinator
       -> LocalLibrary
       -> ObjectStore port
       -> CredentialVault port
  adapters: mDNS, Pi HTTPS, filesystem, SQLite, S3, OS keyring
```

## 8. 推荐深模块接口

接口名在 Wave 0 可微调，语义和 owner 不得改变。

### 8.1 `CaptureProcessSupervisor`

```python
handle = supervisor.start(capture_spec, generation)
handle.request_stop(deadline=absolute_deadline)
outcome = handle.reap(deadline=absolute_deadline, escalation=policy)
```

- 隐藏 worker IPC、process group、stop fd、TERM/KILL、waitpid、structured native outcome、stderr capture 和 startup cleanup。
- `reap()` 只有确认进程已回收才返回 `resources_released=True`；timeout/error 仍保留 owner。
- 测试 adapter 能模拟 start hang、stop ignored、TERM ignored、kill、D-state equivalent、malformed outcome 和 late callback。

### 8.2 `RecorderFinalizer`

```python
commit = finalizer.stop(reason, deadline)
snapshot = finalizer.snapshot()
```

- `stop()` 幂等，第一动作是 delivery stop；日志/manifest failure 不得阻止 delivery。
- 隐藏 mux shutdown、FIFO cycle breaking、native/mux ownership、source fsync、commit marker 和 exactly-once callback。
- 返回结构化 `CaptureCommit`，不用含糊的 `success=True` 表达“编码尚未完成”。

### 8.3 `MediaResourceScheduler`

```python
capture_lease = scheduler.begin_capture(generation, deadline)
background_lease = scheduler.acquire_background(kind, owner, deadline)
```

- capture intent 原子关闭新 background admission，并撤销 encoder/transfer lease。
- Pi 内跨进程 production adapter 使用受限 Unix socket和 OS hard fence；测试使用内存 adapter。
- background owner 必须支持 revoke acknowledgement。transfer 在 chunk 边界释放，encoder 由 supervisor 完全 reap。
- 不能只用 `snapshot().idle` 做 check-then-act。

### 8.4 `CompletedSessionRepository`

```python
catalog = repository.catalog(cursor=None, limit=100)
reader = repository.open(session_id, file_id, byte_range, if_match)
receipt = repository.delete(session_id, if_match, idempotency_key)
```

- 隐藏 scan/cache、publication manifest、disk generation、dirfd/openat、hash、read lease、trash、恢复和分页。
- HTTP 层永远不接收或拼接真实 path。
- `catalog()` 读不可变内存 snapshot；录制期间不深扫。

### 8.5 `TransferApplication` 与 `PairingBroker`

```python
result = app.execute(command, request_context)
view = app.query(query, request_context)
```

- PairingBroker 属于 transfer daemon，不属于 CaptureController。
- network adapter、GUI admin socket 和 tests 只依赖 command/query，不直接修改 token 表或队列。
- pairing decision 携带 attempt ID、generation 和 transcript binding；迟到决定 fail closed。

### 8.6 PC `Application`、`DeviceManager` 与 `TransferCoordinator`

```rust
impl Application {
    async fn query(&self, query: Query) -> Result<QueryResult, AppError>;
    async fn execute(&self, intent: Intent) -> Result<IntentResult, AppError>;
    fn subscribe(&self) -> broadcast::Receiver<AppEvent>;
    async fn shutdown(&self, deadline: Duration) -> ShutdownReport;
}

impl TransferCoordinator {
    async fn enqueue(&self, request: TransferRequest) -> Result<JobId, TransferError>;
    async fn control(&self, id: JobId, action: TransferControl) -> Result<(), TransferError>;
    async fn recover(&self) -> Result<RecoveryReport, TransferError>;
}
```

- 每个 device actor 独占 endpoint、临时 token、heartbeat task、取消 token 和 connection epoch。
- coordinator 独占 durable job journal、并发调度和状态转换；adapter 只执行一次 I/O attempt。
- token 绝不进入 TS；Tauri facade 只输出 view model 和结构化 outcome。
- 建议将领域核心放入不依赖 Tauri 的 `ylx-transfer-core` crate，以获得快速 Rust 测试环。

### 8.7 前端 backend seam

```ts
interface TransferBackend {
  start(onSnapshot: (snapshot: AppSnapshot) => void): Promise<() => void>;
  execute(command: AppCommand): Promise<CommandOutcome>;
}
```

`start()` 必须先注册事件，再读取带 revision 的 snapshot，并缓冲启动窗口事件。UI reducer 忽略旧 revision、旧 pairing attempt 和旧 connection epoch，不在前端乐观篡改权威任务数组。

## 9. Publication 与本地提交协议

### 9.1 Pi publication manifest

最后写入的版本化 manifest 至少包含：

```json
{
  "schema_version": 1,
  "session_id": "opaque-stable-id",
  "revision": "sha256-of-canonical-inventory",
  "captured_at": "2026-08-01T04:00:00Z",
  "published_at": "2026-08-01T04:10:00Z",
  "duration_seconds": 121.4,
  "total_bytes": 483921234,
  "video_bytes": 483920112,
  "integrity_ok": true,
  "files": [
    {
      "id": "f-opaque",
      "display_path": "video/left_00000.mp4",
      "role": "video_left",
      "size_bytes": 123,
      "sha256": "...",
      "media_type": "video/mp4"
    }
  ]
}
```

写入顺序：验证 capture commit -> 编码/完整解码 -> 权限归一化 -> 构建固定 allowlist -> 每文件 hash/fstat -> canonical manifest -> 使用受限设备 publication key 签名 -> 临时 manifest/signature fsync -> 原子 rename -> session 目录 fsync -> 更新内存 catalog。任何 crash point 都不得让不完整会话进入 catalog。

可移动介质和会话目录视为不可信。manifest 与文件放在同一介质上的 SHA-256 只能检测传输损坏，不能防止攻击者同时替换文件和 manifest。因此 Wave 0 必须在 ADR 中选择并冻结 publication authenticity：推荐使用独立、持久、权限为 owner-only 的设备 Ed25519 publication signing key，对 canonical manifest 签名；公钥指纹绑定进 TLS/SAS transcript。该 key 与每次 daemon boot 的临时 TLS key 分离，并定义 key version、rotation、legacy session 重新索引/签名和失钥恢复。若最终不采用签名，必须明确把“同介质整体篡改”移出威胁模型并由用户接受，不能把同盘 hash 描述为真实性保证。

### 9.2 PC 下载提交

1. 从已鉴权 catalog 固定 session revision 和文件 inventory。
2. 目标路径仅由后端使用 device/session/file ID 生成；拒绝绝对路径、`..`、UNC、Windows drive、NUL、保留名和大小写冲突。
3. 写 `.part`，journal 保存已确认 offset、预期 size/hash/ETag；不信任 DOM 传入 bytes/path。
4. Range 必须返回预期的 `206` 和精确 `Content-Range`。返回 `200` 时不得追加；ETag 改变时废弃旧 partial。
5. 单文件 size/hash 通过后 fsync 并原子 rename；全部文件通过后 fsync session 目录并原子发布 LocalLibrary entry。
6. crash 时以真实 `.part` 长度和 journal 的较小可信 offset 恢复，多余尾部截断。
7. PC 必须验证 publication signature、公钥与已配对设备 identity 的绑定以及 manifest revision；只校验文件 hash 不足以接受不可信介质上的整体替换。

### 9.3 S3 提交

- ObjectStore port 提供 memory/mock adapter；production adapter 支持 S3-compatible endpoint 和 multipart。
- multipart upload ID、完成 part ETag 和 source hash durable，支持 crash resume 和 cancel abort。
- multipart ETag 不能当文件 MD5。上传 SHA-256 metadata，完成后用 HEAD 校验 bytes、metadata、version ID/ETag。
- 从 LocalLibrary 延迟上传前重新打开并验证 source size/hash；不能仅信历史 `locally_verified` receipt，因为本地文件可能在下载后被替换。
- 只有全部对象及最终 manifest 验证后写 `object_store_verified` receipt。
- OS keyring 不可用时明确失败，不允许静默回退明文文件。
- 旧 `store.json` secret 迁移顺序：成功写 CredentialVault -> 原子清除旧 secret -> 再报告成功；中间失败不得丢凭据或谎报完成。

## 10. 传输协议与安全硬门禁

### 10.1 Wave 0 必须签署的 ADR

| ADR             | 决策主题                                              | 未决时允许做什么         | 未决时禁止做什么              |
| --------------- | ----------------------------------------------------- | ------------------------ | ----------------------------- |
| `ADR-SEC-001`   | LAN threat model                                      | fake、port、测试 fixture | 生产网络 adapter              |
| `ADR-SEC-002`   | TLS/device identity/SAS/publication key binding       | crypto test vectors      | 明文 bearer release           |
| `ADR-SEC-003`   | token、TTL、heartbeat、revocation                     | connection domain tests  | token storage/logging         |
| `ADR-DISC-001`  | mDNS 仅候选、手动地址同等认证                         | discovery fake           | 以 device_id/TXT 建立信任     |
| `ADR-PROTO-001` | OpenAPI 3.1、JSON Schema、版本/DTO                    | golden fixtures          | 两端各写各的 DTO              |
| `ADR-DATA-001`  | publication inventory/ID/hash/signature/authenticity  | repository port          | 直接发布 `session.json`       |
| `ADR-PERM-001`  | recording目录/文件权限、Snap共享访问、无Unix mode介质 | permission tests         | 沿用`0777/0666`作默认安全边界 |
| `ADR-PATH-001`  | dirfd/openat/O_NOFOLLOW                               | malicious path tests     | URL path join/resolve-open    |
| `ADR-RANGE-001` | Range、ETag、If-Match、206/412/416                    | fake server/client       | 未定义续传                    |
| `ADR-LEASE-001` | read/delete lease、trash、idempotency                 | domain tests             | 同步递归 HTTP delete          |
| `ADR-SCHED-001` | capture-priority admission                            | scheduler prototype      | polling-only admission        |
| `ADR-IPC-001`   | capture/transfer/admin socket capability              | in-memory adapter        | transfer 使用 control IPC     |
| `ADR-HTTP-001`  | TLS server、timeout、body/header/concurrency budgets  | server prototype         | 裸目录 `http.server` 上线     |
| `ADR-PC-001`    | SQLite journal 与 core crate                          | persistence prototype    | 扩展单一忽错 JSON store       |
| `ADR-CRED-001`  | OS keyring 与迁移                                     | memory adapter           | secret 回显/明文 fallback     |

推荐安全选择是生产仅 TLS 1.3，使用与配对 transcript 绑定的双端 SAS；`client_name` 只作展示。若产品决定保持“Pi 只点一下、不核对 SAS”，必须由用户明确接受 active MITM 残余风险并记录 ADR，不能由实现 Agent 默认降级。

### 10.2 建议 v1 HTTP 表面

| Method     | Path                                    | 语义                                                                     |
| ---------- | --------------------------------------- | ------------------------------------------------------------------------ |
| `POST`     | `/api/v1/pairing-requests`              | 立即返回 `202` ticket、poll secret、expiry、SAS 数据，不挂住 60 s worker |
| `GET`      | `/api/v1/pairing-requests/{id}`         | 查询 allow/reject/expired；token 仅在获批后返回一次                      |
| `DELETE`   | `/api/v1/pairing-requests/{id}`         | 取消对应 attempt，必须带 poll secret                                     |
| `POST`     | `/api/v1/session/heartbeat`             | 必须认证，返回 daemon instance/idle/absolute expiry                      |
| `DELETE`   | `/api/v1/session`                       | 撤销当前 connection token                                                |
| `GET`      | `/api/v1/device`                        | 认证后返回协议/capabilities/storage/capture activity/admission           |
| `GET`      | `/api/v1/sessions?cursor=&limit=`       | 返回缓存 catalog revision 和分页 published sessions                      |
| `GET`      | `/api/v1/sessions/{id}`                 | 返回不可变 publication inventory                                         |
| `GET/HEAD` | `/api/v1/sessions/{id}/files/{file_id}` | 单 byte Range、strong ETag、read lease，不接收真实 path                  |
| `DELETE`   | `/api/v1/sessions/{id}`                 | token + `If-Match` + `Idempotency-Key`；exclusive lease 后原子移入 trash |

Wire JSON 固定 `snake_case`；Rust HTTP DTO 与 Tauri/TS camelCase view model 明确分层转换。`/api/v1` 表示 major，minor/capabilities 在 device response；未知 optional minor 字段忽略，未知 major 或缺少 required 字段 fail closed。

机器可读合同的 canonical source 放在 API 生产者 `RP` 的 `capture/docs/transfer-api/v1/`；`PC` 保存带来源 commit 和 SHA-256 的 vendored snapshot，保证其独立 CI 不依赖相邻工作树。协议变更先修改 canonical source并通过Pi serializer测试，再由integration owner更新PC snapshot；cross-repo gate比较规范化digest。现有 `PC/docs/LAN_TRANSFER_PROTOCOL.md` 保留为需求与设计历史，不再作为production wire contract。

### 10.3 Range 精确语义

- v1 只接受一个 byte range；支持 full、`0-0`、open-ended、suffix，拒绝 multi-range 和整数溢出。
- 返回 `Accept-Ranges: bytes`、精确 `Content-Length`、`Content-Range`、strong `ETag`，禁用内容压缩和 redirect。
- resume 使用 `If-Match`；revision/ETag 变化返回 `412`，不可满足返回 `416`。
- active capture 时新重 I/O 返回结构化 `503 capture_busy` 和 `Retry-After`；已在传输的流在 chunk 边界关闭，让 PC 从已确认 offset 续传。
- 活跃 read lease 时删除返回 `409 session_in_use`；revision 不匹配返回 `412`。

### 10.4 错误与审计

使用 `application/problem+json`，至少包含：

```json
{
  "error_schema_version": 1,
  "code": "capture_busy",
  "status": 503,
  "request_id": "...",
  "retryable": true,
  "retry_after_ms": 1000,
  "detail": "..."
}
```

稳定 code 至少覆盖 `invalid_request`、`invalid_session`、`pairing_queue_full`、`rate_limited`、`capture_busy`、`session_in_use`、`revision_mismatch`、`range_not_satisfiable`、`storage_unavailable`、`server_overloaded`。客户端不得按中文 `detail` 分支。审计日志只记录 request ID、opaque client/session ID、操作和结果。

## 11. 多 Agent 协作规则

### 11.1 角色与并发上限

- 主协调 Agent：唯一有权推进两个 integration 分支、修改本计划、解决跨任务接口冲突和执行最终合并。
- Integration owner：可与主协调者为同一 Agent；独占 dependency manifests、lockfiles、composition/glue、compatibility manifest。
- Worker Agent：每个任务一个独立 worktree/branch，只修改 task card 的 owned files。
- Adversarial reviewer：每个 freeze/gate 后只读审查，不能顺手修改共享文件；发现问题退回原 owner。
- 每波最多 10 个 worker 同时运行，至少保留 1 个 coordinator slot、1 个 staging/test slot、1 个 adversarial review slot。

### 11.2 Worktree/branch 规范

计划获批后创建，不在本轮创建：

```text
/home/alpen/DEV/worktrees/RP-YLX/<task-id>
/home/alpen/DEV/worktrees/ylx-transfer/<task-id>

RP task branch: agent/<task-id>
PC task branch: agent/<task-id>
staging branch: staging/wave-<n>
```

每个 Agent 启动包必须包含：

```text
task_id
repo + absolute worktree path
base_sha
owned_files
forbidden_files
frozen input contract/fixture SHA
expected deliverables
exact test commands
merge gate
```

Worker 禁止：

- 在 integration 根工作树直接开发。
- 修改另一个 Agent 的 owned files、现有参考评审文档、计划文档或未知未跟踪文件。
- 自行更新 `Cargo.lock`、`package-lock.json`、依赖 manifest、Snap 依赖和 protocol canonical fixture，除非 task card 明确授权。
- 使用 reset、clean、checkout 覆盖、强推、批量格式化无关文件或合并 integration 分支。
- 在生产 adapter 中临时关闭 TLS、校验、CSP、fsync、hash 或权限检查来让测试通过。

### 11.3 文件所有权原则

高冲突文件在同一波次只允许一个 owner：

| `RP` 文件                                          | Owner 原则                      |
| -------------------------------------------------- | ------------------------------- |
| `capture/src/ylx_capture/controller.py`            | CAP-07 独占                     |
| `capture/src/ylx_capture/recorder.py`              | CAP-04 独占                     |
| `capture/src/ylx_capture/encoding_queue.py`        | CAP-06 独占                     |
| `capture/src/ylx_capture/ipc.py`                   | CAP-08/PI-05 串行，不能并行     |
| `capture/src/ylx_capture/ui.py`                    | CAP-10/PI-07 串行，不能并行     |
| `capture/src/ylx_capture/daemon.py`                | 当前波次 integration owner 独占 |
| `capture/src/ylx_capture/recording_permissions.py` | CAP-09 独占                     |
| `capture/pyproject.toml`、`capture/snap/**`        | PI-08 + integration owner 独占  |
| `native/src/capture.c`                             | CAP-03 独占                     |

| `PC` 文件                                               | Owner 原则                               |
| ------------------------------------------------------- | ---------------------------------------- |
| `src-tauri/Cargo.toml`、`Cargo.lock`                    | PC-00/integration owner 独占             |
| `src-tauri/src/lib.rs`、`commands.rs`                   | PC-08 独占                               |
| `src-tauri/src/models.rs`                               | PC-00 迁移后由 PC-08 独占 Tauri view DTO |
| `src/types.ts`、`src/api.ts`                            | PC-09 独占                               |
| `src/store.ts`                                          | PC-10 独占                               |
| `src/main.ts`、`src/ui/**`、`src/styles/**`             | PC-11 独占                               |
| `package.json`、`package-lock.json`、Vite/Vitest config | PC-09/integration owner 独占             |

Agent 需要新依赖时提交 dependency request：crate/package、固定版本范围、feature、许可、Snap/offline 影响和替代方案。integration owner 批量更新 manifests/locks，并把绿色 dependency commit 作为并行任务的新 base。

### 11.4 提交与 staging

每个 Worker 可在任务分支保留 TDD red -> green 证据，但 red commit 不单独进入 integration。推荐两种接收方式：

1. Worker 最终整理为多个可独立通过门禁的绿色小提交。
2. 协调者在 `staging/wave-N` 使用 `cherry-pick --no-commit` 接收完整 red+green 范围，形成一个绿色逻辑提交。

staging 顺序：

```text
record base SHA
-> cherry-pick task
-> owned-module tests
-> repository fast gates
-> full repository gates
-> adversarial review
-> cross-repo gates（适用时）
-> fast-forward integration branch
```

integration 分支在 staging 未全部绿色前保持不动。回滚一律使用 `git revert` 或上一版已验证产物，不使用 reset/clean。

### 11.5 Agent 完成报告格式

每个任务必须返回：

```text
Task ID / branch / base SHA / head SHA
Owned files actually changed
Commits and intent
Tests: exact commands + pass/fail counts
Faults deliberately injected
Contract/fixture version consumed
Known residual risks
Unexpected worktree changes
Recommended cherry-pick order
```

缺少任一项的任务不进入 staging。

## 12. 接口冻结点与依赖 DAG

### 12.1 Freeze points

| Freeze              | 内容                                                            | Owner | 解冻条件                               |
| ------------------- | --------------------------------------------------------------- | ----- | -------------------------------------- |
| `F0 Lifecycle`      | lifecycle milestones、generation/revision、owner/reap、deadline | W0-01 | 新硬件证据证明契约不可实现，并形成 ADR |
| `F1 Process`        | supervisor start/stop/reap/outcome、escalation policy           | W0-03 | native prototype 或 D-state 证据       |
| `F2 Scheduler`      | capture/background lease、revoke/ack、cross-process hard fence  | W0-04 | Pi 资源测试失败并经协调批准            |
| `F3 Publication`    | published manifest schema、ID/hash/revision                     | W0-05 | version bump，不能静默修改 v1          |
| `F4 HTTP/Security`  | OpenAPI、SAS、token、Range/error/delete/busy                    | W0-05 | protocol major/minor 规则处理          |
| `F5 Pi ports`       | TransferApplication、repository、pairing、admission             | PI-00 | Pi core contract tests绿色后冻结       |
| `F6 PC core`        | domain enums、Application/DeviceManager/Coordinator ports       | PC-00 | migration/versioned event 变更         |
| `F7 Tauri snapshot` | AppSnapshot/AppEvent/AppCommand/Outcome                         | PC-00 | frontend/runtime contract version bump |

每个 freeze 都要产生机器可读 fixture 或接口测试，不能只存在于会议结论。

### 12.2 总体 DAG

```text
W0-01 lifecycle --------+-------------------------> CAP-04 recorder
                        +-------------------------> CAP-07 controller
W0-02 red loops --------+-------------------------> all CAP tasks
W0-03 process ADR ------+--> CAP-02 native supervisor
                        +--> CAP-05 child supervisor --> CAP-06 encoding
W0-04 scheduler ADR ----+--> CAP-07 controller ------+--> PI-05 admission bridge
                        +--> CAP-06 encoding ---------+
W0-05 security/protocol +--> CAP-09 publication -----> PI-01 repository
                        +--> PI-02 pairing --------+
                        +--> PI-03 security -------+---> PI-04 HTTP
                        +--> PC-00 core ---------------> PC-03 Pi HTTP
W0-06 PC persistence ---> PC-01 journal -------------> PC-05 coordinator

CAP-02/03/04/05/06/07/08/09/10
  -> CAP-11 integration + Pi4/Pi5 gate
  -> PI-00/01/02/03/05 parallel
  -> PI-04/06/07 parallel
  -> PI-08 snap
  -> PI-09 integration/HITL
  -> Pi API freeze + fake/real contract green
  -> PC-00
  -> PC-01/02/03/04/06/07/09/10 parallel
  -> PC-05
  -> PC-08/11
  -> PC-12
  -> INT-01..06 cross-repo/release
```

### 12.3 顺序约束

- Wave 1 录制停止可靠性未通过 Pi 4/Pi 5 gate 前，不合并 Pi 生产 `transfer-daemon`。
- Pi OpenAPI/golden fixtures 可在 Wave 0 冻结，但 PC 生产 HTTP adapter 必须等 Pi fake/real contract 通过后进入 Wave 3。
- 前端 UI Agent 必须等待 F7；否则会继续把旧 simulation 布尔状态固化进真实 UI。
- Snap integration 最后接线，不允许业务 Agent并行修改 `snapcraft.yaml`。
- 跨仓库 E2E 只消费两个已绿色的 staging tip，不在 E2E 分支顺手修产品逻辑。

## 13. Wave 0：契约、红灯与架构门禁

Wave 0 只建立能捕获原始症状的 deterministic feedback loop、ADR、schema、ports 和 isolated prototype。除测试 seam/prototype 外，不开始大面积产品实现。

### W0-01 生命周期合同

- 仓库：`RP`
- Start gate：当前基线测试绿色。
- Owned：新增 `capture/docs/RECORDING_LIFECYCLE_CONTRACT.md`、新增纯 contract fixtures；不改产品实现。
- Forbidden：`controller.py`、`recorder.py`、`encoding_queue.py`、native C。
- Deliverable：固定第 5 节 milestones、structured outcome、generation/revision、resource-stuck、absolute deadline 规则。
- Tests：schema/fixture validation；旧/新 event decode compatibility。
- Merge gate：CAP、Pi、PC owners 审阅签字，adversarial reviewer 无 P0 异议。

### W0-02 录制故障红灯套件

- 仓库：`RP`
- Start gate：F0 draft 可用。
- Owned：新增 `capture/tests/fault/test_stop_delivery.py`、`test_native_reap.py`、`test_fifo_backpressure.py`、`test_mux_ownership.py`、`test_encoding_supervision.py`、公共 subprocess watchdog fixture。
- Forbidden：全部产品代码和现有 green 测试。
- Deliverable：stop-before-fsync、native hang、FIFO 不消费、mux second-wait timeout、encoder ignore TERM 的稳定 red tests。
- Tests：每个故障 test 自身由外层 2-5 秒 watchdog 看守；先证明旧代码失败且 pytest 主进程不会挂。
- Merge gate：无真实 sleep/random race；使用 Event/barrier/fake clock；红灯证据存 artifact，正式合入由 staging 与 green 实现一起完成。

### W0-03 Native/child process supervisor ADR 与 prototype

- 仓库：`RP`
- Start gate：F0、W0-02 native/mux red 可用。
- Owned：新增 isolated prototype 目录和 ADR；若 prototype 进入产品目录，文件仅限新 `capture_process.py`/`process_supervisor.py`，不接线。
- Forbidden：现有 recorder/controller/encoding/native C。
- Deliverable：比较 `spawn` worker、subprocess module、process group、pidfd 可选、structured outcome transport、stderr limit、TERM/KILL/reap；验证 Snap/Python 3.14 可行性。
- Tests：fake child 正常、hang、ignore TERM、malformed exit、parent crash；真实 camera 单机 smoke 另列 HITL。
- Merge gate：明确 D-state 残余风险和 fallback；冻结 F1。

### W0-04 Media admission ADR 与 hard-fence prototype

- 仓库：`RP`
- Start gate：F0；已确认 capture 与 encoder 禁并发。
- Owned：新增 ADR、`media_scheduler` prototype 和 tests，不接 controller/transfer。
- Forbidden：controller、encoding_queue、IPC、transfer 业务代码。
- Deliverable：比较 Unix lease server、`flock` shared/exclusive fence、revoke ack、TTL/epoch；推荐“control lease + OS hard fence”的组合。
- Tests：关闭 gate 与 background acquire barrier、旧 generation、lease owner crash、stuck holder、capture timeout fail-safe。
- Merge gate：证明 capture intent 后无新 background lease；冻结 F2。

### W0-05 协议、安全与 publication 合同

- 仓库：以 `RP/capture/docs/transfer-api/v1/` 为 canonical；`PC/docs/protocol/vendor/v1/` 保存带来源 commit/digest 的消费 snapshot；由 integration owner 跨仓库接收。
- Start gate：威胁模型范围确认。
- Owned：OpenAPI 3.1、JSON Schemas、success/error golden fixtures、ADR-SEC/PROTO/DATA/PERM/PATH/RANGE/LEASE/IPC/HTTP。
- Forbidden：Pi/PC 产品实现、现有参考评审文档。
- Deliverable：F3/F4；Python/Rust crypto SAS 与 publication signature 固定向量；persistent signing key/version/rotation 合同；compatibility manifest 格式。
- Tests：schema lint、fixture roundtrip、未知 minor/major、缺失字段、error code；恶意 path、manifest/file 同时篡改、签名 key version fixture。
- Merge gate：明文生产 HTTP、未鉴权 heartbeat、raw path、无 hash 或无真实性决策的 publication 均被 schema/ADR 禁止。

### W0-06 PC core/persistence prototype

- 仓库：`PC`
- Start gate：F4 draft、当前 Cargo 构建基线。
- Owned：ADR-PC-001、isolated SQLite/JSON comparison prototype、core crate build prototype。
- Forbidden：Tauri commands、sim/demo、前端。
- Deliverable：决定 SQLite migration/journal、crash reconcile、keyring seam、core/adapters workspace layout。
- Tests：事务 crash、corrupt DB、disk error、Windows path/rename feasibility；不依赖 Tauri。
- Merge gate：选择 production persistence，给出 migration/rollback；不允许继续吞掉 persist error。

### W0-07 对抗冻结审查

- 仓库：双仓库只读。
- Start gate：W0-01..06 完成。
- Owned：无产品文件；只输出 review report，计划修订由协调者执行。
- Deliverable：尝试推翻 child process、capture-priority、published manifest、TLS/SAS、SQLite/core crate 和任务文件边界。
- Merge gate：所有 P0 objection 已解决或被明确记为阻断；Wave 1 base SHA 和 fixture digest 固定。

### Wave 0 Exit Gate

- F0-F4 均有 ADR + machine-readable fixture。
- 所有红灯稳定复现旧行为，外层 watchdog 保证 CI 不挂。
- supervisor 和 scheduler prototype 证明可实施；否则必须在 Wave 1 前改架构，而不是边写业务边探索。
- 两仓库 staging tip 绿色；integration owner 记录 compatibility manifest 草案。

## 14. Wave 1：录制停止可靠性

Wave 1 结束前不实现 Pi 网络服务。任务可按下面三批并行：

```text
Batch A: CAP-01, CAP-02, CAP-03, CAP-05, CAP-09
Batch B: CAP-04, CAP-06, CAP-07, CAP-08
Batch C: CAP-10, CAP-11 integration/HITL
```

### CAP-01 停止语义与事件合同测试

- Owned：新增 controller lifecycle contract tests，不改 `controller.py`。
- Start gate：F0。
- Deliverable：STOPPING 重入、immutable snapshot、revision/generation、late callback、exactly-once event tests。
- Exact tests：`pytest capture/tests/fault/test_controller_lifecycle.py -q`。
- Merge gate：当前实现稳定 red；与 CAP-07 interface 一致。

### CAP-02 Native worker process supervisor

- Owned：`src/ylx_imu/capture.py`、新增 `capture/src/ylx_capture/capture_process.py`/worker entry、`tests/test_capture_session.py`、新增 supervisor tests。
- Forbidden：native C、recorder、controller、encoding_queue。
- Start gate：F1、W0-03 prototype 通过。
- Deliverable：worker process owns C/USB；structured native outcome；grace/TERM/KILL/reap；abort prepared FIFO；owner 永不早清。
- Tests：normal/timeout/ignore TERM/kill/malformed outcome/start rollback/late exit；所有 hang 在 subprocess watchdog 中。
- Merge gate：fake worker 250 ms grace 后可被 kill/reap；未 reap 时 `resources_released=False` 且 owner 可追踪。

### CAP-03 Native FIFO cancel 和 cleanup

- Owned：`native/src/capture.c`、必要的 `native/include/ylx/capture.h`、native test harness。
- Forbidden：Python recorder/controller/encoding。
- Start gate：F1 与 FIFO red test。
- Deliverable：FIFO writer nonblocking fd + `poll(POLLOUT|cancel)`、absolute drain deadline、EPIPE/cancel/error outcome、所有 cleanup 路径一致。
- Tests：reader open but no consume、pipe capacity overflow、stop/reader close、USB callback race、sanitizer/valgrind 可行时运行。
- Merge gate：writer/main join 有界；无 busy spin、FD/thread leak；CAP-02 能回收最坏路径。

### CAP-04 Recorder stop/finalization

- Owned：`capture/src/ylx_capture/recorder.py`、`capture/tests/test_recorder.py`。
- Forbidden：controller、capture.py/native C、encoding_queue。
- Start gate：CAP-02 interface frozen；stop-before-fsync 与 mux-owner red tests存在。
- Deliverable：先 deliver stop 后 metadata；claimed/delivered/observed 分离；mux owner 保留到 reap；structured CaptureCommit；callback exactly once；finalization stage。
- Tests：metadata fsync hang、request_stop error、native hang、mux ignore TERM、second wait timeout、concurrent stop、startup rollback、generation fencing。
- Merge gate：100 ms stop delivery test；活 mux reference 不丢；成功只代表 capture durable/enqueued，不声称 published。

### CAP-05 通用子进程 supervisor

- Owned：新增 `capture/src/ylx_capture/process_supervisor.py` 及独立 tests。
- Forbidden：encoding_queue、recorder、controller。
- Start gate：F1。
- Deliverable：spawn-to-reap single owner、process group、stdout/stderr cap、cancel、TERM/KILL、phase deadline、structured exit。
- Tests：normal、nonzero、timeout、ignore TERM、child grandchildren、spawn-vs-close barrier、stderr flood。
- Merge gate：任何返回路径没有 unreaped child；无法 reap 时 owner 保留并明确失败。

### CAP-06 Encoding supervision、pause 和 durable failure

- Owned：`capture/src/ylx_capture/encoding_queue.py`、`capture/tests/test_encoding_queue.py`。
- Forbidden：process supervisor、scheduler、controller、UI。
- Start gate：CAP-05、F2 adapter contract。
- Deliverable：encode/cleanup/decode/ffprobe 全部使用 supervisor；pause/quiesce；capture revoke 后 reap；失败持久可见；retry/ack；不阻塞后续 session。
- Tests：四阶段 ignore TERM、spawn-vs-pause race、restart failed visible、retry、queue continues、source retained、close/restart state。
- Merge gate：真实 `_fail_job` snapshot 为 error 而非 idle；`encoder_reaped_at < capture_open_at` 契约可由 CAP-07 验证。

### CAP-07 Media scheduler 与 Controller admission

- Owned：新增 `capture/src/ylx_capture/media_resource_scheduler.py`、`controller.py`、`test_controller.py`、scheduler tests。
- Forbidden：encoding_queue、recorder、IPC、transfer。
- Start gate：F0/F2、CAP-04/CAP-06 ports frozen。
- Deliverable：capture intent 关 gate、撤销并等待 background、back-to-back start 抢占 encoding；STOPPING 重入结构化结果；owner/revision 不变量。
- Tests：barrier race、old generation、scheduler stuck、capture lease timeout、100 次 deterministic start/stop、encoder reap ordering。
- Merge gate：任何后台 owner未 ack/reap 时不调用 camera start；失败进入明确 error，不绕过 gate。

### CAP-08 IPC 控制优先级和不可变事件

- Owned：`capture/src/ylx_capture/ipc.py`、`capture/tests/test_ipc.py`、必要的 `storage.py` timeout tests。
- Forbidden：controller internals、UI、transfer IPC。
- Start gate：CAP-07 snapshot/command contract冻结。
- Deliverable：stop 不等 stalled poll；request ID/deadline；过期命令不迟到执行；存储 scan 移出全局锁且有 timeout；server read deadline/连接上限/shutdown drain。
- Tests：stalled poll vs stop、blocked lsblk vs stop、client timeout late start、server close in-flight handler、slow connection、startup shutdown race。
- Merge gate：stop request 在测试 SLA 内进入 controller；client 已过期的 start 永不执行。

### CAP-09 Publication service 与 recovery

- Owned：新增 `capture/src/ylx_capture/publication.py`、`session_summary.py`、`session_catalog.py`、`recovery.py`、`recording_permissions.py` 和对应 tests。
- Forbidden：encoding_queue、transfer HTTP、controller。
- Start gate：F3。
- Deliverable：versioned inventory/hash/signature/atomic marker、PublicationSigner port、受限 persistent key adapter、最小权限/不支持Unix mode介质策略、权限完成后发布、crash recovery、损坏后隔离、现有 schema migration/read compatibility。
- Tests：每个 publish crash point、byte corruption、manifest/file 同时篡改、signature/key version错误、missing commit、permission failure、Snap双服务访问、FAT/exFAT mode行为、duplicate ID、symlink session/file、external media generation。
- Merge gate：只有 marker durable 且真实性验证通过的 session 能进 published repository input；现有 local UI catalog行为按兼容策略保留。

### CAP-10 录制/编码 UI 状态

- Owned：`capture/src/ylx_capture/ui.py`、`capture/tests/test_ui_helpers.py`。
- Forbidden：controller、IPC、encoding queue。
- Start gate：CAP-07 snapshot、CAP-06 progress冻结。
- Deliverable：STOPPING 不再显示 recording；elapsed/frame freeze；finalization stage；persistent encoding error/retry feedback；重复 stop 可见但无新 start。
- Tests：各 stage label/capability、快速 STOPPING、错误持续可见、old revision ignored、IPC failure busy reset。
- Merge gate：UI 不再显示“仍在录”或失败后“已保存”；无 widget overlap/HITL touch regression。

### CAP-11 Wave 1 integration、故障注入与 HITL

- Owned：integration owner 独占 `daemon.py`、config/example、composition、CI/HITL scripts；不重写业务模块。
- Start gate：CAP-02..10 staging green。
- Deliverable：依赖接线、config deadlines、compat migration、fault runner、Pi 4/Pi 5 evidence bundle。
- Tests：见第 19 节；至少 SIGSTOP ffmpeg、USB disconnect/control hang、ENOSPC/EIO/fsync delay、20 次 start/stop、encoding backlog start。
- Merge gate：Pi 4/Pi 5 `frame_sequence_gaps==0`；worker/mux/encoder/FD 无泄漏；camera reopen probe；旧 session 不误发布。

### Wave 1 Exit Gate

- 原始“stop 没真正停止”由正式测试捕获并变绿。
- native/mux/encoder 均受监督，未 reap owner 不清理。
- capture priority 对 encoding 已成立并有真实 Pi 证据。
- UI/IPC 反映真实阶段，失败可观察可恢复。
- publication v1 已生成但尚未开放网络。
- full RP tests、mypy、native isolated tests、Pi 4/Pi 5 HITL 全绿后才能开始 Wave 2 产品实现。

## 15. Wave 2：Pi `transfer-daemon`

Wave 2 在 Wave 1 release gate 之后开始。Pi core 和 adapters 内部并行，但 PC 生产实现只消费已冻结且由 Pi 通过的 API。

```text
Batch A: PI-00, PI-01, PI-02, PI-03, PI-05
Batch B: PI-04, PI-06, PI-07
Batch C: PI-08, PI-09
```

### PI-00 Transfer core scaffold 与 ports

- Owned：新增 `capture/src/ylx_capture/transfer/{__init__,models,ports,application}.py` 和 core tests。
- Forbidden：HTTP、mDNS、repository internals、pairing internals、GUI、Snap、capture controller。
- Start gate：F3/F4、Wave 1 exit。
- Deliverable：小而深的 command/query facade；wire DTO 与 domain model 分离；typed errors；fake ports。
- Tests：query/execute authorization ordering、unknown command/version、adapter error mapping、immutable snapshots。
- Merge gate：F5 draft；core 不 import HTTP/zeroconf/GTK/Snap。

### PI-01 Completed Session Repository

- Owned：新增 `transfer/{repository,file_access,trash,publication_index}.py` 和 repository tests。
- Forbidden：HTTP handler、pairing、controller、现有 local UI catalog。
- Start gate：CAP-09 publication v1、F3/F4。
- Deliverable：cached immutable catalog、publication signature验证、opaque IDs、dirfd/openat nofollow、read lease、If-Match、trash rename/recovery、disk generation fencing。
- Tests：session/file/intermediate symlink、double encoding path、swap-after-validation、duplicate ID、byte corruption、manifest/file 同时替换、unknown/revoked signing key、mount unplug/replug、active read vs delete、trash crash。
- Merge gate：网络侧没有可由用户 path 到真实 open 的路径；未验证签名不进 catalog；catalog request 不深扫盘。

### PI-02 PairingBroker 与 ConnectionRegistry

- Owned：新增 `transfer/{pairing,connections,admin_broker,rate_limit}.py` 和 fake-clock tests。
- Forbidden：CaptureController、HTTP server、GUI、TLS adapter。
- Start gate：ADR-SEC-003、F4。
- Deliverable：bounded FIFO tickets、attempt secret、allow/reject/cancel/timeout、token TTL/absolute TTL、daemon epoch、authenticated heartbeat、explicit revoke、多 PC budget。
- Tests：late decision generation、token constant-time verify seam、restart clears token、queue fairness/overflow、per-IP limit、name controls、multi-PC。
- Merge gate：token/poll secret 无 persistence/log/exception leak；pairing state完全不进入 capture controller。

### PI-03 TLS identity、SAS 与 security primitives

- Owned：新增 `transfer/{security,tls_identity,sas}.py` 和固定向量 tests。
- Forbidden：HTTP routes、pairing state、mDNS、GUI。
- Start gate：ADR-SEC-001/002 和 crypto fixture冻结。
- Deliverable：daemon-lifetime TLS cert/key policy、persistent publication public key binding、transcript canonicalization、SAS、CSPRNG token factory、redaction helpers。
- Tests：Python/Rust cross-language vector；改 cert/publication key/client nonce/Pi nonce/name/version/request digest 任一字段都改变 SAS；secret repr/log redaction。
- Merge gate：仅使用成熟 crypto/TLS primitive，不自制加密算法；生产 profile 无 HTTP downgrade。

### PI-04 HTTPS/Range adapter

- Owned：新增 `transfer/{http_server,http_handlers,range_response}.py` 和 loopback/fault tests。
- Forbidden：repository/pairing internals、capture IPC、mDNS、GUI、Snap。
- Start gate：PI-00/01/02/03/05 interfaces绿色，F4冻结。
- Deliverable：async pairing ticket、auth middleware、problem+json、pagination、GET/HEAD single Range、ETag/If-Match、request limits、bounded streaming、audit redaction。
- Tests：200/206/400/401/409/412/416/429/503；slowloris、oversize header/body、disconnect、short read、multi-range、concurrency limits、no redirect/compression。
- Merge gate：handler 不持 domain lock 做磁盘 I/O；所有重 I/O 有 admission lease；token/header 不进日志。

### PI-05 Media admission/capture activity bridge

- Owned：新增受限 media admission server/client；在 `ipc.py` 的改动由该 Agent 串行执行；新增 IPC capability tests。
- Forbidden：pairing/token/HTTP/repository、start/stop command handler 语义。
- Start gate：F2、CAP-07 scheduler绿色；CAP-08 已合并。
- Deliverable：独立 socket，只有 `status/acquire/renew/release`；daemon epoch、lease generation、peer/socket ACL、revoke ack、hard fence。
- Tests：transfer acquire vs capture begin barrier、TTL、client crash、server restart、stale epoch、malformed/unauthorized peer、control IPC capability isolation。
- Merge gate：transfer daemon 无法调用 start/stop/select target；capture start 在 transfer lease释放前不开相机。

### PI-06 mDNS 与 daemon lifecycle

- Owned：新增 `transfer/{discovery,daemon}.py` 和 discovery/lifecycle tests。
- Forbidden：HTTP/pairing/repository internals、Snap manifest。
- Start gate：PI-00/03 ports冻结；dependency versions由 integration owner预先加入。
- Deliverable：`_ylx-capture._tcp.local.` advertisement、untrusted TXT、interface update、goodbye、manual address等价认证入口、coordinated shutdown。
- Tests：duplicate announce、TTL/goodbye、IP/port change、multiple interfaces、daemon restart、HTTP startup failure cleanup。
- Merge gate：mDNS 仅暴露候选元数据；不能把 TXT fingerprint/device ID 当受信 identity。

### PI-07 GUI 物理确认与 transfer 状态

- Owned：`capture/src/ylx_capture/frame_ui.py`、`ui.py`、GUI helper tests；必须在 CAP-10 后串行。
- Forbidden：CaptureController、PairingBroker internals、HTTP、Snap。
- Start gate：PI-02 admin socket contract、PI-03 SAS view model。
- Deliverable：非阻塞 pending pairing UI、双端 SAS、allow/reject/timeout、多个 ticket、connected clients、manual disconnect；transfer 故障不影响录制 UI。
- Tests：late ticket、old generation、timeout while dialog open、multiple clients、GTK executor error、XSS/control chars as text。
- Merge gate：一次物理决定只作用于一个 attempt ID；不阻塞 GTK 或 capture stop。

### PI-08 Snap、离线依赖与服务接线

- Owned：`capture/pyproject.toml`、`capture/snap/snapcraft.yaml`、transfer wrapper/config、install hook、snapshot policy、release contract tests、必要 build/install scripts。
- Forbidden：业务模块实现、PC 仓库。
- Start gate：PI-04/06/07绿色；dependency request approved。
- Deliverable：`transfer-daemon` app、最小 plugs、固定 TLS/HTTP/zeroconf 依赖和离线 staging、service order、stop timeout、trash snapshot exclusion。
- Tests：Snap metadata、entrypoint import、strict confinement、socket permissions、mDNS advertise、TLS boot、service restart、offline build。
- Merge gate：transfer app 没有 `camera/raw-usb/gpu/hardware-observe/network-manager/wayland`；只有必要的 `network/network-bind/removable-media`。

### PI-09 Pi fault/security/HITL integration

- Owned：新增 integration/fault/HITL tests、验收报告和 compatibility manifest update；不修产品实现。
- Start gate：PI-00..08 staging绿色。
- Deliverable：真实 TLS/pairing/Range/delete/admission/mDNS、慢客户端、拔盘、daemon restart、多 PC、Pi 4/Pi 5 证据。
- Merge gate：见第 19、20 节；对抗 reviewer 无 P0/P1；Pi API/F5 正式冻结。

### Wave 2 Exit Gate

- 生产 Pi daemon 只通过 TLS 和受限 ports 工作；安全 ADR 全部 resolved。
- Python serializer 对 canonical fixtures 绿色；Range/path/lease/capture-priority 故障测试绿色。
- 录制期间 heartbeat、pairing 和 cached catalog 可用；大文件/删除/deep scan 被暂停或结构化拒绝。
- Pi 4/Pi 5 下载中启动录制仍 `frame_sequence_gaps==0`。
- Snap 离线构建/安装/服务/权限通过；Pi staging API 冻结后才开始 Wave 3 production PC adapter。

## 16. Wave 3：PC 从模拟器迁移到真实实现

推荐建立 Rust workspace：

```text
src-tauri/crates/ylx-transfer-core/
  src/domain/
  src/device/
  src/transfer/
  src/library/
  src/persistence/
  src/ports.rs

src-tauri/crates/ylx-transfer-adapters/
  src/pi_http.rs
  src/discovery_mdns.rs
  src/object_store_s3.rs
  src/credential_keyring.rs

src-tauri/src/
  commands.rs
  composition.rs
  events.rs
  models.rs
  lib.rs
```

```text
Batch A: PC-00
Batch B: PC-01, PC-02, PC-03, PC-04, PC-06, PC-07, PC-09, PC-10
Batch C: PC-05, PC-08, PC-11
Batch D: PC-12
```

### PC-00 Core scaffold、domain 与 dependency freeze

- Owned：两个新 crate 的 manifests/lib/domain/ports、workspace `Cargo.toml/Cargo.lock`、wire fixture consumer、Tauri view DTO migration plan。
- Forbidden：production adapters、commands、frontend。
- Start gate：Pi F4/F5/API green、W0-06 ADR。
- Deliverable：F6/F7；Device/Connection/Transfer/Local/Backup tagged enums；Application/DeviceManager/Coordinator/ports；AppSnapshot/AppEvent/AppCommand/Outcome fixtures；所有依赖一次冻结。
- Tests：wire fixture serde、enum impossible-state compile/API tests、unknown version/errors。
- Merge gate：core 不依赖 Tauri、reqwest、mDNS、S3、keyring；其余 PC Agent从本 commit建 worktree。

### PC-01 Durable SQLite journal

- Owned：`ylx-transfer-core/src/persistence/**`、migrations、persistence tests。
- Forbidden：device/transfer behavior、adapters、Tauri/frontend。
- Start gate：F6。
- Deliverable：schema migrations；library/files/jobs/checkpoints/storage profile/receipts；事务状态转换；crash reconcile；corrupt DB/disk full errors。
- Tests：每个 migration、transaction interruption、running->paused(AppRestart)、duplicate idempotency、partial length reconciliation、busy/corrupt/permission errors。
- Merge gate：不吞 I/O/serialization error；token和secret无列；rollback/backup策略记录。

### PC-02 Per-device actor 与 connection lifecycle

- Owned：`ylx-transfer-core/src/device/**` 和 actor tests。
- Forbidden：production HTTP/mDNS、coordinator、Tauri/frontend。
- Start gate：F6/F4；memory Pi adapter可用。
- Deliverable：多设备 actor、pair/cancel/allow/reject/timeout、heartbeat、connection epoch、re-auth、capture activity/admission view。
- Tests：old epoch callbacks、two Pi out-of-order、token never persisted/exposed、heartbeat jitter/401/restart、manual/discovery endpoint merge intent。
- Merge gate：旧 pairing resolved 不影响新 attempt；一台设备故障不阻塞其他设备 actor。

### PC-03 Pi HTTPS 与 mDNS adapters

- Owned：`ylx-transfer-adapters/src/{pi_http,discovery_mdns}.rs` 和 adapter tests。
- Forbidden：core domain、coordinator、Tauri/frontend、S3/keyring。
- Start gate：Pi API/F4正式冻结。
- Deliverable：TLS pin/SAS inputs、typed problem mapping、auth heartbeat/catalog/Range/delete、mDNS TTL/update、manual endpoint probe、no redirects。
- Tests：golden fake server、cert change、malicious TXT、timeout/short body、401/412/416/503、Range header验证、同 ID 不同 identity不合并。
- Merge gate：token仅留 actor/adapter secret type；HTTP wire snake_case与 core domain显式转换。

### PC-04 LocalLibrary 与 download engine

- Owned：`ylx-transfer-core/src/library/**`、`transfer/download.rs` 和 tests。
- Forbidden：coordinator scheduling、production HTTP、Tauri/frontend。
- Start gate：F6/F4。
- Deliverable：安全目标路径、`.part`、Range checkpoint contract、size/hash、fsync/atomic publish、manifest revision、local receipt、cancel cleanup policy。
- Tests：200 fallback、206、416、bad Content-Range、ETag change、short stream、disk full、permission、symlink、Windows reserved/case collision、crash at every commit point。
- Merge gate：只有全文件 verified 才创建 LocalLibrary entry；DOM path/bytes 永不作为 authority。

### PC-05 Durable TransferCoordinator

- Owned：`ylx-transfer-core/src/transfer/{model,coordinator,recovery,queue}.rs` 和 coordinator tests。
- Forbidden：persistence internals、production adapters、Tauri/frontend。
- Start gate：PC-01/02/04 ports冻结。
- Deliverable：durable enqueue/dedupe、有限并发、pause/resume/retry/cancel、capture busy、offline/pairing wait、startup recovery、progress checkpoint throttle。
- Tests：all tagged states、cancel race、restart each state、capture pause stream closed before ack、multi-device fairness、duplicate batch、partial failure。
- Merge gate：每个 job单一终态；取消必须等 worker/file handles关闭；progress不每 150 ms fsync。

### PC-06 S3 ObjectStore adapter

- Owned：`ylx-transfer-adapters/src/object_store_s3.rs`、mock contract tests、MinIO integration tests。
- Forbidden：coordinator/domain、credential implementation、Tauri/frontend。
- Start gate：ObjectStore port frozen、dependency approved。
- Deliverable：S3 compatible endpoint、path style、multipart、resume/abort、source hash metadata、HEAD verification receipt。
- Tests：normal/idempotent、5xx/429/auth fail、disconnect/restart、multipart resume/abort/orphan cleanup、metadata mismatch。
- Merge gate：upload done 不等于 verified；MinIO 绿色；secret不进入 debug/error。

### PC-07 CredentialVault adapter 与 legacy migration

- Owned：`ylx-transfer-adapters/src/credential_keyring.rs`、credential tests、migration helper。
- Forbidden：UI、S3 logic、core jobs、旧 state JSON直接编辑。
- Start gate：ADR-CRED-001、port冻结。
- Deliverable：OS keyring + memory adapter、secret types/redaction、set/delete/rotate、legacy `store.json`安全迁移。
- Tests：keyring unavailable/locked、migration success/failure/crash、secret scan across JSON/log/events/errors。
- Merge gate：不允许明文 fallback；getter只返回 `secret_configured`。

### PC-08 Tauri composition、commands 与 events

- Owned：`src-tauri/src/{lib,commands,composition,events,state,models}.rs`；最终处理 `sim.rs/demo.rs`。
- Forbidden：core/adapters internals、frontend。
- Start gate：PC-02/03/05/06/07绿色。
- Deliverable：thin command facade、typed error、startup recover、shutdown deadline、revisioned snapshot/events、batch per-item outcome；default composition真实化；移除未使用的 opener plugin 注册并向integration owner提交依赖删除请求。
- Tests：command contract、startup event race、partial batch、shutdown cancel、secret redaction；simulation只保留显式 demo/test feature或删除。
- Merge gate：默认构建不依赖 seed/sim；command不持全局 Mutex跨 await；frontend无法读取 token/secret/path authority。

### PC-09 TS backend adapter 与 runtime contract

- Owned：`src/api.ts`、`src/types.ts`、新增 `src/backend/**`、Vitest config、package manifests/lock；与 integration owner协作。
- Forbidden：store/reducer、UI/main、Rust internals。
- Start gate：PC-00 产出的 F7/command fixture。
- Deliverable：TransferBackend seam、Tauri/in-memory adapters、runtime validation、listen-before-snapshot、event buffer/revision ordering、structured batch outcome；确认无调用后删除前端 opener 依赖。
- Tests：snapshot/event race、unsubscribe、old revision、malformed payload、invoke error、two device attempts、secret fields rejected。
- Merge gate：F7冻结；前端不直接散布 `invoke/listen`。

### PC-10 前端 state/reducer/selectors

- Owned：`src/store.ts`、新增 `src/model/{state,reducer,selectors}.ts` 和 tests。
- Forbidden：api/backend、UI/main、Rust。
- Start gate：F7。
- Deliverable：正交 device states、session query states、tagged jobs、boot degraded/fatal、local/backup integrity、revision/epoch fencing。
- Tests：乱序、old event、parallel pairing、heartbeat expiry、capture pause/resume、restart reconciliation、partial batch、local library offline可用。
- Merge gate：没有非法布尔组合；单设备失败不清空其他设备/本地库。

### PC-11 前端 UX、WebView 安全和无障碍

- Owned：`src/main.ts`、`src/ui/**`、`src/styles/**`、`index.html`、`tauri.conf.json`、`src-tauri/capabilities/default.json`、UI tests。
- Forbidden：backend/store contracts、Rust core。
- Start gate：PC-09/10冻结。
- Deliverable：设备/配对/会话/任务/本地库/S3真实状态；cancel/retry；逐项 batch结果；text-safe DOM；CSP；关闭不需要的 `withGlobalTauri`，移除未使用的 opener capability并收缩Tauri窗口权限；dialog focus/live region。
- Tests：恶意 device/session/path/error不生成 DOM节点；手动 endpoint不重复 pairing；offline禁用；拒绝/超时/cancel；capture paused；disk/hash/S3错误区分；secret不回显。
- Merge gate：所有 LAN 文本使用 `textContent`/可信 escaping；CSP不为 null；不再暴露未使用的 global/plugin capability；UI没有“心跳正常”与 offline并存。

### PC-12 PC integration、三平台和 CI

- Owned：integration tests、`.github/workflows/ci.yml`、compatibility manifest；不修业务实现。
- Start gate：PC-00..11 staging绿色。
- Deliverable：fake Pi、real Pi smoke、MinIO、restart、frontend browser tests、workspace cargo tests、Win/macOS/Linux path/keyring/Tauri build。
- Merge gate：第 19 节全部 PC gates；adversarial review无 P0/P1；PC default无 simulation。

### Wave 3 Exit Gate

- 真实 mDNS/手动地址、TLS/SAS、配对、heartbeat、多 Pi 均通过。
- 下载真实落盘、可重启续传、hash/fsync/atomic local publish；删除真实调用 Pi且遵守 lease/revision。
- S3/MinIO verified receipt和OS credential安全通过。
- Tauri/TS状态与Rust权威事件一致，恶意LAN输入不能注入WebView。
- Windows/macOS/Linux test/build绿色；PC staging commit与Pi protocol digest记录。

## 17. Wave 4：跨仓库集成、压力与发布

### INT-01 Contract compatibility

- Owned：双仓 compatibility manifest、cross-repo runner、fixture digest；无产品代码。
- Inputs：Pi/PC staging SHA。
- Tests：Python serializer -> OpenAPI/schema -> Rust serde -> TS runtime validator；success/error/unknown minor/unknown major。
- Gate：两个仓库记录相同 protocol version和fixture SHA256。

### INT-02 端到端 happy path 与恢复

- 场景：Pi published -> TLS/SAS pairing -> heartbeat -> 多文件Range -> local verified -> app restart -> MinIO verified -> delete lease -> remote deleted。
- 变体：多Pi、多PC、manual address、partial batch、token expiry/re-pair后续传。
- Gate：所有receipt可追溯，任何阶段不越级标记下一阶段完成。

### INT-03 故障与安全对抗

- 注入：slowloris、header/body overflow、malicious mDNS、MITM cert change、path/symlink、range corruption、disk full、EIO、S3 5xx、process kill、daemon epoch change。
- Gate：无secret/path泄漏、无越界、无误删、资源有界、错误结构化、可恢复任务不丢。

### INT-04 Capture priority HITL

- Pi 4/Pi 5：3路Range下载、encoding backlog、hash/trash cleanup中启动录制。
- Gate：新重I/O立即停止接纳，活动流1秒目标内释放，encoder/process已reap后才open UVC；`frame_sequence_gaps==0`。

### INT-05 Desktop release candidate

- Windows/macOS/Linux：bundle、安装、首次启动、keyring、路径、通知、升级/降级、卸载保留策略。
- Gate：签名/产物hash/SBOM/第三方许可；非bundle `--no-bundle`不再作为唯一证明。

### INT-06 Compatibility manifest、回滚与 staged rollout

记录：

```text
protocol_version
RP-YLX commit SHA + snap artifact SHA256
ylx-transfer commit SHA + desktop artifact SHA256
contract fixture SHA256
MinIO/E2E report ID
Pi4/Pi5 HITL report ID
known migrations and rollback floor
```

先一台 Pi 4 + 一台 Pi 5 canary，禁止开发阶段默认部署文档中的全 fleet。观察完整录制/传输/重启周期后再分批扩展。失败用前一版已验证 Snap/PC 包和 `git revert` 回滚。

### Wave 4 Exit Gate

- Definition of Done 全部满足。
- 无 blocker/critical/high 未处理；medium必须有owner、影响和后续版本。
- 两个integration tip绿色，compatibility manifest完整。
- release notes准确区分 stop accepted、capture durable、published、local verified和object verified。

## 18. 计划并行时间线

这里的“并行”只发生在接口和文件边界已经冻结的同一批次内，不跨越硬门禁抢跑：

| 阶段    | 可并行 lanes                                                  | 必须等待              |
| ------- | ------------------------------------------------------------- | --------------------- |
| Wave 0  | lifecycle/red tests/process/scheduler/security/PC persistence | W0 adversarial freeze |
| Wave 1A | native supervisor、native FIFO、child supervisor、publication | F0-F3                 |
| Wave 1B | recorder、encoding、controller、IPC                           | 各自上游接口          |
| Wave 1C | UI、integration、Pi4/Pi5 HITL                                 | 所有 ownership fixes  |
| Wave 2A | Pi repository、pairing、security、admission、core             | Wave 1 exit + F3/F4   |
| Wave 2B | HTTPS、mDNS、GUI                                              | Pi core ports         |
| Wave 2C | Snap、Pi integration/HITL                                     | Pi业务绿色            |
| Wave 3A | PC core scaffold                                              | Pi API正式冻结        |
| Wave 3B | persistence/device/HTTP/download/S3/credential/TS state       | F6/F7和各port         |
| Wave 3C | coordinator/Tauri/UI                                          | 下层绿色              |
| Wave 4  | contract/E2E/security/HITL/desktop packaging                  | 两仓staging tip       |

任何硬门禁失败时，只暂停下游 lanes；无文件冲突的诊断、测试 fixture、文档和 adapter fake 可以继续。

## 19. 测试矩阵与准确门禁

### 19.1 录制停止与 ownership

| 场景                                  | 预期                                                |
| ------------------------------------- | --------------------------------------------------- |
| stop 前 metadata write/fsync 永久阻塞 | 100 ms 内尝试 hardware stop；metadata error后续可见 |
| native 从不返回                       | grace -> TERM -> KILL -> waitpid；reap 前owner保留  |
| native 忽略 stop/TERM                 | KILL后结构化 outcome；不能冒充clean stop            |
| FIFO reader存在但不消费               | writer可cancel，stop不永久join                      |
| mux kill后第二次wait timeout          | 活process引用仍在，callback不触发                   |
| prepare后、native start前失败         | FIFO/FD/process全部abort，无stale resource          |
| concurrent stop/toggle                | exactly-once worker/callback，状态不进入STARTING    |
| 快速STOPPING->IDLE                    | revisioned event保留STOPPING审计边界                |
| stalled IPC poll                      | stop不被同锁阻塞                                    |
| storage scan/lsblk hang               | stop继续；过期start/select不迟到执行                |
| daemon shutdown有in-flight handler    | handler取消/排空，不在closed controller上执行       |

### 19.2 编码、publication 与恢复

| 场景                                  | 预期                                                   |
| ------------------------------------- | ------------------------------------------------------ |
| encode/cleanup/decode/ffprobe各自hang | 有界TERM/KILL/reap，source保留，状态pending/failed准确 |
| pause检查与Popen并发                  | pause ack前不产生漏网process                           |
| encoding failure                      | global/UI持续可见，restart仍可见，其他session继续      |
| retry/acknowledge                     | 只有显式操作改变failed状态                             |
| capture intent while encoding         | encoder完全reap后才camera open                         |
| crash在output rename前后              | 不发布半文件，source可恢复                             |
| crash在permission/hash/marker任一点   | 无marker就不进catalog                                  |
| 已published文件改1 byte               | hash mismatch隔离，不继续发布                          |
| 文件和同盘manifest/hash同时被替换     | publication signature失败，不进入catalog               |
| publication key version未知/已撤销    | fail closed并给出可运维错误，不重新签名掩盖            |
| capture commit缺失/损坏               | encoding manifest不能单独提升complete/published        |
| 外置盘拔出再插不同盘                  | 旧disk generation/session/lease全部失效                |

### 19.3 Pi pairing、HTTP、path、lease

| 场景                                                | 预期                                                     |
| --------------------------------------------------- | -------------------------------------------------------- |
| 同device ID不同cert/mDNS endpoint                   | 不静默合并，重新TLS/SAS                                  |
| SAS任一transcript字段变化                           | Python/Rust短码不同，旧allow无效                         |
| allow/reject/cancel/timeout/late decision           | 只影响对应attempt/generation                             |
| 未鉴权/过期/撤销/重启前token                        | 全部401，日志无secret                                    |
| pairing慢请求/queue overflow/rate limit             | 有界202/poll或结构化拒绝，不占无限worker                 |
| `..`、encoded traversal、NUL、反斜杠、symlink、swap | 全部拒绝，无root外open                                   |
| full/0-0/open/suffix/malformed/multi/overflow Range | 严格200/206/400/416契约                                  |
| stale ETag/If-Match                                 | 412，不混合版本                                          |
| download vs delete                                  | read lease时409；释放后同revision才delete                |
| delete rename后crash                                | catalog立即不可见，trash恢复清理，不重新发布             |
| capture intent during 3 streams                     | 新流拒绝，旧流chunk边界释放；heartbeat/catalog cache可用 |
| 1000 slow connections/huge headers                  | resource budget有效，capture延迟不显著增长               |

### 19.4 PC device、download、persistence

| 场景                                     | 预期                                                     |
| ---------------------------------------- | -------------------------------------------------------- |
| 两台Pi并行pairing且响应反序              | attempt/epoch隔离，状态不串设备                          |
| heartbeat 401/daemon restart             | connection expired，job waiting_for_pairing，partial保留 |
| 手动地址与mDNS指向同受信identity         | 合并endpoint，不重复pairing                              |
| 服务器忽略Range返回200                   | 不追加；安全从零或protocol error                         |
| wrong Content-Range/short body/416       | 不提交错误offset，按契约恢复/失败                        |
| ETag/revision改变                        | 旧partial废弃并明确告知source changed                    |
| disk full/permission/path collision      | local_disk scope error，device不变error                  |
| app在每个job state崩溃                   | 重启为安全paused/waiting，无重复job/library              |
| cancel during write/verify               | 先停worker/关handle/checkpoint，再cancelled              |
| local全部hash成功                        | fsync+atomic publish后才downloaded                       |
| publication signature错误/identity不匹配 | 拒绝local publish，不信任同盘hash                        |
| locally verified后源文件被替换           | S3上传前重验失败，不产生object receipt                   |
| S3 multipart restart/cancel              | resume或abort，无假verified/orphan失控                   |
| keyring unavailable/migration crash      | 明确失败，无明文fallback/secret leak                     |
| batch部分失败                            | 每项accepted/rejected，UI不宣称全成功                    |

### 19.5 前端与 WebView

- startup：loading、ready、degraded、fatal、retry。
- device discovery 与 connection 分开；offline时不得显示“心跳正常”。
- pairing：approved/rejected/timed_out/cancelled/failed，多Pi并行，old event ignored。
- session list：loading/empty/error/ready，按 `captured_at` 本地格式化排序。
- tray：queued、capture-paused、waiting-pairing、transferring、verifying、committing、failed、cancelled、succeeded。
- malicious device ID、IP、session ID、file path、target label、error detail 仅显示字面文本，不产生元素/事件属性。
- storage secret永不回显；测试连接覆盖endpoint/bucket/credential/prefix。
- dialog focus trap、Esc、focus restore、toast/tray live region。

### 19.6 S3/MinIO、mDNS 和跨仓 E2E

- MinIO：normal、idempotent、HEAD verification、5xx、429、auth failure、network loss、multipart resume/abort、metadata mismatch。
- mDNS：duplicate、TTL expiry、goodbye、IP/port update、interface switch、多Pi、恶意TXT；普通PR用scripted adapter，真实multicast放self-hosted/nightly。
- E2E主链：`published -> pair -> heartbeat -> Range -> local verified -> restart -> object verified -> delete lease -> remote deleted`。
- capture链：传输中capture intent -> stream关闭/checkpoint -> recording zero-gap -> capture durable -> 重新取lease -> Range resume。

### 19.7 `RP` 本地/CI命令

基线和每次 staging 必跑：

```bash
shnote --what "运行 SDK 非硬件测试" --why "验证 SDK 与 native wrapper 回归" run \
  env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:capture/src \
  python -m pytest -q -p no:cacheprovider -m "not hardware"

shnote --what "运行 capture 全量测试" --why "验证 Pi 应用完整回归" run \
  env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:capture/src \
  python -m pytest -q -p no:cacheprovider capture/tests

shnote --what "运行录制故障隔离测试" --why "验证停止和子进程故障不会挂住测试进程" run \
  env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:capture/src \
  python -m pytest -q -p no:cacheprovider capture/tests/fault

shnote --what "检查 Python 类型" --why "验证 SDK 与 capture 接口一致" run \
  python -m mypy capture/src/ylx_capture src/ylx_imu
```

说明：当前本机未安装 `mypy`；Wave 0 使用项目隔离环境安装 test extra，不污染系统环境。CI需增加 Python 3.14 lane，因为生产 Core 26 Snap 使用 Python 3.14，而现CI最高为3.12。

Native/ARM/Snap 新增门禁：

```bash
shnote --what "构建 ARM64 Snap" --why "验证生产 Core 26 打包合同" run \
  capture/scripts/core26_build.sh

shnote --what "验证 Snap 发布合同" --why "检查服务入口权限和固定依赖" run \
  env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:capture/src \
  python -m pytest -q capture/tests/test_release_contract.py
```

任何可能hang的fault test都必须由外层process watchdog看守。CI job增加 `timeout-minutes`、并发取消和失败时日志/process tree/manifest artifact上传。

### 19.8 `PC` 本地/CI命令

前端：

```bash
shnote --what "安装锁定的前端依赖" --why "复现 package lock 环境" npm ci
shnote --what "运行前端行为测试" --why "验证 reducer adapter UI 与恶意输入" npm run test -- --run
shnote --what "检查前端格式" --why "保持生成和手写文件格式一致" npm run format:check
shnote --what "检查前端类型" --why "验证 TypeScript 合同" npm run typecheck
shnote --what "检查前端规则" --why "发现不安全或错误调用" npm run lint
shnote --what "构建前端" --why "验证生产 WebView 资源" npm run build
```

Rust workspace：

```bash
shnote --what "检查 Rust 格式" --why "统一 workspace 格式" run \
  cargo fmt --manifest-path src-tauri/Cargo.toml --all -- --check

shnote --what "运行 Rust workspace 测试" --why "验证领域 core 与 adapters" run \
  cargo test --manifest-path src-tauri/Cargo.toml --workspace --all-targets

shnote --what "运行 Rust 严格静态检查" --why "拒绝 warnings 和错误异步边界" run \
  cargo clippy --manifest-path src-tauri/Cargo.toml --workspace --all-targets -- -D warnings

shnote --what "构建 Tauri 应用" --why "验证真实 production composition" npm run tauri build -- --no-bundle
```

集成测试由仓库脚本统一启动随机端口的 fake Pi、MinIO 和 fault proxy，禁止测试依赖开发机固定端口。三平台 CI 必须新增 `cargo test`；frontend CI新增Vitest。正式remote配置前，这些 workflow只能作为本地规范，不能声称已有远端branch protection。

### 19.9 测试实现纪律

- 时间使用 fake monotonic clock；并发使用 Event/barrier；禁止随机失败和长 real sleep。
- C、USB、ffmpeg、fsync hang必须在子进程内注入，pytest/test runner外层持hard timeout。
- network、S3、credential是真外部依赖，必须有memory/mock adapter；MinIO/loopback只在integration lane。
- 每个production adapter共享port contract suite，避免fake与production语义漂移。
- fault test失败必须输出generation、revision、PID/process group、lease、request ID、job ID和最后状态。
- 性能/HITL阈值不得在共享runner上用绝对吞吐做PR gate；zero-gap、资源泄漏和有界释放是硬门禁。

### 19.10 Task-specific test targets

所有 task card 的 `Tests` 字段对应以下精确 target；文件不存在时由该 task 创建，不能改用一个更宽泛但不包含故障形状的旧测试冒充完成。

| Task   | Exact target                                                                                                                                 |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| W0-02  | `capture/tests/fault/test_stop_delivery.py test_native_reap.py test_fifo_backpressure.py test_mux_ownership.py test_encoding_supervision.py` |
| CAP-01 | `capture/tests/fault/test_controller_lifecycle.py`                                                                                           |
| CAP-02 | `tests/test_capture_session.py capture/tests/test_capture_process.py`                                                                        |
| CAP-03 | `tests/test_native_capture_stop.py` 加独立 native harness                                                                                    |
| CAP-04 | `capture/tests/test_recorder.py capture/tests/fault/test_stop_delivery.py capture/tests/fault/test_mux_ownership.py`                         |
| CAP-05 | `capture/tests/test_process_supervisor.py`                                                                                                   |
| CAP-06 | `capture/tests/test_encoding_queue.py capture/tests/fault/test_encoding_supervision.py`                                                      |
| CAP-07 | `capture/tests/test_media_resource_scheduler.py capture/tests/test_controller.py`                                                            |
| CAP-08 | `capture/tests/test_ipc.py capture/tests/test_storage.py`                                                                                    |
| CAP-09 | `capture/tests/test_publication.py test_session_summary.py test_session_catalog.py test_recovery.py test_recording_permissions.py`           |
| CAP-10 | `capture/tests/test_ui_helpers.py`                                                                                                           |
| PI-00  | `capture/tests/transfer/test_application.py test_contract_fixtures.py`                                                                       |
| PI-01  | `capture/tests/transfer/test_repository.py test_file_access.py test_trash_recovery.py`                                                       |
| PI-02  | `capture/tests/transfer/test_pairing.py test_connections.py test_admin_broker.py`                                                            |
| PI-03  | `capture/tests/transfer/test_security.py test_sas_vectors.py`                                                                                |
| PI-04  | `capture/tests/transfer/test_http_server.py test_range_response.py test_http_limits.py`                                                      |
| PI-05  | `capture/tests/test_media_admission_ipc.py`                                                                                                  |
| PI-06  | `capture/tests/transfer/test_discovery.py test_daemon_lifecycle.py`                                                                          |
| PI-07  | `capture/tests/test_ui_helpers.py capture/tests/transfer/test_admin_ui_adapter.py`                                                           |
| PI-08  | `capture/tests/test_release_contract.py` 加 Snap smoke runner                                                                                |

RP task 的统一命令形状：

```bash
shnote --what "运行任务定向测试" --why "验证当前 Agent owned 模块" run \
  env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:capture/src \
  python -m pytest -q -p no:cacheprovider <exact-targets>
```

| Task  | Exact target                                                                                                                                                                                       |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| PC-01 | `cargo test --manifest-path src-tauri/Cargo.toml -p ylx-transfer-core persistence::`                                                                                                               |
| PC-02 | `cargo test --manifest-path src-tauri/Cargo.toml -p ylx-transfer-core device::`                                                                                                                    |
| PC-03 | `cargo test --manifest-path src-tauri/Cargo.toml -p ylx-transfer-adapters --all-targets`                                                                                                           |
| PC-04 | `cargo test --manifest-path src-tauri/Cargo.toml -p ylx-transfer-core library::`，再运行 `cargo test --manifest-path src-tauri/Cargo.toml -p ylx-transfer-core transfer::download::`               |
| PC-05 | `cargo test --manifest-path src-tauri/Cargo.toml -p ylx-transfer-core transfer::coordinator::`，再运行 `cargo test --manifest-path src-tauri/Cargo.toml -p ylx-transfer-core transfer::recovery::` |
| PC-06 | `cargo test --manifest-path src-tauri/Cargo.toml -p ylx-transfer-adapters object_store_s3::` 加标记的 MinIO integration                                                                            |
| PC-07 | `cargo test --manifest-path src-tauri/Cargo.toml -p ylx-transfer-adapters credential_keyring::`                                                                                                    |
| PC-08 | `cargo test --manifest-path src-tauri/Cargo.toml --lib`                                                                                                                                            |
| PC-09 | `npm run test -- --run src/backend`                                                                                                                                                                |
| PC-10 | `npm run test -- --run src/model`                                                                                                                                                                  |
| PC-11 | `npm run test -- --run src/ui src/main`                                                                                                                                                            |
| PC-12 | workspace全测、frontend全测、三平台Tauri build、integration suite                                                                                                                                  |

所有 Rust 命令实际执行时仍需加本项目 `shnote --what/--why run` 包装；表内省略包装只为保持 target 可扫描。

## 20. Hardware HITL 与发布证据

### 20.1 Canary 规则

- 开发阶段显式指定一台Pi 4和一台Pi 5；禁止无参数运行会部署5台Pi 4和2台Pi 5的全量脚本。
- 每次部署记录设备ID、板型、OS/Snap revisions、artifact SHA、config和测试时间。
- 先跑无数据破坏场景；删除测试只用专门fixture session和独立存储。

### 20.2 Wave 1 HITL

1. 正常触屏stop、连续二次stop、跨250 ms的二次点击，绝不创建新session。
2. 每板型20次start/stop；release candidate 100次。比较前后PID、FD、thread、USB owner、FIFO和进程组。
3. `SIGSTOP` mux/encoder 后stop；验证grace/TERM/KILL/reap和source保留。
4. USB disconnect/unbind、control transfer hang、capture worker SIGKILL、daemon SIGKILL。
5. ENOSPC、EIO、慢fsync/不可取消I/O；验证resource_stuck不伪报IDLE。
6. encoding backlog中立即start；`encoder_reaped_at < camera_open_at`。
7. 10秒、10分钟、90分钟录制，验证video/IMU integrity、publication和recovery。
8. stop后独立camera reopen probe，并收集不能reopen时的process/FD证据。

### 20.3 Wave 2/4 HITL

1. 三个并发Range下载中开始录制：新lease关闭、活动流1秒目标释放、zero-gap。
2. 连续20次“下载 -> capture抢占 -> stop -> resume”，无PID/FD/lease/catalog/partial泄漏。
3. 录制期间持续mDNS、heartbeat、cached catalog请求，不触发deep scan或UVC gap。
4. 外置盘下载中拔出并插入另一块盘，旧generation/ETag/session/lease不可复用。
5. transfer-daemon、capture-daemon、GUI分别重启；验证token/epoch、advertisement、resume和录制隔离。
6. 多PC pairing/download/delete race、慢客户端、connection flood、body/header limits。
7. Pi 4/Pi 5 吞吐基准只用于容量规划；capture完整性始终优先。

### 20.4 必收 artifacts

- monotonic event timeline：stop accepted/delivered/observed/quiesced/reaped/durable/published。
- `snap logs`、structured application logs、process tree、FD list、USB owner probe。
- capture/encoding/publication manifests和hash report。
- scheduler lease timeline和HTTP request IDs。
- PC job journal snapshot、partial checkpoint、local/object verification receipts。
- 板型、artifact SHA、protocol/fixture SHA、测试命令和结果。

## 21. CI 与质量门禁升级

### 21.1 `RP` CI

- 保留 Python 3.10/3.11/3.12，增加3.14 capture lane。
- 每job设 `timeout-minutes`，PR同分支新提交取消旧run。
- 增加 isolated fault lane、native sanitizer/ASAN-UBSAN可行lane、ARM64 runner或交叉/原生build lane。
- 增加Snap metadata/release contract；strict confinement和实机Snap放self-hosted/HITL。
- failure上传native stderr、process tree、pytest timeout traceback、session temp artifacts。
- 硬件测试不进入普通GitHub hosted PR；通过明确的 signed report/compatibility manifest成为release gate。

### 21.2 `PC` CI

- frontend增加Vitest/jsdom，随后增加deterministic browser scenario。
- Rust三平台增加workspace `cargo test`，保留fmt/clippy/Tauri build。
- Linux integration增加fake Pi、MinIO、fault proxy、Range/path/crash recovery。
- mDNS真实multicast先nightly/self-hosted；普通PR使用scripted Discovery port。
- 三平台增加path/atomic rename/keyring smoke；release candidate增加真实bundle而非仅`--no-bundle`。
- secret scanning覆盖workspace、test artifacts和logs。

### 21.3 Cross-repo gate

- canonical fixture digest不一致直接失败。
- compatibility manifest中的两个SHA必须与待发布artifact来源一致。
- Pi old minor + PC new minor、Pi new minor + PC old minor分别做兼容测试；未知major fail closed。
- 跨仓E2E失败不能在runner中直接修改任一仓库，必须回到对应task owner。

## 22. 可观察性要求

结构化日志至少携带适用字段：

```text
recording_generation
controller_revision
finalization_stage
native_pid / mux_pid / encoder_pid
process_escalation
lease_id / lease_generation / daemon_epoch
session_id / publication_revision
request_id / connection_id / pairing_attempt_id
device_id / job_id / job_state / attempt
error_code / retryable
```

日志不得包含 bearer token、poll secret、TLS private material、S3 secret、Authorization、服务器绝对文件路径或录制敏感内容。进度事件需要节流和revision；不能用高频fsync换取UI动画。

## 23. 风险、触发器与缓解

| 风险                                 | 触发/证据                     | 影响                         | 缓解                                                       | 回滚/降级                                                 |
| ------------------------------------ | ----------------------------- | ---------------------------- | ---------------------------------------------------------- | --------------------------------------------------------- |
| Kernel D-state不可kill/reap          | fsync/USB driver永久阻塞      | 资源无法证明释放             | 进程隔离、绝对deadline、resource_stuck、硬件证据           | 禁止新capture或切换到经证明独立资源；重启设备作为运维恢复 |
| Worker process引入启动延迟/IPC复杂度 | Pi prototype超预算            | start UX/新故障面            | Wave0 prototype、最小协议、pre-spawn仅在证明必要时         | 保留旧版本artifact；不退回无界thread方案                  |
| Cross-process lease死锁              | owner不ack或socket失败        | capture无法start             | TTL+epoch+OS hard fence+fail-safe timeout                  | 重启background daemon；绝不绕过hard fence                 |
| TLS/SAS UX过重                       | 操作者无法正确核对            | 配对失败/绕过冲动            | 实机UX验证、清晰短码、一次attempt                          | 不允许生产明文fallback；重新评估制造期PKI                 |
| Publication hash成本高               | 长录制/慢盘                   | 会话晚发布                   | 后台低优先级、增量hash、capture可抢占                      | 延迟发布，不跳过hash                                      |
| 外置盘被替换/篡改                    | unplug/replug、0777/0666      | 越界/错误resume              | disk generation、nofollow、hash、最小权限ADR               | 隔离session/要求重新scan，不复用lease                     |
| S3 provider差异                      | multipart/checksum语义不同    | 假备份                       | ObjectStore port、MinIO+目标provider contract、receipt     | 标记upload failed，不允许清理Pi                           |
| SQLite migration损坏                 | upgrade/crash                 | library/job不可用            | transaction、backup、forward-only migration tests          | 打开只读旧库/恢复backup；禁止静默重建                     |
| OS keyring跨平台不可用               | Linux service未运行/权限      | 无法上传                     | capability检测、明确设置指引、memory test adapter          | 禁用S3功能，不明文fallback                                |
| mDNS在VLAN/runner不稳定              | multicast被阻断               | 找不到设备/测试flake         | manual endpoint、scripted PR tests、nightly real multicast | 手动地址走同TLS/SAS，不降级认证                           |
| PC仓库无remote                       | workflow无法运行              | 无branch protection/远端备份 | 配置remote作为单独用户授权任务；本地staging门禁            | compatibility manifest记录本地SHA和artifact               |
| Snap离线依赖膨胀                     | TLS/server/zeroconf新库       | build/release风险            | 固定版本/hash/license、Wave0/2 offline build               | transfer service独立禁用，capture service保持旧稳定版     |
| 状态/协议范围膨胀                    | 顺手加入remote control/stream | 延迟且扩大权限               | 非目标和capability边界                                     | 拒绝scope，另开future RFC                                 |

## 24. 回滚策略

### 24.1 Wave 1

- 每个ownership模块独立绿色提交，可用`git revert`按依赖逆序回退。
- manifest schema采用向后可读；新publication marker不改变旧session原始数据。
- 若supervisor在硬件上失败，回滚整个Wave 1 artifact，不允许只关闭timeout/reap检查。

### 24.2 Wave 2

- transfer-daemon是独立Snap app；可禁用/回滚服务而保持capture-daemon旧可靠版本。
- 不发布到全fleet，先canary。
- 删除功能直到download/read-only路径稳定并通过trash恢复测试后才启用release capability。
- trash删除异常时停止cleaner，保留可恢复目录，不做强制递归清理。

### 24.3 Wave 3

- SQLite migration前备份；新app可在失败时进入degraded只读library，而不是清库。
- simulation只可保留显式dev/test feature，不能作为生产fallback掩盖真实网络故障。
- PC rollback使用上一版签名bundle；新partial/journal必须可被旧版忽略而不删除。
- credential migration只有成功写keyring后才清旧值；失败可重试。

### 24.4 Cross-repo

- 两仓不能原子提交，compatibility manifest是发布单元。
- 仅发布已配对验证的Pi/PC SHA组合。
- protocol major不兼容时客户端fail closed并展示升级要求，不尝试猜测字段。

## 25. 发布标准

发布候选必须同时满足：

1. 两仓full/isolated/integration gates全绿，CI无允许失败的核心job。
2. Pi4/Pi5各100次start/stop、90分钟录制、encoding/download抢占均zero-gap。
3. 真实Snap strict confinement下transfer服务无多余plug。
4. TLS/SAS/token/heartbeat/publication signature/path/lease/delete安全对抗通过，无明文生产模式。
5. Windows/macOS/Linux真实bundle构建、安装、启动、升级和keyring smoke通过。
6. MinIO与至少一个目标S3-compatible provider验证；失败不产生backed-up假状态。
7. CSP开启，恶意LAN字符串测试通过，secret scan无发现。
8. compatibility manifest、SBOM/许可、artifact hashes、HITL和回滚文档齐全。
9. 默认composition无simulation，默认协议不包含PC remote start/stop。
10. canary观察完成后才允许全fleet staged rollout。

## 26. 计划获批后的第一批动作（历史）

严格按以下顺序执行：

1. 确认并处理`RP`未跟踪`uv.lock`来源；未确认前不删除、不提交。
2. 在两个integration分支重新记录base SHA和baseline测试artifact。
3. 创建Wave 0 staging和W0-01..07独立worktree，不创建Wave 1产品任务worktree。
4. integration owner建立ownership ledger和dependency request模板。
5. 并行完成lifecycle/process/scheduler/security/protocol/persistence ADR与red loops。
6. W0-07对抗review，冻结F0-F4和fixture digest。
7. 只在Wave 0 exit gate通过后创建CAP任务worktree并开始产品代码。

## 27. 实施前检查清单（历史）

计划进入实施前，协调者逐项确认：

- [ ] 用户确认本文范围、顺序和“默认不做PC远程录制控制”。
- [ ] 安全ADR选择生产TLS/SAS或明确记录替代方案与残余风险。
- [ ] `uv.lock`来源明确，两个integration根工作区没有未知改动。
- [ ] 每个Agent有唯一owned files且与同批次无重叠。
- [ ] 所有task branch从指定freeze/base SHA创建。
- [ ] red tests有外层watchdog，不会挂CI runner。
- [ ] dependency/lock/glue只有integration owner修改。
- [ ] 每个freeze有machine-readable fixture和digest。
- [ ] staging每次接收后都保持绿色，integration只fast-forward绿色tip。
- [ ] Pi4/Pi5 canary和桌面三平台资源可用。
- [ ] compatibility manifest贯穿Wave 2-4。

本文曾是实施入口。旧 `RECORDING_STATE_MACHINE_REVIEW.md` 与本计划继续作为
历史问题和决策线索；当前任务、优先级、实现状态和验收证据不再由本文正文
推断，而以当前 Issue、源码、测试、`ARCHITECTURE.md` 和已接受 ADR 为准。
