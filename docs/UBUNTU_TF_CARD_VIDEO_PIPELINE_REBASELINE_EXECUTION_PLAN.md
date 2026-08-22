# Ubuntu TF 卡视频链路重新基线执行计划

> 状态：Execution plan；描述从当前代码状态到完整发布验收的剩余工作
>
> 基线日期：2026-08-06（Asia/Shanghai）
>
> 当前分支：`feat/ubuntu-core-card-discovery`
>
> 基线提交：`9e0a181740118f7b3a92be9d6030ac0b2e3e5cb3`
>
> 认证目标：Ubuntu 24.04 LTS x86_64 PC
>
> 范围限制：只修改 `ylx-transfer` PC 端；不修改 Pi 端代码；不使用 worktree

本文是
[`UBUNTU_TF_CARD_VIDEO_PIPELINE_IMPLEMENTATION.md`](UBUNTU_TF_CARD_VIDEO_PIPELINE_IMPLEMENTATION.md)
的重新基线执行计划。原文继续定义目标架构、安全不变量和最终 Definition of Done；本文负责记录
2026-08-06 的真实代码状态、已经发生的合同变化、剩余任务、依赖关系、提交边界、验证顺序、
外部门禁和工期。

如果两份文档在以下已经由真实 Ubuntu Core TF 卡证实的事实上冲突，在原实施规范完成 R0 修订前，
暂以本文为准：

1. Ubuntu Core 录制容器不只位于卷根，还可能位于
   `system-data/var/snap/ylx-capture/common/recordings`。
2. 应用可以通过 UDisks2 请求挂载已经被系统证明为 removable、非系统盘的受支持文件系统；
   mountinfo fallback 没有挂载权限。
3. 完整 publication manifest 可能没有 detached signature/public-key 文件。这种情况不是 signed，
   而是 `UnsignedPublicationV1`，必须经过独立的 unsigned import approval。
4. detached signature/public-key 只存在一个时不得降级为 unsigned，必须 fail closed。

本文创建时只进行了静态读取和差异核对，没有运行 test、check、build、lint、format 或 typecheck。
当前工作区中的未提交代码一律标记为“在途”，不因为文件已经存在就视为完成。

---

## 1. 目标、边界和完成口径

### 1.1 最终目标

在 Ubuntu PC 上完成并证明以下链路：

```text
removable block device
  -> UDisks2 attach（仅在需要且被授权时）
  -> bounded Ubuntu/Ubuntu-Core container scan
  -> signed trust verification / explicit unsigned admission
  -> durable background import into a sealed PC source tree
  -> idempotent media-library projection
  -> approved FFmpeg normalization with durable quality evidence
  -> sealed derived tree and immutable derived receipt
  -> frozen derived upload bundle
  -> durable multipart object-store upload
  -> completion-bound checksum/readback verification
  -> source / derived / remote 三层 truthful projection
```

一旦 source 达到 `LocalVerified`，normalization 和 upload 必须只读取 PC 上的 sealed source/derived
artifact。TF 卡可以拔出，后续作业仍应继续或在重启后恢复。

### 1.2 不在本计划中的工作

- 不修改任何 Pi 仓库、Pi service、录制格式或签名行为。
- 不支持 Windows/macOS removable import。
- 不承诺 Ubuntu 之外的 Linux 发行版或 ARM PC。
- 不支持 raw block-device 读取、文件系统修复、格式化或提权绕过。
- 不在 TF 卡上写 checkpoint、journal、sidecar、派生文件或删除标记。
- 不上传原始 TF source video。
- 不实现 source archival、上传后删除 PC source 或清除 TF 卡。
- 不以临时 feature flag 绕过 profile、quality 或 remote-verification 门禁。

### 1.3 三种完成口径

计划、进度和发布讨论必须明确使用以下一种口径：

| 口径             | 定义                                                                   | 允许的表述           |
| ---------------- | ---------------------------------------------------------------------- | -------------------- |
| Import-ready     | Ubuntu 真卡可发现、批准、后台复制、验证并投影为 local source           | “Ubuntu TF 导入可用” |
| Code-complete    | import、normalize、upload 生产调用链闭合并通过自动化合同/集成/故障测试 | “代码链路完成”       |
| Release-complete | 原实施规范第 21 节全部有真实硬件、corpus、对象存储和发行证据           | “完整链路可发布”     |

只有第三种口径可以宣称
`UBUNTU_TF_CARD_VIDEO_PIPELINE_IMPLEMENTATION.md` 的全部内容已经实现。

### 1.4 永久安全不变量

后续任何排期压力不得改变以下不变量：

1. `removable=Unknown` 与 `removable=No` 都不能成为 TF import candidate。
2. mount path 是位置，不是介质身份；续传必须绑定 generation 和稳定介质证据。
3. signed trust 只来自 PC 上由 SAS-confirmed pairing 写入的 fingerprint receipt。
4. signed verification 失败不能回退为 unsigned。
5. 完全缺少 detached signature pair 可以进入明确的 unsigned schema；半缺 pair 必须拒绝。
6. unsigned import approval 与 unsigned derived upload approval 是两张不同 receipt。
7. TF source 始终只读；所有写入只发生在 PC library filesystem。
8. library root 不得位于当前 TF、其他 removable volume 或与其相同的 filesystem identity。
9. durable row 先于 worker side effect；内存 queue 只是唤醒提示。
10. source、derived、remote 三层状态和 receipt 不得互相冒充。
11. normalization profile 未批准、quality evaluator 不完整或 FFmpeg build 不匹配时保持不可用。
12. remote verified 必须来自与本次 completion/version 绑定的 checksum/readback evidence。
13. shutdown 不得遗留 reader、worker 或 FFmpeg child；超时必须报告 `resource_stuck`。
14. source archival、PC source 自动删除和 TF 删除在 V1 中保持 disabled。

---

## 2. 2026-08-06 真实代码基线

### 2.1 分支和提交状态

当前分支相对 `main`/`origin/main` 已有四个提交：

| Commit    | 内容                                                               | 对计划的影响                        |
| --------- | ------------------------------------------------------------------ | ----------------------------------- |
| `719d3b9` | 发现 Ubuntu Core recordings、UDisks2 attach、per-card access issue | 重开 scan/mount 合同                |
| `ae52f93` | 无 detached signature 的 publication 作为 unsigned admission       | 重开 provenance/schema 合同         |
| `5623f48` | manual dispatch 可只构建 Ubuntu                                    | 缩短迭代反馈，不替代 release matrix |
| `9e0a181` | library entry 记录实际 download root                               | 为 root identity/migration 提供基础 |

`HEAD` 与远端 `alpen-ci/feat/ubuntu-core-card-discovery` 一致，但工作区存在大量尚未提交变更。

### 2.2 工作区状态分类

当前未提交实现约分为以下几组。数量仅说明规模，不代表正确性或完成度。

| 组                | 主要文件                                                                                                  | 状态                                             |
| ----------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| Trusted producer  | `media_store/trust.rs`、MediaStore v7、`media/trust.rs`、pairing write、Ubuntu signed admission           | 在途，生产接线已出现，未验证                     |
| Async import/root | `media/library_root.rs`、`media/ubuntu_workers.rs`、`ubuntu_ingestor.rs`、`composition.rs`                | 在途，命令/worker 分离已出现，未验证             |
| Media library     | AppStore v3、`media_library/app_store_repository.rs`、`ubuntu_projector.rs`                               | 在途，import/derivation projector 已出现，未验证 |
| Normalization     | approved registry、空 manifest、`ubuntu_derivation.rs`、FFmpeg quality analyzer                           | 在途；真实 analyzer 尚未进入 production graph    |
| Derived upload    | TransferStore v20、`derived_upload_store.rs`、S3 adapter 扩展                                             | 只有持久化基础；没有 Ubuntu uploader/worker      |
| PC/LAN 既有链路   | `pi_http.rs`、`pi_download_source.rs`、`application/workflows.rs`、`object_store_s3.rs`、`composition.rs` | 与 TF 工作混合，必须保留并单独归类               |
| 文档              | 原实施规范与本文                                                                                          | 未提交                                           |

