# Ubuntu TF 卡读取、视频规范化与对象存储上传实施规范

> 状态：Implementation-ready design；本文描述目标实现，不代表当前代码已经完成或验收
>
> 日期：2026-08-05（Asia/Shanghai）
>
> 实现仓库：`ylx-transfer` PC/Ubuntu 桌面端
>
> 首个认证平台：Ubuntu 24.04 LTS x86_64；其他 Linux 发行版和架构不自动继承支持结论
>
> 上游需求与证据：
> [`REMOVABLE_MEDIA_IMPORT_AND_VIDEO_NORMALIZATION.md`](REMOVABLE_MEDIA_IMPORT_AND_VIDEO_NORMALIZATION.md)、
> [`SD_CARD_AND_VIDEO_CODEC_EVIDENCE.md`](research/SD_CARD_AND_VIDEO_CODEC_EVIDENCE.md)

本文把现有“Ubuntu TF 卡本地导入 MVP”和后续“视频处理、上传”设计收敛为一份可执行的
PC 端实施规范。它回答以下问题：具体由哪个 module 拥有行为、接口在哪里、哪些表需要迁移、
状态如何推进、崩溃后如何恢复、失败是否可重试、前端如何展示，以及什么证据齐备后才算完成。

本文只要求修改 PC 仓库。Pi 继续按现有 publication/raw/spool 合同写卡；不得为了完成本文
而修改 Pi 录制、编码、目录、签名、FIFO、挂载或清理逻辑。

---

## 1. 目标与完成定义

### 1.1 产品目标

在 Ubuntu PC 上，将通过受支持系统路径访问的 removable TF 卡作为只读来源，完成以下生产链路：

```text
removable block device
  -> UDisks2 attach（仅在需要且被授权时）
  -> Ubuntu mounted removable volume
  -> bounded schema-aware scan
  -> signed trust verification / explicit unsigned admission
  -> durable read-only import into the PC library
  -> source-local verification and immutable publication
  -> codec probe and normalization planning
  -> approved HEVC derivation and full validation
  -> frozen derived upload bundle
  -> durable multipart S3-compatible upload
  -> completion-bound remote byte verification
  -> truthful local/derived/remote projection
```

导入完成后，probe、encode 和 upload 只能读取 PC 已封存的 source tree，不能继续读取 TF 卡。
达到 `LocalVerified` 后，即使用户拔卡，后半段也必须能够继续执行和恢复。

### 1.2 Definition of Done

只有同时满足下列条件，才能宣称“Ubuntu TF 卡到视频处理与上传链路完成”：

1. Ubuntu 只扫描经过可移除介质资格确认、已经挂载或经 UDisks2 受限 attach 后重新枚举的受支持文件系统。
2. `SignedPublicationV1` 使用 PC 持久化的 SAS-confirmed fingerprint 验证，不依赖 Pi 在线。
3. unsigned source 只有在用户明确批准后导入；上传前还有一次绑定 source revision 的独立批准。
4. TF 卡始终以只读文件接口访问；应用不在卡上创建、修改、重命名或删除任何文件。
5. import、derivation、upload 都有 durable job、自然键、checkpoint、取消和重启恢复。
6. `LocalVerified` completion outbox 被幂等消费并投影到统一 media library，不再成为孤立记录。
7. normalization 使用真实 ffprobe/FFmpeg、获批准 profile 和真实质量证据；不能使用 unavailable stub。
8. upload 只接收 frozen `UploadBundleRevision`，并在每个对象完成绑定验证后才发布 remote verified。
9. source、derived、remote 三层状态分别展示；derived upload 不能显示成 source/original backup。
10. 暂停、取消和退出会等待 reader、worker 和 FFmpeg 子进程释放或返回明确 `resource_stuck`。
11. 通过 core contract、adapter integration、Tauri/TypeScript contract、MinIO 和 Ubuntu 真卡验收。
12. source archival、自动删除 PC source、删除卡上数据全部保持关闭。

### 1.3 当前代码基线

| 阶段                         | 当前状态                                                                        | 本文要求                                                              |
| ---------------------------- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Linux volume discovery       | 已存在 UDisks2/mountinfo adapter 和受限 attach 流程                             | 使用 `removable` evidence 收紧资格；attach 后重新枚举权威 mount state |
| bounded scan/detector        | 已实现                                                                          | 保留现有上限和 fail-closed 语义                                       |
| unsigned import              | 可达                                                                            | 保留人工批准并改为后台 worker                                         |
| signed import                | scanner 和 PC registry admission 已接入，需继续由真实 paired-card evidence 验收 | 保持 exact signature/key/inventory fail-closed 语义                   |
| durable copy/verify/commit   | 已有 durable worker/root-authority foundation                                   | 保持不变量，补阶段级 revalidation、拔插和 lifecycle 验收              |
| import completion projection | 已有 shared AppStore projector foundation                                       | 补 boot/restart/CAS/replay 验收                                       |
| FFmpeg/ffprobe adapter       | 已有 process-safe adapter 和 capability probe                                   | 只有真实 quality/stereo evidence 齐全后才进入 production capability   |
| quality evaluator            | `FfmpegQualityAnalyzer` foundation 已存在，当前 production gate 仍关闭          | 实现真实 evaluator 和报告归档，并保持空 profile manifest              |
| `MediaNormalizer` core       | executor/repository/lease/worker foundation 已存在                              | 补 production composition、schema-aware input 和 recovery 验收        |
| derived bundle model/adapter | typed bundle/checkpoint/uploader foundation 已存在                              | 补 production attach、completion-bound verification 和 projector      |
| 通用 S3 multipart            | 已存在                                                                          | 复用 ObjectStorePort，不复制网络实现                                  |
| Ubuntu pipeline              | 生产仅 `ImportOnly`                                                             | 激活 `AutoNormalize`/`AutoUpload`                                     |
| frontend                     | 固定提交 import-only policy                                                     | 提供真实策略和三层状态                                                |
| retention/delete             | 未实现                                                                          | 本版本明确关闭                                                        |

---

## 2. Ubuntu-only 范围

### 2.1 纳入范围

- Ubuntu 24.04 LTS x86_64 桌面环境。
- OS 已挂载且普通用户可读取的 `ext2`、`ext3`、`ext4`、`btrfs`、`xfs`、`f2fs`。
- `EvidenceHint::Yes` 的可移除 block-backed volume。
- 卷根下固定的 `recordings/`、`YLX_RECORDINGS/`，以及 Ubuntu Core 的
  `system-data/var/snap/ylx-capture/common/recordings/`。
- 三个固定目录的直接 session 子目录，不进行全盘递归。
- `RawCaptureV2`、`LegacyMjpegSessionV5`、`ApplianceSpoolV6`、
  `CompleteUnpublishedV6`、`UnsignedPublicationV1` 和 `SignedPublicationV1`。
- PC 本地 source/derived immutable tree。
- S3-compatible object storage，包括 AWS S3 和通过现有配置支持的 MinIO 类实现。
- 用户发起的 import-only、import+normalize、import+normalize+upload 三种 policy。

### 2.2 明确不纳入

- 任何 Pi 仓库改动。
- Windows、macOS、其他 Linux 发行版或 ARM PC 的发布承诺。
- exFAT、FAT32、NTFS、APFS。
- raw block-device 读取、分区识别、自动挂载、自动修复、自动格式化。
- `SelectedFolder` 或任意目录扫描。
- 以管理员/root 权限运行。
- 在 TF 卡上写 checkpoint、sidecar、数据库、完成标记或派生文件。
- 修改或删除 TF 卡 source。
- 上传原始 MJPEG/H.264 source video。
- 自动删除 PC source、自动 retention 或“上传后清卡”。
- 未获批准的 HEVC profile、硬件 encoder 或静默 codec fallback。

### 2.3 兼容性原则

“Ubuntu-only”是认证范围，不是通过 `cfg(target_os = "linux")` 就自动支持所有 Linux。
平台能力必须在启动时实际探测：UDisks2、mountinfo、FFmpeg、ffprobe、libx265、质量 filters、
对象存储配置和 OS credential vault 各自有独立 capability。缺一项只关闭依赖该项的动作，
不能伪造成功，也不能把 unavailable 映射成普通 transient retry。

---

## 3. 不变量

以下不变量优先于 UI 便利和吞吐优化。

### 3.1 来源与内容

1. mount path 是位置，不是介质身份。
2. acquisition source、source identity、source revision 必须分离。
3. 同一内容从 LAN 和 TF 卡进入时收敛到同一 source revision 和 library entry。
4. signed publication 的信任来自 PC 已确认 fingerprint，不来自卡上自带的 key/fingerprint。
5. signed candidate 不能降级成 unsigned candidate。
6. unsigned provenance 永远显示为 `LocallyValidatedUnsigned`。
7. probe 只能验证媒体事实，不能替代 signature、inventory hash 或用户批准。

### 3.2 文件系统

1. source 文件只使用 no-follow、read-only handle 打开。
2. 每个相对路径逐 component 校验；拒绝绝对路径、`..`、NUL、反斜线逃逸和 link。
3. 每次续读前后核对介质 generation、device/inode、size 和必要时间戳证据。
4. library root 与任一 source/removable volume 的 canonical path 和 `st_dev`/filesystem identity 不得重合。
5. staging、journal、SQLite 和派生输出全部位于 PC library filesystem。
6. source tree 和 derived tree 分别封存；派生文件不得写入 source tree。
7. terminal receipt 只在文件系统 publication 已 durable 后提交。

### 3.3 作业与恢复

1. durable row 先于 worker side effect。
2. 内存队列只负责唤醒；SQLite job/outbox 才是恢复权威。
3. 每个自然键只允许一个兼容的 active job；不同 immutable input 必须返回 conflict。
4. Port `Err` 表示没有提交可见 durable mutation；提交后的失败投影为 typed job state。
5. outbox 在下游投影成功后才 acknowledge。
6. pause/cancel 成功必须意味着 worker/子进程已确认边界，不能只是设置一个 UI flag。
7. shutdown 必须停止接收新工作、请求停止、释放句柄并 join；超时返回 `resource_stuck`。

### 3.4 上传与清理

1. upload job 自然键是 `(upload_bundle_revision, storage_profile_identity)`。
2. object key 来自 opaque segment 编码，不直接拼接显示名。
3. multipart handle、part、completion 和 verification 逐步 durable。
4. verification 必须绑定本次 completion/version，不能只 HEAD 当前 key。
5. 最终 derived manifest 最后上传。
6. 只有 `RemoteBundleReceipt` 可以产生 remote verified。
7. derived remote verified 不是 source backup，不允许触发 source 删除。

---

## 4. 目标架构

### 4.1 生产数据流

