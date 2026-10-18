# 面向平陆运河数字孪生的多源感知数据国密可信采集与篡改溯源系统

多源感知设备 → 边缘网关 → 中心平台的可信采集链路。设备端用 SM3 摘要链 + Merkle 树组织记录，
用 SM2 对批次签名；中心平台**不采信网关的通过状态**，用预登记公钥独立重验，
并对外提供单条记录的 Merkle 证明与签名复核。

当前实测（2026-10-17/18，口径详见 `P33_实时延迟口径与数据_20261017.md`）：

| 项 | 结果 |
|---|---|
| 中心侧直连压测（100 台 / 18000 批 / 180000 条，中心 `DELETE + FULL`） | 全部 accepted，0 拒绝、0 审计，P95 **78.07 ms** |
| 三端实时延迟（无积压窗口 312 批） | P95 **740 ms**，99.36% ≤ 1 s；目标 ≤ 1000 ms，**已达标** |
| 三端到达 / 转发 | 到达侧 10 批/秒 = **100 条/秒已达标**（10-18 复测，无跳批、600/600 accepted）；网关转发中位 13.33 批/秒、P90 294 ms，长尾造成排队，端到端 P95 **61.6 s 未达标**，瓶颈已转移到网关 |
| 异常检测（自造 14 例 + 协作者独立 700 例） | 自造 9/9、11/11、5/5、0/3；协作者 600/600 检出、600/600 定位、0/100 误报 |
| 签名 / 验签（每批 1/10/20 条） | 5.17/5.19/5.23 ms、8.34/8.38/8.42 ms，报文 832/4489/8559 B |

> 注意区分：**P95 已达标**（无积压窗口 740 ms / 中心侧 78.07 ms，均 ≤ 1000 ms），
> 但 10-18 定向复测里“稳态到达 100 条/秒 + 网关转发长尾”造成排队，端到端 P95 升到 61.6 s，
> **这一项仍未达标**，瓶颈在网关转发（不是中心处理，也不是发送端）。
> 过载窗口（17:40–17:47）P95 174.6 s 同样由排队主导，不作为实时指标。

---

## 1. 目录结构

| 路径 | 内容 |
|---|---|
| `common_crypto/` | 规范化编码、SM3、SM2、Merkle、摘要链、批次组装（三端共用，唯一密码学实现） |
| `device_simulator/` | 七类设备生成器、组批与签名、密钥登记、MQTT 发布、多设备 runner |
| `edge_gateway/` | 网关：MQTT 订阅、契约初验、SQLite 缓存、按序转发、失败重试 |
| `center_platform/` | 中心：FastAPI 接口、独立验证、入库与审计、网页 `static/` |
| `tests/` | 4 个单元/集成模块 + 检测率评估、签名对照、规模压力、异常注入与离线演练 |
| `tools/` | `import_registry.py`（登记表导入中心）、`verify_batch.py`（逐项核验单批） |
| `test_data/` | 公开登记表、固定样例批次、评估样本 |
| `docs/` | `contract.md`（接口契约）、`progress.md`（每日记录）、`evidence/`（证据归档） |
| `keys/` | 设备私钥（**不入库**，本机保存） |
| `报告图_定稿/`、`*.docx` | 报告插图与交付文档（**不入库**，随提交包单独提供） |

---

## 2. 环境与安装

| 项 | 版本 |
|---|---|
| Python | 3.14（三人统一） |
| 操作系统 | Windows（命令按 CMD 写，PowerShell 见 §5） |
| MQTT Broker | Mosquitto 2.x（仅 MQTT 链路需要） |

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

依赖已锁版本：`gmssl==3.2.2`、`fastapi==0.143.0`、`httpx==0.28.1`、`uvicorn==0.54.0`、
`pytest==9.1.1`、`paho-mqtt==2.1.0`。下文命令中的 `python` 均可替换为
`.venv\Scripts\python.exe`。

**首次运行或要清到干净起点**，先在仓库根目录执行一次初始化：

```bat
python init_project.py --dry-run     :: 先看会改什么
python init_project.py              :: 生成/校验七类密钥，重建中心 devices，复位模拟器链尾
```