禁止使用 `git add -A` 把这些不同责任一次性混进一个提交。高冲突文件必须由集成 owner 按 hunk
或显式路径暂存。

### 2.3 按原 Batch 0-7 的重新评估

状态词定义：

- **已提交**：在当前分支 commit 中存在；本轮没有重新运行验证。
- **在途**：工作区有实现，但尚未形成可追溯提交或通过相应门槛。
- **基础存在**：domain/adapter 可复用，但没有生产调用链。
- **缺失**：没有满足计划所需的 production owner。
- **门禁关闭**：故意 fail closed，不能作为完成。

| 原批次                              | 当前状态       | 完成估计 | 关键证据                                                       | 仍缺内容                                                                                 |
| ----------------------------------- | -------------- | -------: | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Batch 0 合同/fixtures               | 必须重开       |      45% | 真实 Ubuntu Core 卡已验证路径和 9/12 unsigned session          | 更新规范、fixture provenance、attach/signature 矩阵、quality corpus                      |
| Batch 1 trusted signed admission    | 在途           |      80% | v7 registry、pairing digest/write、offline verifier 已出现     | compile/test、显式 revoke surface、trust-write failure UX、真实 signed card matrix       |
| Batch 2 async import/root authority | 在途           |      75% | wake queue、worker lane、shared/exclusive root lease 已出现    | 阶段级 revalidation、queue loss/recovery、拔插、pause/cancel/shutdown 证明               |
| Batch 3 library/outbox              | 在途           |      65% | AppStore v3、CAS repo、import/derivation projector 已出现      | boot/wire/UI exposure、resolver lease、upload outbox、crash matrix                       |
| Batch 4 normalization               | 在途/门禁关闭  |      45% | FFmpeg encode/probe、quality analyzer、profile registry 已出现 | schema-aware input、stereo evaluator、production analyzer wiring、五类 approval evidence |
| Batch 5 derived upload              | 基础存在       |      25% | v20 subject/sidecar、generic derived adapter/S3 engine         | bundle builder、approval receipt、uploader、worker、verification/outbox/projection       |
| Batch 6 pipeline/UI                 | 门禁关闭       |      15% | core policies和部分 DTO 已存在                                 | 移除 production ImportOnly gate、依赖 attach/replay、三层 UI和 capability UX             |
| Batch 7 HITL/CI/release             | 少量真实卡证据 |      10% | 一张 Ubuntu Core 卡的 discovery/unsigned detection 结果        | 全部 fault/HITL/corpus/MinIO/production/legal/package evidence                           |

### 2.4 当前已确认的主要断点

以下是重新计划时必须优先处理的事实，不是未来可选优化：

1. `src-tauri/src/media/ubuntu_pipeline.rs` 仍拒绝所有非 `ImportOnly` policy。
2. `approved_profiles.json` 的 `entries` 故意为空，任何 derivation 都应保持不可用。
3. 新 `FfmpegQualityAnalyzer` 已存在，但 `UbuntuDerivationPort` 仍把旧
   `FfmpegMediaNormalizer` 作为 `SegmentQualityAnalyzer` 注入；旧实现会拒绝制造质量证据。
4. 没有 production `StereoDomainEvaluator` owner，不能生成完整 stereo/CV evidence。
5. `normalization_input_for` 当前主要按 artifact role 推断 input，可能把所有 side-by-side source
   当成 spool、所有 paired source 当成 H.264 publication；必须改为 schema/source-kind-aware mapping。
6. completion projector 目前只消费 import 和 derivation outbox，没有 upload completion lane。
7. AppStore media projection 已有持久化基础，但尚未证明 boot snapshot、wire decoder 和 UI 使用它。
8. TransferStore v20/derived sidecar 已出现，但没有 `ubuntu_uploader.rs` 和 upload worker composition。
9. `accessIssue` 已进入 Rust/TypeScript DTO，尚未证明在可见 UI 上向用户呈现。
10. 当前变更横跨 TF 与 PC/LAN 文件，整理提交时存在误暂存和覆盖用户改动风险。

---

## 3. 修订后的产品合同

R0 完成时必须把本节同步回原实施规范；在此之前，本节是执行依据。

### 3.1 Removable eligibility 与挂载

一个设备只有同时满足以下条件，才允许尝试 attach 或 scan：

1. UDisks2/sysfs 证明它是 block-backed volume。
2. `removable == EvidenceHint::Yes`。
3. 不是系统根、boot、loop、zram、overlay 或内部 fixed disk。
4. filesystem 在 Ubuntu V1 allowlist 中。
5. 当前用户会话通过系统权限模型获得访问权；应用不提权、不修改 policy。

处理顺序：

```text
enumerate block volumes
  -> qualify removable/non-system/filesystem
  -> already mounted ? use mount : request UDisks2 filesystem-mount
  -> re-enumerate authoritative mount state
  -> qualify mount root and access
  -> bounded scan
```

规则：

- attach 是通过系统服务发出的受限请求，不是应用自行执行 `mount`。
- UDisks2 拒绝、需要授权或不可用时，返回 typed diagnostic；不得尝试 sudo 或 shell fallback。
- mountinfo fallback 只观察已经挂载的卷，不拥有 attach 权限。
- scan/recovery 不能因为 attach 失败放宽 removable evidence。
- 用户请求 eject 时，仍必须先释放 application handles，再调用 UDisks2 的非强制能力。

### 3.2 固定录制容器

Ubuntu V1 allowlist 为：

```text
<mount>/recordings/<direct-session-child>/
<mount>/YLX_RECORDINGS/<direct-session-child>/
<mount>/system-data/var/snap/ylx-capture/common/recordings/<direct-session-child>/
```

三个容器必须由一个共享常量驱动 scanner 和 generation fence，不能各自复制字符串。

仍然禁止：

- 从 mount root 递归寻找名为 `recordings` 的目录；
- 扫描 `home`、`DCIM`、任意 snap 或用户选择目录；
- 跟随容器、session 或 artifact symlink；
- 将非 UTF-8 路径 lossy 转换后继续读取。

### 3.3 Publication signature 状态矩阵

| Manifest             | Detached signature | Public key | 结果                                          |
| -------------------- | ------------------ | ---------- | --------------------------------------------- |
| valid                | present            | present    | `SignedPublicationV1`，进入 PC trust 验证     |
| valid                | absent             | absent     | `UnsignedPublicationV1`，要求 import approval |
| valid                | present            | absent     | corrupt，禁止降级                             |
| valid                | absent             | present    | corrupt，禁止降级                             |
| malformed/incomplete | 任意               | 任意       | corrupt/incomplete，禁止导入                  |

即使进入 unsigned path，manifest shape、session identity、artifact path、declared size、digest format、
inventory total、geometry、codec 和 commit boundary 仍必须全部验证。失去的是 producer authenticity，
不是允许忽略内容完整性。

### 3.4 Access issue

权限拒绝不能表现成“卡中没有录像”。投影至少应区分：

- 卡已发现但未挂载；
- attach 被系统拒绝；
- mount 已存在但录制容器不可读；
- 录制容器可读但没有 candidate；
- candidate 存在但 schema/integrity 不可用。

`accessIssue` 是用户可操作诊断，但必须经过长度限制和转义；不能包含原生 D-Bus/IO 错误的无限文本。

---

## 4. 目标生产架构和依赖 DAG

### 4.1 Owner 图