```text
LinuxRemovableMediaBackend
  -> UbuntuMediaRuntime / ConstrainedScanner
  -> ScanCandidate
  -> TrustedProducerRegistry + PublicationTrust
  -> SourceRecording
  -> RecordingIngestor + ImportWorker
  -> MediaStore(import snapshot/checkpoints/receipt/outbox)
  -> ImportCompletionProjector
  -> AppStore(MediaLibraryProjection)
  -> SourceArtifactResolver + source read lease
  -> MediaNormalizerExecutor + DerivationWorker
  -> MediaStore(derivation ledger/receipt/outbox)
  -> DerivationCompletionProjector
  -> FrozenUploadBundle
  -> TransferStore(derived upload job/checkpoint/outbox)
  -> DerivedUploadAdapter + ObjectStorePort
  -> UploadCompletionProjector
  -> AppStore(MediaLibraryProjection)
  -> MediaApplication -> Tauri event -> TypeScript runtime -> Ubuntu media UI
```

### 4.2 Module 所有权

| Module                  | 外部 interface                                       | 拥有的实现复杂度                                                         | 不负责               |
| ----------------------- | ---------------------------------------------------- | ------------------------------------------------------------------------ | -------------------- |
| `UbuntuMediaRuntime`    | scan/candidate/admit/artifact resolve/handle release | volume observation、资格过滤、generation、bounded scan、signed admission | copy、encode、upload |
| `RecordingIngestor`     | start/command/recover/snapshot                       | preflight、copy、hash、checkpoint、seal、atomic source commit            | UI、FFmpeg、S3       |
| `MediaLibraryProjector` | apply completion evidence                            | outbox consumption、AppStore CAS、三层真实状态                           | job execution        |
| `MediaNormalizer`       | start/command/recover/snapshot                       | probe、plan、encode、quality、validation、derived commit                 | TF handle、S3        |
| `DerivedUploadExecutor` | start/command/recover/snapshot                       | bundle freeze、multipart checkpoint、remote verify、upload completion    | source deletion      |
| `SessionPipeline`       | start/command/replay projection                      | dependency DAG、policy、required action                                  | 复制 child job state |
| `UbuntuMediaSupervisor` | recover/start/stop/enqueue                           | worker lanes、wake queue、shutdown/join、projection notifications        | 领域状态决定         |

这些 module 通过现有 core interfaces 连接。新增 seam 只用于真实变化点：生产 SQLite adapter 与
测试内存 adapter、生产 FFmpeg/S3 adapter 与测试 fake。Tauri command 保持浅，只做 DTO 映射和调用。

### 4.3 需要新增或完成的生产 adapters

- `MediaStoreTrustedProducerRegistry`
- `AppStoreMediaLibraryProjectionRepository`
- `UbuntuImportWorkerScheduler`
- `UbuntuDerivationWorkerScheduler`
- `UbuntuDerivedUploadWorkerScheduler`
- `SealedSourceArtifactResolver`
- `SealedDerivedArtifactSource`
- `ApprovedProfileRegistry`
- `FfmpegQualityAnalyzer`
- `TransferStoreDerivedUploadRepository`
- `UbuntuMediaCompletionProjector`

不得新增第二套 FFmpeg 命令生成器、第二套 S3 HTTP client、第二套 import copy engine 或第二套
pipeline aggregate。已有 core/adapter 能力必须通过接口复用。

---

## 5. 身份、信任与准入

### 5.1 身份层级

| 身份                     | 组成                                                              | 生命周期                  |
| ------------------------ | ----------------------------------------------------------------- | ------------------------- |
| `MediaGeneration`        | volume identity + root marker digest + observation epoch          | 一次具体插卡 observation  |
| `CandidateId`            | generation + fixed-root relative path + schema evidence           | 当前 scan cache           |
| `SourceIdentity`         | schema-aware session/content identity                             | 跨位置稳定                |
| `SourceContentRevision`  | signed publication revision 或封存 inventory 的 canonical SHA-256 | immutable source tree     |
| `ProfileRevision`        | normalization 参数和阈值的 canonical SHA-256                      | profile 版本              |
| `DerivedRevision`        | source、profile、encoder build、输出 inventory 和验证证据         | immutable derived tree    |
| `UploadBundleRevision`   | ordered object inventory、role、key、size、SHA-256、policy        | 一次 frozen upload bundle |
| `StorageProfileIdentity` | endpoint、bucket、prefix、URL style 等非 secret 语义摘要          | destination 配置版本      |

显示名、mount path、盘符、bucket label 和临时 job id 都不能替代上述身份。

### 5.2 PC trusted producer registry

当前配对状态只在运行时 device actor 中携带 publication fingerprint，不足以支持 Pi 离线时的
TF 卡验证。应在 `MediaStore` v7 增加 forward-only migration：

```sql
CREATE TABLE media_trusted_producer_keys (
    producer_identity        TEXT NOT NULL,
    key_fingerprint          TEXT NOT NULL,
    trust_source             TEXT NOT NULL CHECK (trust_source = 'sas_pairing'),
    pairing_evidence_digest  TEXT NOT NULL,
    confirmed_at             TEXT NOT NULL,
    revoked_at               TEXT,
    PRIMARY KEY (producer_identity, key_fingerprint)
);

CREATE UNIQUE INDEX media_trusted_producer_active_identity
    ON media_trusted_producer_keys (producer_identity)
    WHERE revoked_at IS NULL;

CREATE UNIQUE INDEX media_trusted_producer_active_fingerprint
    ON media_trusted_producer_keys (key_fingerprint)
    WHERE revoked_at IS NULL;
```

约束如下：

- `key_fingerprint` 必须是 `sha256:<64 lowercase hex>`。
- `pairing_evidence_digest` 绑定用户确认过的 SAS transcript，不保存 connection token 或 secret。
- pairing 成功后，在同一事务中 revoke 该 producer 的旧 active fingerprint 并插入新记录。
- 配对取消、失败或只发现设备时不得写 registry。
- key rotation 必须重新配对；不能由新 manifest 自动更新。
- 升级前的历史配对若没有可验证 transcript，不做推断迁移；用户需重新配对一次。
- 删除 UI device 不等于撤销信任；撤销必须是显式独立动作并写审计记录。

建议 interface：

```rust
pub trait TrustedProducerRegistry: Send + Sync {
    fn resolve_active(
        &self,
        fingerprint: &str,
    ) -> Result<Option<TrustedProducerKeyReceipt>, TrustedProducerError>;
}
```

生产 adapter 使用 `MediaStore`，测试 adapter 使用内存表。调用方只知道 fingerprint 和确认收据，
不知道 SQLite 表或 pairing UI。

### 5.3 Signed publication admission

`UbuntuMediaRuntime::admit` 的 signed 分支必须按以下固定顺序执行：

1. 从 cached candidate 取得 exact `SignedPublicationMaterial`。
2. 再次确认 candidate generation 仍然 live。
3. 解析 presented fingerprint；格式错误直接 `integrity_failed`。
4. 查询 `TrustedProducerRegistry::resolve_active`。
5. 未找到时返回不可重试的 `policy_approval_required`，required action 为 `waiting_for_pairing_key`。
6. 使用 registry 返回的 fingerprint 作为 expected identity。
7. 将 card envelope 交给现有 `PublicationTrust<Ed25519PublicationVerifier>`。
8. 验证 raw public key 的 SHA-256 等于 fingerprint、detached signature 覆盖 exact payload。
9. 验证 session id、revision、inventory、path、size 和 digest 与 scanned candidate 完全一致。
10. 仅通过 `SourceRecording::admit_device_signed(candidate, verified)` 构造 source。

任何一步失败都不能退回 unsigned admission。Pi 不需要在线，Pi 代码也不需要改变。

### 5.3.1 Publication signature state matrix

完整 publication manifest 的 producer authenticity 与本地内容完整性是两个独立门槛。detector
必须保持以下矩阵；detached signature 或 presented public key 只存在一项时不得降级为 unsigned：

| Manifest             | Detached signature | Public key | 结果                                              |
| -------------------- | ------------------ | ---------- | ------------------------------------------------- |
| valid                | present            | present    | `SignedPublicationV1`，进入 PC trust 验证         |
| valid                | absent             | absent     | `UnsignedPublicationV1`，要求独立 import approval |
| valid                | present            | absent     | corrupt，禁止降级                                 |
| valid                | absent             | present    | corrupt，禁止降级                                 |
| malformed/incomplete | 任意               | 任意       | corrupt/incomplete，禁止导入                      |

进入 unsigned path 仍必须验证 manifest shape、session identity、artifact path、declared size、
digest、inventory total、geometry、codec 和 commit boundary。unsigned 只表示没有 producer
signature，不表示可以跳过本地内容验证。

### 5.4 Unsigned admission

`RawCaptureV2`、v5、spool、complete-unpublished v6 和没有任何 detached signature 材料的
`UnsignedPublicationV1` 保持 unsigned：

- scanner 只产生 `ReadyUnsignedRequiresPolicy`，不能自动准入；
- UI 每次首次导入都要求明确批准；
- receipt 绑定 candidate、generation、policy version 和批准时间；
- source 完整复制后由本地 inventory 计算稳定 `SourceContentRevision`；
- 原卡拔出重插后必须重新 scan 并再次批准，不能只凭旧 mount path 自动恢复；
- 上传 unsigned derivative 需要第二张、绑定最终 source revision 的 upload admission receipt。

import approval 只说明“允许把未签名数据复制到本机”，不等于“允许将其上传到远端”。

### 5.5 去重

长期去重依据是 `LibraryImportReceipt` 和对 sealed local evidence 的重读，不是 terminal job row：

- signed：以 stable source identity + publication revision 去重；
- unsigned：以 stable source identity + 本地计算 source revision 去重；
- acquisition locator 不进入 library entry key；
- receipt 存在但 local tree 缺失或摘要错误时返回 evidence failure，不能返回 `AlreadyImported`；
- LAN 与 TF 卡同 revision 命中同一 `LibraryEntryKey`。

---

## 6. Ubuntu volume discovery 与 constrained scan

### 6.1 介质资格

生产扫描资格必须同时满足：

1. adapter 枚举到一个 block-backed volume；已挂载卷可直接观察，未挂载卷只有在同一轮资格检查
   证明为 removable 且非系统盘时才允许请求 UDisks2 attach；
2. mount path 不是 `/`；
3. source 不是 loop/zram/overlay 等虚拟设备；
4. `removable == EvidenceHint::Yes`；
5. filesystem 位于 allowlist；
6. 至少有一个可访问 mount path；
7. mount root 是真实目录且不是 link；
8. 当前用户具有只读访问权限。

`EvidenceHint::No` 和 `EvidenceHint::Unknown` 都不得进入 candidate scan。它们可以保留为内部诊断，
但不能出现在“可导入 TF 卡”列表中。这样可避免第二块内部 SSD 被误扫，也避免 destination guard
把普通内部数据盘误当成 TF 卡。

UDisks2 是主 adapter；需要 attach 时必须通过系统服务请求挂载，随后重新枚举并以 authoritative
mount state 作为 scan 输入。UDisks2 拒绝、需要授权或不可用时返回 bounded typed diagnostic，
不得调用 sudo 或自行执行 mount。`/proc/self/mountinfo` fallback 仍可用于发现已经挂载的卷，
但 fallback 没有 attach 权限，且结果必须经过同一 removable/sysfs evidence 合并和资格过滤，
不能因为 UDisks2 unavailable 而放宽规则。

