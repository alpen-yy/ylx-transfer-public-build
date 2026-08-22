# SD/TF 卡导入与视频编码：代码事实、方案建议和验证门槛

> 文档状态：代码事实与研究证据；Ubuntu-only 本地导入范围已有实现但按当前指令暂未测试，codec/normalization/upload 内容均是未来建议
>
> 调研日期：2026-08-03（Asia/Shanghai）
>
> 历史 PC 端前提：本文曾假设 [`ylx-transfer#1`](https://github.com/mirrorbloom/ylx-transfer/issues/1)
> 的完整目标均已实现；该假设不代表当前实现或验证状态
>
> RP-YLX 上游基线：[`b9c4cc2321cd802584b662c1e7364ef2b9cdc62a`](https://github.com/mirrorbloom/RP-YLX/commit/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a)
> （调研时最新 `origin/main`）
>
> 本地集成分支参考：[`2db57ae68e04197397b8ac84f4d71548aa2fcb36`](https://github.com/mirrorbloom/RP-YLX/commit/2db57ae68e04197397b8ac84f4d71548aa2fcb36)

本文回答两个问题：RP-YLX 目前究竟把数据写在哪里、以什么形式写；PC 插入 SD/TF 卡后
当前怎样安全完成本地导入，以及未来怎样扩展编码和对象存储。文中的“代码事实”固定到
上游 `origin/main` 提交 `b9c4cc2`；本地 `2db57ae` 是有独立 transfer 集成改动的分叉
checkout，不作为“最新录制格式”的替代基线。“建议”仍需产品确认和代表性样片测试。

当前 implemented scope 仅包括 Ubuntu/Linux 读取 OS 已挂载的 Linux 原生文件系统 TF 卡
（推荐 ext4），固定扫描卷根 `recordings/` 与 `YLX_RECORDINGS/`，执行 constrained scan，
并把用户显式批准的 structurally validated unsigned source 以只读、durable、可恢复方式导入
PC 本地，同时处理拔卡、原卡重插和进程崩溃；copy/verify 的 `File` 与 read lease 按 I/O
作用域释放，用户可在完成后显式释放 generation 应用句柄并使用系统弹出，应用退出时也会
释放并等待资源结束。unsigned 原卡重插后必须重新扫描并由用户再次显式批准，不能由 watcher
自动准入。scanner 能识别 signed publication
schema，但 Ubuntu runtime 尚未接入 PC paired-key trust owner，因此所有 signed candidate 的
生产准入一律 fail closed，并保持 `waiting_for_pairing_key` / policy-approval-required 语义；
未来接入 verifier 前，绝不得把 signed publication 降级为 unsigned。

Pi 代码修改、`SelectedFolder`、Windows/macOS、exFAT/FAT、自动挂载/格式化、真实 codec
probe/decode、质量批准的 HEVC、MediaNormalizer、对象存储 upload/verify、retention/deletion
及自动扫描/导入/规范化/上传/删除/prevent-sleep 策略均是 future work。当前没有运行构建、
测试或实机验证，因此“implemented scope”不表示“已测试通过”。

## 1. 结论先行

1. **不能把现状概括成“只录原始视频”。** SDK 的默认 `file` 模式确实持久化
   `stereo.mjpeg`；设备端生产 daemon 则使用 FIFO，把双目 MJPEG 先无损封装成
   30 秒 `spool/source_*.mp4`，随后已经编码为左右两路 H.264。当前 PC 导入器按 schema
   分类并保存 source bytes；真实 codec probe/decode 和 HEVC encode 尚未进入本里程碑。
2. **未来跨平台产品可评估外置数据卡。** Ubuntu
   Core 系统卡的数据分区是 ext4；Windows 和 macOS 都不能提供普通桌面应用可依赖的
   原生 ext4 插卡体验。当前只交付 Ubuntu/Linux OS-mounted Linux-native 介质导入。
3. **未来跨平台数据卡可评估 exFAT，但 RP 端有前置改造。** 当前 daemon 在录制目标的
   `raw/` 下创建 POSIX FIFO；exFAT 不支持这种文件节点。要把瞬态 FIFO 移到设备本地
   ext4/tmpfs，只把普通文件、临时文件和原子完成标记写到 exFAT 数据卡。该 Pi 改造不属于
   当前里程碑。
4. **卡始终是只读来源。** 先把一个会话完整复制到 PC 本地 staging，逐文件校验并
   durable commit；到达 `LocalReady` 后不再需要卡，用户可显式释放 generation 应用句柄并
   使用系统弹出，应用退出时也会释放。编码和上传只读本地副本，不在卡上原地转码、改名、
   写“已导入”标记或删除。
5. **未来统一派生编码候选为 H.265/HEVC Main。** 它可能是 H.264 与 AV1 之间更务实的
   质量/体积折中。标准设计和受控主观测试支持“相同主观质量下显著降低码率”，但
   RP-YLX 的实际节省率和速度必须用本项目样片测得，不能把“减半”写成容量承诺。
6. **未来主设计建议所有受支持输入生成统一 HEVC 派生资产。** 对 appliance H.264
   来说这是第二代有损编码，不能描述为“压缩原始视频”。必须保留不可变 source evidence，
   用实拍样片证明体积收益足以覆盖质量损失；若质量门槛不通过，该 profile 不得发布，
   需要显式修订设计，而不是悄悄 passthrough 或降低门槛。当前不实现或宣称该能力。

## 2. RP-YLX 当前实际保存什么

### 2.1 两条生产者路径

| 生产者                        | 视频传输与持久化                                   | 典型文件                                                             | 未来 normalization 建议                                                 |
| ----------------------------- | -------------------------------------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| SDK `CaptureSession` 默认模式 | `video_transport="file"`，持久 MJPEG               | `capture.json`、`stereo.mjpeg`、`frames.jsonl`、`imu.jsonl`          | 校验完整后拆分双目并编码                                                |
| Appliance capture daemon      | `video_transport="fifo"`；MJPEG 先分段封装，再编码 | `spool/source_*.mp4`，最终 `video/left_*.mp4` 与 `video/right_*.mp4` | 未编码 spool 做首次编码；完整 H.264 按统一 profile 生成 HEVC 二代派生件 |

SDK 构造器默认使用 `file`，路径映射把视频写到普通文件 `stereo.mjpeg`；FIFO 模式则
使用 `stereo.mjpeg.pipe`。两种模式共享 `capture.json`、`frames.jsonl` 和
`imu.jsonl`。[路径定义](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/src/ylx_imu/capture.py#L27-L64)
和 [默认构造参数](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/src/ylx_imu/capture.py#L433-L450)
是这里的直接证据。raw manifest 使用 schema `ylx.stereo_imu.raw.v2`，明确记录
`video.encoding=mjpeg`、transport、persistent 和最终 state；它通过临时文件、
`fsync`、replace 与目录 `fsync` 提交
（[manifest 内容](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/src/ylx_imu/capture.py#L195-L256)，
[原子写入](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/src/ylx_imu/capture.py#L114-L129)）。
这里的 `raw` 是流水线/目录命名，不表示未压缩像素；manifest 明确声明的视频 codec 是
MJPEG。

设备 daemon 明确构造 `CaptureSession(..., video_transport="fifo")`，并把会话交给
持久 encoding queue
（[recorder 调用链](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/recorder.py#L1049-L1134)）。
FFmpeg 在 spool 阶段使用 `-c:v copy`，所以 `source_*.mp4` 仍是 MJPEG，只是换了
MP4 容器；每段约 30 秒并使用 fragmented MP4 参数
（[spool mux 参数](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/encoding_queue.py#L282-L320)）。

后台编码随后把 3840x1080 side-by-side 图像裁成两路 1920x1080@30，使用
`h264_v4l2m2m`、`yuv420p`、每眼 8 Mbit/s、两秒 GOP 和 MP4 faststart
（[编码参数](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/encoding_queue.py#L323-L366)）。
两路合计目标约 16 Mbit/s，即纯视频约 **7.2 GB/小时**，尚未计容器和元数据开销。
只有左右输出都通过 codec、尺寸、帧率、帧数、时长和完整解码检查后，源码才会删除
（[验证与删除门槛](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/encoding_queue.py#L2409-L2543)）。

因此，“新增编码功能”的准确含义是：

- 对 SDK `stereo.mjpeg` 和已 durable commit、但尚未编码的 appliance spool，增加 PC
  本地编码；
- 对 appliance 的完整 H.264 会话，先按签名 publication 原样导入和验证，再生成统一的
  HEVC 存储派生件；
- H.264 -> HEVC 是明确的第二代有损转换。source publication 和 source hashes 保持不变，
  派生结果使用新的 derived revision，不能覆盖或冒充 Pi 原件。

#### 历史 appliance v5 是固定 MJPEG copy pipeline，不是当前配置开关

为覆盖现场所称的“不编码”卡，另外核对了编码队列引入前的历史提交
[`faa7f8f`](https://github.com/mirrorbloom/RP-YLX/commit/faa7f8f7df7743e0820b4d6d4f4610f833d35f6b)。
该版本没有 `disable_encoding` 或同类布尔开关；它的整条 appliance pipeline 固定对
MJPEG 执行 `-c:v copy`，输出 `video/segment_%05d.mp4` 的 300 秒 fragmented MP4
分段，不 decode/不重编码
（[v5 mux 命令](https://github.com/mirrorbloom/RP-YLX/blob/faa7f8f7df7743e0820b4d6d4f4610f833d35f6b/capture/src/ylx_capture/recorder.py#L49-L82)）。

v5 `session.json` 的 `schema_version=5`，明确记录 `camera.layout=left_right_side_by_side`、
`video_codec=mjpeg`、`video_encoder=copy`、`video_container=fragmented_mp4` 和
`video_segments[]`
（[v5 manifest](https://github.com/mirrorbloom/RP-YLX/blob/faa7f8f7df7743e0820b4d6d4f4610f833d35f6b/capture/src/ylx_capture/recorder.py#L145-L170)）。
它有 complete/native/mux 结构门槛，但没有当前 signed publication 和逐文件 hash，
所以 PC 必须将它分类为 `legacy_removable_media + locally_validated_unsigned`，
完整导入后自行计算 inventory SHA-256。

还有两项不能由新版角色名或容器时间戳猜测：

- 当前兼容分类可能把 pre-v6 单轨文件命名为 `video_mono`，但 v5 实际是
  3840x1080 左右并排双目，每眼 1920x1080。detector 必须以
  `camera.layout=left_right_side_by_side` 为准。
- v5 使用 `-reset_timestamps 1`，每段 PTS 从 0 重新开始，也没有 v6
  `video_segment_timing`。PC 需要核对每段帧数/时长后累积重建会话时间轴，
  并以 `raw/frames.jsonl` 作为采集时间证据。

因此首版应使用显式 `LegacyMjpegSessionV5` detector；v1-v4 没有真实 fixture 和
固定合同时应返回 `unsupported_legacy_schema`，不允许套用 v5 逻辑。

### 2.2 录制位置与会话布局

Ubuntu Core 的内部 recording root 是：

```text
/var/snap/ylx-capture/common/recordings/<session>/
```

Snap 同时给 capture/transfer 服务设置 `$SNAP_COMMON/recordings`
（[`snapcraft.yaml`](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/snap/snapcraft.yaml#L66-L95)），
Core 文档把 `$SNAP_COMMON` 展开为 `/var/snap/ylx-capture/common`
（[部署布局](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/docs/UBUNTU_CORE_26.md#L395-L405)）。

配置示例中的默认外置挂载点是 `/media/YLX_DATA`，代码强制在所选 target 下使用
`recordings` 目录，因此拔下外置卡后，卷根所见是：

```text
recordings/<session>/
```

证据见 [外置 target 配置](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/config/config.example.toml#L17-L28)、
[storage target 选择](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/storage.py#L271-L324)
和 [目录名约束](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/config.py#L258-L292)。

会话名实际由本地时区、微秒和 UTC offset 生成，例如
`20260803T142233.123456+0800`；导入器不应硬编码一个旧的秒级正则。当前 catalog 的
正确做法也是枚举 recording root 的直接子目录，再逐个解析
（[命名代码](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/recorder.py#L1049-L1068)，
[catalog 枚举](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/session_catalog.py#L38-L61)）。

一个 appliance 会话可能包含：

```text
<session>/
  session.json
  events.jsonl
  capture.commit.json
  encoding.json
  publication_manifest.json       # 只有发布成功后才有
  video/
    left_00000.mp4
    right_00000.mp4
  spool/
    segments.csv
    source_00000.mp4               # 未完成编码或恢复时可能仍在
  raw/
    frames.jsonl
    imu.jsonl
    stereo.mjpeg.pipe              # 活跃录制时的 FIFO；停止后通常删除
  preview/
    imu.jsonl
```

完整布局的上游说明见
[`capture/README.md`](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/README.md#L139-L172)。
需要特别注意：README 和初始 `session.json` 声明了 `raw/capture.json`，但当前生产
adapter 是 `SupervisedNativeCapture`，worker 直接运行 native capture 并通过 stdout
返回 summary，没有调用 SDK 的 raw manifest writer。因此 **appliance 卡导入不能把
`raw/capture.json` 设为必需文件**；只能把它视为存在时可用的附加证据
（[默认 adapter](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/recorder.py#L736-L751)，
[supervised worker](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/src/ylx_imu/capture.py#L941-L1081)）。

### 2.3 四种“完成”不能混为一谈

| 文件                        | 权威范围                                                             | 不代表什么                                 |
| --------------------------- | -------------------------------------------------------------------- | ------------------------------------------ |
| `capture.commit.json`       | native/mux/preview 和 source segment 已越过 durable capture boundary | 不代表编码或发布完成                       |
| `encoding.json`             | 编码队列状态、source inventory、每段左右输出状态                     | 不单独证明发布身份                         |
| `session.json`              | 本机会话摘要与最终完整性状态                                         | 不是网络/离线发布授权                      |
| `publication_manifest.json` | 发布文件 allowlist、逐文件 size/SHA-256、签名 envelope               | 若 PC 不信任签名 key，不能独自证明设备身份 |

`capture.commit.json` 使用 temp + `fsync` + replace + directory `fsync`
（[durable outbox](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/capture_durable_outbox.py#L431-L445)）。
编码完成时先原子提交 `encoding.json(state=complete)`，最后才提交
`session.json(state=complete)`
（[完成顺序](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/encoding_queue.py#L2086-L2317)）。

发布模块明确不把 `session.json` 当作发布授权；签名 publication manifest 才是
authoritative marker。它只列出最终视频、`session.json`、`capture.commit.json` 和
可选原始 IMU，不发布 spool、frames 或日志
（[发布契约](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/publication.py#L1-L75)，
[allowlist](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/publication.py#L871-L889)）。
正式 schema 要求逐文件摘要和 Ed25519 signature
（[publication schema](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/docs/transfer-api/v1/schemas/publication-manifest.schema.json#L1-L133)），
最终清单也通过 temp、`fsync`、atomic rename 和 directory `fsync` 提交
（[publication commit](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/publication.py#L1373-L1420)）。

卡上的 size/hash 能发现偶发损坏，但如果攻击者同时替换数据和未签名清单，它不能证明
来源。即使 manifest 自带公钥，签名也只证明“由这个 key 签过”；PC 仍须把 key/fingerprint
绑定到此前物理配对确认的设备身份。没有这条外部信任锚时，只能显示“内容自洽，设备身份
未认证”。

## 3. 卡导入判定矩阵

扫描器按文件内容和 schema 判定，不按目录名猜测，不假设存在一个未见于上游代码的
`disable_encoding` 配置项；视频 codec、尺寸、帧率和 stream 数还必须由 `ffprobe` 验证。

| 发现的最强证据                                                                                                    | 分类                   | 自动动作                                                                    | 对象存储资格                      |
| ----------------------------------------------------------------------------------------------------------------- | ---------------------- | --------------------------------------------------------------------------- | --------------------------------- |
| 签名 `publication_manifest.json` 有效，signer 已受信，全部 size/hash 匹配                                         | `TrustedPublished`     | 原样复制 publication allowlist；当前 H.264 再按统一 HEVC profile 生成派生件 | 派生件验证后可自动上传            |
| `capture.commit.json` 有效，`encoding.json` 与 `session.json` 均 complete/integrity-valid，但没有受信 publication | `CompleteUnpublished`  | 复制最终左右 H.264 和权威元数据，编码 HEVC 并生成 PC 派生清单               | 仅按明确的“未认证来源”策略上传    |
| `capture.commit.json` 有效，session/encoding 仍为 encoding，closed spool inventory 自洽                           | `CommittedRawSpool`    | 复制全部闭合 `source_*.mp4` 与元数据到本地，再编码                          | PC 编码、校验并提交派生件后可上传 |
| standalone `capture.json` 为 complete、persistent file/MJPEG，`stereo.mjpeg` 存在                                 | `SdkRawCapture`        | 复制 raw dataset 到本地，再拆眼和编码                                       | PC 编码、校验并提交派生件后可上传 |
| `session.json.schema_version=5`，state complete，`video_segments[]` 与 MJPEG/fMP4 文件一致                        | `LegacyMjpegSessionV5` | 按 side-by-side 布局和重置 PTS 合同导入，本地求全 inventory hash 后编码     | 仅按明确的“未认证来源”策略上传    |
| `session.json.schema_version` 为 1-4，但没有对应真实 fixture/固定合同                                             | `UnsupportedLegacy`    | 显示版本不受支持与导出诊断；不猜测 v5 兼容                                  | 禁止自动上传                      |
| commit 缺失/无效，或 state 为 active、interrupted、failed，或文件/摘要不匹配                                      | `RecoveryOnly`         | 只展示诊断和手工恢复入口                                                    | 禁止自动上传                      |

这张表故意把“完整性”和“真实性”分开。PC 为 raw/unpublished 输入生成的清单应使用
PC 自己的 schema/签名域，至少记录：source device/session identity、原始文件 SHA-256、
源 manifest/commit 的原始 bytes hash、编码器及版本、完整参数、输出文件 size/SHA-256、
创建时间和 PC key identity。它不能沿用或伪造设备端 `publication_manifest.json` 的
签名身份。

## 4. 系统卡与外置数据卡的未来跨平台边界

### 4.1 为什么系统卡不能成为跨平台主路径

Ubuntu Core 的 `ubuntu-data` 是可写 ext4，`$SNAP_COMMON/recordings` 位于该分区；
见 Ubuntu 官方的
[storage layout](https://documentation.ubuntu.com/core/explanation/core-elements/storage-layout/)。
Linux 上的 `mount -o ro` 也不是应用可依赖的“绝对不写卡”：内核 ext4 文档说明，脏文件
系统即使以 read-only 挂载仍可能回放 journal；`ro,noload`/`norecovery` 才阻止 journal
加载，但文件系统此时可能不一致
（[ext4 admin guide](https://docs.kernel.org/admin-guide/ext4.html)，
[`mount(8)` ext4 options](https://man7.org/linux/man-pages/man5/ext4.5.html)）。因此 PC 应用
不自动挂载、修复或扫描系统卡；Linux 高级恢复也只接受由操作员安全预挂载的目录或磁盘
镜像，并明确其 best-effort 边界。
Windows 的官方 WSL 文档不仅要求管理员权限，还明确说明 `wsl --mount` 当前不支持
USB/flash/SD-card reader
（[WSL mount limitations](https://learn.microsoft.com/en-us/windows/wsl/wsl2-mount-disk)）。
macOS Disk Utility 官方可格式化列表包含 APFS、HFS+、MS-DOS(FAT) 和 exFAT，但不含 ext4
（[macOS file-system formats](https://support.apple.com/guide/disk-utility/file-system-formats-dsku19ed921c/mac)）。

所以产品边界应写清楚：

| 介质                     | Linux                                               | Windows/macOS            | 建议定位                                 |
| ------------------------ | --------------------------------------------------- | ------------------------ | ---------------------------------------- |
| RP 的 Ubuntu Core 系统卡 | 仅限操作员预挂载目录/镜像的高级恢复；应用不自动挂载 | 普通桌面应用不可可靠直读 | 不纳入未来跨平台首版                     |
| 独立外置数据卡，exFAT    | 需随目标 Ubuntu Core 镜像做读写与断电测试           | 原生可挂载               | 未来跨平台首版主路径候选                 |
| FAT32 数据卡             | 可读写，但单文件上限 4 GiB                          | 原生可挂载               | 未来只兼容已可靠分段的旧卡，不作为新默认 |

SD Association 规定 SDHC 使用 FAT32，SDXC/SDUC 使用 exFAT
（[capacity standards](https://www.sdcard.org/developers/sd-standard-overview/capacity-sd-sdhc-sdxc-sduc/)）；
Microsoft 的文件系统对比给出 FAT32 单文件 4 GiB 上限，同时说明 FAT32/exFAT 均无
metadata journaling
（[File System Functionality Comparison](https://learn.microsoft.com/en-us/windows/win32/fileio/filesystem-functionality-comparison)）。
这也是未来跨平台新数据卡应优先评估 exFAT、长录制应继续按独立 segment 落盘的原因；
它不是当前 Ubuntu-only MVP 的文件系统支持承诺。

### 4.2 当前 RP 端对 exFAT 的阻塞项

生产 recorder 当前直接在选中的 session filesystem 上执行
`os.mkfifo(raw/stereo.mjpeg.pipe)`
（[FIFO 创建代码](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/recorder.py#L549-L557)）。
Python 把 `mkfifo` 定义为 Unix named pipe 文件节点
（[`os.mkfifo`](https://docs.python.org/3/library/os.html#os.mkfifo)）；FAT/exFAT 不提供
这种 POSIX 特殊文件类型。因此，不能只把现有外置 target 格式化为 exFAT 就宣称完成。

RP 端前置设计应是：

1. 把 `stereo.mjpeg.pipe` 放在 device-local ext4 runtime 目录或 tmpfs；它不是录制资产，
   停止/重启时清理。
2. FFmpeg 从本地 FIFO 读取，只向数据卡写普通的 bounded source segments、元数据 temp
   文件和原子完成标记。
3. 继续以 `capture.commit.json`、`encoding.json`、`session.json` 和 publication manifest
   表达完成边界，不能靠目录“看起来不再增长”。
4. 在真实卡、真实断电和重新插卡上验证 `fsync`/rename 语义。FAT-like 文件系统没有
   Unix mode bits；当前权限模块已经承认这一差异
   （[权限策略](https://github.com/mirrorbloom/RP-YLX/blob/b9c4cc2321cd802584b662c1e7364ef2b9cdc62a/capture/src/ylx_capture/recording_permissions.py#L10-L42)），
   但这不等于 durable commit 已经经过 exFAT 硬件验证。

### 4.3 PC 不应尝试“找所有 SD 卡”

操作系统暴露的是卷、文件系统和设备事件，不是可靠的业务含义“这是 YLX SD 卡”。
USB 读卡器内的 microSD 在 Windows 甚至可能报告为 `BusTypeUsb` 而不是 `BusTypeSd`。
正确策略是：**枚举所有当前已挂载且可读的候选卷，再用受约束的 RP-YLX 内容布局和
manifest schema 识别数据卡**。

- Windows 完整枚举应使用 Volume GUID，并用
  [`GetVolumePathNamesForVolumeNameW`](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumepathnamesforvolumenamew)
  解析盘符和目录挂载点；`DRIVE_REMOVABLE` 只能作 UI 提示
  （[`FindFirstVolumeW`](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-findfirstvolumew)，
  [`GetDriveTypeW`](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getdrivetypew)）。
  Windows 8+ 可用
  [`CM_Register_Notification`](https://learn.microsoft.com/en-us/windows/win32/api/cfgmgr32/nf-cfgmgr32-cm_register_notification)
  收 PnP 通知；官方明确不会补发已经存在的设备，因此顺序必须是“先注册，再全量枚举”。
- macOS 用 Disk Arbitration 接收 disk appeared/disappeared，并从已挂载 volume URL 开始
  文件级扫描；不要解析 `/Volumes/...` 字符串来猜设备拓扑
  （[Disk Arbitration Programming Guide](https://developer.apple.com/library/archive/documentation/DriversKernelHardware/Conceptual/DiskArbitrationProgGuide/Introduction/Introduction.html)）。
  正式 app 还需声明
  [`NSRemovableVolumesUsageDescription`](https://developer.apple.com/documentation/bundleresources/information-property-list/nsremovablevolumesusagedescription)；
  sandbox build 用用户选择的 read-only entitlement/security-scoped access，权限拒绝应是
  明确状态，不得误报“卡损坏”。
- Linux 用 UDisks2 的 Drive/Block/Filesystem 对象获取 removable 提示、挂载点及变更事件；
  filesystem API 返回的是字节数组 mount points，不能假设只存在 `/media/$USER/...`
  （[UDisks2 Drive](https://storaged.org/doc/udisks2-api/latest/gdbus-org.freedesktop.UDisks2.Drive.html)，
  [UDisks2 Filesystem](https://storaged.org/doc/udisks2-api/latest/gdbus-org.freedesktop.UDisks2.Filesystem.html)）。
  exFAT/VFAT 是可选内核能力；启动时需实际检测目标发行版是否能挂载，不能只因系统名是
  Linux 就承诺支持。

Volume GUID、卷序列号、mount path、卷标、容量和 removable/bus type 都不是内容身份。
恢复绑定至少使用“平台卷标识 + YLX dataset/session identity + manifest/commit hash + 源文件
size/hash”；绝不能仅因下一次又挂载成 `E:` 就把旧 `.partial` 续到另一张卡。

### 4.4 只读导入不变量

1. 只扫描 mount root 下固定深度的 `recordings/<direct-child>` 和 standalone raw candidate；
   不递归遍历整张卡。
2. 逐组件安全解析路径，只接受 regular file；拒绝绝对路径、`..`、symlink、FIFO、socket、
   block/character device、Windows reparse point/junction、macOS alias 等逃逸。FIFO 本身
   只是内核 pipe 的目录项，数据不存于该文件
   （[`fifo(7)`](https://man7.org/linux/man-pages/man7/fifo.7.html)）。manifest 中只接受
   规范相对路径和 allowlist。
3. 普通用户做文件级读取，不直接打开 physical disk/raw volume，不偷偷分配盘符或要求
   应用常驻管理员。Windows 官方说明 physical disk/volume 的直接访问受限
   （[`CreateFileW`](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)）。
4. 卡可以是只读或锁定状态；导入仍应成功。所有 `.partial`、checkpoint、派生 manifest、
   编码临时文件和上传状态都写 PC 本地资料库。
5. 导入前检查 PC 本地可用空间，预算至少覆盖“尚未复制的源 bytes + 最大编码临时输出 +
   配置的安全余量”；空间不足在读卡前失败，不能靠固定百分比猜测。
6. 复制时持续检查来源 generation；同时计算 source SHA-256，目标完成后重新读取本地文件
   再算一次 SHA-256，并验证 size/hash。无来源 hash 的 raw 文件只有完整复制和双侧 hash
   一致后才能冻结 immutable source revision。mtime 只作诊断；FAT 写时间可只有 2 秒粒度，
   不能用“时间戳暂时未变”推断录制完成
   （[Windows file times](https://learn.microsoft.com/en-us/windows/win32/sysinfo/file-times)）。
7. 拔卡或读错误使任务进入 `waiting_for_card`，不是 `network_error` 或永久失败；保留本地
   partial 和已验证 ledger。重新插卡后先重新认证 dataset identity；unsigned source 还必须
   由用户再次显式批准，才能安装新 locator 并按身份强度续传或从头重验。
8. 纯读取流程不否决用户拔卡。收到移除事件后停止派发、取消并排空在途 I/O、关闭句柄；
   Windows 的 [`CancelIoEx`](https://learn.microsoft.com/en-us/windows/win32/fileio/cancelioex-func)
   只请求取消而不等待完成，不能在调用后立即复用 buffer。
9. 应用内“安全弹出”必须先关闭自身全部句柄和 watcher。Windows 使用
   [`CM_Request_Device_EjectW`](https://learn.microsoft.com/en-us/windows/win32/api/cfgmgr32/nf-cfgmgr32-cm_request_device_ejectw)
   并展示 veto；不得用结果不可预测的强制 dismount 代替。

建议状态序列：

```text
Detected -> Scanning -> CopyingToLocal(.partial) -> LocalReady
                                                      |
                                                      v
                                      Normalizing/Encoding -> Uploading
                                                                |
                                                                v
                                                  RemoteVerified -> Done
```

卡只影响 `Scanning` 和 `CopyingToLocal`。一旦 durable `LocalReady`，编码、上传和失败重试
都不再依赖卡，UI 可以明确提示“本地副本已验证，可以安全弹出”。

## 5. 编码格式选择

### 5.1 候选比较

| 编码            | 质量/体积                             | 编码成本                                   | 兼容性与本项目现状                               | 结论                                               |
| --------------- | ------------------------------------- | ------------------------------------------ | ------------------------------------------------ | -------------------------------------------------- |
| H.264/AVC High  | 基线；体积最大                        | 最成熟，软硬件路径广                       | RP appliance 已验证；Windows/浏览器/播放器最稳妥 | 保留为 source/兼容归档策略，不作为统一派生 profile |
| H.265/HEVC Main | 同等主观质量下通常显著低于 H.264 码率 | 编码决策更重，硬件与软件实现差异大         | 主流新设备可解码，但授权与旧终端支持需确认       | **选择为所有受支持 Pi 输入的统一对象存储派生编码** |
| AV1 Main        | 压缩潜力高、开放媒体联盟主导          | 软件编码通常最重，硬件覆盖与旧平台支持不均 | 适合后续离线归档评估                             | 不作为 V1 默认，保留 benchmark lane                |

ITU 对 HEVC 的标准定位是达到 H.264/AVC 前代约一半码率
（[ITU press release](https://www.itu.int/net/pressoffice/press_releases/2013/01.aspx)）。
更严格的标准设计者对比研究在特定 Full HD/WVGA、8-bit 4:2:0 样本和参考编码器上，
测得 HEVC Main 在相同主观 MOS 下相对 AVC High 平均节省 49.3% 码率；客观等 PSNR 的
随机访问配置平均节省 35.4%
（[论文摘要与出处](https://www.microsoft.com/en-us/research/publication/comparison-of-the-coding-efficiency-of-video-coding-standards-including-high-efficiency-video-coding-hevc/)，
[DOI](https://doi.org/10.1109/TCSVT.2012.2221192)）。这些数字来自特定测试集和
参考 encoder，不是 RP-YLX 容量承诺。合理目标是“先验证约 35%-50% 的码率下降区间”，
而不是写死“必定减半”。

H.264 与 H.265 标准都不规定 encoder 的搜索过程，因此标准不能推出一个固定的“HEVC
慢几倍”结论
（[H.264 V15](https://www.itu.int/rec/T-REC-H.264-202408-I/en)，
[H.265 V9](https://www.itu.int/rec/T-REC-H.265-202309-S/en)）。RFC 7798 只定性说明
HEVC 编码计算需求明显更高，并介绍其并行化工具
（[RFC 7798 §1.1.3](https://www.rfc-editor.org/rfc/rfc7798.html#section-1.1.3)）。
实际吞吐必须分别测软件编码和目标 PC 的硬件 encoder。

AV1 的公开规范和参考软件由 Alliance for Open Media 维护
（[AOMedia AV1 specification](https://aomedia.org/specifications/av1/)，
[固定规范仓库提交](https://github.com/AOMediaCodec/av1-spec/tree/5e04f3f75e73a5898d7616c47c52f032144b8f80)）。它值得作为
后续离线归档候选，但 V1 不能在没有目标 PC 编码吞吐、解码设备矩阵和端到端质量样片的
情况下只按理论压缩率选择。浏览器/平台兼容差异可查
[MDN 固定版本的 H.264 广泛兼容说明](https://github.com/mdn/content/blob/7ed7b730bf88307cc6cf34b82bb1d735b9a1aa1f/files/en-us/web/media/guides/formats/video_codecs/index.md#L638-L642)、
[HEVC 浏览器限制](https://github.com/mdn/content/blob/7ed7b730bf88307cc6cf34b82bb1d735b9a1aa1f/files/en-us/web/media/guides/formats/video_codecs/index.md#L1043-L1048)
和 [AV1/Safari 硬解限制](https://github.com/mdn/content/blob/7ed7b730bf88307cc6cf34b82bb1d735b9a1aa1f/files/en-us/web/media/guides/formats/video_codecs/index.md#L477-L486)。
Microsoft 自己的固定文档也说明 H.265 并非所有设备都支持，H.265/AV1 可能依赖可选 codec
package
（[Windows codec matrix](https://github.com/MicrosoftDocs/windows-dev-docs/blob/518c7d79db786d6f27191ed1510e8ea8b5a78c26/hub/apps/develop/media-authoring-processing/supported-codecs.md#L708-L728)，
[optional packages](https://github.com/MicrosoftDocs/windows-dev-docs/blob/518c7d79db786d6f27191ed1510e8ea8b5a78c26/hub/apps/develop/media-authoring-processing/supported-codecs.md#L846-L881)）。

### 5.2 建议的 HEVC 派生 profile

以下是与主设计一致的 **候选 profile**，不是未经实拍 benchmark 就能发布的参数承诺：

| 字段                     | 候选值                                                                                                               |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| profile family id        | `hevc-main-cfr-sourcefps-gop2s-x265-slow-v1`                                                                         |
| codec/profile            | H.265/HEVC Main，8-bit                                                                                               |
| chroma                   | `yuv420p`，避免引入当前链路没有验证过的 10-bit/4:2:2 兼容问题                                                        |
| geometry                 | 保留单目源分辨率，不缩放；当前 appliance 通常为每眼 1920x1080                                                        |
| frame rate               | 保留 manifest/frame evidence 验证过的 source FPS，不静默抽帧；appliance 通常 30 fps，SDK raw 可能 60 fps             |
| time base                | `1/90000`；30 fps 每帧 3000 ticks，60 fps 每帧 1500 ticks                                                            |
| random access            | 2 秒 closed GOP：`keyint=2*fps`、`min-keyint=2*fps`、`open-gop=0`，禁止 scene-cut 插入额外关键帧，左右关键帧位置一致 |
| container / sample entry | MP4 / `hvc1`；左右对齐的独立 30 秒 bounded segments，末段可短                                                        |
| quality variant          | MJPEG/spool 首代：x265 `CRF 20`；Pi H.264 二代：`CRF 18`；二代失败时仅允许独立版本的 `CRF 16` retry variant          |
| preset                   | x265 `slow`                                                                                                          |
| audio                    | 无，不创建空 audio track                                                                                             |
| metadata                 | IMU、frames/timing 和 provenance 独立保留；不伪造未知 color metadata                                                 |

MJPEG `CRF 20`、Pi H.264 `CRF 18`、二代 retry `CRF 16` 和 preset `slow` 都是待实测的
候选；两个 generation 必须拥有不同的 exact profile/variant revision。x265 官方说明
preset 是编码速度/压缩效率权衡，CRF 的输出大小取决于内容复杂度
（[preset docs, fixed commit](https://github.com/videolan/x265/blob/419182243fb2e2dfbe91dfc45a51778cf704f849/doc/reST/presets.rst#L9-L18)，
[CRF docs, fixed commit](https://github.com/videolan/x265/blob/419182243fb2e2dfbe91dfc45a51778cf704f849/doc/reST/cli.rst#L1630-L1637)）。

`yuv420p` 也只是候选输出合同。源码证明当前 Pi H.264 输出是 8-bit
`yuv420p`，但不能证明实机 UVC MJPEG 的 JPEG chroma sampling、range 和 color
matrix。冻结 profile 前必须检查真实 JPEG SOF/解码 pixel format；若源是
4:2:2，转 4:2:0 是额外且不可逆的色度降采样，必须进入双目/CV 质量回归。
Main10 不会恢复 8-bit 源中不存在的精度，也不会消除 4:2:2 -> 4:2:0 的信息损失。

候选发布门槛也必须与参数分开记录，当前提案是：

1. 所有结构/完整性校验 100% 通过，无新增丢帧或重复帧；
2. 固定版本 VMAF NEG model；尤其 H.264 二代编码，每眼全片 mean `>= 95`、逐帧
   1% 分位 `>= 90`；
3. 每眼全片 SSIM mean `>= 0.99`；
4. 对当前 Pi H.264 corpus，派生总视频体积的样本中位数 `<= source 的 65%`；
5. 双目/CV 领域指标通过，包括特征点保留、左右匹配、视差稳定性、重投影误差和关键 ROI；
6. 最低支持 PC 的 realtime factor、CPU/RAM、临时磁盘、功耗/温度、取消延迟和并发影响
   均在待定义的产品上限内。

上述 VMAF/SSIM/65% **全是待代表性实拍确认的产品门槛提案，不是研究已证实的结果**。
fixture 至少覆盖静态细纹、快速运动、低照度噪声、过曝、长时间、最后短 segment、30/60
fps，以及 raw MJPEG 和 Pi H.264。不同 encoder 的 `CRF`、`CQ` 或 preset 数字不能互相
等价；软件与硬件 encoder 必须使用不同 profile revision 分别验收。
任一结构、质量、体积或领域门槛失败都阻止 derived commit 和上传。Pi H.264 的 `CRF 18`
失败时可以创建独立 `CRF 16` variant 重试；再次失败仍然阻止上传，不能 passthrough、改传
H.264 或把失败文件标为已规范化。

还必须把 **codec 决策** 与 **encoder 分发决策** 分开。x265 是 GPLv2/商业双许可，项目
文档也明确说明该许可不授予 HEVC 专利权
（[x265 licensing, fixed commit](https://github.com/videolan/x265/blob/419182243fb2e2dfbe91dfc45a51778cf704f849/doc/reST/introduction.rst#L28-L34)，
[patent notice](https://github.com/videolan/x265/blob/419182243fb2e2dfbe91dfc45a51778cf704f849/doc/reST/introduction.rst#L70-L74)）。
在实现前需决定是调用平台/硬件 encoder、接受相应 OSS 分发义务，还是取得商业许可；
选定 HEVC profile 本身不等于已经解决 encoder binary 和专利授权。

### 5.3 后续统一 normalization 设计：若 profile 获批，H.264 也必须重编码

本节是 future design，不属于当前 Ubuntu-only 本地导入。只有真实 codec probe/decode、
代表性 corpus、质量/体积/CV 门槛、encoder 分发与许可全部获批后，卡导入会话才可进入
normalization job；当前必须 fail closed，不能由 `autoNormalize` 触发不存在的执行路径。
未来 normalizer 根据 probe 结果走不同 effect：

| 输入                                    | 默认 effect                                                          | 是否有损           |
| --------------------------------------- | -------------------------------------------------------------------- | ------------------ |
| `stereo.mjpeg` / MJPEG `source_*.mp4`   | 按现有 crop 规则拆左右眼并编码 HEVC                                  | 是，第一代有损编码 |
| v5 MJPEG `video/segment_*.mp4`          | 以 side-by-side schema 布局拆眼，重建每段归零的累计时间轴后编码 HEVC | 是，第一代有损编码 |
| 当前 appliance 的完整 H.264             | 先原样导入并验证 source publication，再解码为统一 HEVC 派生件        | 是，第二代有损编码 |
| future Pi HEVC                          | 作为 source 仍实际 decode + HEVC encode；需先修订输入 schema/profile | 是，新一代有损编码 |
| 未知 codec、错误 geometry/fps、双路缺失 | 当前 profile fail closed                                             | 无输出             |

这满足主设计的“对象存储派生资产统一为 HEVC”，但代价是完整 appliance H.264 会再次
有损编码。UI、provenance 和验收报告都必须明确 generation；Pi H.264 source 是否额外上传
属于独立的 source-archival 策略，不能与默认派生 bundle 混为一谈。
唯一允许跳过编码的是：PC 已经持有由同一 `SourceContentRevision + profile_revision` 生成、
且 derived manifest 与全部输出 hashes 再次验证通过的 `DerivedRevision`。这是 durable job
恢复的幂等复用，不是按 source codec 选择 passthrough/remux。

## 6. 当前本地导入与未来对象存储的事务边界

当前 Ubuntu-only 里程碑止于 durable local import：

```text
Ubuntu OS-mounted Linux-native volume
  -> fixed-root constrained scan + schema classification
  -> signed fail closed / explicit structurally validated unsigned approval
  -> copy source inventory to PC revision-scoped staging
  -> verify copied bytes / durable atomic LocalReady
  -> explicit removable-media handle release / shutdown release
```

这里的 schema classification 不包含真实 codec probe/decode。当前 runtime 对所有 signed
publication 一律 fail closed；paired-key verification 要等未来接入 PC trust owner 后才能
启用。signed 不得降级为 unsigned。structurally validated unsigned source 没有用户显式批准
时不得自动导入。拔卡、原卡重插和进程崩溃通过 durable job/checkpoint 恢复；unsigned 原卡
重插后必须由用户再次显式批准，watcher 不得自动重新准入；不同卡不能仅凭相同 mount path
续接旧 job。

未来完整产品流水线才继续执行：

```text
durable LocalReady
  -> real codec probe/decode
  -> encode supported Pi source to an approved, versioned HEVC profile
  -> full probe + decode + stereo/timing validation
  -> output fsync + atomic local commit + derivative manifest/hash
  -> multipart/object upload
  -> bind remote receipt/version/checksum to this exact output hash
  -> remote verification
  -> retain or expire source/local raw according to explicit retention policy
```

未来扩展仍须保持的关键不变量：

1. **不从卡直接边读边编码。** 否则拔卡会把耗时编码也变成 source failure，且无法清楚
   区分“源 bytes 已安全取得”与“派生件失败”。
2. **未来编码以一个 segment 为一个可恢复工作单元。** 输出先写临时名，完整 probe/decode 后 `fsync`，
   再 atomic rename 和 directory `fsync`；左右同 index 全部提交后才推进会话 ledger。
3. **容器恢复与 codec 是两件事。** FFmpeg 文档说明 fragmented MP4 在写入中断后仍可解码，
   普通 MP4 往往不行；这支持现有 source spool 的选择，但不能代替 temp/validate/commit
   事务，也不能说明 HEVC 本身“更抗断电”
   （[FFmpeg muxer docs, fixed commit](https://github.com/FFmpeg/FFmpeg/blob/883e8a6336b2651f7be79a6a9aa5f3cc22937948/doc/muxers.texi#L380-L405)）。
4. **视频码流不是完整性协议。** H.264/H.265 都不规定应用如何处理损坏或非合规码流；
   当前本地导入以 source SHA-256 保证 bytes 完整性，未来上传还需版本/receipt、远端校验
   和源保留保证正确性。
5. **删除是最后且独立的决策。** 编码成功不删除 raw；上传 API 返回成功也不删除 raw。
   至少要等本地派生件提交、对象版本绑定和远端 bytes 校验都完成。V1 最安全的默认是从不
   自动改动卡，源卡清理由 RP 设备或显式维护流程负责。

## 7. 当前已决范围与后续产品决策

当前已确定只支持 Ubuntu/Linux OS-mounted Linux-native TF 卡，推荐 ext4；不修改 Pi，不做
`SelectedFolder`、Windows/macOS 或 exFAT/FAT。当前识别 signed schema，但生产准入一律
fail closed；paired-key trust owner 接入属于 future/deferred，且 signed 绝不能降级为 unsigned。
当前实际可达准入只有显式人工批准的 structurally validated unsigned source。当前只交付
只读 durable local import，不交付任何自动 normalization、upload、retention 或 prevent-sleep 行为。

以下是 future work 的产品决策，不得作为当前 Ubuntu-only MVP 的完成条件：

1. **未来跨平台“内存卡”指哪张卡？** 若接受独立数据卡，才可推进 exFAT + device-local FIFO 的产品路径。
2. **完整 H.264 的统一 HEVC profile 是否确认当前门槛提案？** “必须转码”已经是主设计
   约束；仍需确认 VMAF NEG mean/1% 分位、SSIM、65% 体积和双目领域指标。任何候选未同时
   过线都阻塞 profile 发布和上传，不能沿用同一 profile id、静默 passthrough 或放宽门槛。
3. **未受信 publication 的旧数据未来能否上传？** 当前只允许显式批准后本地导入；未来即使
   允许编码或上传，也必须再次使用明确策略，并使用 PC 派生 provenance，绝不冒充 device publication。
4. **raw 保留多久？** 需要按本地磁盘容量定义 retention：远端验证后立即删除、保留 N 天，
   或始终人工清理。任何选择都不应写回/删除源卡。
5. **HEVC 授权与播放支持范围。** 在冻结默认 profile 前，产品必须列出受支持的 Windows、
   macOS、Linux 版本、播放器/下游工具、是否分发 codec，以及相应的专利授权评审结果。
6. **硬件 encoder 是否是必需能力？** 若最低支持 PC 无 HEVC 硬件编码，需定义软件编码的
   可接受 realtime factor、并发数和暂停/取消体验。平台若没有合规且已通过门槛的 encoder，
   必须报告“不具备编码能力”并阻止上传，不能在运行时静默改成 H.264 输出。

## 8. 分阶段验证清单

### 8.1 当前 Ubuntu-only 本地导入

当前实现范围的 verification 按本轮指令 deferred；以下测试和实机检查尚未运行，不能写成
“已通过”：

- 在固定 RP 提交上准备 trusted published、complete unpublished、committed spool、SDK raw、
  legacy v5、interrupted/corrupt fixtures，并覆盖末段、序号缺口、路径逃逸和损坏 hash。
- 在 Ubuntu 上使用 OS 已挂载的 Linux-native 卡验证固定 roots、资源上限、no-follow、权限拒绝、
  空间不足和只读来源不被修改。
- 验证当前所有 signed publication 无论卡内 key/fingerprint 内容为何都保持 fail closed，且
  不能降级为 unsigned；paired signed verification 的正向用例移到 future trust-owner 接入阶段。
- 验证 unsigned source 没有显式批准时不导入，任何自动策略都不能代替批准。
- 注入 source copy、checkpoint、hash、seal 和 atomic commit crash；重启后收敛且不产生半个
  `local_verified`。
- 验证读到一半拔卡、unsigned 原卡重插后再次显式批准、相同 mount path 换卡、重复/乱序
  volume event、I/O reader/lease 按作用域释放、用户显式释放 generation 应用句柄与退出释放；
  释放后应可由操作系统安全弹出，watcher 不得自动重新准入 unsigned source。

### 8.2 后续跨平台、codec、normalization 与对象存储

以下全部是 future verification，不阻塞当前 Ubuntu-only 本地导入：

- Windows/macOS 原生读卡、`SelectedFolder`、exFAT/FAT、自动挂载/格式化和 Pi
  device-local FIFO relocation。
- 接入 PC paired-key trust owner，验证 publication fingerprint、Ed25519 signature 与 producer
  identity 后才允许 signed admission；未匹配和验证失败继续 fail closed。
- 对 staging source 进行 bounded codec probe/full decode，并核对 codec、layout、geometry、
  FPS、frame count、duration 和 stereo timing。
- 建立 MJPEG 与 Pi H.264 的代表性质量/吞吐 corpus，冻结 VMAF NEG、SSIM、CV、体积、CRF、
  encoder、profile revision、分发和许可门槛。
- 验证 DerivationJob 的 segment recovery、derived manifest 和 source/derived revision 分离。
- 注入 multipart complete、remote verify 和 retention 各边界故障，证明 upload receipt 绑定
  exact bytes，且任何自动清理都不会在 remote verification 与批准策略之前发生。

当前可以冻结的是来源分类、固定 roots constrained scan、signed schema fail-closed、explicit
structurally validated unsigned 准入、只读 durable staging、source bytes 完整性与真实性分离。
不能冻结或宣称已完成的是 paired signed verification、
真实 codec validity、HEVC 参数、MediaNormalizer、对象存储上传、自动 retention 及任何假设
存在但没有 execution owner 的 automation toggle。