```text
LinuxNativeBackend/UDisks2
  -> UbuntuMediaRuntime
       owns catalog, generation, scan cache, admission, destination guard
  -> UbuntuRecordingIngestor
       owns import executor + wake queue
  -> import WorkerLane
       owns copy execution under LibraryRootLease
  -> MediaStore import receipt/outbox
  -> UbuntuMediaCompletionProjector
       owns outbox replay into shared AppStore
  -> MediaLibraryProjection source layer
  -> UbuntuDerivationPort
       owns approved profile lookup + MediaNormalizerExecutor
  -> derivation WorkerLane
       owns FFmpeg process lifecycle
  -> MediaStore derived receipt/outbox
  -> MediaLibraryProjection derived layer
  -> UbuntuDerivedUploader
       owns frozen bundle + TransferStore v20 + ObjectStorePort
  -> upload WorkerLane
       owns multipart/recovery/verification
  -> TransferStore upload receipt/outbox
  -> MediaLibraryProjection remote layer
  -> strict Rust/TypeScript DTO + UI
```

### 4.2 Critical path

```text
R0 contract freeze
  -> R1 stabilize current working tree
  -> R2 trustworthy async import + source projection
  -> R3 approved derivation + derived projection
  -> R4 durable derived upload + remote projection
  -> R5 full policy/UI/lifecycle activation
  -> R6 release evidence
```

可提前并行但不能绕过依赖：

- R2 trust、import worker 和 AppStore repository 可以分 owner 并行。
- R3 corpus/evaluator、schema-aware planner 和 FFmpeg adapter 可以并行。
- R4 TransferStore repository、object-store contract harness 和 UI DTO 可以在 R3 期间开发。
- R4 最终 bundle 必须读取 `DerivedReceipt`；不能读取 import staging 或未封存 output。
- R5 只能在 R3/R4 的 final DTO、receipt 和 capability contract 冻结后合并。

---

## 5. R0：重新冻结合同与真实 fixtures

### 5.1 目标

让文档、scanner、detector、wire schema 和真实 Ubuntu Core TF 卡使用同一套输入事实，防止后续
normalization/upload 在错误 schema 上继续实现。

### 5.2 工作项

#### R0.1 修订主实施规范

更新 `docs/UBUNTU_TF_CARD_VIDEO_PIPELINE_IMPLEMENTATION.md`：

- 把“只处理已经挂载”改为“已挂载，或经 UDisks2 对已证明 removable volume 请求 attach”。
- 把两个 fixed roots 改为三个 allowlisted containers。
- 增加 `UnsignedPublicationV1`。
- 写入完整/半缺 detached signature 状态矩阵。
- 增加 attach permission、access issue 和重新枚举要求。
- 更新文件级实施映射、HITL 表、最终验收清单和硬门禁。
- 明确这些都是 PC 端适配，不要求 Pi 改动。

#### R0.2 建立 fixture manifest

每个 fixture 必须记录：

- fixture id；
- schema/source kind；
- 来源设备/软件版本的匿名标识；
- 采集日期和脱敏许可；
- 原始 digest；
- 是否允许进入仓库，或只允许保留 digest/生成脚本；
- expected candidate verdict；
- expected geometry、codec、segment/frame facts；
- corrupt/unknown variant 的具体破坏点。

最低 fixture 集：

| 类别                     |            最低数量 | 必须覆盖                                                        |
| ------------------------ | ------------------: | --------------------------------------------------------------- |
| RawCaptureV2             | 2 valid + 3 invalid | 30/60 fps、bad JPEG boundary、frame gap                         |
| LegacyMjpegSessionV5     | 2 valid + 3 invalid | side-by-side、PTS reset、末段/index mismatch                    |
| Appliance/Unpublished V6 | 2 valid + 4 invalid | complete、open tail、missing/duplicate segment、commit conflict |
| SignedPublicationV1      | 1 valid + 4 invalid | paired/offline trust、rotated key、bad signature/hash           |
| UnsignedPublicationV1    | 2 valid + 3 invalid | MJPEG/H.264、both absent、half-present signature pair           |
| Unknown/corrupt          |              至少 5 | unknown version、oversize manifest/index、unsafe path、symlink  |

#### R0.3 冻结 wire contract

- Rust/TypeScript 都显式列出 `unsigned_publication_v1`。
- `accessIssue` 必须是 nullable、bounded text。
- candidate readiness 继续区分 `ready_signed`、`waiting_for_pairing_key` 和
  `ready_unsigned_requires_policy`。
- `sourceKey` 不能由 mount path 生成。
- 超过 JavaScript safe integer 的 bytes/duration 不得被静默舍入为另一个值。

### 5.3 主要文件

- `docs/UBUNTU_TF_CARD_VIDEO_PIPELINE_IMPLEMENTATION.md`
- `docs/research/SD_CARD_AND_VIDEO_CODEC_EVIDENCE.md`
- `src-tauri/crates/ylx-transfer-core/src/ingest/scan.rs`
- `src-tauri/crates/ylx-transfer-core/src/ingest/detector.rs`
- `src-tauri/crates/ylx-transfer-core/src/ingest/source.rs`
- `src-tauri/crates/ylx-transfer-core/tests/`
- `src-tauri/src/media/types.rs`
- `src/runtime/media/types.ts`
- `src/runtime/media/decoder.ts`
- `src/ui/media/render.ts`

### 5.4 完成门槛

- 主实施规范不再包含与三个新事实冲突的 mounted-only、two-root 或 signed-only 表述。
- scanner 和 generation fence 使用同一个 container allowlist。
- fixture manifest 有 provenance、digest 和 expected verdict。
- unknown schema 与 half-present signature pair fail closed。
- UI 能区分 access denied 与 empty card。

### 5.5 建议提交

```text
docs(media): rebaseline Ubuntu Core TF card contract
```

R0 只提交文档、fixture metadata 和必要的合同测试；不混入 worker、normalizer 或上传实现。

---

## 6. R1：稳定和拆分当前在途代码

### 6.1 目标

先把当前约 8,000 行工作区改动变成可审查、可验证、可回滚的责任批次，再继续新增功能。
R1 不以“所有文件能编译”作为唯一门槛，还必须消除跨批次的隐式调用和错误所有权。

### 6.2 工作区保护规则

1. 不回退任何不属于本计划的用户改动。
2. 不使用 worktree。
3. 不使用 `git add -A`、`git add .` 或按目录无差别暂存。
4. 每次提交前用显式路径和必要的 hunk 暂存。
5. `composition.rs`、`lib.rs`、`media.rs`、workspace `Cargo.toml` 和 schema manifest 由一个集成 owner 修改。
6. `pi_http.rs`、`pi_download_source.rs` 与 LAN workflow 的改动先单独确认责任，不塞进 TF commit。
7. `object_store_s3.rs` 的公共行为先区分 generic object-store 修复和 TF derived upload 扩展。
8. 未跟踪文件在首个提交前检查 module export、cfg 和 license/header，一次只加入一个责任集合。

### 6.3 静态断链检查

在运行广泛验证前，先检查：

- 每个新 module 都从正确的 `mod.rs`/`media.rs` 导出；
- Linux-only 类型不泄漏到非 Linux build surface；
- AppStore 只共享一个 `Arc<AppStore>`，projector 不另开绕过 CAS 的连接；
- MediaStore schema v7 和 TransferStore schema v20 保持 contiguous append-only；
- approved profile resource 由 `include_bytes!` 固定进 build；
- `UbuntuMediaRuntime::start` 的新 trust 参数所有 call site 都更新；
- `UbuntuRecordingIngestor` 的 queue 与 worker lane 是同一个实例；
- lifecycle shutdown 持有 retryable join handle；
- production composition 没有同时启动旧 inline drain 和新 worker；
- non-Linux composition 仍使用 unavailable adapter，不初始化 UDisks2/FFmpeg。

### 6.4 R1 子批次

#### R1-A Trusted producer

包含：

- MediaStore v7 migration；
- `TrustedProducerRegistry`；
- confirm/re-pair/rotate/revoke/audit；
- pairing success 写入 SAS evidence digest；
- Ubuntu signed admission；
- readiness/provenance wire projection。

不得包含：worker、AppStore media table、normalizer、derived upload。

#### R1-B Async import/root authority

包含：