attach 失败、mount 已存在但录制容器不可读、以及候选目录不可访问都必须保留为 bounded typed
diagnostic。它们不能被压扁成“卡中没有录像”：

- 卡已发现但未挂载；
- attach 被系统拒绝、需要授权或 UDisks2 不可用；
- mount 已存在但固定录制容器不可读；
- 录制容器可读但没有 candidate；
- candidate 存在但 schema/integrity 不可用。

attach/error 文本经过长度限制和转义后才进入 Rust/TypeScript wire projection；原始 D-Bus 或
无限长度的 IO 文本不得直接暴露给 UI。

### 6.2 固定扫描范围

每个合格 volume 只检查：

```text
<mount>/recordings/<direct-session-child>/
<mount>/YLX_RECORDINGS/<direct-session-child>/
<mount>/system-data/var/snap/ylx-capture/common/recordings/<direct-session-child>/
```

这三个容器由 scanner 与 generation fence 共享同一个固定 allowlist。禁止从 mount root 递归寻找
名为 `recordings` 的目录，也禁止扫描 `home`、`DCIM`、任意 snap 或用户选择目录。

禁止递归搜索整个卷、禁止猜测目录名、禁止扫描 home、DCIM 或任意用户目录。`SelectedDirectory`
scope 不在 Ubuntu TF 产品 interface 中暴露。

### 6.3 资源上限

继续使用 `ScanLimits::default()`，并把数值纳入 contract tests：

| 限制                            |        值 |
| ------------------------------- | --------: |
| 三个 fixed roots 中观察的目录项 |     2,048 |
| candidates                      |       512 |
| 单 manifest                     |     2 MiB |
| 单辅助 index                    |    64 MiB |
| index records                   | 5,000,000 |
| 单 candidate 文件数             |    20,000 |
| relative path bytes             |     1,024 |
| path components                 |        32 |
| 单 candidate 声明总字节         |    16 TiB |
| root marker entries             |     2,048 |
| 单 root marker                  |     2 MiB |
| root marker 合计                |    32 MiB |

超过上限返回明确 diagnostic，并将 scan 标记为 truncated/candidate unavailable；不能静默忽略
超限部分后仍把 candidate 标记为 ready。

### 6.4 Path 与文件类型

- JSON 必须用 `serde_json` 和版本化 DTO 解析。
- relative path 逐 component 验证。
- directory enumeration 使用 `symlink_metadata`。
- detector 只打开 schema 明确命名的文件。
- inventory 只包含 schema/manifest 授权的 regular files。
- 拒绝 symlink、hard-link identity 异常、socket、FIFO、device node 和目录冒充文件。
- 非 UTF-8 名称作为诊断跳过，不执行 lossy path 转换。
- extension、文件名或 ffprobe 结果都不能单独决定 provenance。

### 6.5 MediaGeneration 与 cache

arrival、remove 和 poll 只触发一次 serialized refresh。refresh 必须覆盖：

```text
enumerate -> qualify -> root marker -> generation reconcile
          -> constrained scan -> cache replace -> projection publish
```

同一 mount path 换卡必须产生新 generation。resolver 在真正打开 artifact 前再执行 authoritative
refresh；若 generation/root marker/candidate claim 变化，旧 locator 返回 `media_changed` 或
`waiting_for_media`，不能继续读新卡。

### 6.6 Destination guard

每次 create、resume、copy、verify、commit 和 recovery drain 前都验证 PC destination：

- canonical library root 不与任何 qualified source mount 互相包含；
- library root `st_dev` 不等于 source mount `st_dev`；
- filesystem identity 不等于任何 observed removable filesystem identity；
- staging、final source tree、derived staging 和 final derivative 都位于同一预期 PC filesystem；
- library root 在 job 执行期间不能无保护地切换。

不要长时间持有一个阻止 pause/cancel 的普通 mutex。应把现有 callback gate 深化为
`LibraryRootAuthority`：worker 获取 shared `LibraryRootLease`，设置变更获取 exclusive lease。
lease 固定 canonical path、`st_dev`、filesystem identity 和 root generation；worker 在每个 I/O
阶段重新 assert。切换 root 时若存在 active lease 或 durable active job，返回 conflict。

### 6.7 Handle release 与 eject

`release_media_handles(generation)` 只在以下条件满足后返回 released：

- scan directory handles 已关闭；
- active artifact reader 数为零；
- import worker 不再持有该 generation 的 read lease；
- watcher 已停止对该 generation 做深扫描。

`eject` 必须先经过 release，再调用 UDisks2 的非强制 eject/unmount 能力。权限或平台不支持时，
UI 只能显示“应用句柄已释放”，不能显示“系统已弹出”。

---

## 7. Durable local import

### 7.1 Preflight 顺序

start pipeline/import 必须在创建 durable job 前完成不会产生 side effect 的校验：

1. 读取当前 library-root lease 和 destination identity。
2. authoritative refresh candidate generation。
3. 执行 signed/unsigned admission。
4. 计算 exact inventory 和 wire-safe projection。
5. 检查剩余源字节、512 MiB safety margin 和 policy 所需派生 working-set reserve。
6. 检查现有 import receipt 和 active natural-key conflict。
7. 检查 requested pipeline policy 的 profile/storage capability。
8. 验证所有即将返回的 wire DTO 可编码，不超过 JavaScript safe integer。
9. 在 `MediaStore` 一个事务中创建 import job 和 session pipeline。

事务提交后，命令返回 created/existing snapshot 并 enqueue；不能同步复制完整 session。

### 7.2 ImportJob 状态

```text
queued
  -> waiting_for_media
  -> preflighting
  -> copying
  -> verifying
  -> committing
  -> local_verified

running states -> pausing -> paused
running states -> cancelling -> cancelled
transient error -> retry_wait
terminal integrity/policy error -> failed
```

`local_verified` 是 terminal success。它要求 source tree 已封存、receipt 与 completion outbox 已在
同一 SQLite transaction 中写入。

### 7.3 后台 worker

替换 `InlineImportScheduler` 和 command 内的同步 drain：

- `start`/`resume` 只持久化 intent 并向 bounded wake queue 放 job id；
- queue 使用 job id 去重，满时只返回 wakeup failure，durable job 仍可被 recovery 发现；
- worker 读取最新 snapshot，不相信 enqueue 时的副本；
- 一个 job 同时只有一个 process-local worker owner；跨进程由 CAS fence；
- 每个复制 chunk 检查 desired state、shutdown 和 generation；
- pause/cancel command 不获取 library-root shared lease，只提交 desired state 并唤醒/通知 active worker；
- completion 后发布完整 import collection projection，不发送 row patch。

建议每个 lane 初始并发为 1。TF 卡随机读和 PC 磁盘写通常是瓶颈；在真实 benchmark 前不要并行
复制多个大 session。队列实现是内部细节，不需要暴露成新的应用 interface。

### 7.4 Copy 与 checkpoint

对 inventory 中每个文件按稳定 ordinal 执行：

1. 通过 generation-fenced mounted-file resolver 打开 source。
2. 使用 no-follow/read-only handle，记录 open 前后的 identity。
3. 创建或校验 PC `.part` 和 journal。
4. 从较低的 durable offset 恢复，永远不信任未 checkpoint 尾部。
5. 每 256 KiB 或文件结束时 flush、sync、写 SQLite checkpoint。
6. 读取过程中计算 source SHA-256。
7. 关闭 writer 后重新读取目标 `.part` 计算 target SHA-256。
8. 对 signed source 比对 manifest size/hash；unsigned source 固化 computed hash。
9. 两个 hash、size 和 identity 一致后标记 file verified。

checkpoint、journal 和 `.part` 都不得位于 TF 卡。

### 7.5 拔卡与重插

- copy I/O error 或 remove event 停止新读，关闭当前 handle，job 进入 `waiting_for_media`。
- signed source 可在 exact publication revision、generation re-admission 和 file claim 一致后复用
  已验证 checkpoint。
- unsigned source 重插必须重新批准；acquisition fence 改变时清零不再可信的 unsigned checkpoint。
- 不同卡复用相同 mount path 时绝不续传。
- 用户取消时清理 PC staging 可异步进行，但 terminal cancel 必须先确保没有 writer。

### 7.6 原子提交与目录布局

```text
library/
  sources/{source-revision-hex}/...
  derivatives/{source-revision-hex}/{profile-revision-hex}/{derived-revision-hex}/...
  .ylx-import-staging/{import-job-id}/...
  .ylx-derived-staging/{derivation-job-id}/...
```

source commit 顺序：

1. 所有 file checkpoint verified。
2. 生成 canonical local source manifest/commit receipt。
3. sync files、manifest、staging directory。
4. 获取 source revision exclusive publication lease。
5. 若 final tree 已存在，重读并验证 exact revision；一致则 idempotent replay，不一致则 integrity failure。
6. same-filesystem atomic rename 到 `sources/{revision}`。
7. sync parent directory。
8. 在 MediaStore 同一事务写 terminal snapshot、source receipt、import receipt、completion outbox。

数据库绝不能在 rename/directory sync 前宣称 `local_verified`。

---

## 8. Media library projection 与 completion outbox

### 8.1 为什么不能生成旧 `LibraryEntry`

现有普通上传 UI 的 `LibraryEntry` 假设 LAN signed publication，并用 `device_id|session_id` 作为 key。
TF source 还包含 unsigned raw/legacy/spool，强行转换会丢失 provenance 或伪造 signed evidence。

TF 工作流必须使用 core 已有的 `MediaLibraryProjection`，旧 LAN `LibraryEntry` 继续兼容运行。
两者可以在 UI 上统一展示，但 persistence payload 和上传入口不能混用。

### 8.2 AppStore migration

在 `AppStore` 增加独立表，不把新 payload 塞进 `app_library_entries`：

```sql
CREATE TABLE app_media_library_entries (
    entry_key            TEXT PRIMARY KEY,
    projection_revision  INTEGER NOT NULL CHECK (projection_revision >= 1),
    payload               BLOB NOT NULL,
    updated_at            TEXT NOT NULL
);
```

更新 `app_store_meta.revision` 与 replacement 必须在同一事务中。实现
`AppStoreMediaLibraryProjectionRepository` 对应 `LibraryProjectionRepository`；CAS 同时核对
global store revision 和 per-entry projection revision。

`AppStore` 由 `Arc` 共享给 application 和 media composition，不能为 worker 再打开一个绕过 CAS 的
写连接。不要在持有 `AppData` 大锁时执行 outbox replay。

### 8.3 Completion projector

新增一个幂等 `UbuntuMediaCompletionProjector`，按 lane 顺序消费：

| Outbox     | 读取的长期证据                                   | Library command              | acknowledge 条件                   |
| ---------- | ------------------------------------------------ | ---------------------------- | ---------------------------------- |
| import     | `LibraryImportReceipt`                           | `RecordImport`               | AppStore committed/already-applied |
| derivation | `DerivedReceipt`                                 | `RecordDerived`              | AppStore committed/already-applied |
| upload     | frozen bundle + completion-bound object receipts | `RecordRemoteBundleVerified` | receipt 重构和 AppStore CAS 成功   |