它会在改动前自动备份到 `center_platform/data/init-backup/`（该目录不入库）；
`--reset-audits` 连审计一起清空。

---

## 3. 单机四终端跑通（最快路径）

四个 CMD 终端，都在仓库根目录，按顺序启动：

```bat
:: 终端 1 — MQTT Broker
mosquitto -c mosquitto.conf -v

:: 终端 2 — 中心平台
python -m uvicorn center_platform.main:app --host 127.0.0.1 --port 8000

:: 终端 3 — 边缘网关
python -m edge_gateway.main

:: 终端 4 — 设备模拟器（七类各一台，每台每秒 1 条，每批 10 条，发一轮）
python -m device_simulator.runner --devices 7 --rate 1 --batch-size 10 --rounds 1 ^
  --keys-dir keys --registry test_data/registry_public.json --db-path simulator.db ^
  --broker 127.0.0.1 --port 1883
```

| 地址 | 用途 |
|---|---|
| http://127.0.0.1:8000/ | 中心页面（六块：总览 / 七类感知数据 / 异常审计 / 批次细节与 Merkle 证明 / 离线恢复 / 性能对比） |
| http://127.0.0.1:8000/docs | Swagger，可直接提交一批 JSON 手工验证 |

期望：终端 3 打印 `[接收] … 初验通过` 与 `[转发] …`；页面总览批次/记录数增加，
点开某条记录能看到叶摘要、证明路径与验签结果。

---

## 4. 三机局域网部署

| 电脑 | 角色 | 启动方式 |
|---|---|---|
| 电脑 1 | 设备端 | `python -m device_simulator.runner ... --broker <电脑2IP> --port 1883` |
| 电脑 2 | Broker + 边缘网关 | `mosquitto -c mosquitto.conf -v`；网关 `CENTER_URL=http://<电脑3IP>:8000` |
| 电脑 3 | 中心平台 | `python -m uvicorn center_platform.main:app --host 0.0.0.0 --port 8000` |

- 中心必须 `--host 0.0.0.0` 才能被另两台访问；放通防火墙入站：电脑 2 的 1883、电脑 3 的 8000。
- 三台电脑的公开登记表必须**完全一致**；导入中心后逐台核对公钥指纹：

```bat
python tools\import_registry.py --fingerprint-only
```

---

## 5. 配置项

所有地址、端口、库路径都可用**同名环境变量**覆盖，业务代码不写死；完整清单见
[`.env.example`](.env.example)（仓库不自动加载它，需要按下面的方式设置）。

**中心平台**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PINGLU_CENTER_DB` | `center_platform/data/center.db` | 中心库路径；异机/实测时切换 |
| `PINGLU_SIMULATOR_DB` | `simulator.db` | 离线页降级读取的待发队列路径 |
| `PINGLU_GATEWAY_STATUS` | 见 §7 | 网关只读状态文件路径，优先级最高 |
| `PINGLU_SQLITE_JOURNAL` | `WAL` | 中心库 journal 模式；设 `DELETE` 可回退做对照 |
| `PINGLU_SQLITE_SYNC` | `NORMAL` | 中心库 synchronous；设 `FULL` 可回退做对照 |

> SQLite 持久化：中心默认 `WAL + synchronous=NORMAL`（进程崩溃不丢已提交事务，
> 突然断电可能丢最后若干事务）；网关缓存库固定 `WAL + synchronous=FULL`，不做可配。
> 报告里 18000 批 / 180000 条的中心侧压测（P95 78.07 ms）是在优化前 `DELETE + FULL`
> 下测的，2 分钟对照（1200 批）才用 WAL + NORMAL，见 `验证结果_优化前_2分钟.csv`、
> `验证结果_优化后_2分钟.csv`。

**边缘网关**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MQTT_BROKER` / `MQTT_PORT` | `127.0.0.1` / `1883` | Broker 地址与端口 |
| `MQTT_TOPIC` | `pinglu/batches/+` | 订阅主题 |
| `CENTER_URL` | `http://127.0.0.1:8000` | 中心地址 |
| `DB_PATH` | `edge_gateway/data/edge_cache.db` | 网关缓存库 |
| `REGISTRY_PATH` | `test_data/registry_public.json` | 预登记公钥（**只能用主登记表**） |
| `RETRY_INTERVAL` / `HTTP_TIMEOUT` | `5` / `10` | 重试节拍、单次 POST 超时（秒） |
| `MAX_MESSAGE_BYTES` | `1048576` | 单条 MQTT 消息上限 |
| `MAX_PER_DEVICE_PER_ROUND` | `20` | 一轮转发里同一设备最多连发几批（串行按序） |