- `LibraryRootAuthority`；
- bounded deduplicating wake queue；
- import worker lane；
- command 只持久化 intent + enqueue；
- root switch exclusive conflict；
- lifecycle stop/join。

#### R1-C Media library projector

包含：

- AppStore v3；
- `AppStoreMediaLibraryProjectionRepository`；
- import/derivation outbox projector；
- shared AppStore composition；
- startup replay 和 projector worker lane。

#### R1-D Normalization foundation

包含：

- approved profile registry；
- empty fail-closed release manifest；
- FFmpeg analyzer/parser/report archive primitives；
- Ubuntu derivation worker scaffolding。

这批只能声明“foundation available, capability closed”，不能声明 normalization 已完成。

#### R1-E Derived upload persistence foundation

包含：

- TransferStore v20；
- typed upload subject；
- derived natural-key index；
- sidecar/frozen checkpoint CAS repository。

这批不能开启 pipeline upload capability。

### 6.5 完成门槛

- 每个子批次可以独立解释其 invariant 和 migration impact。
- 没有 TF commit 意外包含 Pi 端文件。
- 当前工作区不再同时悬挂多个无法归属的大型新 module。
- foundation commit 的功能门禁保持关闭，不以 stub 数据制造成功。

### 6.6 建议提交

```text
feat(media): persist PC trusted producer admission
feat(media): run Ubuntu TF imports on owned background workers
feat(media): project durable TF receipts into AppStore
feat(media): add fail-closed normalization qualification foundation
feat(upload): persist typed derived upload jobs
```

如果现有 PC/LAN 改动已形成完整修复，应在这些提交之前或之后独立提交，不与以上 subject 混合。

---

## 7. R2：闭合可信异步导入和 source projection

### 7.1 Trusted signed admission

生产顺序必须固定：

1. 从 authoritative cached candidate 取得 exact signed material。
2. 再确认 volume generation 仍 live。
3. 验证 presented fingerprint 格式。
4. 查询 PC `TrustedProducerRegistry`。
5. 未找到 active receipt 时返回不可重试的 `waiting_for_pairing_key`。
6. registry unavailable 时返回持久化/capability 错误，不伪装成 unpaired。
7. 用 receipt fingerprint 作为 expected identity 调用唯一 `PublicationTrust`。
8. 验证 raw public key fingerprint、detached signature 和 exact payload。
9. 对比 session id、revision、inventory、path、size 和 digest。
10. 只通过 typed signed constructor 创建 `SourceRecording`。

还需补齐：

- trust 持久化失败不能只写 stderr；pairing UI/diagnostic 必须说明 LAN pairing 成功但 offline-card trust
  没有落盘。
- 显式 revoke 应有 PC 端 command、审计和 UI 反馈；删除 device 不能隐式 revoke。
- 历史 pairing 没有 SAS transcript 时不推断迁移，提示重新 pairing。
- 当前真实卡是 unsigned 的事实不能被用来跳过 signed fixture 验收。

### 7.2 Unsigned admission

- `RawCaptureV2`、V5、V6 和 `UnsignedPublicationV1` 首次 import 都要求明确 approval。
- approval receipt 绑定 candidate id、generation、policy version 和批准时间。
- 卡拔出重插后重新 scan 和重新批准。
- import approval 不授权 remote upload。
- half-present signature pair 不能到达此路径。

### 7.3 后台 import

命令行为：

```text
preflight without durable side effect
  -> atomic create import + pipeline rows
  -> enqueue job id
  -> immediately return created/existing projection
```

worker 行为：

- 每次 dequeue 后重新读取 durable snapshot。
- 单 lane 初始并发为 1。
- queue 满或 wake 丢失不能丢 job；startup recovery 必须重新发现 active row。
- 每个 chunk 检查 desired state、shutdown 和 media generation。
- pause/cancel 只写 intent 并唤醒，不等待 library-root command mutex。
- 同 mount path 的另一张卡不得复用 checkpoint。

### 7.4 Root authority 与 destination guard

`LibraryRootLease` 必须固定：

- canonical path；
- Linux `st_dev`；
- filesystem identity；
- root generation。

必须在以下边界重新验证：

1. preflight；
2. durable job create 前；
3. staging/open writer 前；
4. 每次 copy/resume turn；
5. target verify 前；
6. atomic rename/commit 前；
7. recovery drain 前；
8. derived staging 和 upload source resolution 前。

仅在 worker 外层每 turn 调用一次 `assert_current` 还不够；需要确认 core executor 的阶段 callback
不能跨越 root identity 变化继续写入。

### 7.5 Source projection

- import terminal transaction写 source receipt + import receipt + completion outbox。
- projector 从 immutable receipt 重建 `RecordImport`，不信任 event payload。
- AppStore CAS conflict 时 reload/recompute，有界重试。
- committed 或 exact already-applied 后才能 ack outbox。
- crash 在 AppStore commit 后、ack 前时必须幂等重放。
- UI 读取独立 media projection，不构造 legacy signed `LibraryEntry`。
- sealed source resolver 只接受 logical revision + relative artifact ref，不接受 arbitrary absolute path。

### 7.6 R2 验收场景

| 场景                            | 预期                              |
| ------------------------------- | --------------------------------- |
| 已配对 signed 卡，Pi 离线       | signed admission 成功             |
| unknown/rotated/bad signature   | fail closed，无 unsigned fallback |
| unsigned publication            | 明确批准后导入                    |
| 大文件导入中查询/暂停           | command 及时响应，不同步复制      |
| copy 中拔卡                     | `waiting_for_media`，reader 释放  |
| 原 signed 卡重插                | exact claim 后恢复                |
| 原 unsigned 卡重插              | 重新批准                          |
| 同 mount path 换卡              | checkpoint 不续用                 |
| library root 位于任一 removable | job 创建前拒绝，零写入            |
| AppStore commit 后 crash        | restart replay 后 outbox 清空     |
| `LocalVerified`                 | 无 TF reader，可 release/eject    |

### 7.7 完成门槛

- 达到 Import-ready 口径。
- `pending_import_completions` 最终清空。
- source local state 在重启后可见且 provenance truthful。
- import-only 仍不要求 FFmpeg 或 object store 可用。

### 7.8 建议提交

```text
fix(media): close Ubuntu TF import recovery and projection
```

---

## 8. R3：闭合真实 normalization 和 derived projection

### 8.1 首要修正：schema-aware input

不得只根据 artifact role 推断 normalization input。必须同时使用 source schema、source kind、codec、
layout 和 detector 冻结的 timing/frame evidence。

| Source                                  | NormalizationInput                                                | 必需 evidence                                   |
| --------------------------------------- | ----------------------------------------------------------------- | ----------------------------------------------- |
| RawCaptureV2                            | `raw_capture_v2`                                                  | raw frame index/boundary/count/timing           |
| LegacyMjpegSessionV5                    | `legacy_mjpeg_session_v5`                                         | ordered segments + legacy timing evidence       |
| ApplianceSpoolV6/CompleteUnpublishedV6  | `appliance_spool_v6`                                              | capture commit digest + closed ordered segments |
| Signed paired H.264 publication         | `paired_h264_publication_v1`                                      | exact manifest digest + paired order            |
| Unsigned publication H.264              | paired publication-shaped input with unsigned provenance retained | local manifest/inventory digest + paired order  |
| Unsigned publication MJPEG side-by-side | schema/codec appropriate MJPEG input                              | local inventory/timing/geometry evidence        |

若现有 `NormalizationInput` 无法表达最后两类，先扩展 core typed input；禁止把它们伪装成另一个 schema。

### 8.2 Sealed source resolution

- 从 `LibraryImportReceipt` 找 sealed root。
- 每个 artifact 按 receipt relative path、size、digest 解析。
- no-follow 打开并重新核对 regular-file identity。
- normalizer 获取 source revision read lease。
- library root switch、source deletion或证据不一致时停止，不尝试 arbitrary path fallback。