算法：

1. 从权威 store 读取 pending envelopes，按 sequence 排序。
2. 根据长期 receipt 重新构造 command，不相信 event payload 中的 path/display text。
3. 读取目标 projection 和两个 revision。
4. 应用 `LibraryProjector`。
5. CAS conflict 时 reload/recompute，有界重试；immutable evidence conflict 直接停止该 envelope。
6. committed 或 exact already-applied 后 acknowledge source outbox。
7. acknowledge 失败则下次重放，AppStore 命令必须幂等。
8. 发布完整 media-library/projection 视图。

跨 MediaStore、TransferStore 和 AppStore 不使用伪原子双写；outbox 就是唯一正确的事务 seam。

### 8.4 Truthful projection

每个 library entry 至少独立保存：

- source local state、tree locator、inventory digest、provenance；
- current card presence observation；
- derived local state、profile revision、derived revision、validation evidence；
- upload bundle state、storage profile identity、remote receipt；
- source archival proof（V1 始终 absent）；
- retention policy/evaluation（V1 始终 disabled/blocked）。

不得继续使用单个 `uploaded: bool` 或 `backedUp: bool` 表示这条 pipeline。

### 8.5 Sealed artifact resolution

normalizer/upload 不接收任意 absolute path。实现 resolver：

```text
logical revision + LocalArtifactRef
  -> read immutable receipt
  -> validate expected tree locator
  -> acquire shared revision lease
  -> open no-follow regular file below current library root
  -> verify size/hash evidence at required boundary
  -> return reader + lease guard
```

source resolver只解析 source tree，derived resolver 只解析 derived tree。任何 path escaping、receipt/tree
不一致或 fencing token 过期都返回 typed integrity/storage error。

---

## 9. Codec probe 与 normalization

### 9.1 Capability gate

Ubuntu production composition 在启动 recovery 前构造 `FfmpegMediaNormalizer`：

1. 解析显式配置的 `ffmpeg`/`ffprobe` executable path，默认使用发行包声明的系统路径。
2. 禁止通过 shell 启动。
3. 有界执行 `ffmpeg -version`、encoder/filter capability inspection。
4. 确认 `libx265`、`libvmaf`、`ssim` 可用。
5. 记录 exact encoder build fingerprint 和 compatibility class。
6. 从 `ApprovedProfileRegistry` 加载与 build compatible 的已批准 profile。
7. 任一硬门禁失败，normalizer port 返回稳定 `encoder_unavailable`；import 仍可使用。

不得静默改用 VAAPI、NVENC、其他 HEVC encoder、H.264 copy 或不同 CRF。新 encoder 是新 profile/
compatibility qualification，不是运行时 fallback。

### 9.2 输入矩阵

| Source schema                              | 本地媒体形态                                   | Probe 必须确认                                   | Normalization plan                                |
| ------------------------------------------ | ---------------------------------------------- | ------------------------------------------------ | ------------------------------------------------- |
| `RawCaptureV2`                             | 单个 side-by-side MJPEG elementary stream      | MJPEG、geometry、fps、frame count                | 按 frame slice crop left/right，输出配对 segments |
| `LegacyMjpegSessionV5`                     | side-by-side MJPEG/fMP4 segments，PTS 每段重置 | 每段 MJPEG、序号、帧数、duration                 | 用 `frames.jsonl` 累积 timeline 后 crop/encode    |
| `ApplianceSpoolV6`/`CompleteUnpublishedV6` | side-by-side MJPEG/MP4 segments                | capture commit、segment inventory、MJPEG         | 按 segment crop/encode                            |
| `SignedPublicationV1`                      | paired left/right H.264 MP4 segments           | H.264、paired ordering、geometry/fps/frame count | 两眼独立 decode/re-encode，保持 pair 对齐         |

真实 probe 与 schema claim 不一致时 fail closed。未知 codec/profile、多 video track、audio track、可变
frame rate、geometry 改变、segment gap 或时长证据冲突均不能猜测修复。

### 9.3 Probe 安全合同

- 仅 probe sealed PC source artifact。
- `ffprobe` argv 每个参数独立构造，不经 shell。
- stdout JSON 上限沿用 16 MiB；stderr 保留上限 64 KiB；对外诊断上限 1,024 bytes。
- process 有 capability deadline、operation deadline、poll interval、terminate grace 和 kill/reap。
- 解析 structured JSON，不从人类文本正则推断 codec。
- probe report 绑定 artifact id、size、SHA-256 和 source revision。
- probe 结果写入 DerivationJob snapshot 后才能进入 planning。

### 9.4 Profile family

当前 core 中的 HEVC V1 仅是 candidate：

```text
codec              HEVC Main
pixel format        yuv420p
container           MP4
sample entry        hvc1
eye geometry        preserve source eye dimensions
frame rate          preserve source CFR
GOP                 closed, 2 seconds
scene-cut keyframe  disabled
segment             approximately 30 seconds, pair aligned
audio               none
encoder             libx265 preset=slow compatibility class
MJPEG candidate     CRF 20
H.264 candidate     CRF 18
H.264 retry         CRF 16 as a separate profile revision
```

这些数值不得仅凭本文变成 production profile。`NormalizationProfile::require_approved()` 必须继续
阻止未批准 profile 创建 derivation。

### 9.5 Profile approval

`ApprovedProfileRegistry` 只加载携带以下 digest receipts 的 exact profile revision：

- representative quality corpus report；
- throughput、CPU、memory、disk working-set report；
- stereo/CV domain report；
- encoder distribution/legal review；
- playback compatibility report。

批准清单放在 PC 仓库的只读发行资源
`src-tauri/resources/media-profiles/approved_profiles.json`，并通过 `include_bytes!` 编入二进制；
它不是用户可编辑设置，也不从网络动态下载。每个 entry 包含完整 profile、五类 report digest、
批准时间和允许的 encoder compatibility class。构造 registry 时重新 canonicalize profile、计算
revision、核对 approval receipt 和 report digest 格式；任一不一致使该 entry unavailable。

完整 report 文件归档在版本化 release evidence 中，发行清单只保存 digest 和批准时间。任何
profile 参数、阈值、encoder compatibility 或 report 变化都必须产生新的清单 entry 和审查记录。

### 9.6 真实质量 evaluator

当前 `SegmentQualityAnalyzer for FfmpegMediaNormalizer` 故意返回 unavailable。必须替换为真实
`FfmpegQualityAnalyzer`，完成：

1. 对 left/right 分别运行 source-reference 对齐后的 VMAF NEG 与 SSIM。
2. 保存逐帧 VMAF，计算 mean 和 frame p01，使用 fixed-point 转换。
3. 执行经过算法 owner 批准的 stereo/CV domain evaluator。
4. 输出 canonical、bounded quality report，包含 tool/version/model/profile/input/output digests。
5. 将 report 写入 derived staging 并 fsync，`QualityEvidence.report_digest` 指向 exact bytes。
6. 将每眼 evidence 交给 core `SegmentValidationReport::evaluate`。

stereo/CV evaluator 的算法不得在实现时临时用布尔常量替代。若项目尚未提供该 evaluator 和
批准 corpus，normalization 可以保持 capability unavailable，但不能进入 `DerivedVerified` 或 upload。
这是发布硬门禁，不是可忽略的测试缺口。

### 9.7 DerivationJob

```text
queued
  -> waiting_for_source
  -> probing
  -> planning
  -> encoding
  -> validating
  -> committing
  -> derived_verified

active -> pausing -> paused
active -> cancelling -> cancelled
transient -> retry_wait
terminal validation/profile/integrity issue -> failed
```

自然键为 `(source_revision, profile_revision)`；exact spec 还绑定 source manifest digest、resolved
artifact inventory、encoder build 和 created profile evidence。不同 immutable input 是 conflict。

### 9.8 编码和 pair checkpoint

一个 left/right segment pair 是最小恢复单元：

1. 持有 source shared revision lease。
2. 清理未封存的当前 pair partial。
3. 根据 frozen plan 启动 encode process。
4. pause/cancel/shutdown 请求 terminate whole process tree，deadline 后 kill，再 wait/reap。
5. 生成 left/right partial 后运行质量 evaluator。
6. 运行 full output validation：codec/profile/container/sample entry/pixel format/geometry/fps/time base/
   frame count/duration/GOP/keyframes/track count/full decode。
7. 核对两眼 frame count、duration 和 keyframe alignment。
8. 计算每个输出 SHA-256。
9. atomic publish pair 并写 pair checkpoint + job snapshot 的同一事务。

已验证 pair 在重启后重读 evidence 并复用，不重复有损编码。exact profile 的质量失败是不可重试；
CRF 16 必须作为新 profile/new job 明确启动，不能在旧 job 内偷偷换参数。

### 9.9 Derived commit

所有 pair verified 后：

1. 重读每个 checkpoint 对应文件和 digest。
2. 构造 canonical `DerivedManifest`。
3. manifest 包含 source revision/manifest digest、profile、encoder build、output inventory、quality evidence。
4. 计算 `DerivedRevision`。
5. sync staging files、quality reports、manifest 和目录。
6. 获取 derived revision exclusive publication lease。
7. atomic rename 到 immutable derivative tree，sync parent。
8. 在 MediaStore 同一事务提交 terminal snapshot、derived receipt、completion outbox。
9. completion projector 更新 media library 后 acknowledge outbox。

---

## 10. Frozen bundle 与对象存储上传

### 10.1 V1 bundle 内容

默认仅上传 derived bundle：

- normalized left/right HEVC segments；
- `derived_manifest.json`；
- source manifest 或 unsigned local source manifest 的只读副本；
- signed inventory 中允许的 IMU/metadata，或 unsigned schema 对应的准入 metadata；
- PC provenance/validation/quality reports。

默认不包含 source MJPEG/H.264 video。`SourceArchivalPolicy::Disabled` 是硬约束；adapter 若看到
source video archive 对象必须拒绝。

### 10.2 Object namespace

```text
{configured-prefix}/{origin-identity}/{session-or-source-id}/{source-revision}/
  source/source_manifest.json
  derivatives/{profile-revision}/{derived-revision}/
    video/left_00000.mp4
    video/right_00000.mp4
    metadata/...
    reports/...
    derived_manifest.json
```

每个 raw segment 使用 `ObjectNamespace` opaque encoding。禁止把 display name、absolute path、`..`、
slash-containing id 或 credential 放入 key。`derived_manifest.json` 必须是 ordered bundle 最后一项。

### 10.3 Freeze

pipeline 只有在 `DerivedReceipt` 和 media-library derived local verified 一致后才 freeze：

1. 获取 derived shared revision lease。
2. 读取 derived manifest 和所有 local artifact evidence。
3. 根据 source provenance 决定是否需要 unsigned upload admission。
4. 生成每个 object 的 role、logical local ref、key、size、SHA-256、media type。
5. 固定 storage profile identity 和 source archival policy。
6. `FrozenUploadBundle::freeze` 计算 canonical `UploadBundleRevision`。

