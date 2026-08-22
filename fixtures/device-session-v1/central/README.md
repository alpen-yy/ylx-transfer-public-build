# YLX 0.5 产品契约包

本目录只维护 YLX 0.5 跨仓实现需要的机器可读**产品契约**及其可执行样例。它约束设备录制、会话数据、发布数据、设备 HTTP API、运行时存储安全边界和兼容性，不定义项目管理、组织审批或发布治理流程。

产品语义和跨仓边界分别由以下文档补充说明：

- [`docs/DATA_CONTRACT.md`](../docs/DATA_CONTRACT.md)：跨对象数据语义和无法仅靠 JSON Schema 表达的不变量。
- [`docs/CONTRACTS.md`](../docs/CONTRACTS.md)：生产者、消费者、兼容和迁移边界。
- [`contract-identities.yaml`](contract-identities.yaml)：产品 Schema 与 OpenAPI 的稳定身份索引。

当说明文档与机器契约不一致时，应停止相关实现并修正差异；本地验证通过只表示这些已实现的结构和不变量通过检查，不代替地瓜派真机、真实存储、网络或 GPU 测试。

## 产品契约清单

### 当前持久化数据 Schema（10 个）

`schemas/` 中的产品数据 Schema 均使用 JSON Schema Draft 2020-12。

| 文件 | 稳定类型 | 用途 |
|---|---|---|
| `schemas/ylx-volume-v1.schema.json` | `ylx.volume.v1` | `.ylx-volume.json` 存储卷身份 |
| `schemas/ylx-device-session-v1.schema.json` | `ylx.device-session.v1` | 设备端完成校验并封存的冻结 v1 录制会话清单；无 audio 字段 |
| `schemas/ylx-device-session-v2.schema.json` | `ylx.device-session.v2` | audio-capable 设备会话清单；required explicit `audio` 状态，raw IMU 使用 `raw_device_axes` |
| `schemas/ylx-recording-state-v1.schema.json` | `ylx.recording-state.v1` | 录制中的状态及失败、放弃等保留结果 |
| `schemas/ylx-bucket-publication-v2.schema.json` | `ylx.bucket-publication.v2` | Device Session v1 绑定的对象存储发布结果、来源和转换记录 |
| `schemas/ylx-bucket-publication-v3.schema.json` | `ylx.bucket-publication.v3` | Device Session v2 绑定的对象存储发布结果；包含 explicit `source_audio` |
| `schemas/ylx-imu-sample-v1.schema.json` | `ylx.imu-sample.v1` | `imu.jsonl` 的单条 IMU 样本 |
| `schemas/ylx-imu-physical-acceptance-v1.schema.json` | `ylx.imu-physical-acceptance.v1` | IMU 物理验收 evidence envelope；strict JSON、safe ASCII locator、closed vendor URI policy、typed artifacts、axis_mapping authority、YLX hardware profile、closed packet layout、false PASS fail closed，且不声明 `STABILITY-01=PASS` |
| `schemas/ylx-frame-record-v1.schema.json` | `ylx.frame-record.v1` | `frames.jsonl` 的单条双目帧记录 |
| `schemas/ylx-external-authority-boundaries-v1.schema.json` | `ylx.external-authority-boundaries.v1` | 设备运行时对旧数据和 TLS pin 等外部能力边界的失败关闭描述 |

### Legacy v2 只读 Schema（1 个）

| 文件 | 稳定类型 | 用途 |
|---|---|---|
| `schemas/ylx-safe-swap-participant-authority-v1.schema.json` | `ylx.safe-swap-participant-authority.v1` | 换盘时各运行时参与者及其卷访问路径约束 |

Legacy v2 Schema 仅用于严格读取和拒绝损坏的历史 v2 回执；当前 Device API 不再生产或要求该对象。两个文件名中的 `authority` 表示设备运行时允许执行的能力范围，是防止越权访问存储或网络资源的产品安全契约；它们不是人员、组织或发布审批机制。

### 发布兼容 Schema（2 个）

| 文件 | 用途 |
|---|---|
| `publication-manifest-v1.schema.json` | `RP-YLX` 发布清单 v1 的机械镜像，用于保持现有介质和消费者兼容 |
| `publication-signature-v1.schema.json` | 发布清单内 `publication_signature` 的 Ed25519 信封形状 |