### 8.3 FFmpeg build/capability

启动时记录并比较：

- `ffmpeg`/`ffprobe` version output digest；
- libx265 availability；
- encoder compatibility class；
- required demuxers/decoders/filters；
- libvmaf/SSIM filter availability；
- packaging/build fingerprint。

缺少某项只关闭依赖它的 normalization capability；import-only 继续工作。

### 8.4 Quality analyzer production wiring

必须把真实 `FfmpegQualityAnalyzer` 注入 `MediaNormalizerExecutor`，并提供一个经过评审的
`StereoDomainEvaluator`。旧 `FfmpegMediaNormalizer` 的拒绝型 `SegmentQualityAnalyzer` 只可作为
fail-closed fallback，不能在 profile 已批准时继续占据 production path。

每个 segment/eye 至少产生：

- VMAF mean；
- VMAF frame p01；
- SSIM；
- frame/timestamp/keyframe alignment；
- stereo/CV domain verdict；
- full decode frame count；
- source/candidate digest binding；
- analyzer/build/profile revision；
- canonical report digest。

报告先写入 derived staging，sync 后纳入 derived receipt；不能只存在日志或内存中。

### 8.5 Approved profile gate

`approved_profiles.json` 保持空，直到五类证据均存在：

1. representative quality corpus report；
2. throughput/CPU/memory/temp-disk report；
3. stereo/CV domain report；
4. FFmpeg/libx265 distribution and legal review；
5. playback/decoder compatibility report。

manifest 只保存这些报告的 digest、profile revision、encoder compatibility class 和批准时间。
loader 必须从代码重建 candidate profile并比较 canonical revision，防止参数改变后继承旧批准。

### 8.6 Durable derivation

- natural key 绑定 source revision + profile revision + encoder build identity。
- staging 与 final derived tree 在同一 PC filesystem。
- pair checkpoint、process pid/lease、pause/cancel/recovery 都 durable。
- encode 后执行结构 probe、full decode、quality gate。
- 所有 evidence 通过后才 atomic rename。
- terminal transaction 写 `DerivedReceipt` + completion outbox。
- projector 从 derived receipt 构建 `RecordDerived` 并 ack。

### 8.7 R3 验收场景

- 四类核心输入和两类 unsigned publication 选择正确 typed input。
- malformed/timing-gap/geometry mismatch 在 encode 前或 validation 阶段失败。
- FFmpeg child timeout/cancel/shutdown 最终 terminate/kill/reap。
- crash 在 partial encode、quality report、rename 和 terminal transaction 各边界后可收敛。
- 未批准 profile 返回不可重试 capability unavailable，不创建假的 derived success。
- profile 批准后，真实 corpus 产生 `DerivedVerified`。

### 8.8 完成门槛

- production graph 不再使用 unavailable normalizer stub 或拒绝型 quality analyzer代替真实证据。
- 每个 `DerivedVerified` 都有可重算的 receipt 和 report digest。
- source layer 与 derived layer 在 projection 中独立。
- TF 卡拔出后 derivation 仍可继续。

### 8.9 建议提交

```text
feat(media): wire qualified Ubuntu video normalization
```

---

## 9. R4：闭合 durable derived upload

### 9.1 新 production owner

新增或完成专用 `src-tauri/src/media/ubuntu_uploader.rs`，不要把上传 composition 继续塞入
`ubuntu_pipeline.rs` 或通用 `composition.rs`。

owner 负责：

- 从 `DerivedReceipt` 冻结 bundle；
- unsigned upload admission；
- TransferStore v20 repository；
- sealed derived artifact source；
- `DerivedUploadAdapter` 和 `ObjectStorePort` composition；
- upload wake queue/worker/recovery/shutdown；
- completion receipt/outbox。

### 9.2 Frozen bundle

bundle 必须包含并 canonical 排序：

- source revision；
- derived revision；
- profile revision；
- encoder/analyzer build identity；
  -每个 derived data/evidence object 的 role、relative path、size、SHA-256；
- quality/validation report digests；
- final manifest object；
- storage namespace/profile identity；
- unsigned upload admission receipt（若 source unsigned）。

`UploadBundleRevision` 由 canonical bytes 计算。display name、mount path、temporary job id 和当前
credential value 不进入自然键。

### 9.3 独立 unsigned upload approval

对于 unsigned source：

- import approval 不能复用；
- upload 前再次向用户说明 provenance；
- receipt 绑定 final source revision、derived revision、bundle revision、storage identity、policy version
  和批准时间；
- bundle 改变后旧 approval 失效；
- approval error 不可自动重试或静默接受。

### 9.4 TransferStore v20

核对并完成：

- `subject_kind = derived_bundle`；
- `(upload_bundle_revision, storage_profile_identity)` natural key；
- job/spec/activity/sidecar 原子创建；
- frozen bundle/checkpoint JSON 有严格大小和 schema bound；
- active-attempt serialization fence；
- checkpoint version CAS；
- multipart handle/part/completion/version evidence durable；
- terminal state/activity/completion outbox 同事务。

### 9.5 Upload worker

- command 只创建 durable job并 enqueue。
- worker 重新读取 frozen sidecar，不从当前 UI/settings 重建 bundle。
- credential 由现有 vault按 storage identity解析，不持久化 plaintext。
- 已验证 part 不重复上传。
- ambiguous complete 后先通过 completion-bound能力判定，不能直接重新 complete。
- final manifest 最后上传。
- 每个 object 以服务端 checksum或 streamed readback证明 exact digest。
- versioned bucket 要把 version id 纳入 receipt；unversioned并发覆盖必须 fail closed或使用隔离 key。

### 9.6 Upload projection

扩展 completion projector 第三 lane：

```text
pending upload completion
  -> reload frozen bundle + immutable object receipts
  -> reconstruct RemoteBundleReceipt
  -> RecordRemoteBundleVerified
  -> AppStore CAS commit/already-applied
  -> acknowledge TransferStore completion outbox
```

不得将 derived remote receipt 写入 legacy source backup 字段。source archival proof 在 V1 中仍 absent。

### 9.7 R4 验收场景

- MinIO path-style 完成整个 bundle。
- 至少一个 production-compatible virtual-host target smoke。
- process restart 后续传 multipart，不重复已验证 part。
- complete response 丢失时可判定真实 remote state。
- server checksum present/absent 两条路径都有 exact-byte proof。
- readback mismatch 不能产生 remote verified。
- credentials失效/更新后按 typed retry继续。
- 同 key 并发覆盖不会让旧 completion证明新 object。
- manifest 在所有 data/evidence object verified 后才可见。

### 9.8 完成门槛

- `RemoteBundleReceipt` 是 remote layer 唯一成功来源。
- pipeline restart 能找回 existing natural-key job并重新 attach。
- unsigned upload没有独立 receipt时保持 required action。
- source backup/archival/retention状态不被 derived upload改变。

### 9.9 建议提交

```text
feat(media): upload verified Ubuntu derivatives durably
```

---

## 10. R5：激活完整 pipeline、UI 和 lifecycle

### 10.1 Policy activation

生产接受三种 typed policy：

- `ImportOnly`；
- `AutoNormalize { profile_revision }`；
- `AutoUpload { profile_revision, storage_profile_identity }`。

移除“所有非 ImportOnly 一律 encoder unavailable”的硬编码拒绝，改为逐 capability gate：

| Gate                        | ImportOnly   | AutoNormalize | AutoUpload               |
| --------------------------- | ------------ | ------------- | ------------------------ |
| removable/import capability | required     | required      | required                 |
| approved profile + encoder  | not required | required      | required                 |
| stereo/CV evaluator         | not required | required      | required                 |
| storage profile/vault       | not required | not required  | required                 |
| unsigned upload approval    | not required | not required  | unsigned source required |

关闭一个后半段 capability 不能阻止用户选择 ImportOnly。

### 10.2 Dependency attach/replay

pipeline replay matrix：