查看网关当前生效配置：`python -m edge_gateway.config`。设置方式：

```bat
:: CMD
set CENTER_URL=http://192.168.1.30:8000 && python -m edge_gateway.main
```

```powershell
# PowerShell
$env:CENTER_URL = "http://192.168.1.30:8000"; python -m edge_gateway.main
```

网关也支持命令行覆盖：`--broker / --port / --topic / --center / --db / --interval`。

---

## 6. 中心平台（C 端）

| 接口 | 方法 | 用途 |
|---|---|---|
| `/health` | GET | 健康检查 |
| `/batches` | POST | 接收批次，返回 accepted / rejected / duplicate |
| `/devices`、`/batches`、`/records`、`/audits`、`/stats` | GET | 登记表、批次、记录、审计、统计 |
| `/batches/{batch_id}/proof/{sequence}` | GET | 单条 Merkle 证明（叶摘要、路径、根、复验结果） |
| `/batch-raw` | GET | 按设备 + 批次回读原始批次 |
| `/verify-signature` | POST | 用中心预登记公钥复核签名 |
| `/offline-status` | GET | 缓存数、断点、补传进度、实时延迟 |
| `/performance` | GET | 签名对照数据与来源文件信息 |

登记表导入（中心没有 `POST /devices`，必须用脚本）：

```bat
python tools\import_registry.py --dry-run        :: 只看会导入什么
python tools\import_registry.py                 :: 写库
python register_all_devices.py                  :: 同类工具（原样登记公开登记表）
```

导入前后务必确认 `PINGLU_CENTER_DB` 与中心服务使用的是同一个库。

---

## 7. 边缘网关（B 端）

```bat
python -m edge_gateway.main
```

启动后依次打印生效配置、预登记设备清单、从数据库恢复的未确认批次数与队列状态。
网关只用 `REGISTRY_PATH` 的预登记公钥验签，**不使用消息里自带的公钥**；
初验失败的批次不丢弃，原文与初判一并落库并送中心复核。

- 缓存库默认 `edge_gateway/data/edge_cache.db`，对 `device_id + batch_id` 唯一约束；
- 转发按设备与批次号升序推进，前一批未获中心终态时后续不抢跑；
- 只有中心明确返回 accepted / 已记审计的 rejected / 内容一致的 duplicate 才标记已确认；
- 传输失败、超时、5xx 一律保留待传，重启从数据库恢复队列；
- 统计命令：`python -m edge_gateway.storage`（打印队列分布、待处理清单与实时延迟 P95）。

**离线页数据来源**：中心 `/offline-status` 优先读网关只读状态文件
`edge_gateway/data/gateway_status.json`（可用 `PINGLU_GATEWAY_STATUS` 指向别处），
文件不存在时自动降级读 `PINGLU_SIMULATOR_DB`（`simulator.db`）的待发队列，
页面会标注实际数据源。状态文件字段样例见 `test_data/gateway_status_sample.json`。
注意：该状态文件目前**尚未由网关写出**，离线页显示的是降级来源。

---

## 8. A 端：设备模拟与密码模块

负责设备登记、SM2 签名、SM3 摘要链、Merkle 树、批次生成及 MQTT QoS 1 发布。
支持水位、气象、航标、边坡、船闸、无人机、测量船七类设备；
100 台测试表示扩展模拟设备数量，不是增加设备类型。

### 8.1 当前联调样例

以 WL-001 身份重新生成并签名的样例，其密钥沿用已验证的原 WL-900 密钥：