发布签名用于校验设备发布数据的来源绑定和内容完整性。验证端必须从已认证的设备连接或配对上下文取得 `external_device_identity`，再用外部可信密钥注册表解析 `key_version`；介质自身不能替代该绑定，注册表不可用、设备未知、密钥撤销或签名不匹配时必须拒绝。

该签名是产品数据兼容与完整性契约，不是四人或任何多人签署治理，也不是人工批准或版本晋级凭据。`fixtures/publication-signature-v1/` 只包含测试向量和合成密钥注册表，不包含生产密钥，也不授予任何组织权限。

### 设备 API

`openapi/ylx-device-v4.openapi.yaml` 是当前设备控制、状态、事件、会话查询和已完成会话传输的 OpenAPI 3.1 契约，API 版本为 `4.0.0`，服务基路径为 `/api/v4`。v4 以完整 canonical v3 操作面为基底，只改变版本身份、v4 device/status/event/snapshot 判别符、live IMU 的 `host_monotonic` 时钟、`raw_int16` 加速度/陀螺仪预览值和 `sync.quality`，并新增 `/camera/focus` 的 V4L2 focus read/set surface 与 `runtime.camera_focus` nullable 状态。参数、响应状态、headers、content schema、SSE、Range、HEAD、safe-swap、preview 和 session 语义不得因 v4 引入而缩减。safe-swap 新生产回执仍使用最小 `ylx.safe-swap-receipt.v3`。

`ylx-device-v2.openapi.yaml` 和 `ylx-device-v3.openapi.yaml` 都以固定 SHA-256 与字节数作为冻结兼容面保留，分别服务 `/api/v2` 和 `/api/v3`。v2/v3 的 live IMU 语义保持 canonical SI-or-null；不得把 v4 raw 形状或 camera focus surface 回填到旧 major。当前查询可读取旧 v2 回执，但不再生产 v2 回执。当前 exact identities 是 v2 `274216d7f140b296dacf70fb669e37eb7be2ccf48f51e9d354a5245e01e05599` / `67834` bytes，v3 `72b70dd6d9ab87e70abc0bf4af519435435bba05a33d512d4c394f25b1ef4297` / `68520` bytes，v4 `6740c9875ee6dcf1564062b3b7e63d995d4c01cdd1e3fadcc49bd54b13ffc899` / `75767` bytes。

预览 Web Server、网页静态资源和这些设备 API 的实现均属于 `mirrorbloom/RP-YLX`。未来移动端 `mirrorbloom/ylx-preview` 不属于 0.5 实现或验收范围。

### 身份索引

`contract-identities.yaml` 分组登记 10 个当前 Schema 和 1 个 legacy v2 只读 Schema 的文件名、类型判别符、`$id` 和 JSON Schema 方言，以及 v2/v3/v4 OpenAPI 的路径、规范版本、API 版本、服务基路径、生命周期、SHA-256 和字节数。未知 API major 和未知数据 discriminator 必须失败关闭。新增、删除、改名或调整 Schema 生命周期时，必须同时更新该索引及对应样例。

## 产品样例与语料

- `fixtures/valid/`：10 个当前 Schema 和 1 个 legacy v2 只读 Schema 的有效样例。
- `fixtures/invalid/`：结构、跨字段和闭合语料层面的无效样例；`expected-errors.json` 声明预期失败原因。
- `fixtures/api/valid/`、`fixtures/api/invalid/`：冻结 v3 OpenAPI 组件及 API 过程不变量的正反例；`fixtures/api/expected-results.json` 是索引。
- `fixtures/api/v4/`：当前 v4 raw/null live IMU、camera focus status/set request、capture status、capture progress event 以及 wrong units、wrong time base、missing raw、missing sync quality、legacy epoch_id、out-of-range、unknown major、missing camera focus、empty focus set request、wrapper/state revision mismatch、live IMU active-session mismatch 失败关闭正反例；`fixtures/api/v4/expected-results.json` 是闭合索引。
- `fixtures/corpora/`：完整录制记录、take 聚合和产物响应的跨对象语料及变异用例。
- `fixtures/publication-signature-v1/`：发布签名规范化、验签、设备身份绑定和失败关闭测试向量。
- `fixtures/publication-prefix-authority.synthetic.json`：对象发布前缀冲突检查使用的合成运行时输入，不是组织授权记录。

`scripts/validate.py` 是本产品契约包的独立验证入口。它检查：