| Durable state                                                   | Replay action                                      |
| --------------------------------------------------------------- | -------------------------------------------------- |
| import active                                                   | enqueue import                                     |
| import verified、policy requires derivation、无 dependency      | create/find derivation，CAS attach，enqueue        |
| derivation active且已 attach                                    | enqueue derivation                                 |
| derived verified、AutoUpload、缺 approval                       | project required action                            |
| derived verified、AutoUpload、有 approval、无 upload dependency | freeze/create/find upload，CAS attach，enqueue     |
| upload active且已 attach                                        | enqueue upload                                     |
| receipt已存在但dependency缺失                                   | natural-key find + CAS attach，不复制 job          |
| terminal failure/cancel                                         | project exact recovery actions，不自动新建不同 job |

### 10.3 UI 信息架构

每个 media entry 独立展示：

- Source：card presence、import state/progress、local verified、provenance；
- Derived：requested profile、encode/quality state/progress、derived verified；
- Remote：storage target、upload state/progress、object-store verified；
- Required action：pairing key、unsigned import approval、unsigned upload approval、credential、retry；
- Capability：FFmpeg/profile/evaluator/storage unavailable 的真实原因。

禁止：

- 单个 `uploaded`/`backedUp` boolean 汇总三层状态；
- 把 remote derived bundle 标成 source backup；
- 在 UI 中展示任意 filesystem absolute path 或 credential；
- access denied 显示成 empty card；
- operation-level failure覆盖 batch 中其他 item 的真实结果。

### 10.4 Command UX

- 二进制设置使用 toggle/checkbox。
- policy 使用 segmented control 或明确的 option selector。
- pause/resume/cancel/retry 使用相应 icon + tooltip。
- unsigned import 和 upload 是两个独立确认动作。
- root/storage conflict 显示当前阻塞 owner，不让设置界面假装成功。
- 长 id、错误文本和 access issue 在 Ubuntu viewport不溢出。

### 10.5 Startup 顺序

```text
1. migrate/load config and three stores
2. build inert runtime and shared AppStore/MediaStore/TransferStore handles
3. register managed Tauri state
4. replay import/derivation/upload outboxes into AppStore
5. recover durable pipeline dependencies
6. start projector lane
7. start import/derivation/upload lanes
8. enqueue active jobs
9. start mounted-media watcher/poller
10. publish complete initial projection
```

不能在 state registration 前启动 worker 或 emit event。

### 10.6 Shutdown 顺序

```text
1. reject new pipeline commands
2. stop watcher/deep scan
3. request import/derivation/upload executor shutdown
4. terminate/kill/reap FFmpeg child when needed
5. stop and join producer lanes
6. drain/stop projector lane
7. release media readers and root leases
8. close runtime/store owners
```

超时保留 join handle，使第二次 shutdown 可继续等待；不得 detach 后报告成功。

### 10.7 完成门槛

- 用户可以从插卡一直到 remote verified，不进入 legacy LAN upload入口。
- restart 后三层状态、progress、required action和 controls恢复。
- import-only 在 normalization/storage不可用时仍可用。
- `accessIssue`、waiting-for-key 和独立 approvals 都有真实 UI。

### 10.8 建议提交

```text
feat(media-ui): activate Ubuntu normalize and upload workflow
```

---

## 11. R6：自动化、真卡、对象存储与发行证据

### 11.1 验证阶段

执行阶段采用由窄到宽的门禁，任何失败先在最窄 owner 修复：

1. 静态字段/call-site/schema manifest 核对。
2. core unit/contract。
3. adapters integration。
4. application/Tauri Rust contract。
5. TypeScript decoder/reducer/UI contract。
6. workspace check/lint/format/build。
7. MinIO integration。
8. Ubuntu 真卡 HITL。
9. codec/quality/throughput corpus。
10. production object-store smoke。
11. Ubuntu release package/install/playback。
12. full release CI matrix。

单元测试通过不能替代 7-11。

### 11.2 Core contract

- 三个 container allowlist和 direct-child boundary。
- mount/access diagnostic wire bounds。
- signature 状态矩阵。
- trusted registry confirm/rotate/revoke/audit。
- import natural key/checkpoint/CAS/terminal outbox。
- root authority shared/exclusive/generation。
- AppStore dual CAS和 projector replay。
- schema-aware normalization input。
- approved profile/build mismatch。
- quality fixed-point/threshold/report digest。
- derived upload natural key/sidecar/checkpoint CAS。
- pipeline attach/replay matrix。

### 11.3 Adapter integration

- UDisks2 attach/refusal/re-enumeration。
- mountinfo fallback无 attach authority。
- sysfs removable evidence merge。
- mounted-file no-follow/inode swap/truncate/generation change。
- FFmpeg argv、bounded probe/report parsing、stderr drain、timeout/reap。
- VMAF/SSIM log corrupt/oversize/non-finite handling。
- stereo evaluator error/threshold/recovery。
- multipart initiate/part/complete/abort/readback。
- ambiguous complete、versioned object、same-key overwrite。
- keyring unavailable/locked/credential rotation。

### 11.4 Fault injection

至少覆盖以下 crash point：

- UDisks2 attach成功、re-enumerate前；
- import file write后、checkpoint前；
- target hash后、source rename前；
- source rename后、terminal transaction前；
- import outbox后、AppStore commit前/后、ack前；
- encode partial后、quality report前；
- quality report sync后、derived rename前；
- derived rename后、terminal transaction前；
- bundle freeze后、upload job create前/后；
- upload job create后、pipeline attach前；
- multipart initiate后、handle checkpoint前；
- part成功后、part checkpoint前；
- complete响应后、completion checkpoint前；
- verification后、receipt/terminal/outbox各边界；
- AppStore remote projection后、upload outbox ack前。

每个点重启后必须收敛，且不能生成假的 terminal success。

### 11.5 Ubuntu 真卡 HITL

| 场景                                 | 预期                                        |
| ------------------------------------ | ------------------------------------------- |
| 未挂载 removable TF 插入             | 受限 UDisks2 attach或明确授权错误           |
| Ubuntu Core nested recordings        | 只扫描固定 nested container direct children |
| 内部 ext4 SSD                        | 不 attach、不进入 candidate                 |
| removable unknown                    | fail closed diagnostic                      |
| 不同 UID 导致 recordings不可读       | 显示 access issue，不显示空卡               |
| library root 位于当前 TF             | preflight拒绝，零写入                       |
| library root 位于另一 removable      | preflight拒绝                               |
| scan/copy中拔卡                      | 无崩溃，waiting_for_media，句柄释放         |
| 同 mount path 换卡                   | 不续传                                      |
| signed原卡重插                       | exact revision re-admit后恢复               |
| unsigned原卡重插                     | 重新批准                                    |
| >4 GiB/长 session                    | 无整数截断，checkpoint恢复                  |
| disk read-only/full/inode exhaustion | typed failure，无 local verified            |
| process kill/restart                 | durable evidence收敛                        |
| pause/cancel copy/encode/upload      | deadline内确认或 resource_stuck             |
| release/eject                        | 无 reader，UDisks2 outcome truthful         |

### 11.6 Codec/quality corpus

真实 corpus 包括：

- 低纹理、强运动、曝光变化、重复纹理；
- 近景/远景和左右目亮度差；
- IMU 高频运动与长时录制；
- MJPEG 与 H.264 generation；
- source corruption、VFR、extra track、audio、truncated MP4、decode failure；
- target playback 和下游 CV workload。

报告比较：

- VMAF mean/frame p01和 SSIM；
- stereo/CV domain metrics；
- frame/timestamp/keyframe alignment；
- output size ratio；
- encode fps、CPU、peak memory、temporary disk；
- target decoder/player compatibility。

### 11.7 Release evidence layout

建议每次 qualification/release 保存：