bundle 一旦进入 upload job 不得修改。storage prefix/bucket/endpoint 变化会产生新的
`StorageProfileIdentity` 和新的 upload natural key。

### 10.4 TransferStore migration

upload 高层状态继续由现有 `transfer_jobs`/`transfer_upload_activity`/completion outbox 拥有，不在
MediaStore 新建第二套 UploadJob。现有 `UploadJobSpec` 的自然键是旧
`(LibraryEntry key, publication revision)`，不能直接复用为 derived bundle 的自然键。TransferStore
v20 必须先为 upload subject 增加 typed discriminator：

```sql
ALTER TABLE transfer_upload_job_specs
    ADD COLUMN subject_kind TEXT NOT NULL DEFAULT 'library_publication'
    CHECK (subject_kind IN ('library_publication', 'derived_bundle'));

ALTER TABLE transfer_upload_job_specs
    ADD COLUMN storage_profile_identity TEXT
    CHECK (storage_profile_identity IS NULL OR length(storage_profile_identity) > 0);

CREATE INDEX transfer_derived_upload_natural_key
    ON transfer_upload_job_specs (revision, storage_profile_identity)
    WHERE subject_kind = 'derived_bundle';

CREATE UNIQUE INDEX transfer_upload_subject_context
    ON transfer_upload_job_specs (job_id, revision, storage_profile_identity);

CREATE TRIGGER transfer_upload_subject_insert_guard
BEFORE INSERT ON transfer_upload_job_specs
WHEN (NEW.subject_kind = 'derived_bundle' AND NEW.storage_profile_identity IS NULL)
  OR (NEW.subject_kind = 'library_publication' AND NEW.storage_profile_identity IS NOT NULL)
BEGIN
    SELECT RAISE(ABORT, 'upload subject/storage profile mismatch');
END;

CREATE TRIGGER transfer_upload_subject_update_guard
BEFORE UPDATE OF subject_kind, storage_profile_identity ON transfer_upload_job_specs
WHEN (NEW.subject_kind = 'derived_bundle' AND NEW.storage_profile_identity IS NULL)
  OR (NEW.subject_kind = 'library_publication' AND NEW.storage_profile_identity IS NOT NULL)
BEGIN
    SELECT RAISE(ABORT, 'upload subject/storage profile mismatch');
END;
```

对应 core persistence type 使用 tagged `UploadSubject`，而不是在 `entry_key` 中拼接 storage identity：

```text
LibraryPublication {
  entry_key,
  publication_revision
}

DerivedBundle {
  media_library_entry_key,
  upload_bundle_revision,
  storage_profile_identity
}
```

legacy create/retry query 继续按 `(entry_key, revision)` 查询 `library_publication`；derived create/retry
query 只按 `(revision=upload_bundle_revision, storage_profile_identity)` 查询 `derived_bundle`。
`entry_key` 对 derived row 仍保存真实 media-library context，但不参与其自然键。

自然键索引不能做成全历史唯一：TransferStore 的 explicit retry 会为同一 immutable input 创建
parent/child attempts。initial create 和 retry 必须使用 `BEGIN IMMEDIATE`，在同一事务中 join
`transfer_jobs` 查询该自然键的 active attempt；有 active exact attempt 时返回 Existing，有 active
conflicting evidence 时返回 Conflict，只有旧 attempt terminal 且调用的是 explicit retry 时才创建
新 child。这样同一自然键最多一个 active attempt，同时保留完整 terminal history。

随后增加 frozen bundle/checkpoint sidecar：

```sql
CREATE TABLE transfer_derived_upload_jobs (
    job_id                    TEXT PRIMARY KEY
                              REFERENCES transfer_jobs(job_id) ON DELETE CASCADE,
    media_library_entry_key   TEXT NOT NULL,
    upload_bundle_revision    TEXT NOT NULL,
    storage_profile_identity  TEXT NOT NULL,
    frozen_bundle_json        TEXT NOT NULL CHECK (json_valid(frozen_bundle_json)),
    checkpoint_json           TEXT NOT NULL CHECK (json_valid(checkpoint_json)),
    checkpoint_version        INTEGER NOT NULL CHECK (checkpoint_version >= 1),
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL,
    FOREIGN KEY (job_id, upload_bundle_revision, storage_profile_identity)
        REFERENCES transfer_upload_job_specs (job_id, revision, storage_profile_identity)
);
```

创建 derived upload job 的 store 方法必须在一个事务中写：

- `transfer_jobs(operation_kind='upload')`；
- typed immutable upload spec/activity；
- frozen bundle；
- `DerivedUploadAdapter::checkpoint_for` 的初始 checkpoint。

同一 natural key + exact active bundle 返回 Existing；same natural key + changed bundle 返回 conflict；
terminal failed/cancelled 只能通过 explicit retry lineage 创建新 attempt。
retry child 复用 parent 的 immutable frozen bundle/storage identity，但必须从
`checkpoint_for(bundle)` 创建 fresh checkpoint，不复制 parent multipart handles、parts、verified
receipts 或 activity progress。成功 terminal parent 直接复用其 completion-bound receipt，不创建 retry。
现有 `transfer_upload_receipts` 可继续保存长期 object receipt：`DerivedMedia` 映射为 `Data`，其余
manifest、metadata 和 report 映射为 `Evidence`；完整细粒度 role 仍由 frozen bundle 提供，不能从
这两个 persistence role 反推。

### 10.5 Upload worker

新增 `DerivedUploadExecutor`，使用现有：

- `DerivedUploadAdapter`；
- `ObjectStorePort`/`S3ObjectStore`；
- `LocalArtifactSource`；
- `UploadCheckpointSink`；
- `TransferStore` upload job/outbox。

执行顺序：

1. 加载 latest job、frozen bundle 和 checkpoint。
2. 验证 storage profile identity 与当前非 secret config 一致；credential 仅从 keyring 获取。
3. 获取 derived shared revision lease。
4. 对每个 pending object 打开 logical artifact，先核对 local size/hash。
5. multipart initiate 成功后先 durable handle，再上传 part。
6. 每个 part 成功后 durable part ETag/size，再继续。
7. complete 响应和 version/ETag 先 durable 为 `Completed`。
8. 对 exact completion 执行 server checksum 或 streamed readback SHA-256。
9. verification receipt durable 为 `Verified`。
10. 所有 data/evidence object verified 后才处理最终 manifest。
11. 用 frozen bundle + exact receipts 构造 `DerivedUploadReceipt`。
12. 将 verified receipts 持久化并完成 upload job + completion outbox。
13. completion projector 写 remote verified 后 acknowledge。

part size 默认 8 MiB，允许范围 5-128 MiB。不能为了性能在运行中改变已开始 job 的 part size。

### 10.6 LocalArtifactSource

`SealedDerivedArtifactSource` 根据 `LocalArtifactRef` 解析文件：

- 不接受 caller path；
- 必须位于 receipt 指向的 immutable derived tree；
- 持有 shared lease 直到 reader drop；
- open no-follow regular file；
- streaming 时重新计算 SHA-256；
- local evidence mismatch 时停止并尝试 abort 当前 multipart；
- abort 失败保留 handle/checkpoint，返回包含 cleanup 状态的 typed failure。

### 10.7 Remote verification

- 优先使用对象存储提供并被 adapter 可信解析的 full-object SHA-256。
- 缺少可信 checksum 时执行 streamed GET/readback hash。
- HEAD 只能验证 size/metadata，不能单独证明 bytes。
- verify 请求必须绑定 complete 返回的 version id/ETag；bucket 开启 versioning 时必须读取 exact version。
- complete 响应不明时保留 ambiguous state，先按持久化 handle/version 进行恢复探测，不能直接创建
  同 key 新 multipart 覆盖。
- 任一对象 digest mismatch，bundle 不得 remote verified。

### 10.8 Unsigned upload approval

unsigned source 在 freeze 前创建 `UnsignedUploadAdmissionRequest`，UI 显示 source identity/revision、
目标 bucket/profile 和“derived only”。用户批准生成 receipt，至少绑定：

```text
source_revision
upload_bundle_revision or deterministic pre-freeze request digest
storage_profile_identity
source_archival=disabled
policy_version
approved_at
```

receipt mismatch、过期 policy 或 destination 变化都需要重新批准。approval error 不可自动重试。

### 10.9 Retention

本版本固定：

```text
SourceArchivalPolicy::Disabled
SourceRetentionPolicy::Disabled
```

即使 derived bundle remote verified，也只更新 derived remote state，不生成 source-removal proposal。
卡上数据永远不由本应用删除。

---

## 11. SessionPipeline orchestration

### 11.1 支持 policy

使用 core 已有的 typed policy：

- `ImportOnly`
- `AutoNormalize { profile_revision }`
- `AutoUpload { profile_revision, storage_profile_identity, source_archival=Disabled, source_retention=Disabled }`

删除 Ubuntu production 中“所有非 ImportOnly 都直接 encoder unavailable”的硬拒绝；改为基于真实
capability/profile/storage 检查。若 capability 缺失，在创建 import 前返回明确错误；已经 durable
的旧 pipeline 则保留 required action，不能被丢弃。

### 11.2 Dependency attachment

pipeline 自身只保存 dependency reference：

```text
import job id
derivation natural key + job id
upload natural key + job id
```

不要复制 child snapshot。projection 每次从三个权威 store 读取 child state并组合 source/derived/
remote layer。

### 11.3 推进算法

```text
pipeline start
  -> atomic create import + pipeline
  -> enqueue import

import LocalVerified
  -> consume import outbox
  -> project source local verified
  -> if policy requires: build exact DerivationSpec
  -> create/existing derivation
  -> attach dependency by pipeline CAS
  -> enqueue derivation

derivation DerivedVerified
  -> consume derivation outbox
  -> project derived local verified
  -> if AutoUpload: freeze bundle
  -> create/existing TransferStore upload job
  -> attach dependency by pipeline CAS
  -> enqueue upload

upload remote verified
  -> consume upload outbox
  -> project exact RemoteBundleReceipt
  -> pipeline remote layer object_store_verified
```

### 11.4 跨 store crash 顺序

MediaStore 与 TransferStore 无法做一个 SQLite transaction。创建 upload 的顺序固定为：

1. deterministic freeze bundle；
2. idempotent create/existing TransferStore job；
3. pipeline CAS attach dependency；
4. 只有 attach 成功后 enqueue remote work。

若在 2 和 3 之间崩溃，restart replay 用 natural key 找到 existing job 并 attach。未被 pipeline claim
的 queued derived upload job不能自行开始 remote mutation。

### 11.5 Recovery replay matrix

| Durable evidence                                           | Recovery action                              |
| ---------------------------------------------------------- | -------------------------------------------- |
| import dependency active                                   | enqueue import                               |
| import verified、无 derivation dependency、policy requires | create/attach/enqueue derivation             |
| derivation active                                          | enqueue derivation                           |
| derived verified、无 upload dependency、AutoUpload         | freeze/create/attach/enqueue upload          |
| upload active且 dependency 已 attach                       | enqueue upload                               |
| upload terminal success、library projection缺失            | replay completion projector                  |
| child terminal failure                                     | 投影 typed failure，不自动创建不同参数 child |
| required approval missing                                  | 保存 action，等待用户命令                    |
| capability unavailable                                     | 保存 action/error，不伪造 active worker      |