- 正常批次：`test_data/verified_wl001/normal_batch.json`
- 配套公开登记表：`test_data/verified_wl001/registry_public.json`

上述两个公开文件随仓库提供，可用于本地验签复现。私钥和模拟器状态数据库不随仓库提供。

> **WL-001 密钥状态（重要）**：登记表里 WL-001 的公钥是 `26aeb5c9…`（与上面的样例配套，
> 样例可验签通过），但本机 `keys/WL-001.json` 对应的公钥是 `c1bc73ce…`，**没有配套私钥**。
> 因此 WL-001 只能“验已签名的样例”，不能再现签；需要现签的演示与脚本请改用 NM-001 /
> SL-001 / WE-001 / LK-001 / DR-001 / SB-001 六台，或让 A 通过安全渠道补齐新私钥。

### 8.2 本地检查

```powershell
python -m device_simulator.publish --batch-file .\test_data\verified_wl001\normal_batch.json --registry .\test_data\verified_wl001\registry_public.json --dry-run
```

预期显示本地验签与合同检查通过，批次为 WL-001-000001，主题为 `pinglu/batches/WL-001`。
`--dry-run` 不连接 Broker、不发送消息，显示“未连接 Broker”属于预期行为。
运行前请激活虚拟环境，Windows 也可把命令开头的 `python` 换成 `.\.venv\Scripts\python.exe`。

### 8.3 独立生成设备数据

以下命令创建独立测试目录，生成七类设备各一台、每台三批，共 21 批、210 条记录；
不连接 MQTT，不影响既有联调密钥和数据库。

```powershell
$runDir = Join-Path $env:TEMP ("pinglu-demo-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $runDir | Out-Null

python -m device_simulator.runner `
  --devices 7 --rate 1 --batch-size 10 --rounds 3 `
  --keys-dir "$runDir\keys" `
  --registry "$runDir\registry_public.json" `
  --db-path "$runDir\simulator.db"

Write-Host "测试资料目录：$runDir"
```

预期每台设备生成批次 `000001`～`000003`，序号分别为 1~10、11~20、21~30；
状态为 `MQTT=pending`、`中心=not_requested`。
此目录使用新生成的测试密钥，不能直接替换真实联调的登记表。

### 8.4 MQTT 发布

先确认 B 已启动网关、加载配套公开登记表，并核实 Broker 当前 IP：

```powershell
python -m device_simulator.publish --batch-file .\test_data\verified_wl001\normal_batch.json --registry .\test_data\verified_wl001\registry_public.json --broker <网关IP> --port 1883 --timeout 15
```

MQTT PUBACK 只表示 Broker 已确认接收；中心是否入库、网关是否完成任务要另行核对。
当前联调应显式指定这两个文件，不使用默认 `--sample`。

### 8.5 已验证结果

- 当前合并版本全套测试：52 passed、14 subtests passed，3 条警告（7 个模块）。
- 单设备重启续链：4 批、40 条记录检查通过。
- 100 台设备三轮本地测试：300 批、3000 条记录，批次校验、记录摘要与跨批次摘要链全部通过。
- 正式 WL-001 样例已完成一次真实三端联调。
- 另一次百设备真实短测中，300 批获得 Broker 确认，中心接受 300 批、保存 3000 条记录。

本地校验、真实短测和 30 分钟性能验收属于不同验证：**百设备连续 30 分钟尚未完成验收**；
**P95 在无积压窗口为 740 ms、中心侧 78.07 ms，已满足 ≤ 1000 ms 目标**，
未达标的是三端持续吞吐（约 90 条/秒，目标 100 条/秒），不能在报告或答辩中把功能测试
表述为性能验收通过。

更详细的 A 端验证记录见 `docs/a_crypto_simulation_notes.md`。

---

## 9. 测试与实验