```text
docs/release-evidence/ubuntu-tf/<release-id>/
  fixture-manifest.md
  discovery-import-hitl.md
  fault-injection-report.md
  codec-quality-report.md
  throughput-resource-report.md
  stereo-cv-report.md
  encoder-legal-review.md
  playback-compatibility.md
  minio-contract-report.md
  production-storage-smoke.md
  package-install-report.md
  checksums.txt
```

如原始视频不能进仓库，报告仍需记录受控存放位置、内容 digest、执行工具版本和 expected verdict。

### 11.8 完成门槛

- 原实施规范第 21 节每一项有 evidence path和执行日期。
- Ubuntu package在目标机器冷安装后完成真卡流程。
- MinIO和至少一个 production-compatible target通过。
- legal/playback/profile approval与 shipped FFmpeg/build artifact一致。
- full release CI通过；Ubuntu-only manual dispatch不作为 release证据。

### 11.9 建议提交

```text
test(media): prove Ubuntu TF pipeline recovery contracts
docs(media): record Ubuntu TF release qualification evidence
```

---

## 12. 多代理并行执行方案

用户要求速度时可以使用多个 `luna_worker`，但共享当前分支和同一工作区意味着文件 ownership
必须比普通多分支开发更严格。

### 12.1 固定 ownership

| Worker     | 责任                                                           | 独占文件/目录                                                        |
| ---------- | -------------------------------------------------------------- | -------------------------------------------------------------------- |
| Contract   | R0文档、fixture manifest、合同测试                             | `docs/`、ingest contract tests                                       |
| Trust      | MediaStore v7、registry、signed admission                      | `media_store/trust.rs`、相关 Ubuntu admission局部                    |
| Import     | root authority、queue、ingestor                                | `library_root.rs`、`ubuntu_workers.rs`、`ubuntu_ingestor.rs`         |
| Library    | AppStore v3、repository、projector                             | `app_store.rs`、`app_store_repository.rs`、`ubuntu_projector.rs`     |
| Normalize  | planner、FFmpeg quality、derivation                            | normalization modules、`media_normalizer.rs`、`ubuntu_derivation.rs` |
| Upload     | v20、derived store、uploader                                   | derived upload persistence/adapter、`ubuntu_uploader.rs`             |
| Frontend   | DTO/reducer/render/workflow                                    | `src/runtime/media/`、`src/ui/media/`、media workflow局部            |
| Integrator | composition、module exports、lifecycle、Cargo/schema manifests | `lib.rs`、`composition.rs`、`media.rs`、Cargo files、共享 schema     |

### 12.2 共享工作区规则

- worker 开始前记录其 ownership，不修改其他 owner 文件。
- 发现必须跨 owner 的变化时发送接口请求，由目标 owner或 integrator完成。
- 不运行会批量改写全仓库的 formatter，直到 integration窗口。
- 不回退看似“不属于自己”的变化；它可能是另一 worker刚写入。
- 每个 worker交付具体 changed paths、未解决断链和建议验证命令。
- integrator逐批显式暂存并提交，worker不自行做混合提交或 push。
- 同时最多一个 worker修改 `composition.rs` 或 `lib.rs`。
- schema migration版本由 integrator串行分配，防止两个 worker复用同一版本。

### 12.3 推荐并行 wave

```text
Wave A（R0/R1）
  Contract || Trust || Import || Library || Normalize-foundation || Upload-persistence
  -> Integrator compile/static merge

Wave B（R2/R3 early）
  Import fault closure || planner input || stereo evaluator/corpus || object-store harness
  -> Integrator production composition

Wave C（R3/R4）
  normalizer qualification || uploader worker || upload projection || frontend DTO
  -> Integrator pipeline attach/replay

Wave D（R5/R6）
  UI workflow || automated contracts || MinIO || Ubuntu HITL/corpus
  -> release evidence and final CI
```

Wave之间按完成门槛推进，不以“worker已经写完文件”推进。

---

## 13. 提交和推送计划

### 13.1 提交原则

- 保持当前分支 `feat/ubuntu-core-card-discovery`，不创建 worktree。
- 每批只提交一个可解释的 domain change。
- migration与读取/写入 repository放在同一提交，避免中间 commit打开数据库后没有合法访问层。
- capability foundation默认关闭；启用 capability的提交必须同时包含 production wiring和验收。
- 文档和 evidence提交不混入生成物或业务代码。
- 每次 push前确认 upstream仍是预期远端/分支，不假定是 `origin/main`。

### 13.2 推荐提交序列

```text
1. docs(media): rebaseline Ubuntu Core TF card contract
2. feat(media): persist PC trusted producer admission
3. feat(media): run Ubuntu TF imports on owned background workers
4. feat(media): project durable TF receipts into AppStore
5. feat(media): add fail-closed normalization qualification foundation
6. feat(upload): persist typed derived upload jobs
7. fix(media): close Ubuntu TF import recovery and projection
8. feat(media): wire qualified Ubuntu video normalization
9. feat(media): upload verified Ubuntu derivatives durably
10. feat(media-ui): activate Ubuntu normalize and upload workflow
11. test(media): prove Ubuntu TF pipeline recovery contracts
12. docs(media): record Ubuntu TF release qualification evidence
```

如果当前 PC/LAN 改动需要提交，应插入独立批次，例如：

```text
fix(transfer): preserve existing PC download and cleanup invariants
fix(object-store): harden shared completion verification
```

不得把 `pi_http.rs` 归入名为 Ubuntu TF import/normalization 的提交；虽然它属于 PC 仓库，责任仍不同。

### 13.3 每批 push 门槛

1. 工作区差异与 staged paths逐项核对。
2. 没有意外 Pi 仓库或无关文件。
3. 该批最窄验证通过。
4. migration checksum/history一致。
5. capability状态 truthful。
6. commit message与实际行为一致。
7. push当前 upstream分支。
8. CI失败先修该批，不继续叠加高依赖批次。

---

## 14. 工期和里程碑

### 14.1 中心估计

假设：

- 多个 `luna_worker` 按第 12 节并行；
- 一个 integrator持续处理高冲突文件；
- 真实 TF 卡和 Ubuntu机器可用；
- quality corpus、stereo evaluator owner、legal review和对象存储能力说明及时提供；
- 没有要求修改 Pi。

| 里程碑                    |     剩余时间 | 从 2026-08-06 起预计日期 |
| ------------------------- | -----------: | -----------------------: |
| R0/R1重新基线和稳定工作区 |  1-3个工作日 |                8月7-11日 |
| R2 Import-ready           |  3-6个工作日 |               8月11-14日 |
| R3/R4/R5 Code-complete    | 8-12个工作日 |               8月18-24日 |
| R6 Release-complete       |        3-5周 |          8月27日-9月10日 |

单个资深工程师串行完成 Release-complete，估计为 6-9 周。

### 14.2 建议周计划

#### 第 1 周

- R0 更新合同和 fixtures。
- R1 拆分并稳定当前 trust/import/projector/normalization/upload persistence。
- R2 完成 signed/unsigned admission、async import和 source projection。
- 达到 Import-ready。

#### 第 2 周

- schema-aware normalization input。
- production quality analyzer和 stereo evaluator接线。
- corpus harness和 report archive。
- derived upload repository、bundle builder和 worker并行开发。

#### 第 3 周

- profile qualification后启用 derivation。
- MinIO derived upload和 completion projector。
- pipeline attach/replay和三层 UI。
- 自动化 fault matrix。
- 达到 Code-complete。

#### 第 4-5 周

- Ubuntu真卡拔插/kill/空间不足/权限/HITL。
- codec quality和throughput corpus。
- production object-store smoke。
- Ubuntu package、legal、playback和release evidence。
- 达到 Release-complete。

### 14.3 不能被编码压缩的时间

以下工作不能通过增加代码 worker线性缩短：

- 真实长视频编码和throughput benchmark；
- stereo/CV evaluator评审；
- HEVC profile五类报告批准；
- FFmpeg/libx265/libvmaf发行/legal review；
- 真卡拔插、断电、kill/restart长时测试；
- production object-store管理员提供version/checksum/readback能力；
- CI runner/billing或发布账号外部状态。