### 11.6 Commands

- pipeline pause：按 dependency 顺序停止当前 active child，确认后更新 desired state。
- resume：重新检查 capability/root/storage，再提交 child resume 和 enqueue。
- cancel：停止 active child；已 local verified 的 immutable source 不删除。
- approve unsigned upload：只对匹配 request 生效，随后 freeze/create upload。
- retry：仅对 retryable exact job；quality/profile/integrity/approval failure 不自动重试。

---

## 12. Worker、并发与 lifecycle

### 12.1 Worker lanes

初始生产配置：

| Lane                 |                            并发 | 原因                                      |
| -------------------- | ------------------------------: | ----------------------------------------- |
| import               |                               1 | 避免 TF 和目标盘随机 I/O 争用             |
| derivation           |                               1 | libx265 CPU/内存密集                      |
| upload               | 1 bundle；单对象 multipart 顺序 | 先证明恢复正确性，再 benchmark 并发       |
| completion projector |                               1 | 保证 outbox sequence 和 AppStore CAS 收敛 |

后续提升并发必须由 benchmark 证明，不改变 job/interface 语义。

### 12.2 Wake queue

- `Mutex<QueueState> + Condvar` 或等价 bounded queue；
- queue 内按 job id 去重；
- durable job 是事实源，queue 丢失只影响唤醒；
- startup/reconnect/storage recovery 会重新枚举 active jobs；
- worker 每次 side effect 前 reload snapshot；
- projection callback 在 store commit 和锁释放后执行。

### 12.3 启动顺序

```text
1. load/migrate AppStore, TransferStore, MediaStore
2. build inert composition and capability report
3. register AppState, TransferApplication, MediaApplication
4. replay import/derivation/upload outboxes into media library
5. recover exact durable projections
6. construct worker lanes, but do not process unclaimed upload jobs
7. recover/enqueue active imports, derivations, attached uploads
8. start mounted-media watcher
9. publish one exact MediaProjectionSet
10. start ordinary background loops
```

watcher 不能在 store recovery 和 managed-state registration 前发事件。

### 12.4 停止顺序

```text
1. lifecycle state -> stopping; reject new start/resume
2. stop watcher and prevent new scan callbacks
3. stop accepting queue items
4. request import readers pause/shutdown
5. request normalizer shutdown; terminate/kill/reap FFmpeg trees
6. request upload readers/network operations stop at checkpoint boundary
7. wait active workers and completion projector
8. release media handles and revision leases
9. join every owned thread/task
10. lifecycle state -> stopped
```

`stop()` 必须可重试。若某资源在 deadline 内没有结束，返回 `resource_stuck` 并保留 handle，以便
第二次 stop 继续等待；不能 detach 后报告成功。

### 12.5 Suspend 与 sleep

Ubuntu 首版不自动实现全局 prevent-sleep，除非已有获审 adapter。系统 suspend 后：

- file/network I/O error 按 typed transient failure 处理；
- resume 时重新验证 media generation、library root、lease 和 storage profile；
- active FFmpeg process 若状态未知，必须确认已退出/reap，再恢复 pair；
- 不从内存 progress 推断 durable offset。

---

## 13. Tauri、wire contract 与 UI

### 13.1 Backend composition

`src-tauri/src/lib.rs` 的 Ubuntu wiring 应替换 unavailable normalizer，并注入：

- one shared `MediaStore`；
- one shared `TransferStore`；
- shared `AppStore` projection repository；
- trusted producer registry；
- `UbuntuMediaRuntime`；
- import executor + worker scheduler；
- FFmpeg probe/encoder + approved profile registry + quality analyzer；
- derivation repository/source leases/staging + worker scheduler；
- derived upload repository/artifact source/object store factory + worker scheduler；
- pipeline orchestrator；
- completion projector；
- lifecycle supervisor with real shutdown closures。

初始化失败按 capability 分层：scanner/import 能用时不得因 FFmpeg unavailable 关闭整个 media facade。

### 13.2 Stable commands

保留现有 command names。扩展行为而不是新增平行 RPC：

- `media_scan`
- `media_start_import`
- `media_start_pipeline`
- `media_start_pipeline_batch`
- `media_start_derivation`
- `media_command_import`
- `media_command_derivation`
- `media_command_pipeline`
- `media_release_handles`
- `media_eject`

如需要批准 unsigned upload，使用现有 typed `PipelineCommand::ApproveUnsignedUpload`，不要做通用
字符串 command。

### 13.3 Wire types

Rust/TypeScript decoder 同步扩展：

- capability summary：scan/import/normalize/upload；
- pipeline policy：import-only/auto-normalize/auto-upload；
- required action：pairing key、unsigned import approval、unsigned upload approval、profile/storage unavailable；
- source/derived/remote 三层状态；
- upload bundle id/job id/progress；
- stable error code、retryable、bounded details。

所有 `u64` 进 WebView 前检查 `Number.MAX_SAFE_INTEGER`。超限返回明确 wire projection error，不静默
round。列表更新继续使用 complete replacement + source version，不能发送 progress patch。

### 13.4 UI workflow

Ubuntu media screen 的主流程：

```text
检测 TF 卡
  -> 展示 candidate、schema、trust/readiness 和逐项原因
  -> 用户选择策略：仅导入 / 导入并处理 / 导入、处理并上传
  -> unsigned import 显式批准
  -> 本地导入进度
  -> LocalVerified 后提示应用已可释放卡句柄
  -> 视频处理和 validation 独立进度
  -> unsigned upload 独立批准
  -> upload 和 remote verification 独立进度
  -> 展示 source local / derived local / remote verified 三层结果
```

不能合成一个虚假的端到端百分比。每层显示当前文件/pair/object、bytes/frames、throughput、ETA 和
typed failure。只有真实 capability 可用时才允许选择对应 policy。

### 13.5 默认策略

- 自动 scan：允许 watcher 触发，但 scan 本身只读且 bounded。
- 自动 import：关闭。
- 默认 action：用户明确选择；不从历史设置静默批准 unsigned。
- normalization：只在 approved profile capability ready 时可选。
- derived upload：只在 storage configured 且 normalization 可用时可选。
- source upload、PC source auto-delete、card delete：隐藏并保持 disabled。

---

## 14. Error 与重试合同

| 场景                             | Code/状态                               | Retryable | 恢复动作                      |
| -------------------------------- | --------------------------------------- | --------: | ----------------------------- |
| removable evidence unknown/no    | candidate 不出现或 scan diagnostic      |        否 | 修复 OS/reader detection      |
| signed fingerprint 未配对        | `policy_approval_required`              |        否 | 完成 PC pairing               |
| signature/key/inventory mismatch | `integrity_failed`                      |        否 | 拒绝 source，重新取得可信数据 |
| unsigned 未批准                  | `policy_approval_required`              |        否 | 用户明确批准                  |
| 卡拔出                           | `waiting_for_media`                     |        是 | 插回原卡、重新准入            |
| destination 在 removable volume  | `storage_not_configured`                |        否 | 选择 PC 内部目标盘            |
| 空间不足                         | `insufficient_local_space`              |        是 | 释放空间/选择新 root          |
| source bytes/hash mismatch       | `integrity_failed`                      |        否 | 重新扫描或更换 source         |
| FFmpeg/ffprobe/libx265 缺失      | `encoder_unavailable`                   |        否 | 安装获支持 build              |
| profile 未批准                   | `encoder_unavailable` + required action |        否 | 完成 profile approval         |
| process timeout但已 reap         | derivation failed/retry_wait            |  视 stage | exact profile retry           |
| process 无法 reap                | `resource_stuck`                        |        是 | retry stop/人工结束资源       |
| quality below threshold          | validation failed                       |        否 | 新 approved profile/job       |
| S3 transient network/5xx         | upload retry_wait                       |        是 | 从 durable checkpoint 恢复    |
| credential 缺失/拒绝             | storage not configured/auth failure     |        否 | 更新 credential               |
| multipart completion ambiguous   | upload recovery-required                |        是 | completion-bound reconcile    |
| remote digest mismatch           | `remote_verification_failed`            |        否 | 保留 local bytes，调查 remote |
| unsigned upload 未批准           | `policy_approval_required`              |        否 | 用户独立批准                  |
| AppStore CAS conflict            | internal bounded retry                  |        是 | reload/recompute CAS          |

错误 message 和 details 必须 bounded、无 secret、无未清洗 native stderr。代码用于逻辑，文本只用于人读。

---

## 15. 安全要求

### 15.1 不可信输入

- volume metadata、mount path、directory entry、JSON、manifest、media bytes、ffprobe output、object-store
  response 全部视为不可信。
- 所有 schema 有 major version 和大小上限。
- serde 解析后继续执行 domain validation，不能只以 JSON parse 成功为准。
- manifest 内 public key 只用于验证 exact signature；是否可信由 PC registry 决定。

### 15.2 Process

- FFmpeg/ffprobe 不经 shell。
- executable path 来自受控配置/发行包，不来自 card manifest。
- argv 中不使用未验证 absolute source path；resolver 提供 sealed local path。
- stdout/stderr 有界持续 drain，防止 child pipe deadlock。
- pause/cancel/shutdown terminate whole process tree并 reap。
- 日志不输出完整用户 path 或媒体 metadata。

### 15.3 Object storage

- access/secret key 只在 OS credential vault。
- `StorageProfileIdentity` 不含 credential，但包含所有影响 destination 语义的非 secret 配置。
- endpoint scheme、URL style、bucket、prefix 在创建 job 前规范化并冻结。
- 禁止 object key path injection。
- remote metadata 不能替代真实 byte digest。

### 15.4 权限

- 应用不要求 root。
- 只读取 OS 已允许访问的 mount。
- UDisks eject permission failure是正常 typed outcome。
- 不自动更改 mount options。
- 若用户要求物理只读保证，应由 OS 只读挂载或硬件写保护提供；应用只保证自身不写卡。

---

## 16. Observability 与审计

### 16.1 Structured events

每个 job transition 记录：

```text
timestamp
job_id / pipeline_id
stage
from_state / to_state
source_revision / derived_revision / bundle_revision（存在时）
media_generation_id（仅 import）
attempt/retry count
bytes/frames/object ordinal
error_code
retryable
```

不得记录 credential、signature payload、完整 key material、未经清洗的 stderr 或用户 absolute path。

### 16.2 Metrics

- scan duration、candidate count、truncation count；
- import queue depth、copied bytes/s、checkpoint latency、waiting-for-media count；
- encode fps、pair duration、quality gate failures、process reap duration；
- upload bytes/s、part retries、readback duration、remote mismatch count；
- outbox oldest pending age、CAS retry count；
- shutdown duration、resource-stuck count。

metrics 是诊断，不参与 durable correctness decision。

### 16.3 Release evidence

每次 Ubuntu release 保存：

