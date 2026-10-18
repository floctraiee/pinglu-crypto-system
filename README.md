# pinglu-crypto-system

## A端：设备模拟与密码模块

负责设备登记、SM2签名、SM3摘要链、Merkle树、批次生成及MQTT QoS 1发布。

支持水位、气象、航标、边坡、船闸、无人机、测量船七类设备；
100台测试表示扩展模拟设备数量，不是增加设备类型。

### 当前联调样例

当前使用以WL-001身份重新生成并签名的样例，其密钥沿用已验证的原WL-900密钥。

- 正常批次:`test_data/verified_w001/normal_batch.json`
- 配套公开登记表:`test_data/verified_w001/registry_public.json.`

上述两个公开文件随仓库提供，可用于本地验签复现。
私钥和模拟器状态数据库不随仓库提供。

### 本地检查

在项目根目录、已安装项目依赖的Python环境中执行：

```powershell
python -m device_simulator.publish --batch-file .\test_data\verified_wl001\normal_batch.json --registry .\test_data\verified_wl001\registry_public.json --dry-run
```

预期显示本地验签与合同检查通过，批次为WL-001-000001，
主题为pinglu/batches/WL-001。

dry-run不连接Broker、不发送消息，显示未连接Broker属于预期行为。

### MQTT发布

先确认B已启动网关、加载配套公开登记表，并核实Broker当前IP。
以下命令中的地址为此前联调地址：

```powershell
python -m device_simulator.publish --batch-file .\test_data\verified_wl001\normal_batch.json --registry .\test_data\verified_wl001\registry_public.json --broker 172.24.76.45 --port 1883 --timeout 15
```

MQTT PUBACK只表示Broker已确认接收。
中心接收结果及网关任务完成状态需要另外核对。
当前联调应显式指定这两个文件，不使用默认--sample。

### 已验证结果

- 修改后全套测试：32 passed、14 subtests passed，3条弃用警告。
- 单设备重启续链：4批、40条记录检查通过。
- 100台设备三轮本地测试：300批、3000条记录。
- 上述100台测试的批次校验、记录摘要和跨批次摘要链检查全部通过。
- 已完成一次真实三端联调。

100台三轮测试未发送MQTT，不代表已完成100台运行30分钟、
真实断网恢复补传或端到端P95延迟测试。

更详细的A端验证记录见 `docs/a_crypto_simulation_notes.md`。
## B端：边缘网关与离线补传

负责 MQTT 订阅、九步流程（记录接收时间 → 大小/解析校验 → 取预登记公钥 → 契约校验 →
持久化入队 → 初验失败原文留证 → 同设备按序转发 → 终态确认 → 定时重试与 DB 恢复）、
本地 SQLite 持久队列与网关审计、离线补传与 QoS 1 幂等。

| 文件 | 作用 |
|---|---|
| `edge_gateway/config.py` | 全部配置项，均可用环境变量覆盖 |
| `edge_gateway/storage.py` | 本地 SQLite：待传队列、设备链尾、网关审计、延迟统计 |
| `edge_gateway/validator.py` | 初验，内部调用 `common_crypto.batch.verify_batch`（与中心同一个函数） |
| `edge_gateway/forward.py` | 按序转发、终态分类、定时重试 |
| `edge_gateway/main.py` | 入口：MQTT 连接 + 接收队列 + 工作线程 + 转发线程 |

### 启动

    cd /d <仓库目录>
    set CENTER_URL=http://<中心IP>:8000
    .venv\Scripts\python.exe -m edge_gateway.main

### 可配置项（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MQTT_BROKER` / `MQTT_PORT` | `127.0.0.1` / `1883` | broker 地址与端口 |
| `MQTT_TOPIC` | `pinglu/batches/+` | 订阅主题 |
| `MQTT_CLIENT_ID` | `edge-gateway` | 固定客户端 ID，防止双实例重复处理 |
| `CENTER_URL` | `http://127.0.0.1:8000` | 中心地址 |
| `DB_PATH` | `edge_gateway/data/edge_cache.db` | 本地队列库 |
| `REGISTRY_PATH` | `test_data/registry_public.json` | 预登记公钥表 |
| `RETRY_INTERVAL` | `5` | 重试扫描间隔（秒） |
| `HTTP_TIMEOUT` | `10` | 中心请求超时（秒） |
| `MAX_MESSAGE_BYTES` | `1048576` | 单条 MQTT 消息上限 |
| `MAX_PER_DEVICE_PER_ROUND` | `20` | 每设备每轮最多连发批数 |

### 九步流程与代码落点