---

## 15. 外部硬门禁与降级策略

| 门禁                                   | 缺失时允许继续                               | 缺失时禁止声明                       |
| -------------------------------------- | -------------------------------------------- | ------------------------------------ |
| 真实 TF fixtures和脱敏许可             | mock/unit contract、代码结构                 | 真实 import/signed完成               |
| Ubuntu 24.04 PC/reader/fs matrix       | core/adapters开发                            | Ubuntu HITL完成                      |
| SAS transcript digest定义              | unsigned import                              | signed offline admission完成         |
| quality corpus和stereo evaluator owner | import、fail-closed normalizer foundation    | normalization可用                    |
| 五类 HEVC approval reports             | candidate profile代码、qualification harness | profile approved/DerivedVerified发布 |
| FFmpeg/legal/playback review           | import、内部qualification                    | release package可发布                |
| production object-store能力说明        | MinIO开发和contract                          | production remote verified完成       |

降级规则：

- normalization门禁缺失：`ImportOnly`保持可用，normalizer capability明确 unavailable。
- object-store门禁缺失：import/normalize可用，AutoUpload不可选或显示 required capability。
- signed fixture/历史trust缺失：unsigned schema仍可按真实 provenance导入；signed candidate等待重新 pairing。
- UDisks2不可用：只观察 mountinfo中已经挂载且removable evidence完整的卷，不自行mount。

---

## 16. 风险登记表

| 风险                                         | 概率  | 影响  | 处置                                                  |
| -------------------------------------------- | ----- | ----- | ----------------------------------------------------- |
| 当前8,000行在途代码未编译且互相依赖          | 高    | 高    | R1先冻结、拆分、窄验证，不继续叠加                    |
| Ubuntu Core nested path扩大扫描面            | 中    | 高    | 固定allowlist、direct child、共享常量和bounds         |
| attach行为误碰内部磁盘                       | 低/中 | 极高  | attach前removable=Yes+non-system；unknown fail closed |
| 无签名publication被误报为signed              | 中    | 高    | 独立UnsignedPublicationV1和两阶段approval             |
| half-present签名被安全降级                   | 中    | 高    | detector状态矩阵contract test                         |
| trust写入失败只在stderr                      | 中    | 高    | 投影typed diagnostic，不宣称offline trust成功         |
| root只在worker turn验证而跨阶段变化          | 中    | 极高  | create/copy/verify/commit/recovery阶段callback        |
| normalization input按role误分类schema        | 高    | 极高  | schema-aware typed builder + fixtures                 |
| 新quality analyzer存在但production仍接旧stub | 高    | 高    | composition contract test验证concrete owner           |
| 空approved manifest被临时填入假digest        | 中    | 极高  | evidence路径+review gate；无报告不加entry             |
| derived upload复用legacy source backup语义   | 中    | 极高  | typed subject、独立receipt和projection断言            |
| completion验证只HEAD当前key                  | 中    | 极高  | version/checksum/readback绑定本次completion           |
| 多worker共享工作区覆盖改动                   | 高    | 高    | 固定ownership、单integrator、显式暂存                 |
| CI billing/runner阻断                        | 中    | 中/高 | 本地证据与CI状态分开；修复账号/runner后再release      |

---

## 17. 进度报告模板

每次阶段结束使用以下格式，防止“代码量”替代“完成门槛”：

```text
基线 commit:
工作区是否干净:
当前阶段:

本阶段已提交:
- commit / behavior / paths

完成门槛:
- [x] 已满足并给出 evidence
- [ ] 未满足，原因

运行过的验证:
- command / result / evidence path

未运行的验证:
- reason

外部阻塞:
- owner / input / impact

下一阶段:
- exact work packages

完成口径:
- Import-ready / Code-complete / Release-complete
```

百分比只能作为辅助指标；任何未满足的安全、durability、quality或remote-verification门槛必须单独列出。

---

## 18. 最终完成检查表

### 18.1 Contract/discovery

- [ ] 主实施规范已同步 Ubuntu Core nested path、UDisks2 attach和 unsigned publication。
- [ ] 只认证 Ubuntu 24.04 LTS x86_64。
- [ ] removable=Yes、filesystem allowlist和non-system gate全部生效。
- [ ] attach拒绝/权限/access issue真实投影。
- [ ] 三个container、direct child、bounds和no-follow全部生效。
- [ ] 内部SSD、unknown removable和虚拟设备不进入candidate。

### 18.2 Admission/import

- [ ] paired signed卡在Pi离线时通过，rotated/bad/unpaired fail closed。
- [ ] unsigned publication只有明确批准后导入。
- [ ] half-present signature pair不能降级。
- [ ] command不再同步复制大文件。
- [ ] root authority和destination guard覆盖所有I/O阶段。
- [ ] 拔卡/换卡/原卡重插语义正确。
- [ ] `LocalVerified`后无TF reader。

### 18.3 Library/normalization

- [ ] import outbox有幂等consumer和ack。
- [ ] media projection进入boot/wire/UI且独立于legacy LibraryEntry。
- [ ] normalizer只读sealed PC source。
- [ ] 六类实际输入使用正确typed normalization input。
- [ ] production使用真实FfmpegQualityAnalyzer和stereo evaluator。
- [ ] VMAF/SSIM/stereo-CV/full-decode report真实、bounded且durable。
- [ ] approved profile五类receipt齐全并匹配shipped build。
- [ ] derived atomic commit和outbox可恢复。

### 18.4 Upload

- [ ] v20 typed derived subject和natural key生效。
- [ ] frozen bundle只来自DerivedReceipt和sealed artifact。
- [ ] unsigned upload使用独立approval receipt。
- [ ] multipart每一步durable并可restart。
- [ ] completion-bound checksum/readback通过。
- [ ] final manifest最后上传。
- [ ] remote receipt投影后才显示object-store verified。
- [ ] derived upload不显示为source backup。
- [ ] archival/retention/delete保持disabled。

### 18.5 Lifecycle/UI/release

- [ ] startup先outbox/recovery，后worker/watcher。
- [ ] shutdown停止、reap、join全部owned resources。
- [ ] ImportOnly/AutoNormalize/AutoUpload按真实capability激活。
- [ ] source/derived/remote状态和进度独立。
- [ ] access issue、waiting-for-key、两次approval和credential action可见。
- [ ] strict decoder、batch、stale projection和root/storage conflict contract通过。
- [ ] MinIO、production target、真卡HITL和codec corpus有证据。
- [ ] Ubuntu package/legal/playback/build artifact一致。

任何未勾选项都意味着 Release-complete 尚未达到。尤其不能因为真实卡已经出现在 UI、某个视频已经
转码，或某个对象已经上传，就宣称整份实施规范完成。

---

## 19. 下一步执行顺序

从当前工作区继续时，严格按以下顺序开始：

1. 完成 R0：同步主实施规范和 fixture contract。
2. 由 integrator 对当前未提交代码做责任拆分，不新增 upload/UI 功能。
3. 先稳定 R1-A/R1-B/R1-C，使 trusted async import + source projection形成闭环。
4. 达到并证明 Import-ready。
5. 修正 schema-aware normalization input，再接真实 quality analyzer；不要先填 approved manifest。
6. 在 corpus和五类报告通过后启用 profile和 `DerivedVerified`。
7. 完成 derived uploader、remote projector和MinIO contract。
8. 最后移除 production ImportOnly硬门禁并接UI/lifecycle。
9. 完成R6真实证据后，才声明整份实施规范完成。

当前最重要的近期交付不是“尽快让按钮显示上传成功”，而是把已经在工作区中的 trusted import、
background worker、root authority和completion projector稳定成一个可验证的 Import-ready基线。
这个基线一旦成立，normalization和upload才能只依赖sealed PC evidence继续推进，而不会再次把TF卡、
mount path或legacy upload状态变成隐藏的事实源。