- app commit SHA、Cargo.lock/package lock digest；
- Ubuntu image/version/kernel；
- FFmpeg/ffprobe/libx265 build fingerprint；
- approved profile revision 和五类 approval report digest；
- fixture/corpus revision；
- MinIO/S3 contract run；
- 真卡 reader/filesystem/容量矩阵；
- fault-injection 和长时运行结果。

---

## 17. Persistence migration 计划

### 17.1 MediaStore

追加 v7，不修改已发布 SQL：

- trusted producer keys/rotation/revocation；
- quality report 作为 immutable derived tree artifact，并由 derived manifest/receipt 绑定 digest，
  不再创建第二份可漂移的 quality 数据库事实源；
- 保留 import/derivation/pipeline collection revisions；
- 不在 MediaStore 新建 upload aggregate。

### 17.2 AppStore

- 追加 v3，新增 `app_media_library_entries`；
- global revision 与 media entry CAS 同事务；
- boot snapshot 增加独立 media library payload，不让旧 `LibraryEntry` decoder读取新 payload；
- forward migration 不把旧 LAN entry伪装成 media projection；后续可通过真实 receipt rebuild。

### 17.3 TransferStore

- 追加 v20，增加 typed upload subject、derived natural-key lookup index 和 serialized active-attempt fence；
- 新增 `transfer_derived_upload_jobs` frozen bundle/checkpoint sidecar；
- derived create 方法原子写 job/spec/activity/sidecar；
- checkpoint version CAS，防止两个 worker 覆盖；
- verified object receipts 继续使用 immutable receipt rows或由 checkpoint 重建后原子 stage；
- terminal upload state、activity 和 completion outbox 同事务。

### 17.4 Migration 安全

- `PRAGMA integrity_check`、WAL、`synchronous=FULL`、foreign keys 保持开启。
- migration checksum/history 只追加。
- invalid existing row fail closed并报告具体 table，不做 lossful repair。
- 发布升级前备份三个 SQLite 文件；旧二进制不保证能读取新 schema。
- 回滚应用时同时恢复升级前 DB 备份，不能只替换 executable。

---

## 18. 文件级实施映射

| 路径                                                                  | 主要改动                                                               |
| --------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `src-tauri/crates/ylx-transfer-core/src/ingest/source.rs`             | 保持 typed signed/unsigned constructors；必要时增加 trust receipt 类型 |
| `src-tauri/crates/ylx-transfer-core/src/media_store/schema.rs`        | MediaStore v7 trusted producer migration                               |
| `src-tauri/crates/ylx-transfer-core/src/media_store/*`                | registry、receipt/outbox 查询与 ack                                    |
| `src-tauri/crates/ylx-transfer-core/src/media_library/*`              | 保持三层 projection，补 repository contract tests                      |
| `src-tauri/crates/ylx-transfer-core/src/media_normalizer/*`           | 复用 executor/store/lease，补 shutdown/quality recovery contract       |
| `src-tauri/crates/ylx-transfer-core/src/media_pipeline/*`             | 复用 policy、bundle、replay，补 production replay cases                |
| `src-tauri/crates/ylx-transfer-core/src/persistence/schema.rs`        | TransferStore v20 upload subject/sidecar migration                     |
| `src-tauri/crates/ylx-transfer-core/src/persistence/app_store.rs`     | media projection CAS repository support                                |
| `src-tauri/crates/ylx-transfer-core/src/persistence/upload_store.rs`  | derived upload job/checkpoint/outbox support                           |
| `src-tauri/resources/media-profiles/approved_profiles.json`（新增）   | 编入二进制的只读 approved profile/receipt 清单                         |
| `src-tauri/crates/ylx-transfer-adapters/src/removable_media/linux.rs` | removable evidence contract，不扩大平台范围                            |
| `src-tauri/crates/ylx-transfer-adapters/src/mounted_file.rs`          | 复用 no-follow/generation reader                                       |
| `src-tauri/crates/ylx-transfer-adapters/src/publication_verifier.rs`  | 复用 Ed25519 verifier；不建立第二套 trust 逻辑                         |
| `src-tauri/crates/ylx-transfer-adapters/src/media_normalizer.rs`      | 实现真实 quality analyzer/report archive，保留 process safety          |
| `src-tauri/crates/ylx-transfer-adapters/src/derived_upload.rs`        | 复用 frozen-bundle executor，必要时补 cancel/recovery hooks            |
| `src-tauri/crates/ylx-transfer-adapters/src/object_store_s3.rs`       | 仅补 contract 所需行为，不复制 client                                  |
| `src-tauri/src/media/ubuntu.rs`                                       | removable filter、trusted signed admission、destination/root lease     |
| `src-tauri/src/media/ubuntu_ingestor.rs`                              | 移除同步 drain，连接 import worker                                     |
| `src-tauri/src/media/ubuntu_normalizer.rs`                            | 用真实 production port 替换 unavailable stub                           |
| `src-tauri/src/media/ubuntu_pipeline.rs`                              | 激活 typed policies、attach/replay derivation/upload                   |
| `src-tauri/src/media/ubuntu_projection.rs`                            | core-to-wire 和三层 library projection                                 |
| `src-tauri/src/media/ubuntu_lifecycle.rs`                             | 注入真实 worker shutdown/recovery owners                               |
| `src-tauri/src/media/ubuntu_workers.rs`（新增）                       | import/derivation/upload/projector worker ownership 与 join            |
| `src-tauri/src/media/ubuntu_uploader.rs`（新增）                      | TransferStore/DerivedUpload/ObjectStore composition                    |
| `src-tauri/src/composition.rs`                                        | shared stores、pairing trust write、storage/object-store factory       |
| `src-tauri/src/state.rs`                                              | shared AppStore/media library boot projection                          |
| `src-tauri/src/lib.rs`                                                | 完整 Ubuntu media composition 和启动/停止顺序                          |
| `src/runtime/media/*`                                                 | strict DTO decoder、policy、commands、projection reducer               |
| `src/ui/media/*`                                                      | policy controls、approval、三层状态、capability/error UX               |
| `src/app/transferApp.ts`                                              | 不再固定 import-only；提交用户选择的 typed policy                      |

若实施中发现某文件需要承担两个无关 owner，应新建专用 module，不继续扩大 `composition.rs` 或
`ubuntu_pipeline.rs` 的职责。

---

## 19. 实施批次与依赖 DAG

### Batch 0：合同与 fixtures

目标：冻结输入和 release gate，禁止后续用假数据完成 production path。

- 收集四类最小脱敏 source fixtures和 corrupt/unknown variants。
- 冻结 signed card envelope 与 LAN publication 的共享 contract。
- 冻结 Ubuntu removable eligibility、destination guard 和 scan limits。
- 建立代表性 codec/quality corpus 清单。
- 冻结 storage profile identity canonicalization。

完成门槛：fixtures 有 provenance、来源 commit、expected verdict 和 digest；未知 v1-v4 fail closed。

### Batch 1：PC trusted signed admission

- MediaStore trust migration/repository。
- pairing success/re-pair/revoke 持久化。
- `UbuntuMediaRuntime` signed admission。
- removable `Yes` eligibility filter。
- signed/unsigned/readiness wire projection。

完成门槛：Pi 离线时，已重新配对 fingerprint 的真实 signed card 可准入；未配对/rotated/bad signature
全部 fail closed；没有 Pi 代码提交。

### Batch 2：后台 import 与 root authority

- `LibraryRootAuthority` shared/exclusive lease。
- destination guard 每阶段 revalidate。
- import wake queue/worker/recovery/shutdown。
- command 从同步 drain 改为 durable create + enqueue。
- pause/cancel/remove/reinsert semantics。

完成门槛：大文件复制期间 command 可响应；exit 后无 reader/thread；不同卡同 mount path 不续传。

### Batch 3：media library/outbox projection

- AppStore media table/CAS repository。
- import/derivation completion projector。
- source/derived artifact resolver和 leases。
- UI 可见 source local verified，不生成 legacy `LibraryEntry`。

完成门槛：crash 在 AppStore commit/ack 任一侧均可幂等重放；`pending_import_completions` 最终清空。

### Batch 4：normalizer qualification 与 production wiring

- FFmpeg capability/build fingerprint。
- approved profile registry。
- real VMAF/SSIM/stereo-CV analyzer和 report archive。
- derivation repository/staging/lease/scheduler/worker composition。
- Tauri commands/projections/pause/cancel/recover。

完成门槛：四类输入在批准 profile 下生成 `DerivedVerified`；所有结构、full decode、quality gate 通过；
未批准 profile继续不可用。

### Batch 5：derived upload

- TransferStore derived upload migration/repository。
- frozen bundle builder和 unsigned upload receipt。
- sealed derived artifact source/checkpoint sink。
- derived upload worker、completion-bound verification、outbox projection。
- pipeline attach/replay upload dependency。

完成门槛：MinIO/AWS-compatible contract 下完成、崩溃续传和 exact remote digest；derived receipt 不被
投影成 source backup。

### Batch 6：完整 pipeline 与 UI

- 激活 `AutoNormalize`/`AutoUpload` policy。
- required action和 capability UX。
- batch outcome、三层状态、独立进度。
- startup/shutdown全图接线。
- storage/library root change conflict UX。

完成门槛：用户从插卡到 remote verified 不进入 legacy upload入口，重启后状态和动作完全恢复。

### Batch 7：Ubuntu HITL、CI 与 release evidence

- contract/integration/fault tests。
- 真卡、真 reader、拔卡、重插、断电/kill、空间不足。
- FFmpeg quality/throughput corpus。
- MinIO和至少一个生产目标 smoke test。
- Ubuntu packaging/dependency/legal/playback evidence。

完成门槛：第 21 节全部 acceptance 项有可追溯证据。

依赖关系：

```text
Batch 0
  -> Batch 1
  -> Batch 2
  -> Batch 3
       -> Batch 4
            -> Batch 5
                 -> Batch 6
                      -> Batch 7
```

Batch 1 和 Batch 2 的内部开发可并行，但合并验收必须在 Batch 3 前完成。normalization 和 upload
不得绕过 media-library receipt/outbox seam 直接读取 import staging。

---

## 20. 测试与验证计划

### 20.1 Core unit/contract

- scan bounds、path traversal、link/special file、unknown schema。
- trust registry active/revoke/rotate/idempotent migration。
- signed exact envelope、fingerprint、signature、inventory mismatch。
- unsigned approval与重插 re-approval。
- import natural key、checkpoint、CAS、terminal outbox。
- library projector immutable evidence/CAS/replay。
- normalization plan四类输入、profile/build conflict、pair ledger。
- quality fixed-point/threshold/report digest。
- frozen bundle ordering、roles、namespace、unsigned admission。
- pipeline restart replay matrix。
- derived upload natural key/storage identity。

### 20.2 Adapter integration

- UDisks2/sysfs/mountinfo removable evidence merge。
- mounted-file no-follow、inode swap、truncate/replace、generation change。
- FFmpeg argv、probe JSON bound、stderr drain、timeout、terminate/kill/reap。
- quality report parsing、corrupt/oversize report。
- object store multipart initiate/part/complete/abort/readback。
- ambiguous complete、versioned object、concurrent same-key overwrite。
- keyring unavailable/locked/credential rotation。