- 10 个当前 Schema、1 个 legacy v2 只读 Schema 及其正反例；
- 会话、take、帧、IMU、发布结果之间的跨对象不变量；
- OpenAPI v2/v3/v4 exact identity、v4 全量 `$ref` 解析、v3/v4 operation/component delta、v4 procedural fixture invariants、API 正反例和 consumer drift gate 输入；
- 发布清单规范化、签名、revision 和时间顺序；
- 换盘、介质丢失和外部运行时边界的失败关闭行为。

`consumer-matrix.yaml` 声明每个实际消费者的 Device API 支持范围。`rp-ylx` 与 `openaria-conductor` 是生产方，必须声明并 pin exact v2/v3/v4；`openaria-echo-web` 是 raw live IMU 与 camera focus 消费方，只声明 v4 exact identity，不虚称 v2/v3。Echo 的真实 Device API client 还使用 `file_exact` pin 中央已审查的 `src/api/client.ts` 精确 SHA-256 与字节数；这是有意的 consumer-first source identity boundary。结构化 TypeScript probes 仍读取真实 `API_ROOT`、`/camera/focus` GET/POST route、`CameraFocusStatus` discriminator 和 `DeviceRuntime.camera_focus` nullable 类型，但它们只作为可读诊断和审查辅助，不声称完成 JavaScript sandbox、alias analysis 或防止外部进程预先篡改 intrinsics。真实 Echo 构建应由产品侧 runtime test 证明导出的 `deviceApi` 为 `Object.isFrozen` 且普通 mutation attempts 不改变 route；contract gate 证明的是当前已审查 source bytes 未漂移。`ylx-transfer`、`ylx-card-pipeline`、`egoview-console` 和 `egoview-v5` 只消费 sealed session/publication/run 数据，对 live IMU 预览不受影响。`contracts/scripts/check_consumer_contracts.py` 默认拒绝 dirty 或非 Git/不可读消费者工作区；显式 `--allow-dirty` 只能用于本地排查，输出会标注 `NON-AUTHORITATIVE`，CLI 以 exit 2 表示非权威结果。

同一文件的 `data_contracts` section 是 Device Session v2 与 Bucket Publication v3 的 rollout matrix。当前所有新数据契约均为 `pending`：生产者/消费者只标 `producer_pending`、`consumer_pending` 或 `unsupported`，不声明任何产品仓库已经实现 v2/v3。Publication v2 继续 exact 绑定 Device Session v1；Publication v3 是唯一绑定 Device Session v2 的 publication major。

Device Session v2 recorded audio 的 `sample_count`、`segments[].start_sample` 和 `segments[].end_sample` 都是每声道 PCM frame 数，不是跨声道 sample slot 总数。validator 要求每段 `(end_sample - start_sample) / sample_rate` 与段时间跨度一致，允许误差不超过一个 sample period 加 `1e-9` 秒；top-level `sample_count`、sync duration 和所有段的 sample/time 总域必须一致。每段 WAV 还必须声明 `pcm_payload_bytes` 与 `wav_header_bytes`：`pcm_payload_bytes = frames * channels * 2`，`artifact.bytes = pcm_payload_bytes + wav_header_bytes`，header 必须在 `44..65536` bytes 的闭区间内。

## 历史治理实验

以下目录是早期流程治理设计留下的历史实验，本轮先保留文件，避免把大规模删除混入产品开发：

- `governance-schemas/`
- `fixtures/governance/`
- `fixtures/governance-models/`
- `governance-oracles/`

这些历史资产**不属于产品契约包，不进入默认产品验证，不是 YLX 0.5 的实现依赖或验收前置**。新功能不得依赖其中的状态链、审批对象、回执或验证器。后续如需清理，应作为独立维护任务处理，不阻塞产品功能。

## 验证

在仓库根目录执行默认产品契约验证：

```bash
make validate-contracts
```

该目标运行 `contracts/scripts/validate.py` 及产品契约所需依赖，覆盖上述 Schema、OpenAPI、普通 fixtures、corpora 和发布签名向量；它不运行历史治理 Schema、治理 fixtures 或治理 oracle。

需要直接调试验证器时可执行：

```bash
uv run contracts/scripts/validate.py
```

契约验证通过不代表硬件和跨仓实现已经完成。最终仍需由 Agent 自动完成跨仓集成测试和地瓜派真机测试，并由人类进行最终产品验收。