```bat
:: 单元与集成（合并前必须全绿）
python -m pytest -q

:: 异常注入对照（协作者提供，6 类 × 100 + 100 正常，约 1 分钟生成 700 例）
python -m pytest tests/test_tamper.py -v -s          :: 结果写入 tests/anomaly_report.md
:: 若目录只读（CI/受限环境），用环境变量把样本与报告重定向到可写目录：
::   set ANOMALY_CASES_DIR=tests\anomaly_cases && set ANOMALY_REPORT=_tmp\anomaly_report.md

:: 离线补传、重启、重复投递、缓存篡改（本地桩中心，判定逻辑与真中心一致）
python -m pytest tests/test_offline.py tests/tamper_cache.py -v -s

:: 中心功能冒烟（自带临时库，不写正式库）
python 验证脚本_中心功能冒烟.py

:: 页面六块接口实测（先起中心）
python 验证脚本_页面接口.py http://127.0.0.1:8000
```

| 目的 | 命令 | 输出 |
|---|---|---|
| 检测率 / 定位准确率 / 误报率 | `python tests/evaluate.py --make-samples --out test_data/eval_samples.json`，再 `python tests/evaluate.py --samples test_data/eval_samples.json --submit --url http://127.0.0.1:8000` | 汇总 JSON + 明细 CSV |
| 签名批次对照 | `python tests/benchmark_signing.py --records 100` | `docs/evidence/benchmark_signing_<日期>.csv` |
| 100 台规模压力 | 先 `python 验证脚本_建百台环境.py --devices 100`，把中心 `PINGLU_CENTER_DB` 指向 `_tmp/stress100/` 的库，再 `python tests/stress_center.py --devices 100 --minutes 30 --batch-size 10 --url <临时中心地址> --registry <临时登记表> --keys-dir <临时密钥目录>` | 汇总 CSV/JSON |
| 网关 → 中心打通 | `python 验证脚本_端到端打通.py --center http://127.0.0.1:8000 --device WL-001` | 转发结果与延迟统计 |
| 单批逐项核验 | `python tools/verify_batch.py --batch test_data/normal_batch.json` | 分项 PASS/FAIL，退出码 0/1 |
| 页面演示库 | `python 验证脚本_搭建页面数据.py center_platform/data/demo.db` | 七类设备 + 多种异常的演示库 |
| 恢复正式库 | `python 验证脚本_一键恢复.py --dry-run`（确认后再去掉 `--dry-run`） | 备份 + 恢复记录 |

测试脚本的库隔离方式不同：冒烟/页面/打通类会自检库路径，指向正式库直接拒绝运行；
压力与初始化类会真的写库，动手前先 `--dry-run` 或把中心指向临时库。

---

## 10. 证据与交付文档

| 文件 | 内容 |
|---|---|
| `验证结果_中心功能冒烟_*.txt` | 中心 14 项行为逐项 PASS |
| `验证结果_全量pytest_20261018.txt` | 7 个模块 52 项 + 14 subtests 的全套通过记录与依赖环境 |
| `验证结果_异常样本700例_20261018.txt` | 协作者 700 例样本本机复现：600/600 检出、定位一致、零误报 |
| `验证结果_离线与缓存篡改_20261018.txt` | 断网/补传/重启/重复/缓存篡改 5 项演练记录 |
| `验证结果_三端重测_round5_20261018.txt` | 10-18 定向复测：到达 100 条/秒、600/600 accepted、端到端 P95 61.6 s（网关排队） |
| `验证结果_页面接口_*.txt` | 六块接口 + 证明逐条复验（26 项） |
| `验证结果_检测率评估_*.json/.csv/.txt` | 14 个带标签用例的期望/实际对照与三项指标 |
| `tests/anomaly_report.md` | 协作者独立样本（700 例）的生成、对照与三项指标 |
| `验证结果_中心侧_100台30分钟.csv/.json` | 18000 批 / 180000 条的中心侧压力与延迟分位 |
| `验证结果_压力测试_*.csv/.json/.txt` | 7 台真实登记设备的预演压力 |
| `验证结果_端到端打通_*.txt` | 网关 → 中心打通与实时延迟 |
| `P33_实时延迟口径与数据_20261017.md` | 三端四类延迟口径、对账（5865 = 5782 + 83）与结论建议 |
| `docs/evidence/` | 证据归档目录（性能页优先读这里的 `benchmark_signing*.csv`） |
| `docs/contract.md` | 三端接口契约（字段、编码、验证次序、错误码） |
| `docs/progress.md` | 每日联调与待办记录 |