### 20.3 Persistence crash points

至少注入：

- import file write 后、checkpoint 前；
- final file hash 后、source rename 前；
- source rename 后、terminal transaction 前；
- import outbox 后、AppStore projection 前/后、ack 前；
- encode partial 后、quality report 前；
- pair publish 后、pair checkpoint 前；
- derived rename 后、terminal transaction 前；
- bundle freeze 后、upload job create 前/后、pipeline attach 前；
- multipart initiate 后、handle checkpoint 前；
- part成功后、part checkpoint 前；
- complete响应后、Completed checkpoint 前；
- verification 后、receipt/job terminal/outbox 各边界；
- AppStore remote projection 后、upload outbox ack 前。

每个 crash point 都要求 restart 收敛，且不产生假的 terminal success。

### 20.4 Codec fixtures

- RawCaptureV2：30/60 fps、坏 JPEG boundary、frame gap、timestamp error。
- v5：side-by-side、PTS reset、末段、序号 gap、frames index mismatch。
- spool v6：完整、缺段、重复段、open tail、capture commit conflict。
- signed v1：paired H.264、left/right gap、geometry/fps/frame mismatch、坏 signature/hash。
- unknown codec、VFR、extra track、audio、truncated MP4、decode failure。

### 20.5 Quality corpus

corpus 必须覆盖真实场景：低纹理、强运动、曝光变化、重复纹理、近/远景、左右目亮度差、IMU 高频
运动、长时录制和编码后下游 CV workload。至少比较：

- source 与 candidate derivative 的 VMAF mean/frame p01、SSIM；
- stereo/CV domain metrics；
- frame/timestamp/keyframe alignment；
- MJPEG 和 H.264 两种 generation 的体积比；
- encode fps、CPU、峰值内存、临时磁盘；
- target player/decoder compatibility。

CRF 20/18/16 在 report 批准前都只是候选。

### 20.6 Ubuntu 真卡 HITL

| 场景                                   | 预期                                                                                                       |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| 未挂载 removable TF 插入               | 仅在 removable/non-system/filesystem gate 通过后请求 UDisks2 attach；拒绝或需授权时返回 typed access issue |
| Ubuntu Core nested recordings          | 只扫描固定 nested container direct children                                                                |
| ext4 TF 正常插入                       | 只出现 fixed-root candidates                                                                               |
| 第二块内部 ext4 SSD                    | 不作为 removable candidate                                                                                 |
| removable hint unknown                 | fail closed diagnostic                                                                                     |
| fixed container 权限拒绝               | 显示 access issue，不显示空卡                                                                              |
| library root 位于当前 TF               | preflight 拒绝且零写入                                                                                     |
| library root 位于另一 removable disk   | preflight 拒绝                                                                                             |
| scan/copy 中拔卡                       | 无崩溃；waiting_for_media；句柄释放                                                                        |
| 同 mount path 插入另一卡               | 不续传                                                                                                     |
| 原 signed 卡重插                       | exact revision 后恢复                                                                                      |
| 原 unsigned 卡重插                     | 要求重新批准                                                                                               |
| >4 GiB 文件和长 session                | 无 `u32`/wire rounding，checkpoint恢复                                                                     |
| 磁盘只读/满/inode耗尽                  | typed failure，无 local_verified                                                                           |
| app kill/restart                       | 从 durable evidence 收敛                                                                                   |
| pause/cancel during copy/encode/upload | 在规定 deadline 确认或 resource_stuck                                                                      |
| release/eject                          | 无 active reader；UDisks outcome truthful                                                                  |

### 20.7 对象存储 E2E

- MinIO path-style；
- production virtual-host style target；
- 5xx、timeout、connection reset；
- credentials失效与更新；
- multipart restart；
- bucket versioning on/off；
- server checksum present/absent；
- 同 key 并发覆盖；
- readback digest mismatch；
- final manifest last可见性。

### 20.8 前端 contract

- Rust fixture与 strict TypeScript decoder 一致。
- stale source version 不覆盖新 projection。
- batch per-item failure与 operation error分离。
- signed waiting-for-key、unsigned两次 approval、capability unavailable。
- import/derivation/upload pause/cancel/retry eligibility。
- source/derived/remote状态不互相冒充。
- long id/error/message 在所有 Ubuntu viewport不溢出。

### 20.9 建议验证命令

实施完成后按风险由窄到宽执行；本文创建时没有运行这些命令：

```bash
cd src-tauri
cargo test -p ylx-transfer-core
cargo test -p ylx-transfer-adapters
cargo test -p ylx-transfer
cargo check --workspace

cd ..
npm test
npm run typecheck
npm run lint
npm run format:check
npm run build
```

随后运行 MinIO integration、Ubuntu真卡HITL和 release build。单元测试通过不能替代真卡和真实 codec
corpus。

---

## 21. 最终验收清单

### 21.1 Discovery/admission

- [ ] 只认证 Ubuntu 24.04 LTS x86_64。
- [ ] 只处理已挂载或经 UDisks2 受限 attach 后重新枚举的 allowlisted、`removable=Yes` volume。
- [ ] 内部 SSD 和 unknown removable evidence 不进入 TF candidate。
- [ ] 三个 fixed containers、direct children/bounds/no-follow 全部生效。
- [ ] attach refusal、authorization required 和 per-card access issue 有 bounded typed projection。
- [ ] paired signed card 在 Pi 离线时通过；unpaired/rotated/bad signature fail closed。
- [ ] unsigned raw/v5/v6/publication import 明确批准且 provenance truthful；half-present signature pair fail closed。

### 21.2 Local import

- [ ] destination guard 覆盖 create/copy/verify/commit/recovery。
- [ ] library-root switch 与 active job有 lease fence。
- [ ] command 不再同步 drain 大文件。
- [ ] copy/checkpoint/hash/atomic commit通过 crash matrix。
- [ ] 拔卡/换卡/原卡重插行为正确。
- [ ] `LocalVerified` 后无 TF reader，用户可释放/eject。

### 21.3 Library/normalization

- [ ] import outbox有幂等消费者和 acknowledge。
- [ ] media library projection独立于 legacy `LibraryEntry`。
- [ ] normalizer只读 sealed PC source。
- [ ] FFmpeg build/profile capability fail closed。
- [ ] 四类输入真实 probe/decode/encode。
- [ ] VMAF/SSIM/stereo-CV report真实且 durable。
- [ ] pair checkpoints、full decode和derived atomic commit可恢复。
- [ ] approved profile receipts齐全。

### 21.4 Upload

- [ ] frozen bundle revision和storage identity形成自然键。
- [ ] unsigned upload使用独立绑定 receipt。
- [ ] multipart每一步 durable，restart不重复已验证对象。
- [ ] completion-bound checksum/readback验证通过。
- [ ] final manifest最后上传。
- [ ] remote receipt投影后才显示 object-store verified。
- [ ] derived upload不显示为source backup。
- [ ] source archival和retention保持disabled。

### 21.5 Lifecycle/UI/release

- [ ] startup先outbox/recovery，后worker/watcher。
- [ ] shutdown停止、释放、kill/reap、join全部owned resources。
- [ ] pause/cancel响应或明确resource-stuck。
- [ ] source/derived/remote三层状态和进度独立。
- [ ] strict decoder、batch和stale projection contract通过。
- [ ] MinIO、production target smoke、真卡HITL和codec corpus有release evidence。
- [ ] 文档、package prerequisite、legal/playback review与build artifact一致。

任何未勾选项都意味着完整链路尚未完成，不能只因 UI 出现“上传成功”或某个单元测试通过而发布。

---

## 22. 发布与回滚

### 22.1 分阶段启用

1. 先发布 signed trust + async import，normalization/upload capability仍关闭。
2. 在 qualification build 中运行 normalizer corpus，不对普通用户发布 derivative。
3. profile evidence批准后，对少量 Ubuntu canary启用 `AutoNormalize`。
4. MinIO/production target验证后启用 derived upload。
5. 完整 HITL 和长时运行后扩大范围。

capability由真实依赖和批准 evidence决定，不使用一个可被随意打开的布尔 feature flag绕过硬门禁。

### 22.2 回滚

- import path可以独立保留；normalizer/upload wiring可回退为 fail-closed unavailable port。
- 已发布 source/derived trees和receipts不可删除或重写。
- active jobs保持durable，旧版本无法理解新schema时禁止启动worker。
- 应用二进制回滚必须配套恢复升级前SQLite备份。
- 已上传remote objects不在自动回滚中删除；以bundle revision保持可审计。
- credential rotation与trust revocation不能因应用回滚自动撤销。

---

## 23. 实施前硬门禁

以下不是“以后再补的测试”，而是激活对应 production stage 前必须具备的输入：

1. 现场真实 TF 卡 fixtures和脱敏许可。
2. Ubuntu 24.04实际部署硬件、reader和filesystem矩阵。
3. pairing success时可持久化的SAS transcript digest定义。
4. 代表性视频质量corpus和stereo/CV evaluator owner。
5. HEVC profile五类approval report。
6. FFmpeg/libx265/libvmaf发行方式和legal review。
7. production对象存储的version/checksum/readback能力说明。

缺少第1-3项时不得宣称signed TF导入完成；缺少第4-6项时normalizer必须保持不可用；缺少第7项时
upload不得标记remote verified。所有门禁都在PC端完成，不要求修改Pi代码。

---

## 24. 参考实现

现有代码中应复用的主要事实源：

- `src-tauri/src/media/WIRING.md`
- `src-tauri/src/media/ubuntu.rs`
- `src-tauri/src/media/ubuntu_ingestor.rs`
- `src-tauri/src/media/ubuntu_pipeline.rs`
- `src-tauri/crates/ylx-transfer-core/src/ingest/`
- `src-tauri/crates/ylx-transfer-core/src/recording_ingestor/`
- `src-tauri/crates/ylx-transfer-core/src/media_store/`
- `src-tauri/crates/ylx-transfer-core/src/media_library/`
- `src-tauri/crates/ylx-transfer-core/src/normalization/`
- `src-tauri/crates/ylx-transfer-core/src/media_normalizer/`
- `src-tauri/crates/ylx-transfer-core/src/media_pipeline/`
- `src-tauri/crates/ylx-transfer-core/src/persistence/upload_store.rs`
- `src-tauri/crates/ylx-transfer-adapters/src/removable_media/linux.rs`
- `src-tauri/crates/ylx-transfer-adapters/src/mounted_file.rs`
- `src-tauri/crates/ylx-transfer-adapters/src/publication_verifier.rs`
- `src-tauri/crates/ylx-transfer-adapters/src/media_normalizer.rs`
- `src-tauri/crates/ylx-transfer-adapters/src/derived_upload.rs`
- `src-tauri/crates/ylx-transfer-adapters/src/object_store_s3.rs`

本文与现有设计文档冲突时，以更严格的 fail-closed、安全、持久化和证据门禁为准；任何范围扩展
必须先更新本文，再开始编码。