| 步 | 动作 | 代码位置 |
|---|---|---|
| 1 | 记录接收时间 | `main.py` 的 `on_message` |
| 2 | 大小与解析校验 | `main.py`（`MAX_MESSAGE_BYTES`） |
| 3 | 取预登记公钥 | `validator.get_registration` |
| 4 | 契约校验 | `validator.verify` → `common_crypto.batch.verify_batch` |
| 5 | 持久化入队 | `storage.save_received` |
| 6 | 初验失败原文留证转发 | `forward.run_once` 的 `audit_pending` 分支 |
| 7 | 同设备按序提交 | `storage.next_pending` + `forward.run_once` |
| 8 | 终态确认 | `forward.classify` + `storage.mark_result` |
| 9 | 定时重试与 DB 恢复 | `forward.run_forever` + `storage.list_unconfirmed` |

### 已验证结果

- **三端联调**：`WL-001-000001` → 网关初验通过 → 中心 `accepted`，
  零重试，往返 **337 ms**，网关链尾推进到序号 10。
- **异常注入对照实验**（`tests/test_tamper.py`）：6 类篡改 × 100 份 + 100 份正常对照，
  **检测率 100%、定位准确率 100%、误报率 0%**，详见 `tests/anomaly_report.md`。
- **断网补传演练**（`tests/test_offline.py`）：断网时队列保留、只有队首批重试；
  恢复后按序补传、全部 `accepted`、中心侧序号连续；QoS 1 重传不产生重复任务。
- **本地缓存篡改**（`tests/tamper_cache.py`）：直接修改 `edge_cache.db` 中
  `pending_batches.raw_json` 后转发，中心判 `INVALID_RECORD_HASH` 并通过
  `affected_sequences` 精确指出被改序号，网关记终态 `rejected`。

## C端：中心平台

负责设备登记、独立复核（设备登记 → 批次签名 → 数量/起止/序号 → 每条摘要 →
链连接 → Merkle 根 → 历史重放）、入库、审计与 Merkle 证明。

    cd /d <仓库目录>
    .venv\Scripts\python.exe -m uvicorn center_platform.main:app --host 0.0.0.0 --port 8000

浏览器打开 `http://<中心IP>:8000` 查看汇总与批次列表，`/docs` 为接口文档。

## 整体部署与启动顺序

三台机器分别承担设备模拟端、边缘网关（含 MQTT broker）、中心平台。
**下游先起、上游后起，顺序不能乱：**

1. **中心平台**
       .venv\Scripts\python.exe -m uvicorn center_platform.main:app --host 0.0.0.0 --port 8000
2. **MQTT broker**
       mosquitto -c mosquitto.conf -v
   `mosquitto.conf` 必须是 `listener 1883 0.0.0.0`，否则其他机器连不上。
3. **边缘网关**
       set CENTER_URL=http://<中心IP>:8000
       .venv\Scripts\python.exe -m edge_gateway.main
4. **设备模拟端**
       python -m device_simulator.publish --batch-file <批次文件> --registry <登记表> --broker <网关机器IP> --port 1883

依赖安装：

    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt

## 测试

    .venv\Scripts\python.exe -m pytest -q

预期 **39 passed, 14 subtests passed**。

| 测试文件 | 覆盖内容 |
|---|---|
| `tests/test_crypto.py` | 规范化编码、SM
3 摘要链、Merkle、SM2 验签 |
| `tests/test_device_runner.py` | 多设备生成与重启续链 |
| `tests/test_contract_integration.py` | 中心接口与契约校验（17 例） |
| `tests/test_tamper.py` | 6 类篡改的检测率 / 定位准确率 / 误报率 |
| `tests/test_offline.py` | 断网补传与恢复、按序停等、QoS 1 幂等 |
| `tests/tamper_cache.py` | 本地缓存篡改检测 |

## 辅助工具

| 工具 | 用途 |
|---|---|
| `tools/import_registry.py` | 把公开登记表导入中心 `devices`；`--fingerprint-only` 只打印公钥 SM3 指纹用于核对 |
| `tools/verify_batch.py` | 逐项核验一个批次文件（不依赖历史状态），输出 PASS/FAIL 报告 |

## 安全约定

- 设备私钥保存在 `keys/`，**仅设备端持有，不随仓库分发**（`.gitignore` 已忽略）。
- 网关与中心只使用预登记公钥表 `test_data/registry_public.json`，
  **绝不使用消息里自带的公钥作为信任来源**。
- 批次签名只覆盖九个批次头字段，`records` 不在签名范围内 ——
  记录完整性由逐条 SM3 摘要 + 摘要链 + Merkle 根共同保证。