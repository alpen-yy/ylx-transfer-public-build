# 内存卡导入与 PC 端视频规范化设计

> 状态：Ubuntu-only 本地导入范围已有实现，按当前指令暂未执行测试或检查；本文其余内容是未来设计，不是已实现行为或编码参数的发布承诺
> 日期：2026-08-03（Asia/Shanghai）
> 历史设计前提：[`ylx-transfer` issue #1](https://github.com/mirrorbloom/ylx-transfer/issues/1)
> 曾被本文作为完整流水线目标；这不是当前实现或验证状态的声明
> Pi 调研基线：`mirrorbloom/RP-YLX` `origin/main`，提交
> [`b9c4cc2321cd802584b662c1e7364ef2b9cdc62a`](https://github.com/mirrorbloom/RP-YLX/tree/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a)
> 范围：只定义产品语义、模块所有权、数据合同、恢复规则和验收门槛；本文不修改代码

当前里程碑的 implemented scope 仅包括：Ubuntu/Linux 读取 OS 已挂载的 Linux 原生
文件系统 TF 卡（推荐 ext4），在卷根下固定扫描 `recordings/` 与 `YLX_RECORDINGS/`，
执行 bounded、no-follow、schema-aware 的 constrained scan；当前实际可达的生产准入只有
用户显式批准的 structurally validated unsigned source。source 以只读方式复制到 PC staging，
并通过 durable checkpoint、逐文件摘要校验和原子提交形成本地 import；拔卡、原卡重插和
进程崩溃可恢复，其中 unsigned 原卡重插后必须由用户再次显式批准，不能由 watcher 自动
重新准入。copy/verify 的 `File` 与 read lease 会在各自 I/O 边界离开作用域；到达
`local_verified` 后用户可显式释放该 generation 的应用句柄并使用系统弹出，应用退出时也会
取消 reader、释放句柄并等待资源结束。当前 scanner 能识别 signed publication schema，
但 Ubuntu runtime 尚未接入 PC paired-key trust owner，因此所有 signed candidate 的生产准入
一律 fail closed，并保持 `waiting_for_pairing_key` / policy-approval-required 语义。未来接入
paired verifier 前，绝不得把已有 signed publication 降级为 unsigned 绕过签名。

本阶段明确不包括 Pi 代码修改、`SelectedFolder`、Windows/macOS、exFAT/FAT 录制目标、
自动挂载或格式化、真实 codec probe/decode、质量批准的 HEVC profile、MediaNormalizer、
对象存储上传、retention/deletion，以及 `autoScan`、`autoImport`、`autoNormalize`、
`uploadSourceVideo`、`autoDeletePcSource`、`preventSleepWhileActive` 等自动化策略。
这些能力均是 future work。当前实现的构建、测试和实机验证按本轮指令后置，因此本文不能
将 implemented scope 描述为“已测试通过”或“已验收”。

## 1. 结论先行

当前里程碑先冻结一条可恢复的 Ubuntu 本地导入路径：

```text
Ubuntu OS-mounted Linux-native TF
  -> constrained scan
  -> signed fail closed / explicit structurally validated unsigned approval
  -> durable read-only local import
  -> LocalReady + explicit handle release / shutdown release
```

LAN source 合并、PC 规范化编码、对象存储上传和 retention 仍是后续流水线设计，不属于
当前 TF 卡本地导入完成条件。

完整产品方向曾建议冻结以下决定；其中第 1-2 项适用于当前导入边界，第 3-7 项只描述
future normalization/upload 方向：

1. **LAN 和内存卡只是两种来源，同一录制内容只有一个本地身份。** 同一 session 先经 LAN 下载、后从卡导入，或反过来，都不能产生两个资料库条目。
2. **对本应用而言，内存卡始终是只读来源。** 标准流程先把源录制完整复制到 PC 的 revision staging，校验并原子提交；之后转码和上传只读 PC 本地文件，用户可以尽快拔卡。应用不写卡不代表宿主 OS、索引器或杀毒软件绝不会写卡；真正的介质只读仍依赖只读挂载或物理写保护。
3. **未来：Pi 端是否编码不决定 PC 最终格式。** 后续 PC normalizer 需识别底层 SDK 持久 MJPEG、历史 appliance v5 MJPEG segments、当前 appliance 的 MJPEG spool、Pi 已完成的 H.264 publication，并在质量门槛批准后为受支持输入生成同一类规范化派生资产。
4. **未来候选：规范化编码选择 H.265/HEVC，而不是 H.264 或 AV1。** 初始候选 profile family 为左右目独立 MP4、HEVC Main、`yuv420p`、`hvc1`、保留源分辨率和帧率、固定 2 秒 GOP、30 秒左右对齐分段。软件 `libx265 preset=slow` 只是参考候选；MJPEG 的 CRF 20 和 Pi H.264 的 CRF 18 都必须由代表性实拍基准冻结，当前不能作为发布参数。
5. **编码结果是派生资产，不是对 Pi publication 的改写。** Pi 签名的 manifest、源 revision 和源文件证据保持不可变；PC 生成新的 `derived_revision` 和 `upload_bundle_revision`。
6. **未来：导入、规范化、上传应是三个独立的 durable job。** 当前只交付本地 ImportJob；DerivationJob、UploadJob 及其 session policy orchestration 尚未进入本里程碑。
7. **当前不自动删除源数据。** 应用不修改卡，也不执行 PC source retention。未来任何自动清理都必须等待独立批准的规范化、远端验证和 retention 合同。

## 2. 已核实的 RP-YLX 录制事实

### 2.1 当前 main 的两条录制路径与历史 v5

“Pi 上录的是原始视频”与“Pi 已经会编码”都可能成立，取决于调用的是底层 SDK 还是完整 appliance 栈。

#### 路径 A：底层 SDK 的持久 MJPEG

`ylx_imu.capture.CaptureSession` 的 `video_transport` 默认值是 `"file"`。该模式不会调用 appliance 的 `EncodingQueue`，而是在调用者指定的任意 `output_dir` 中写：

```text
<output_dir>/
  capture.json
  stereo.mjpeg
  frames.jsonl
  imu.jsonl
```

`capture.json` 的 schema 是 `ylx.stereo_imu.raw.v2`。本文用 `RawCaptureV2` 作为这种输入的设计名称；RP-YLX 源码中没有同名 Python 类型。其中视频被声明为：

- codec：MJPEG；
- layout：左右目 side-by-side；
- transport：`file`；
- persistent：`true`；
- width/height、单目宽度、source FPS、输出 FPS、抽帧比例和协商后的 MJPEG quality；
- native capture 结果、帧数、序号缺口、IMU 样本数和时间戳错误。

源码依据：

- [`src/ylx_imu/capture.py` 的路径与 transport 定义](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/src/ylx_imu/capture.py#L27-L64)
- [`CaptureSession(video_transport="file")` 默认值](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/src/ylx_imu/capture.py#L433-L468)
- [`capture.json` 的视频和文件字段](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/src/ylx_imu/capture.py#L195-L256)
- [SDK 原始录制示例](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/README.md#L146-L180)

这里的“不编码”是 SDK interface 的 `video_transport="file"`，不是当前 appliance TOML 中的布尔开关。当前提交的 `capture/config/config.example.toml` 没有 `encoding_enabled` 或 `skip_encoding` 字段；现场若还有未入库配置，需要把它作为额外 fixture 补入本设计。

录制历史也解释了“不编码”这个印象：编码队列引入前的 v5 实现只把 MJPEG stream-copy 到 MP4，不做视频 codec 转换。这是历史 pipeline，不是当前 `main@b9c4cc2` 里的已提交配置开关。不论现场 Pi 最终使用这类旧路径、SDK file transport，还是当前 H.264 pipeline，PC 都按实际 probe 结果进入同一规范化阶段。

#### 路径 B：appliance 的 FIFO、spool 与 Pi 硬件 H.264

完整的 `ylx-captured` production composition 固定使用 FIFO：

```text
3840x1080@60 MJPEG
  -> 每两帧保留一帧，得到 30 fps
  -> raw/stereo.mjpeg.pipe（瞬态 FIFO）
  -> FFmpeg stream-copy
  -> spool/source_00000.mp4（30 秒 MJPEG 分段）
  -> 停止采集后依次 crop 左右目
  -> 两路 h264_v4l2m2m，1920x1080@30，各 8 Mbps
  -> video/left_00000.mp4 + video/right_00000.mp4
  -> ffprobe + 完整解码校验
  -> 删除对应 source MJPEG 分段
```

Pi 4 上采集和两路硬编码不能并行；实测并行会造成 UVC sequence gap。因此 source spool 是持久中间层，编码只能在采集停止后运行。任一分段编码或验证失败时 source 必须保留。

源码依据：

- [Ubuntu Core recording pipeline](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/docs/UBUNTU_CORE_26.md#L68-L100)
- [MJPEG spool 命令](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/encoding_queue.py#L282-L320)
- [H.264 crop/encode 参数](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/encoding_queue.py#L323-L366)
- [源/左右输出的帧数与时间校验](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/encoding_queue.py#L2470-L2520)
- [验证完成后删除 source](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/encoding_queue.py#L2530-L2543)

#### 历史路径 C：appliance v5 的持久 MJPEG/fMP4 segments

用户手上所谓“不编码”的卡，还可能来自编码队列引入前的 appliance v5。在历史提交 `faa7f8f` 中，这不是一个 config 布尔开关，而是整条 appliance pipeline 的固定行为：

```text
3840x1080@60 MJPEG
  -> 每两帧保留一帧，得到 30 fps
  -> raw/stereo.mjpeg.pipe
  -> FFmpeg -c:v copy，不 decode/不重编码
  -> video/segment_00000.mp4（默认 300 秒 MJPEG/fMP4 分段）
```

v5 的 `session.json` 明确写入 `schema_version=5`、`video_codec=mjpeg`、`video_encoder=copy`、`video_container=fragmented_mp4` 和 `video_segments[]`。它在正常停止、native capture 成功、FFmpeg 成功且至少有一个非空 segment 后标为 `complete`，但没有当前 v6 的 signed `publication_manifest.json` 和逐文件 SHA-256 发布合同。因此 PC 必须用独立的 legacy detector，将 source kind 标记为 `legacy_removable_media`、provenance 标记为本地结构可验证但 producer 身份未签名，不能将 v6 schema 或签名语义倒灌给它。

v5 还有两个必须显式编码的媒体差异。第一，现有兼容分类可能把 pre-v6 单轨文件命名为 `video_mono`，但 v5 画面实际是 `camera.layout=left_right_side_by_side` 的 3840x1080 双目，每眼 1920x1080；PC 必须以 schema 布局字段为准，不能把 role 显示名当成几何真相。第二，v5 FFmpeg 使用 `-reset_timestamps 1`，每个 segment 的 PTS 从 0 重新开始，且没有 v6 `video_segment_timing`；PC 必须核验每段帧数/时长后累积重建会话时间轴，并以 `raw/frames.jsonl` 作为采集时间证据。

v1-v4 在没有真实样本和固定合同前不纳入兼容推断；scanner 应返回可操作的 `unsupported_legacy_schema`，而不是套用 v5 detector。

历史源码依据：

- [v5 `-c:v copy` 与 `video/segment_%05d.mp4`](https://github.com/mirrorbloom/RP-YLX/blob/faa7f8f7df7743e0820b4d6d4f4610f833d35f6b/capture/src/ylx_capture/recorder.py#L49-L82)
- [v5 `session.json` schema 与 MJPEG/copy 字段](https://github.com/mirrorbloom/RP-YLX/blob/faa7f8f7df7743e0820b4d6d4f4610f833d35f6b/capture/src/ylx_capture/recorder.py#L145-L170)
- [v5 结果目录与 complete 门槛](https://github.com/mirrorbloom/RP-YLX/blob/faa7f8f7df7743e0820b4d6d4f4610f833d35f6b/capture/README.md#L60-L82)

### 2.2 appliance 完成 session 的目录布局

当前 schema v6 的典型目录是：

```text
<recording-root>/<session>/
  session.json
  capture.commit.json
  publication_manifest.json
  publication_lifecycle_ack.json
  events.jsonl
  ffmpeg.log
  encoding.json
  encoding.log
  video/
    left_00000.mp4
    right_00000.mp4
  spool/
    segments.csv
    source_00001.mp4       # 只在尚未验证、失败或中断时保留
  raw/
    capture.json            # SDK path 会写；当前 appliance supervised path 不保证存在
    frames.jsonl
    imu.jsonl
  preview/
    imu.jsonl
```

这棵树不是一个全部都要传的固定文件列表。可信下载/导入的文件集合仍以
`publication_manifest.json.files[]` 为准。当前固定 publication allowlist 是：

- 左右目视频；
- `session.json`；
- `capture.commit.json`；
- 可选的 `raw/imu.jsonl`。

`events.jsonl`、日志、`encoding.json`、`raw/frames.jsonl`、`preview/` 和 spool 中间文件不会自动进入已签名 inventory。PC 若为了诊断或 raw recovery 读取它们，必须明确它们不继承 publication 签名的可信度。

源码依据：

- [RP-YLX 结果结构](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/README.md#L139-L170)
- [publication 固定 allowlist](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/publication.py#L860-L904)
- [每个 inventory 文件的 size 与 SHA-256](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/publication.py#L907-L957)

### 2.3 录制数据的位置

| 运行形态                     | 设备运行时位置                                                                                     | 插到 PC 后的含义                                                                            |
| ---------------------------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| SDK `video_transport="file"` | 调用者传入的任意 `output_dir`                                                                      | 当前仅在文件夹位于固定 recording roots 时发现；任意目录的 `SelectedFolder` 支持属于 future  |
| 传统 Ubuntu appliance        | `<selected-mount>/YLX_RECORDINGS/<session>/`，目录名可配置                                         | 扫描已挂载卷根下的 `YLX_RECORDINGS`                                                         |
| Ubuntu Core/Snap 内置系统 TF | `$SNAP_COMMON/recordings/<session>/`；运行设备上通常位于 `/var/snap/ylx-capture/common/recordings` | 系统 TF 通常不是跨平台数据卡；拔卡后宿主 OS 的挂载路径不同，Windows/macOS 也不原生读取 ext4 |
| Ubuntu Core/Snap 外接数据卡  | 配置的 exact mountpoint `/media/YLX_DATA` 下的 `recordings/<session>/`                             | 当前只承诺 OS-mounted Linux-native filesystem；跨平台 exFAT 与 Pi 改造属于 future           |

Pi 端已经显式容忍 FAT/exFAT 不具备 Unix mode bits 的情况，但这不等于当前 appliance 已能直接在 exFAT 上录制。当前 recorder 会在选中的 session filesystem 上调用 `os.mkfifo(raw/stereo.mjpeg.pipe)`，而 FAT/exFAT 不支持 POSIX FIFO。因此，跨平台 exFAT 数据卡成为正式 Pi 录制目标前，Pi 端需把瞬态 FIFO 放到本机 ext4/tmpfs runtime 目录，卡上只放普通文件、临时文件和完成标记。当前仓库也没有负责格式化或自动挂载 `/media/YLX_DATA` 的逻辑；格式化、挂载、FIFO 迁移和断电持久性验证都是独立的 Pi 部署合同。

### 2.4 PC 必须支持的输入矩阵

| 输入种类                     | 权威入口                                                                        | 视频形态                                                    | 原始信任                                                                                            | 当前行为 / future                                                                                                         |
| ---------------------------- | ------------------------------------------------------------------------------- | ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `SignedPublicationV1`        | `publication_manifest.json`                                                     | 左右 H.264/MP4 分段，未来也可能是其他 manifest 声明的 codec | 当前只识别 schema 并 fail closed；未来接入 paired publication key 后再执行 Ed25519 + 每文件 SHA-256 | 当前保持 `waiting_for_pairing_key` / policy-approval-required，且禁止降级为 unsigned；未来 paired verifier 验证后才可准入 |
| `RawCaptureV2`（本文设计名） | `capture.json`，schema `ylx.stereo_imu.raw.v2`                                  | 单个 side-by-side `stereo.mjpeg`                            | 完成状态与采集计数可验证，但没有 session signature、文件 SHA-256 或稳定 session id                  | 当前经显式批准后只做本地导入；normalization/upload 属于 future                                                            |
| `LegacyMjpegSessionV5`       | `session.json` 且 `schema_version=5`                                            | `video/segment_*.mp4` 中的 side-by-side MJPEG/fMP4          | complete 与 native/mux 结果可结构验证，但没有 signed publication 或逐文件摘要                       | 当前经显式批准后只做本地导入，source kind 为 `legacy_removable_media`；normalization/upload 属于 future                   |
| `ApplianceSpoolV6`           | `session.json` + `capture.commit.json` + `encoding.json` + `spool/segments.csv` | side-by-side MJPEG/MP4 分段                                 | 有 durable capture evidence，但尚无最终 signed publication                                          | capture evidence 结构自洽时可经显式批准做本地导入；normalization/upload 属于 future                                       |
| 失败/录制中/结构损坏         | 任意                                                                            | 任意                                                        | 不完整                                                                                              | 只显示诊断，不进入导入                                                                                                    |
| 未知 major schema            | 未知                                                                            | 未知                                                        | 未知                                                                                                | fail closed，不猜测                                                                                                       |

检测顺序必须由 schema 和 manifest 语义驱动，不能只看扩展名或当前 Pi 配置。当前不执行真实
codec probe/decode；未来可使用 bounded `ffprobe` 复核真实 codec、轨道、分辨率、帧率、帧数
和时长，但 probe 结果仍不能替代来源 manifest 的身份和完整性证据。

## 3. 术语与身份模型

### 3.1 核心术语

| 术语                    | 含义                                                                                                                |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `AcquisitionSource`     | 此次从哪里读 bytes：某个已认证 Pi session、某个已挂载 volume、或用户选择的本地目录                                  |
| `SourceRecording`       | 准入后的源录制；绑定来源 schema、内容 inventory、媒体布局和 provenance，字段私有，调用者不能自行拼装                |
| `SourceContentRevision` | 源录制内容身份；signed publication 直接使用 Pi revision，unsigned 输入在完整复制和哈希后生成 PC content revision    |
| `ImportJob`             | 把源 inventory 复制到 PC staging、验证并原子提交到本地资料库的 durable job                                          |
| `NormalizationProfile`  | 冻结 codec、容器、像素格式、分段、GOP、质量模式、encoder compatibility class 和验证规则的版本化 profile             |
| `DerivationJob`         | 从一个已验证的 `SourceContentRevision` 生成规范化媒体派生版本的 durable job                                         |
| `DerivedRevision`       | 对源 revision、profile revision、encoder build、输入摘要和输出 inventory 的 canonical manifest 求哈希得到的派生身份 |
| `UploadBundleRevision`  | 实际上传对象集合及其顺序、key、大小、摘要和媒体类型的不可变身份                                                     |
| `MediaGeneration`       | 一次已挂载介质实例；至少绑定 OS volume identity、根标记和观察 epoch，不等同于盘符或 mount path                      |

### 3.2 内容身份与来源位置必须分离

对 signed publication，逻辑身份继续是：

```text
(origin_device_identity, session_id, source_publication_revision)
```

下列字段绝不能进入内容自然键：

- `source_kind = lan | removable_media | local_folder`；
- Windows 盘符、macOS/Linux mount path；
- volume UUID、volume serial、reader model；
- 本次扫描 ID 或连接 token。

否则同一录制从 LAN 和卡进入会被建成两个 job，破坏 issue #1 已建立的幂等语义。来源位置只属于 attempt/locator，用于决定本次从哪里续读 bytes。

对 unsigned raw/spool 输入，扫描阶段只能产生 provisional candidate。完成本地复制并对全部受支持文件求摘要后，才生成稳定 `SourceContentRevision`。camera serial、目录名、创建时间、volume serial 都不能单独充当内容身份。

当前 appliance 的 `session_id` 也不是全局唯一身份。它只由 session 目录 basename 的清洗值和该 basename 的 SHA-256 前缀派生，所以目录名不变时能跨 mount path 稳定，但重命名会改变 ID，两台设备的同名目录还会得到同一 ID。因此 signed content 必须使用：

```text
(trusted producer/publication key fingerprint, session_id, publication revision)
```

`session_id` 单独只适合显示或受 producer identity 约束后的局部查找，不能用于跨设备去重。

### 3.3 provenance 必须是不可伪造的判别联合

建议领域值至少区分：

```text
DeviceSigned {
  verified_publication,
  publication_key_fingerprint
}

LocallyValidatedUnsigned {
  source_schema,
  validation_report,
  computed_inventory_digest,
  user_admission_receipt?
}
```

不能用 `trusted: bool`。调用者若能把 unsigned source 的布尔值改成 `true`，后续上传和清理策略就失去意义。上传 bundle、UI 文案和审计记录都必须保留 provenance variant。

## 4. 模块与 seam

### 4.1 `RecordingIngestor`：深的导入模块

应用层只需要学习一个小 interface：

```text
scan(media_or_folder) -> scan snapshot
start_import(candidate_id, policy) -> created | existing | conflict
command(job_id, pause | resume | cancel | retry)
snapshots() -> import job snapshots
```

其 implementation 隐藏：

- 可移除卷枚举、受限目录扫描和 schema detector；
- publication trust 或 raw/spool admission；
- safe path、regular-file、symlink/reparse-point 检查；
- `MediaGeneration` fencing；
- Range/seek 映射、`.part`、checkpoint、SHA-256 和 revision staging；
- 卡拔出分类、恢复和句柄释放；
- signed 与 unsigned 内容身份的收敛。

删除这个模块时，上述复杂度会重新扩散到命令、UI、TransferStore、文件复制器和平台代码，因此它通过 deletion test。

### 4.2 只在“读取 artifact bytes”处建立共同 source seam

LAN 和本地卷真正相同的能力很小：对某个已准入 artifact，从 offset 开始读取，并观察源版本是否变化。这里已有两个真实 adapter，因此值得建立内部 seam：

```text
ArtifactSource
  open(file_id, expected_source_revision, offset) -> byte stream/outcome
```

- `PiHttpArtifactAdapter`：把 HTTP `200/206/412/416` 映射为 core outcome；
- `MountedFileArtifactAdapter`：把 seek/EOF/文件变化/介质消失映射为相同 outcome；
- in-memory adapter：运行与 production 相同的 contract suite。

不要把 mDNS、卷发现、配对、卡扫描、安全弹出全部硬塞进同一个 source interface。它们没有共同语义，会把 interface 做成浅而庞大的方法集合。

### 4.3 `MediaNormalizer`：深的派生模块

建议 interface：

```text
start(source_content_revision, profile_revision) -> created | existing | conflict
command(job_id, pause | resume | cancel | retry)
snapshots() -> derivation job snapshots
```

其 implementation 独自拥有：

- 输入 probe 与 media plan；
- stereo split、时间轴和分段计划；
- encoder 进程从 spawn 到 reap 的唯一所有权；
- per-segment checkpoint、partial cleanup 和 restart；
- full-decode、帧数、时长、同步和质量验证；
- derived manifest、`DerivedRevision` 和原子发布。

FFmpeg 参数、临时文件名、process handle 和 probe JSON 都不能越过该 interface。若未来同时支持 `libx265`、NVENC/QSV/VideoToolbox 和测试 fake，encoder port 是一个真实的内部 seam；不同 encoder 不能假装属于同一个 profile revision。

### 4.4 `SessionPipeline`：只拥有依赖和策略

`SessionPipeline` 不执行复制、编码或上传。它持久化：

```text
SourceRecording
  -> ImportJob(local_verified)
  -> DerivationJob(derived_verified)
  -> UploadJob(object_store_verified)
```

它负责：

- dependent job 的 idempotent enqueue；
- “自动规范化”“自动上传”“仅导入”等用户策略；
- 批量逐项结果；
- provenance 不满足上传策略时进入 `action_required`；
- 应用重启后的依赖重放。

它不复制底层 job state，不合成一个虚假的总百分比，也不越过各 aggregate 直接选择终态。

### 4.5 现有模块的所有权保持不变

| 模块                                              | 新功能中的所有权                                                                                                                                  |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PublicationTrust`                                | signed card 和 LAN 使用同一份 schema、key identity、Ed25519、inventory、路径和摘要验证                                                            |
| `TransferStore` / `JobAggregate`                  | 继续拥有 byte-transfer job；card import 是本地 source adapter 驱动的 acquisition job，不拥有编码状态                                              |
| `ArtifactInspector` / staging                     | 继续作为 final/partial/checkpoint 的单一证据与 revision 原子发布机制                                                                              |
| `AppStore`                                        | 本地资料库 projection；记录源 revision、derived revision、provenance 与上传状态，不成为 job authority                                             |
| `MediaNormalizer` 的 durable repository/aggregate | 拥有 derivation spec、segment ledger、state/version、desired state 和 completion outbox；可与 TransferStore 共用物理 SQLite，但逻辑所有权必须分开 |
| object-store module                               | 只消费已冻结的 `UploadBundleRevision`，执行 multipart、completion-bound verify 与真实摘要验证                                                     |

不应把 `DerivationJob` 塞进现有 download 状态枚举。两者共享调度、CAS、outbox 等机制是合理复用，共享一套含义模糊的状态图不是。

还需要两类不同的 lease：

- 每张物理卡默认只允许一个顺序 reader，避免多个 session 随机读抢占读卡器带宽并放大拔卡恢复状态；不同卡可并行。
- `LibraryRevisionLease` 保护一个已封存 source/derived revision 的读取和替换/删除。Import commit 或 retention 需要 exclusive lease，normalizer 和 upload 需要 shared lease。现有 target-write lease 只能防止两个 acquisition writer 同时写目标，不能防止 upload 读到一半时目录被 retention 删除或被新 revision 替换。

## 5. 内存卡导入工作流

### 5.1 发现与扫描

1. 应用监听系统已挂载 volume 的到达/移除事件，并在启动、事件丢失恢复和用户手动刷新时做协调扫描。
2. 自动扫描只检查受限位置：卷根下的 `recordings/`、`YLX_RECORDINGS/` 及其直接 session 子目录。
3. 当前 Ubuntu-only MVP 不提供 `SelectedFolder`；SDK 任意 `output_dir`、系统未作为已挂载 volume 暴露的读卡器和无自动挂载路径均留待后续显式目录导入能力。
4. scanner 有目录数、候选数、manifest 大小、单 session 文件数、路径长度和总声明字节上限。
5. scanner 不跟随 symlink、junction 或 reparse point，不打开 manifest 未声明的任意路径。
6. 插卡只触发扫描，不自动导入、转码、上传或删除。

当前实现只消费 Ubuntu/Linux 已挂载 volume。Windows 的 `DRIVE_REMOVABLE`、bus type、
卷标、盘符和 macOS volume discovery 均属于 future cross-platform work，不是本阶段的兼容承诺。

### 5.2 预检

预检输出逐 session 结果：

- `ready_signed`（为未来 paired-key verifier 保留；当前 Ubuntu runtime 不产生可准入的 signed candidate）；
- `ready_unsigned_requires_policy`；
- `already_imported`；
- `waiting_for_pairing_key`；
- `recording_or_encoding_incomplete`；
- `unsupported_schema`；
- `unsafe_path`；
- `insufficient_local_space`；
- `corrupt`。

当前对 signed source 只完成 schema、安全路径、inventory 形状和声明大小检查，然后生产准入
fail closed。未来 paired-key verifier 接入后，信任结果必须分成三个独立 verdict，不能压成
一个 `trusted` 布尔值：

1. `inventory_hashes_valid`：实际文件 bytes 与 manifest 声明的 size/SHA-256 一致；该结论只能在复制并重读 PC staging 后最终成立。
2. `manifest_signature_valid`：canonical manifest 的签名能被某个具体 public key 验证。
3. `producer_key_trusted`：该 public key 已在 PC 信任库中绑定为预期 producer/device。

卡上的 fingerprint、public key 或 signature 都不能自己成为 trust anchor。未来预检可先完成
签名和 key-trust 判定，完整文件 SHA-256 则在复制流中计算，避免为了预检先把整张卡读一遍。

生产级 signed admission 未来必须从 PC 已有的配对信任 owner 取得 producer key。当前 Ubuntu
runtime 尚未接入该 owner，因此即使 PC 其他模块已经保存配对 key，所有 signed candidate 也
一律保持 `waiting_for_pairing_key` / policy-approval-required 并 fail closed。接入 verifier 前
不得忽略已有签名、把 signed publication 重新解释为 unsigned，或仅信任卡内自带 key。

unsigned raw/spool 必须至少满足：

- manifest state/result 表示采集完成；
- persistent MJPEG 或闭合 spool 分段存在且非空；
- frame index 连续、JPEG offset/length 在文件范围内；
- IMU 与 capture summary 的完整性约束通过；
- manifest 中 codec、布局、尺寸、帧率、帧数和时长声明在 schema 层自洽；
- 没有未知/额外 source 文件被悄悄并入 inventory。

这些检查只证明 manifest、index、文件边界和复制后摘要层面的结构自洽，不能证明它来自
某个已信任 Pi，也不能证明 MP4/JPEG 码流可完整 probe/decode。当前若沿用
`locally_validated_unsigned` provenance，其含义仅限上述结构与 bytes 验证，不得解释为
codec validity 或质量验证。真实 bounded codec probe/decode 属于 future normalizer/admission
hardening；在实现前不得据此自动规范化或上传。unsigned source 还必须由用户逐项显式批准，
不能由 `autoImport` 或其他默认策略自动准入。

### 5.3 ImportJob 状态

建议状态图：

```text
queued
  -> waiting_for_media
  -> preflighting
  -> copying
  -> verifying
  -> committing
  -> local_verified

preflighting/copying/verifying -> waiting_for_media
任何非终态 -> cancelling -> cancelled
可恢复错误 -> retry_wait -> queued
不可恢复错误 -> failed(code, retryable=false)
```

`waiting_for_media` 不是失败。用户拔卡、读卡器断开、OS 卸载、volume generation 改变、Windows `ERROR_NOT_READY`/`ERROR_MEDIA_CHANGED` 等都应先收敛到 source unavailable，再由 job owner 持久化状态。

### 5.4 Copy、校验与原子提交

标准路径：

1. 为 source revision 创建隐藏的 revision staging；
2. 按 manifest/validated inventory 顺序复制；
3. 每个 chunk 先写 PC `.part`，达到 durability 约定后再推进 checkpoint；
4. 从 source stream 计算 SHA-256；当前可准入的 unsigned source 形成 PC-computed inventory；未来 paired verifier 接入后，signed source 还必须与 Pi hash 比较；
5. 文件写入、flush 并关闭后，从 PC staging 重新读取目标文件并计算 SHA-256；不能用同一读写 buffer 的一次 hash 同时声称验证了 source 和 target；
6. 每个完整文件进入 `ArtifactInspector::Verified` 后才计为完成；
7. 全部文件验证后 seal staging；
8. 一个目录 rename 发布 source revision；
9. completion outbox 更新本地资料库；
10. copy/verify 的 `File` 与 read lease 离开作用域；用户可在 `local_verified` 后显式释放该
    generation 的应用句柄并使用系统弹出，应用退出时也执行释放与资源 join。

卡被拔出后，只有 `Missing` 或 `Partial(durable_offset)` 文件需要继续读取。已验证文件不重复复制。
插回时必须同时检查 `MediaGeneration`、candidate identity、manifest revision 以及将续读文件的
size/hash claim；相同盘符下的另一张卡不能续写旧 `.part`。unsigned source 的旧批准不跨
media generation 复用：重新扫描并关联旧 waiting job 后，用户必须再次显式批准，才能安装
新 locator；弱 provisional identity 跨 acquisition fence 时还必须清零进度并从头重验。

### 5.5 为什么首版必须先落本地

| 方案                        | 结果                                                                                 | 首版决定 |
| --------------------------- | ------------------------------------------------------------------------------------ | -------- |
| 先完整落本地，再规范化/上传 | 快速释放卡；三个 job 独立恢复；源证据稳定；最容易复用现有 staging                    | **采用** |
| 直接从卡转码到 PC           | 少一份 source 占用，但卡需插到编码结束；拔卡使当前 segment 重做；源证据不稳定        | 不做     |
| 在卡上写转码结果            | 源与派生在同一故障域；写放大；空间不足或拔卡可能损坏；改变 signed inventory 周边文件 | 禁止     |
| 直接从卡上传                | 云端抖动长期占卡；恢复依赖原卡；无法尽快复用介质                                     | 不做     |
| 边转码边上传                | 上传重试可能强制重新编码；无法先冻结 output hash 与 bundle revision                  | 不做     |

空间预检必须按最坏阶段计算，而不是只看源文件大小：

```text
source staging
+ 当前 derivation 的 partial 与已验证 segment
+ SQLite/WAL、manifest 和安全余量
```

CRF 输出大小不固定，UI 只能显示基于代表性样本的估算区间。

## 6. PC 规范化编码

### 6.1 codec 决策

默认选择 **H.265/HEVC**：

- 相比 H.264，在相近主观质量下通常能以更低码率保存；
- 相比 AV1，软件编码时间和现有硬件加速覆盖更适合作为当前桌面端默认；
- MP4/HEVC 适合对象存储中的归档、下载和主流本地播放器；
- PC 端可以承担 Pi 不适合承担的慢速高质量编码。

不选择 H.264 作为新的统一归档 profile，因为 Pi 当前完成态已经是两路各 8 Mbps H.264；再次编码为 H.264 很难在保持质量的同时取得足够体积收益。

不选择 AV1 作为首版默认。AV1 可作为未来的 `archive-av1-*` profile，但要单独验证编码时长、解码环境、硬件差异和对象消费方兼容性，不能用“理论压缩率更高”替代真实 workflow 门槛。

HEVC 与 `libx265` 的分发涉及 FFmpeg 构建选项、GPL 以及 HEVC 专利许可问题。正式发布前必须完成依赖/分发法律审查；不能把系统上偶然存在的 FFmpeg 当作产品许可方案。

### 6.2 初始候选 profile

下列参数是需要拿真实数据验证的 **候选**，不是未经测试就冻结的常量：

| 项目                     | 候选值                                                                                                                   |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| profile family id        | `hevc-main-cfr-sourcefps-gop2s-x265-slow-v1`                                                                             |
| codec                    | HEVC/H.265 Main, 8-bit                                                                                                   |
| container / sample entry | MP4 / `hvc1`                                                                                                             |
| pixel format             | `yuv420p`                                                                                                                |
| 质量 variant             | MJPEG/spool 首代编码：`CRF 20`；Pi H.264 二代编码：`CRF 18`                                                              |
| preset                   | `slow`                                                                                                                   |
| 空间布局                 | 左右目独立文件，不保留 side-by-side 作为规范化输出                                                                       |
| 分辨率                   | 保留单目源分辨率；当前设备通常为 1920x1080                                                                               |
| 帧率                     | 保留经过 manifest 与 frame evidence 验证的 source FPS，不做静默抽帧；当前 appliance 通常为 30 fps，SDK raw 可能为 60 fps |
| time base                | `1/90000`；30 fps 每帧 3000 ticks，60 fps 每帧 1500 ticks                                                                |
| GOP                      | closed GOP，固定间隔 `2 * fps` 帧，禁止 open GOP 和 scene-cut 额外关键帧，左右关键帧位置严格一致                         |
| 分段                     | 左右目严格对齐的独立 30 秒 segment；末段可短                                                                             |
| audio                    | 无；不能创建空 audio track                                                                                               |
| metadata                 | 不伪造未知 color range/primaries/transfer；已知值按 profile 规则显式保留                                                 |

两个 quality variant 生成相同的 codec/container/layout 合同，但不冒充同一个精确 encoding profile。MJPEG -> HEVC 是第一代有损编码，CRF 20 在体积与保真间取候选中心；current Pi H.264 -> HEVC 是第二代有损转换，因此用更保守的 CRF 18。若 H.264 输入在 CRF 18 下未通过质量门槛，可用明确版本化的 CRF 16 variant 重试；再次失败则保留 source 并阻止上传，不能改用 passthrough、静默降低门槛或沿用同一 profile revision。

x265 只定义 CRF 数值越高量化越强、质量越低，不承诺任意素材在某个 CRF 下的绝对质量或固定体积。上述 CRF 都是 benchmark 起点，已发布 variant 的参数不得原地修改。

`yuv420p` 同样是候选，不是已证实的源格式。当前源码能证明 Pi H.264 输出是 8-bit `yuv420p`，却不能仅凭配置证明真实 UVC MJPEG 的 JPEG chroma sampling、color range 和 matrix。冻结 profile 前必须对实机文件检查 JPEG SOF/解码后 pixel format；若源是 4:2:2，转 `yuv420p` 会新增不可逆色度降采样，需单独通过 CV 回归或另建版本化 4:2:2 profile。把 8-bit 源放进 Main10 不会恢复不存在的精度，也不解决 4:2:2 -> 4:2:0 的信息损失。

### 6.3 “总是规范化”不等于每次都重复做无用工作

用户要求所有 Pi 输入到 PC 后统一编码。首版按以下语义执行：

- 原始 MJPEG：实际 decode/crop/HEVC encode；
- Pi H.264：实际 decode/HEVC encode；
- future Pi HEVC：只要它不是由同一 `SourceContentRevision + profile_revision` 生成并带可验证 derived manifest，仍按 source 输入重新规范化；
- 已存在且全部验证通过的同 profile `DerivedRevision`：幂等复用，不重复编码。

这保证对象存储里的媒体合同一致，同时避免应用重启或重复点击造成重复有损转换。

### 6.4 四类输入的转换计划

| 输入                             | PC 处理                                                                                                                                                                                                                                                                   |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `stereo.mjpeg`                   | 按 `capture.json` 和 `frames.jsonl` 验证 JPEG 边界与顺序；按 frame plan 切成 30 秒段；一次 decode 后 crop 左右半幅；分别编码 HEVC                                                                                                                                         |
| v5 `video/segment_*.mp4`         | 要求 `schema_version=5` 且 session complete；以 `camera.layout=left_right_side_by_side` 而非 `video_mono` role 推导几何；核对 `video_segments[]`、MJPEG codec、帧率、序号和 raw summary；按段帧数/时长累积重建被 `-reset_timestamps 1` 重置的时间轴，crop 后编码左右 HEVC |
| `spool/source_*.mp4`             | 以闭合的 `segments.csv` 顺序为准；验证每段 MJPEG、序号、interval 和 capture commit；crop 后编码左右 HEVC                                                                                                                                                                  |
| `video/left_*.mp4 + right_*.mp4` | 验证 manifest role 与左右段一一对应；同步 decode；保持分段、帧数和时间轴；分别重编码 HEVC                                                                                                                                                                                 |

不能把 `-r 30` 当作修复输入时间轴的工具。输出 frame plan 必须来自已验证 manifest/索引；一旦发现变帧率、左右帧数不一致、时长偏差或未知时间基，job 失败并给出结构化诊断。

### 6.5 DerivationJob 状态与 checkpoint

```text
queued
  -> waiting_for_source
  -> probing
  -> planning
  -> encoding
  -> validating
  -> committing
  -> derived_verified

encoding/validating -> retry_wait -> queued
任何非终态 -> cancelling -> cancelled
不可恢复错误 -> failed(code, retryable=false)
```

checkpoint 粒度是一个左右目都完成并验证的 segment pair。崩溃时：

- 已验证 pair 复用；
- 正在写的 `.partial.mp4` 删除并重做该 pair；
- 不允许只提交 left 或 right；
- encoder build/profile 与 job spec 不同，旧 segment 不复用；
- pause/cancel 返回成功前，process owner 必须确认 FFmpeg 及其子进程已 reap、文件句柄已释放。

### 6.6 必须通过的结构和完整性校验

每个 pair 至少验证：

- codec/profile/container/pixel format 符合 profile；
- 左右分辨率相同且等于 source 单目分辨率；
- 输出 FPS 等于 source plan；
- source/left/right frame count 完全相等；
- 左右 duration 差异不超过一帧，输出与 source 差异不超过 `1/fps + 0.01s`；
- 首帧可独立解码，GOP 不超过 profile 上限；
- 从头到尾完整解码，FFmpeg `-xerror` 下没有错误；
- 输出非空、没有多余 track；
- encoder 正常退出并关闭输出后，重新打开 partial 做完整解码与 SHA-256；随后对 partial 执行 durability flush、关闭句柄，再原子发布；
- segment pair 的开始帧、结束帧和 source timing 映射进入 derived manifest。

### 6.7 质量与体积发布门槛

codec 决策可以先定为 HEVC，但 CRF/preset 不能只凭经验冻结。需要建立一组真实 YLX 双目 fixture，至少包含：

- 快速运动与运动模糊；
- 细纹理、棋盘格、重复纹理和远距离小目标；
- 低照度、高噪声和高动态范围；
- 静态长镜头；
- raw MJPEG 输入和 Pi H.264 输入；
- 30 fps 与实际存在的 60 fps raw 输入。

每个 raw fixture 还必须记录实际 JPEG chroma sampling、解码 pixel format、range/matrix 判定及其证据，不允许 encoder 用默认值静默猜测。

候选发布门槛：

1. 所有结构/完整性校验 100% 通过，无新增丢帧或重复帧。
2. 使用固定版本的 VMAF NEG model；尤其对 H.264 二代编码，每眼全片 mean 候选门槛为 `>= 95`，逐帧 1% 分位候选门槛为 `>= 90`。
3. 每眼全片 SSIM mean 候选门槛为 `>= 0.99`。
4. 对 current Pi H.264 输入，规范化后总视频体积的样本中位数建议不高于 source 的 65%。
5. 记录编码 real-time factor、峰值 CPU/RAM、临时磁盘、取消延迟和两路并发影响；支持的最低 PC 必须在可接受时间内完成。
6. 若视频用于标定、立体匹配、tracking 或其他 CV 任务，必须增加特征点保留、左右匹配、视差稳定性、重投影误差和关键 ROI 等领域指标。VMAF/SSIM 通过不代表算法输入等价；领域门槛未通过时禁止上传该派生件，也禁止据此删除 source。

阈值是本轮需要共同确认的提案。实测若无法同时达到质量和 65% 体积目标，应调整 CRF/preset 或明确保留 source，不能降低门槛后继续沿用同一 profile id。

### 6.8 软件与硬件 encoder profile 必须分开

参考 profile 使用 `libx265`，目的是让 Windows/macOS/Linux 在相同 encoder compatibility class 下得到一致的质量语义。输出 bytes 不要求跨平台完全相同；实际文件摘要形成 `DerivedRevision`。

NVENC、QSV、AMF、VideoToolbox 或其他硬件 HEVC 可作为后续“快速 profile”。它们必须拥有不同的 profile revision、参数映射、质量基准和 fixture 证据，不能在运行时无提示地替换 `libx265` 后仍声称生成相同 profile。

`libx265` 是 GPLv2/商业双许可，它的软件许可不代替 HEVC 专利许可。启用 GPL component 的 FFmpeg 分发也会带来 GPL 合规要求。如果某个平台既不能合规分发 x265，也没有通过同等质量门槛的系统/硬件 HEVC encoder，在“强制统一 HEVC”产品约束下必须报告不具备编码能力并阻止上传，不能静默回退到另一 codec。

## 7. Derived manifest 与本地资料库

派生 manifest 至少包含：

```text
schema_version
source_content_revision
source_provenance
source_manifest_digest
normalization_profile_revision
encoder {
  implementation
  version
  build_fingerprint
  parameters
}
media_plan {
  layout
  source_fps
  eye_dimensions
  segment_duration
  timing_basis
}
input_inventory[] {
  source_file_id/path
  size_bytes
  sha256
}
output_inventory[] {
  output_file_id/path
  role
  segment_index
  first_frame
  frame_count
  duration
  size_bytes
  sha256
  media_type
}
validation {
  structural
  full_decode
  sync
  quality_profile
}
transcode_generation
derived_revision
created_at
```

`derived_revision` 对不含自身的 canonical manifest 求 SHA-256。任何输入、profile、encoder build、输出 bytes 或验证证据变化都会产生新 revision。

source 和 derivative 必须是两棵独立封存的 immutable tree，不能把 PC 编码结果追加进 Pi source seal。建议的逻辑布局是：

```text
library/
  sources/{source-revision}/...
  derivatives/{source-revision}/{profile-revision}/{derived-revision}/...
  .ylx-derived-staging/{job-id}/...
```

派生 staging 完成后只能以一次目录发布进入 `derivatives/`；失败不能改动 `sources/` 下的任何 bytes。`TransferStore` 的成功 job 可能在 completion outbox 被消费后退役，所以它不能充当长期重复导入栅栏。本地资料库必须额外保留 revision-aware `LibraryImportReceipt`，至少绑定 source identity/revision、sealed inventory digest、provenance、local path 和 commit receipt；再次扫到 LAN/卡上的同一内容时，重读本地证据后返回 `AlreadyImported`，而不是依赖已退役 job。

本地资料库不能继续用一个 `uploaded: bool` 描述全流程。至少要投影：

- source local verified；
- source provenance；
- derived local verified；
- derived profile/revision；
- upload bundle verified；
- source object 是否也上传；
- 卡上 source 是否仍在；
- 本地 source retention 状态。

“HEVC 已上传”不能显示成“原始数据已备份”。只有产品明确批准“该 profile 可作为归档副本”的策略后，才允许据此触发 source 清理。

## 8. 对象存储合同

### 8.1 上传对象集合

默认 bundle 包含：

- 规范化左右目 HEVC segments；
- derived manifest；
- 原始 Pi `publication_manifest.json` 或 unsigned source manifest 的只读副本；
- signed inventory 中的 IMU 与 metadata；
- PC 生成的 provenance/validation report。

默认不上传 raw MJPEG 或 Pi H.264 source 视频；可由独立的“source archival”策略开启。即使 source 视频不上传，source manifest 和 source hashes 也必须保留，以说明派生版从何而来。

### 8.2 object key

建议 key 结构：

```text
{prefix}/{origin-device}/{session-or-source-id}/{source-revision}/
  source/source_manifest.json
  derivatives/{profile-revision}/{derived-revision}/
    video/left_00000.mp4
    video/right_00000.mp4
    derived_manifest.json
    metadata/...
```

所有 path segment 使用经过编码的 opaque identity，不能直接信任显示名。`derived_revision` 进入 key，避免两个 encoder attempt 或 profile 更新覆盖同一个对象。

### 8.3 UploadBundleRevision 与完成验证

上传前冻结完整 bundle；upload job 自然键至少绑定：

```text
(upload_bundle_revision, storage_profile_identity)
```

每个对象执行：

1. multipart upload；
2. 保存 completion ETag/version id；
3. 绑定本次 completion 做 HEAD/readback；
4. 验证 size、metadata 中的 source SHA-256 和远端真实 bytes 摘要；
5. 全部媒体与最终 derived manifest 都验证后，才提交 `object_store_verified`。

上传最后提交 derived manifest，使对象集合在消费方表现为“数据对象先到、权威入口最后到”。若对象存储不提供可信 full-object checksum，继续沿用 issue #1 决策：流式读回并重新计算摘要。

## 9. 卡移除、恢复与安全弹出

### 9.1 MediaGeneration fencing

mount path 不是身份。任务至少记录：

- OS volume GUID/UUID/opaque identity（若系统提供）；
- volume serial、filesystem、容量等辅助属性；
- 扫描时的根标记/manifest digest；
- media observation epoch；
- candidate relative path；
- expected source revision/claims。

恢复时重新准入 candidate。OS volume identity 只加速匹配，最终必须由录制 identity、manifest 和续读文件 claim 防止接错卡。

### 9.2 拔卡行为

- arrival/remove callback 只入队事件，不执行阻塞扫描或 hash；
- 收到移除通知或 source I/O 错误后停止派发新读，取消/排空在途 I/O，关闭句柄；
- ImportJob 转为 `waiting_for_media`，保留 durable `.part`；
- 纯读取导入不应为了阻止用户拔卡而长期 veto OS remove；
- 一旦 source revision 已 `local_verified`，DerivationJob 和 UploadJob 与卡完全解耦。

### 9.3 安全弹出

应用只在以下条件满足时启用“安全弹出”：

- 该 media generation 没有 active import reader；
- scanner watcher、目录句柄和异步 I/O 已释放；
- 所有本地 durability 写都发生在 PC 目标盘，而非源卡；
- platform eject adapter 返回成功。

Windows 应优先使用 PnP eject 并显示 veto 原因，不能用强制 dismount。若平台或权限不支持应用内 eject，UI 只能显示“应用已释放，可在系统中安全移除”，不能伪造 OS 已弹出。

### 9.4 文件系统支持范围

当前 Ubuntu-only MVP 只读取 **操作系统已经挂载并允许普通文件访问的 Linux 原生文件系统**
volume，推荐 ext4。应用不读取 raw block device，不自动挂载、修复或格式化介质，不捆绑
文件系统驱动，也不要求管理员/root。由 OS 或操作员预先解锁并挂载的介质仍按同一 bounded、
no-follow、安全路径和只读来源合同处理；“OS 能访问”本身不等于该文件系统进入当前支持范围。

exFAT、FAT32、NTFS、APFS 以及 Windows/macOS 文件系统兼容性均属于 future cross-platform
work。未来若承诺这些格式，必须分别通过同一安全 contract suite；其中 FAT32 还受单文件
4 GiB 限制，NTFS 必须具备完整 reparse-point 防护。面向跨平台产品的 `/media/YLX_DATA`
外接 exFAT 数据卡仍只是候选方向，并依赖 Pi FIFO relocation 与真实硬件 durability 验证，
不能解释为当前 Ubuntu TF 卡 MVP 已支持 exFAT/FAT。

## 10. 安全约束

1. 卡以及 future `SelectedFolder` 选择的目录都视为不可信输入。
2. manifest 有大小与深度上限，JSON 使用结构化 parser，不允许未知 major schema。
3. 所有相对路径逐 component 校验；拒绝绝对路径、`..`、NUL、反斜线逃逸、symlink、junction/reparse point 和非 regular file。
4. 扫描结果只是 candidate；只有字段私有的 `SourceRecording` 才能进入 ImportJob。
5. 当前 signed publication 一律 fail closed；未来接入后必须使用 PC 已信任的真实公钥验证，manifest 中只有 fingerprint/signature，不能从卡上自带的任意公钥静默建立信任。
6. 未配对设备卡若要离线建立信任，需要单独的物理/SAS/QR 产品流程；首版不能把“拿到卡”自动等同于“信任设备身份”。
7. unsigned raw/spool 不能被 UI 或对象 metadata 描述成 device-signed。
8. 卡上不写 `.partial`、数据库、导入标记、转码结果或删除操作。
9. Future FFmpeg/ffprobe process 的错误输出必须有大小上限并清洗路径/敏感数据，同时具有 deadline、取消与 reap 合同。
10. object key、临时路径和日志都不使用未经编码的设备名/session 显示名。

## 11. 用户工作流与状态展示

建议主流程：

```text
检测到介质
  -> 显示可导入 session 与逐项原因
  -> 用户选择并开始导入
  -> 所有选中 session 尽快复制到本地
  -> 提示可以安全弹卡
  -> 后台规范化
  -> 后台上传并远端验证
  -> 显示 source/derived/remote 三层真实状态
```

进度按 job 分开：

- ImportJob：文件名、已复制/总字节、吞吐、ETA；
- DerivationJob：segment pair、已处理帧/总帧、编码 fps、ETA；
- validation：已完整解码 segment 数，不隐藏在“99%”；
- UploadJob：已上传/总字节、part、吞吐、ETA；
- batch：成功、处理中、需要用户操作、失败的 session 数与逐项 tagged outcome。

不合成一个看似精确的端到端百分比。用户更需要知道“卡是否还要插着”“本地源是否安全”“编码是否完成”“远端是否真正验证”。

默认策略：

- 自动扫描：开；
- 自动导入：关；
- 导入后自动规范化：开；
- derived verified 后自动上传：沿用现有设置；
- 上传 source 视频：关；
- 自动删除 PC source：关；
- 删除卡上 source：首版不提供；
- 阻止系统睡眠：仅 active copy/encode/upload 时开启，可配置；
- 并发：优先 session 内 segment 并行受控，不能让多个大 session 抢满磁盘和 CPU。

## 12. 故障与恢复合同

| 故障                                         | 必须收敛到的行为                                                      |
| -------------------------------------------- | --------------------------------------------------------------------- |
| 扫描时拔卡                                   | candidate scan 失效；不创建空/半 spec job                             |
| copy 时拔卡                                  | `waiting_for_media`；已 durable checkpoint 保留                       |
| 相同盘符插入另一张卡                         | 不恢复旧 job；显示等待原介质或允许用户取消                            |
| manifest 在扫描后变化                        | source revision mismatch；旧 attempt fenced，重新扫描                 |
| 应用在文件 copy 后、checkpoint 前崩溃        | 从文件证据与较低 durable offset 恢复，不信任尾部 bytes                |
| 全文件 hash 完成、目录 commit 前崩溃         | staging 恢复并重放 seal/rename                                        |
| 编码进程崩溃                                 | 当前 pair partial 删除；已验证 pair 复用                              |
| pause/cancel 时 encoder 不退出               | job 显示 resource-stuck/typed failure，不能假装暂停成功               |
| derived manifest commit 后、资料库更新前崩溃 | completion outbox 重放 library projection                             |
| multipart complete 响应不明                  | 用已保存 completion/version 与 expected digest 验证；不能直接重传覆盖 |
| 上传最后 manifest 前崩溃                     | 数据对象可能存在但 bundle 不可见；重启续传并最后提交 manifest         |
| 远端 digest 不匹配                           | upload failed/integrity，绝不标记 verified 或允许清理 source          |

## 13. 验收标准

### 13.1 RP 输入合同

- fixtures 覆盖 `RawCaptureV2`、`LegacyMjpegSessionV5`、`ApplianceSpoolV6`、`SignedPublicationV1`。
- raw fixture 覆盖 SDK file transport 的 30/60 fps、完整与失败结果、坏 JPEG offset、帧 gap、IMU error。
- v5 fixture 覆盖 `video_mono` 兼容 role 但 side-by-side 布局、每段 PTS 重置、末段、序号缺口和 `raw/frames.jsonl` 时间累积；v1-v4 返回 `unsupported_legacy_schema`。
- spool fixture 覆盖 source 完整、缺段、重复段、未闭合末段、capture commit 冲突和编码中断。
- signed fixture 复用 LAN 的真实 schema/signature/path/hash contract suite。
- unknown major、路径逃逸、symlink/reparse、同大小错误内容全部 fail closed。

### 13.2 当前 Ubuntu-only 本地导入验收边界

- 只扫描 Ubuntu/Linux 由 OS 暴露的已挂载 Linux-native volume；推荐 ext4。
- 只枚举卷根的 `recordings/`、`YLX_RECORDINGS/` 及其直接 session 子目录。
- 扫描有固定资源上限，不跟随 link，不接受路径逃逸，也不把额外文件并入 inventory。
- 当前识别 signed publication schema，但所有 signed candidate 一律 fail closed；paired-key verification 是 future/deferred，接入前保持 `waiting_for_pairing_key` / policy-approval-required。
- unsigned source 只有在用户显式批准后进入 durable import，不能由自动策略代替批准。
- copy 每个 crash point、强制拔卡、I/O error 或应用退出后都不产生半个 `local_verified`。
- 原卡重插后重新扫描并关联 durable source/checkpoint；unsigned source 必须由用户再次显式
  批准后才恢复，不得由 watcher 自动准入；不同介质复用 mount path 不得误续传。
- 导入完成后 copy/verify 的 `File` 与 read lease 已离开作用域，后续操作只依赖已提交的 PC
  本地副本；用户可显式释放 generation 应用句柄并使用系统弹出，应用退出也会释放并等待资源结束。

以上是 implemented scope，构建、测试、fixture 和 Ubuntu 实机验证按当前指令后置，尚不能
表述为“已测试通过”或“已验收”。真实 codec probe/decode 也不在本阶段验收范围内。

### 13.3 后续跨平台介质验收

以下全部属于 future work：Windows、macOS、exFAT/FAT、`SelectedFolder`、自动挂载或
格式化、volume GUID/多 mount path 协调，以及为 exFAT 录制修改 Pi FIFO/runtime 布局。
后续交付仍需验证同一 volume 换盘符或 mount path 后的重匹配，以及不同 volume 复用路径
时不会误续传。

### 13.4 后续幂等与来源切换

- 同一 signed session 经 LAN、卡、LAN+卡、卡+LAN 都收敛到同一个 source revision 和资料库条目。
- 已验证文件不会因换来源而重传。
- active LAN attempt 切换到卡的首版语义必须明确：建议暂停/取消旧 attempt 后由同一 job 安装新 locator，不做两个 writer 热切换。
- raw provisional candidate 完整哈希后能与已有相同 content revision 合并。

### 13.5 后续规范化验收

本节不是当前 Ubuntu-only TF 卡本地导入的完成条件。只有真实 codec probe/decode、代表性
fixture、质量/体积/CV 门槛、encoder 分发和许可均获得批准后，才能发布 normalization
profile；在此之前必须 fail closed，不能声称 HEVC、MediaNormalizer 或自动规范化可用。

- 四类输入全部实际 decode/encode，并生成同 profile family 的 derived manifest。
- 左右目/source frame count 完全相同，duration 差异不超过一帧，完整解码无错误。
- restart/pause/cancel/retry 不重复有损编码已验证 pair。
- encoder/profile/app upgrade 不能把不同参数产物混进同一 DerivedRevision。
- 代表性数据通过第 6.7 节质量、体积和领域指标门槛。

### 13.6 后续上传与清理验收

本节不是当前 Ubuntu-only TF 卡本地导入的完成条件。对象存储 upload owner、completion-bound
remote byte verification、source archival policy 和 retention/deletion 尚未实现或批准。

- 只上传 frozen UploadBundleRevision；同 key 并发覆盖不能通过 completion-bound verify。
- 无 server checksum 时真实 stream readback hash 通过后才显示 verified。
- derived upload 不被误显示为 source/original backup。
- 任何自动 retention 策略都必须等待同 revision 的 remote verification、批准的 archive policy 和宽限期；首版默认关闭。

## 14. 分阶段交付建议

### Ubuntu-only MVP scope

首个 TF 卡交付只承诺 Ubuntu/Linux 上读取 OS 已挂载的 Linux 原生文件系统介质，推荐
ext4 卡或 ext4 分区。scanner 只检查卷根下 `recordings/`、`YLX_RECORDINGS/` 及其直接
session 子目录，并执行 bounded、no-follow、schema-aware classification。当前能识别 signed
schema，但 runtime 尚未接入 PC paired-key trust owner，所有 signed candidate 一律 fail closed，
并且绝不能降级为 unsigned。当前实际可达准入只有用户显式批准的 structurally validated
unsigned source。准入后的 source 只读复制到 PC staging，以 durable checkpoint、逐文件摘要和
原子 publication 完成本地导入；拔卡、原卡重插和进程崩溃可恢复，其中 unsigned 原卡重插
必须重新扫描并由用户再次显式批准后才能恢复，不能自动准入。完成后 I/O reader/lease 已
离开作用域；用户可显式释放 generation 应用句柄并使用系统弹出，退出时也会释放并 join。

本阶段不要求修改 Pi repo。`SelectedFolder`、Windows/macOS、exFAT/FAT、自动格式化或
挂载、Pi FIFO relocation、真实 codec probe/decode、质量批准的 HEVC、MediaNormalizer、
对象存储上传、retention/deletion 和自动化策略全部后置。实现范围的 verification 按当前
指令 deferred；未运行测试或实机验收，不能把本节理解为测试结果。

### Phase A：冻结 fixture 与合同

- 从真实 Pi/旧卡收集四类最小脱敏 fixture；
- 冻结 raw/spool admission schema、provenance 和 `SourceContentRevision`；
- 冻结 removable-media source outcome 与 LAN Range outcome 的 contract suite；
- 后续 normalization 阶段再运行 MJPEG/Pi H.264 质量 corpus，并完成 encoder 分发与许可评审。

### Phase B：只读卡导入

- Ubuntu OS-mounted Linux-native volume discovery；
- 固定 `recordings/`、`YLX_RECORDINGS/` roots 的 bounded scanner 与 detectors；
- MountedFile artifact adapter；
- 当前 explicit structurally validated unsigned approval；
- future/deferred paired signed verification；接入前 signed candidate 一律 fail closed；
- ImportJob、MediaGeneration fencing、staging、原卡重插和 crash recovery；
- local library projection、句柄释放和系统弹出引导。

### Phase C：MediaNormalizer

Future work，不属于当前 Ubuntu-only 本地导入里程碑：

- durable DerivationJob/segment ledger；
- raw/spool/H.264 input plan；
- libx265 reference profile；
- full decode/sync/quality validation；
- derived manifest 与 DerivedRevision。

### Phase D：对象存储与完整 workflow

Future work，不属于当前 Ubuntu-only 本地导入里程碑：

- UploadBundleRevision；
- 上传 derived media + provenance/metadata；
- completion-bound remote verification；
- SessionPipeline 策略、批量 UI、重启重放；
- 三平台 E2E 和真实卡拔出故障注入。

## 15. 已确定的当前范围与后续决定

当前已确定：

1. 只支持 Ubuntu/Linux OS-mounted Linux-native filesystem，推荐 ext4。
2. 只扫描固定 recording roots，不提供 `SelectedFolder`，本阶段不修改 Pi。
3. 当前 signed schema 可识别但生产准入一律 fail closed；paired-key trust owner 接入后才可验证准入，且 signed 永不降级为 unsigned。当前只有 structurally validated unsigned source 可经显式人工批准准入。
4. 只完成只读、durable、可恢复的 PC 本地导入；I/O reader/lease 按作用域释放，导入后提供
   显式应用句柄释放与系统弹出引导，应用退出也会释放并等待资源结束。
5. 不宣称真实 codec probe、normalization、upload、retention 或任何自动策略已经可用。

后续仍需决定：Windows/macOS 与 exFAT/FAT 产品路径、Pi FIFO relocation、真实 codec
probe 的 admission 合同、HEVC/CRF/质量门槛、encoder 许可与分发、对象存储 bundle 与
remote verification、source archival 和 retention policy。所有这些决定都不能反向扩大
当前 Ubuntu-only MVP 的验收范围。

## 16. 证据边界

本文描述的 RP 事实基于 2026-08-03 获取的 `origin/main` 提交 `b9c4cc2`。现场部署、未提交配置或其他分支若改变 raw 文件名、schema、编码开关或 publication 行为，必须作为 fixture 和版本化 detector 加入，不能靠文档假设兼容。

codec 的方向性选择是 HEVC；具体 profile 的质量与体积数字仍需真实 YLX 数据证明。任何未运行的 benchmark 都不是通过结果，任何对象存储 metadata 都不能替代远端 bytes 的真实摘要验证。

平台 API、RP-YLX 固定提交源码链接、codec 官方资料及“事实/建议”分界详见
[`research/SD_CARD_AND_VIDEO_CODEC_EVIDENCE.md`](research/SD_CARD_AND_VIDEO_CODEC_EVIDENCE.md)。