交付文档与其生成脚本：

| 文件 | 说明 |
|---|---|
| `作品设计报告_面向平陆运河数字孪生的多源感知数据国密可信采集与篡改溯源系统.docx` | 按官方模板生成，27 页；封面与基本信息表已填作品编号 `CACR2026JDSXHB` |
| `签字页_官方_CACR2026JDSXHB.docx` | 官方审批及签字页（已填三人姓名/院系/专业与任务分工；签字、导师意见、盖章待手写） |
| `报告_内容_定稿.py` | 报告正文的**唯一数据源** |
| `报告_生成脚本_定稿.py` | 套模板排版、目录页码、图表；`--measure-out` 导出测页码副本 |
| `报告图_定稿/*.png` | 报告插图（`报告图_生成脚本.py` 出底图，裁掉图内标题后使用） |
| `审批及签字页_*.docx`、`签字页_生成脚本.py` | 官方签字表与生成脚本（实名信息留待手填） |

`*.docx` 与 `报告图_*/` 被 `.gitignore` 排除，随提交包单独提供；要改报告请改
`报告_内容_定稿.py` 后重新生成，不要直接在 Word 里改正文（会与数据源分叉）。

---

## 11. 常见问题排查

| 现象 | 先查什么 |
|---|---|
| 设备发了但网关没收到 | Mosquitto 是否启动、`MQTT_BROKER`/`MQTT_PORT`、主题 `pinglu/batches/+`、防火墙 1883 |
| 网关连不上中心 | `CENTER_URL`、中心是否 `--host 0.0.0.0`、防火墙 8000 |
| 批次被判 `UNKNOWN_DEVICE` | 中心 `devices` 表是否已导入且与登记表一致（`tools/import_registry.py --fingerprint-only` 核对指纹） |
| 验签一直失败 | 是否加载了**主登记表**（`test_data/registry_public.json`）；不要指向 `test_data/migration_wl900_to_wl001/`（冲突中间产物，公钥不匹配）；再逐字比对双方 canonical 输出 |
| 提交总是 `BATCH_CONTENT_CONFLICT` | 历史残留：同一 `batch_id` 内容不同。先 `python init_project.py` 复位链尾（会自动备份） |
| 断网后少数据 | SQLite 事务是否提交、`pending` 是否只在中心确认后才改、重启后的队列扫描是否完整 |
| 中心收到两次 | QoS1 与 HTTP 超时都会重试，按 `device_id + batch_id` 幂等，不依赖“恰好一次” |
| 离线页数字对不上 | 网关状态文件未写出时页面降级读 `simulator.db`，页面会标注实际来源 |
| 三端吞吐上不去 | 发送端是否每批重连 MQTT、待发队列是否按 `device_id + batch_id` 串行、网关 `MAX_PER_DEVICE_PER_ROUND` 与转发轮询间隔 |
| 端口被占 | 用 `netstat -ano` 找到占用 8000（或 1883）的进程后结束它，或换端口启动 |

---

## 12. 仓库约定

- **不进 Git**：`keys/`（私钥）、`.venv/`、`*.db`、`*.docx`、`报告图_*/`、含个人路径的日志、`backup/`、`_tmp/`、`tests/anomaly_cases/`（按需生成）。
- **公开登记表**只含 `device_id / device_type / longitude / latitude / public_key`，不含私钥；
  导入中心后逐台核对公钥 SM3 指纹。
- **合并前**必须跑 `python -m pytest -q` 并记录结果；问题按「复现命令、期望、实际、日志」四段记录到 `docs/progress.md`。
- **验证脚本分两类**：冒烟/页面/打通类会自检库路径，指向正式库直接拒绝运行；
  压力与初始化类会真的写库，动手前先 `--dry-run` 或先把中心指向临时库。
- **报告数字必须可追溯**：指标要能在证据文件里查到，且口径写清
  （中心侧直连 / 三端实时 / 过载窗口；P95 与吞吐分开表述）。
