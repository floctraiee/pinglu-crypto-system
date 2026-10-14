# 队员 B 执行清单：边缘网关与离线补传（10-09 更新版）

> **适用人**：队员 B
> **依据**：《队员B_边缘网关与离线补传全程任务书》（2026-10-01 至 10-19）+ `contract.md`
> **本版更新原因**：`main` 已推进到 `8c3dd24`（**队友A / floctraiee** 提交），中心平台已改吃契约扁平批次，你原来的阻塞项解除了，本文按新状态重排。
> **分支**：`feature/edge-gateway`
> **工作目录**：`D:\24291\Documents\pinglu-crypto-system`
> **核对时间**：2026-10-09

---

## 0. 现状（已核对，不是推测）

| 项 | 状态 |
|---|---|
| `main` | `8c3dd24` —「fix: 统一扁平批次验证与中心入库，补全冲突和拒绝审计」，作者 **floctraiee（队友A）**，2026-10-09 13:28 |
| `origin/main` | 同上 `8c3dd24`，你本地已 `pull` 到位 |
| `origin/feature/edge-gateway` | `74451d5` —— **你上次让我改的历史还没推上去** |
| 本地 `feature/edge-gateway` | `ee2f84b`（我改写的版本，已剔除 `f2ccc54`/`6081de8`），仍基于**旧 main `cda3be2`** |
| `edge_gateway/` | 仍不存在，要从零写 |
| `device_simulator/publish.py` | **不存在**（任务书第 73 行的入口，A 未做） |
| `center_platform/static/index.html` | **不存在**（队长 D9 未做） |
| `requirements.txt` | 仍是坏的：第 2 行是占位符 `pytest==实际版本号`，且缺 `httpx2` |
| `keys/` 私钥 | 不存在 |

### 0.1 阻塞解除情况

| 编号 | 内容 | 状态 |
|---|---|---|
| **D5** | 中心改吃契约扁平批次 | ✅ **已解决**（A 的 `8c3dd24`） |
| **D6** | 初验失败要能送中心留证据 | ✅ **已解决**，但形式和你预期不同：**没有** `POST /audits`，中心在 `storage.process_batch()` 里对**任何非 accepted 结果自动写 audits**（连原始批次一起存）。你只要照常 `POST /batches`，失败也会被审计 |
| **D7** | 真实 `reason_code` / `affected_sequences` | ✅ **已解决**，两者都如实回传 |
| **D8** | 和队长+A 核对 `registry_public.json` 七个公钥的 SM3 指纹 | ⏳ 待办（`contract.md` §7） |
| **D9** | 队长做中心首页展示页 | ❌ 未做 |
| **D10** | A 私发 `keys/WL-001.json` 设备私钥 | ❌ 未给 |
| **D11** | A 实现 `device_simulator/publish.py` | ❌ 未做 |
| **D12** | 修 `requirements.txt`（去掉占位符 + 加 `httpx2`） | ❌ 未做 |
| **D13** | `python -m pytest` 现在**收集就失败**（缺 `httpx2`） | ❌ 未做，任务书第 76 行要求合并前必须通过 |
| **D14** | 把 `test_data/registry_public.json` 导入中心 `devices` 表的手段（**没有** `POST /devices`，也没有导入脚本） | ❌ 缺，联调必需 |

**实测证据**（我用 `test_data/normal_batch.json` 跑的真实结果）：

```
第 1 次提交 : {"status":"accepted",  "reason_code":"OK",                  "device_id":"WL-001","batch_id":"WL-001-000001"}
重复提交   : {"status":"duplicate", "reason_code":"DUPLICATE_BATCH",      ...}
旧header格式: {"status":"rejected",  "reason_code":"INVALID_BATCH_STRUCTURE","device_id":null,"batch_id":null}
同ID改内容  : {"status":"rejected",  "reason_code":"BATCH_CONTENT_CONFLICT", ...}
```

**所以：中心这一侧通了，你从第 4 阶段（转发）开始不再被卡。**

### 0.1b 被 D10 / D11 卡住时的后路（重要，能让你今天就跑通全链路）

现在你手上只有 `normal_batch.json` 这**一个**签名批次，而私钥在 A 那里（D10）、`publish.py` 也没写（D11）。只靠这一个样本，你最多只能验证第一次提交，第二次就是 `DUPLICATE_BATCH`，测不出补传和重试。

**不用等 A**——`device_simulator` 本身就能给你造合法的签名批次，我已经实测通过：

```
批次: WL-001-000001 记录数: 3
新验证器结果: True OK
```

做法是**用你自己的测试设备**，不动 A 的登记文件：

```python
from common_crypto.sm import generate_keypair
from device_simulator.batch import create_next_batch
from center_platform import storage

# 1) 自己生成一对密钥，注册一台测试设备到中心（不要用 register_devices，它会覆盖 registry_public.json）
private_key, public_key = generate_keypair()
registration = {
    "device_id": "WL-001", "device_type": "water_level",
    "longitude": "108.500000", "latitude": "22.000000",
    "public_key": public_key,
}
storage.register_device("WL-001", "water_level", public_key)

# 2) 按需生成任意多批（batch_id / 序号 / 跨批次摘要链自动衔接）
batch = create_next_batch(registration, private_key, batch_size=10, db_path=".selftest.db")
```

把它接到你的 MQTT 发布（或直接喂给网关的入队函数），**全链路（MQTT → 网关初验 → 入队 → 转发 → 中心 accepted/duplicate）今天就能自己跑完**，不必等 D10/D11。

⚠️ 前提：这台 `WL-001` 的公钥必须和中心 `devices` 表里的一致。用它之前先把中心库里 A 的那份登记换成你的测试公钥（本机 `center_platform/data/center.db` 删掉重建即可），或者干脆换一个没被占用的 `device_id`。

⚠️ 这是**本机自测**方案；正式联调仍要用 A 的 `keys/WL-001.json` 和 `publish.py`。

### 0.2 你的分支状态（10-09 二次核对后的结果）

**历史改写没有生效，反而被撤销了。** 经过是这样的：

1. 我上次把 `f2ccc54` / `6081de8` 从本地分支历史剔除，得到 `ee2f84b`，**但没有强推**
2. 你在 GitHub Desktop 里做了 `pull`，远端 `74451d5`（仍带着那两个提交）和本地分支分叉，于是产生了一次合并 `39f0256`
3. 结果：**`f2ccc54` / `6081de8` 又回到分支历史里了**（已核对：两者都是 `HEAD` 的祖先）

| 项 | 当前值 |
|---|---|
| `HEAD` = `feature/edge-gateway` = `origin/feature/edge-gateway` | `39f0256`（合并提交） |
| 分支顶端有没有 `edge_gateway/` 文件 | **没有**（文件确实是删掉的） |
| 分支历史里有没有那两个提交 | **有**（被合并拉回来了） |
| `main` = `origin/main` | `8c3dd24` |

**结论：文件层面已经达成目标（分支和 main 里都没有那三个文件）；只有历史层面没达成。**

如果历史也要清，必须**在 `39f0256` 上重做一次改写再强推**：

```powershell
git fetch origin
# 以最新 main 为父、剔除含 edge_gateway 的提交，重建 feature/edge-gateway
# 这步我可以帮你生成并执行
git push origin feature/edge-gateway --force-with-lease
```

⚠️ 前提：**A 和队长都不能已经拉过这个分支**，否则要通知他们重来。`--force-with-lease` 会在远端有别人新提交时拒绝，是安全网。
⚠️ 就算强推成功，GitHub 上按 SHA 仍能打开旧提交（见 §0.4）。
⚠️ **我这边连不上 GitHub**（`Connection was reset`），`fetch`/`push` 都要你自己跑。

**建议：** 除非历史干净是硬要求，否则先别折腾——优先级明显低于把网关写出来。

### 0.3 中心的真实验证契约（A 改完后，你必须按这个写 `forward.py`）

`common_crypto/batch.py::verify_batch(batch, registration, previous_hash, start_sequence)`
返回：`{valid, reason_code, errors, record_errors, affected_sequences}`

**判定顺序（很重要）——** `center_platform/storage.py::process_batch()` 的实际流程：

```
取 device_id/batch_id  →  查设备登记  →  查是否已存在同 batch_id  →  【验证】  →  跨批次约束  →  入库
   ①                        ②                ③                      ④            ⑤
```

| 阶段 | 返回 | 含义 | 你该怎么做 |
|---|---|---|---|
| ① | `rejected` / `INVALID_BATCH_STRUCTURE` | 连 device_id/batch_id 都取不到 | 不确认，告警 |
| ② | `rejected` / `UNKNOWN_DEVICE` | 设备没在中心登记 | 不确认，告警 |
| ③ | `duplicate` / `DUPLICATE_BATCH` | 同 batch_id，**内容完全一致** | **可确认**（中心已审计） |
| ③ | `rejected` / `BATCH_CONTENT_CONFLICT` | 同 batch_id，**内容不一样** | **绝不确认**，这是异常 |
| ④ | `rejected` / `INVALID_SIGNATURE` | SM2 验签失败 | 不确认 |
| ④ | `rejected` / `INVALID_RECORD_HASH` | 某条摘要不匹配，`affected_sequences` 给出序号 | 不确认 |
| ④ | `rejected` / `INVALID_HASH_CHAIN` | 摘要链断开 | 不确认 |
| ④ | `rejected` / `INVALID_SEQUENCE` | 序号与中心可信下一序号不符 | 不确认 |
| ④ | `rejected` / `INVALID_MERKLE_ROOT` | Merkle 根与记录摘要不符 | 不确认 |
| ④ | `rejected` / `INVALID_BATCH_RANGE` | count 或起止序号与记录不一致 | 不确认 |
| ④ | `rejected` / `INVALID_TIMESTAMP` | 起止时间与首尾记录不一致 / 时间没递增 | 不确认 |
| ④ | `rejected` / `INVALID_DEVICE_IDENTITY` | 记录里的 device/batch/device_type 与登记不符 | 不确认 |
| ④ | `rejected` / `INVALID_BATCH_STRUCTURE` | 字段多一个少一个、类型不对、payload 字段与设备类型不匹配 | 不确认 |
| ⑤ | `rejected` / `INVALID_BATCH_SEQUENCE` | 批次号没比上一批大 | 不确认，检查你的排序 |
| ⑤ | `rejected` / `INVALID_TIMESTAMP` | `start_time` 没晚于上一批最后一条记录时间 | 不确认 |
| — | `accepted` / `OK` | 通过 | **可确认** |

**三个必须记住的陷阱：**

1. **③ 在 ④ 之前。** 你造"改一条值"的异常样本时，如果**沿用同一个 `batch_id`**，中心回的是 `BATCH_CONTENT_CONFLICT`，**根本走不到逐条摘要定位**，你就拿不到 `affected_sequences`。所以异常样本必须用**新的 batch_id**（例如 `WL-001-000002`），或者在干净的库上提交。
2. **⑤ 是跨批次约束。** 中心要求批次号递增、`start_time` 晚于上一批最后一条记录时间。你按序补传时这两条自然满足；但如果你乱序发送，就是 `INVALID_BATCH_SEQUENCE` / `INVALID_TIMESTAMP`。
3. **④ 的字段检查极严。** 批次必须**恰好** 11 个字段、记录必须**恰好** 12 个字段、`payload` 的键必须**完全等于**该设备类型的约定集合。多一个 `public_key` 字段就会 `INVALID_BATCH_STRUCTURE`。这三个集合 A 已经导出成常量，你直接 import：

```python
from common_crypto.batch import BATCH_FIELDS, RECORD_FIELDS, PAYLOAD_FIELDS
```

### 0.4 关于"抹掉历史"的一个硬限制

强推之后，GitHub 上按 SHA 仍能打开旧提交（`74451d5`），对象不会立刻回收。**要彻底从 GitHub 抹掉只能找 GitHub Support 或删库重建。** 另外如果 A 或队长拉过这个分支，他们本地仍有旧历史。

### 0.5 不要碰的过期文件

| 文件 | 结构 | 结论 |
|---|---|---|
| `test_data/normal_batch.json` | 契约扁平格式（11 键） | ✅ 唯一可用的批次种子 |
| `test_data/registry_public.json` | 7 台设备公钥 | ✅ 可用 |
| `test_data/sample_batch.json` | 旧 `header/records/signature`，记录里是 `data` | ❌ 不要用 |
| `test_data/submit_batch.json` | 旧结构，外面套 `{"batch": ...}` | ❌ 不要用 |
| `test_data/tampered_batch.json` | 旧结构，记录里没有 `payload` | ❌ 不要用，自己生成 |
| `test_data/device_registry.json` | 旧的单设备 `water_001` | ❌ 不要用 |

---

## 1. 目标文件清单

**已完成**：

```
edge_gateway/
├── __init__.py                    ✅ S01
├── config.py                      ✅ S02
├── storage.py                     ✅ S05 / S06   pending_batches + device_chain + gateway_audit
├── validator.py                   ✅ S10         薄封装，复用 common_crypto.batch.verify_batch
├── main.py                        ✅ S15 / S16   MQTT 订阅 + 快速入队 + 工作线程
└── forward.py                     ✅ S20         按序转发 + ack 判定 + 退避重试

mosquitto.conf                     ✅ S03
tools/import_registry.py           ✅ S22
tests/manual/mqtt_pub_smoke.py     ✅ S04
tests/manual/mqtt_sub_smoke.py     ✅ S04
tests/manual/publish_sample.py     ✅ 手工联调：把样本批次通过 MQTT 发出去
```

**待完成**：

```
tests/
├── inject_anomalies.py            S25  六类异常样本 + label.json
├── test_tamper.py                 S28  异常对照
├── test_offline.py                S30  断网/重启/缓存篡改/重复/交错
└── tamper_cache.py                S32  篡改 SQLite 缓存
```

**已删除**：`tests/test_mqtt_pub.py`、`tests/test_mqtt_sub.py`（内容迁到 `tests/manual/`）

### 网关数据流

```
MQTT ──on_message──▶ 内存队列 ──工作线程──▶ 初验 + 落库 ──▶ SQLite
                      (只入队，不 IO)                          │
                                                               ▼
                中心 ◀── POST /batches ── 转发线程（按设备按序、定时重试）
```

---

## 2. 分步操作

### 阶段 P0 清理（先做）

#### S01 · 建包骨架
- **在哪**：仓库根目录
- **干什么**：新建 `edge_gateway/` 与空的 `edge_gateway/__init__.py`
- **需要谁的文件**：无

#### S02 · 写 `edge_gateway/config.py`
- **在哪**：`edge_gateway/config.py`
- **干什么**：所有可调项集中，**每项都能被同名环境变量覆盖**（表 2 R5）

```python
import os
MQTT_BROKER       = os.getenv("MQTT_BROKER", "127.0.0.1")
MQTT_PORT         = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC        = os.getenv("MQTT_TOPIC", "pinglu/batches/+")
CENTER_URL        = os.getenv("CENTER_URL", "http://127.0.0.1:8000")
DB_PATH           = os.getenv("DB_PATH", "edge_gateway/data/edge_cache.db")
REGISTRY_PATH     = os.getenv("REGISTRY_PATH", "test_data/registry_public.json")
RETRY_INTERVAL    = int(os.getenv("RETRY_INTERVAL", "5"))
HTTP_TIMEOUT      = int(os.getenv("HTTP_TIMEOUT", "10"))
MAX_MESSAGE_BYTES = int(os.getenv("MAX_MESSAGE_BYTES", "1048576"))
```

- **需要谁的文件**：无

#### S03 · 写 `mosquitto.conf`
- **在哪**：仓库根目录
- **干什么**：`listener 1883 127.0.0.1` + `allow_anonymous true`
- **自检**：`D:\Program Files\Mosquitto\mosquitto.exe -c mosquitto.conf -v`
- **需要谁的文件**：无

#### S04 · 把两个 MQTT 冒烟脚本移出 pytest 收集范围 ★必做
- **在哪**：仓库根目录
- **干什么**：
  1. 建 `tests/manual/`（**不要**放 `__init__.py`）
  2. `git mv tests/test_mqtt_pub.py tests/manual/mqtt_pub_smoke.py`
  3. `git mv tests/test_mqtt_sub.py tests/manual/mqtt_sub_smoke.py`
  4. 两个文件里把 `client.connect(...)` 及其后所有语句缩进进 `if __name__ == "__main__":`
- **为什么**：它们在 import 阶段就连 `127.0.0.1:1883`，会让 `python -m pytest` 整套挂掉（任务书第 76 行）
- **注意**：这两个文件**只存在于你的分支**，`main` 里没有
- **需要谁的文件**：无

---

### 阶段 P1 持久队列

#### S05 · `edge_gateway/storage.py` 建表
- **在哪**：`edge_gateway/storage.py`
- **干什么**：字段严格按表 2 R2

```sql
CREATE TABLE IF NOT EXISTS pending_batches (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id           TEXT    NOT NULL,
    batch_id            TEXT    NOT NULL,
    first_seq           INTEGER NOT NULL,
    last_seq            INTEGER NOT NULL,
    raw_json            TEXT    NOT NULL,
    edge_result         TEXT,
    status              TEXT    NOT NULL DEFAULT 'pending',
    attempts            INTEGER NOT NULL DEFAULT 0,
    received_at         TEXT    NOT NULL,
    last_error          TEXT,
    acked_at            TEXT,
    gateway_received_at TEXT,     -- 九步第1步：给队长算实时处理延迟
    batch_no            INTEGER,  -- 九步第7步：按序补传排序用
    confirmed_at        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pending_device_batch ON pending_batches(device_id, batch_id);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_batches(status, device_id, batch_no);
```

再建两张你自己要用的表：

```sql
-- 网关侧可信链尾（跨批次衔接、序号连续性判定的依据）
CREATE TABLE IF NOT EXISTS device_chain (
    device_id     TEXT PRIMARY KEY,
    last_hash     TEXT NOT NULL,
    last_sequence INTEGER NOT NULL,
    last_batch_no INTEGER NOT NULL
);
```

- **需要谁的文件**：无

#### S06 · `storage.py` 事务与幂等
- **干什么**：实现
  - `init_db()`
  - `save_received(batch, edge_result, gateway_received_at)` → `INSERT ... ON CONFLICT(device_id,batch_id) DO NOTHING`（九步第 5 步：重复不生成第二条待传任务）
  - `list_pending_for_device(device_id)` → 按 `batch_no` 升序
  - `list_unconfirmed()`
  - `mark_acked(...)` / `mark_rejected(...)` / `record_attempt(...)`
  - `get_device_chain(device_id)` / `set_device_chain(...)`
  - `count_by_status()` → 阶段 7 演练的前后统计
- **依据**：任务书 P0015——先存原始 JSON，**事务提交成功才算网关已持有**
- **需要谁的文件**：无
- **验收**：正常批次入队；**停网关重启后 pending 不丢**

---

### 阶段 P2 初验（现在轻松多了）

#### S10 · 写 `edge_gateway/validator.py` —— 薄封装，**不要自己写密码学**
- **在哪**：`edge_gateway/validator.py`
- **干什么**：

```python
from common_crypto.batch import verify_batch as contract_verify

def verify(batch, registration, previous_hash, start_sequence):
    """直接复用 A 的契约验证器，只做网关侧的状态注入。"""
    return contract_verify(batch, registration, previous_hash, start_sequence)
```

- ⚠️ **不要再用自己写的 SM2/摘要/Merkle 校验**。A 已经把 `common_crypto/batch.py` 重写成正式契约验证器，字段检查、`payload` 与设备类型匹配、时区、经纬度范围、reason_code、`record_errors`、`affected_sequences` 全都做完了。
- ⚠️ **不要**用旧的 `header/records/signature` 思路，扁平格式是唯一格式。
- **网关的职责只剩三件**：
  1. 按 `device_id` 从**预登记表**取公钥（**绝不使用消息自带的公钥**，九步第 3 步）
  2. 从 `device_chain` 表取出该设备的 `previous_hash` / `start_sequence` 传进去
  3. 校验通过并入队后，**只在中心 acked 之后**才更新 `device_chain`（否则重传会对不上）
- **首次见到某设备**：`previous_hash = "0"*64`，`start_sequence = 1`（可用 `common_crypto.hash_chain.ZERO_HASH`）
- **需要谁的文件**：
  - A：`common_crypto/`（✅ 已到位）
  - A：`test_data/registry_public.json`（✅ 已到位）
  - 队长 + A：**D8 指纹核对结果**

#### S11 · 用 A 的样本自检
- **干什么**：读 `test_data/normal_batch.json` + `registry_public.json["WL-001"]`，走一遍 `validator.verify(batch, reg, ZERO_HASH, 1)`
- **期望**：`valid=True`、`reason_code="OK"`
- **反向**：改某条记录的 `payload` 一个字符（**不重算 `record_hash`，不重签**）→ 期望 `valid=False`、`reason_code="INVALID_RECORD_HASH"`、`affected_sequences` 命中你改的序号
- **需要谁的文件**：A 的 `normal_batch.json`、`registry_public.json`

---

### 阶段 P3 接收与入队

#### S15 · 写 `edge_gateway/main.py`
- **干什么**：
  1. 启动先 `storage.init_db()`，并从 DB **恢复未确认队列**（九步第 9 步：不靠内存计数）
  2. paho-mqtt 用 **`CallbackAPIVersion.VERSION2`**（任务书第 6 行）
  3. `subscribe("pinglu/batches/+")`
  4. `on_message` **只做快速入队**（塞进 `queue.Queue`），不做 DB、不做网络（表 2 R1 验收）
  5. 工作线程按九步 1–6 处理
  6. 加 `if __name__ == "__main__":`，支持 `python -m edge_gateway.main`
- **需要谁的文件**：无

#### S16 · 工作线程九步 1、2、3、5、6
| 步 | 动作 |
|---|---|
| 1 | 记 `gateway_received_at` 入库（给队长统计延迟） |
| 2 | 先判 `len > config.MAX_MESSAGE_BYTES`；再 `json.loads`，**失败也要写审计**，异常全捕获，**绝不让订阅程序崩溃** |
| 3 | 按 `device_id` 查预登记公钥；取不到 → `UNKNOWN_DEVICE` |
| 4 | 调 `validator.verify(...)` |
| 5 | 事务写 SQLite；重复批次标重复，不生成第二条待传任务 |
| 6 | 通过 → 入待传队列；**失败 → 把原文与初判结果 `POST /batches` 送中心**（中心会自动写 audits，**D6 已解决**） |

- **需要谁的文件**：无（D6 已解除）

---

### 阶段 P4 转发与确认 ★ 现在可以真正做了

#### S20 · 写 `edge_gateway/forward.py`
- **干什么**：
  - **第 7 步**：逐设备取 pending，按 `batch_no` 升序；**不同设备可交错，同设备前一批未 acked 就不发后一批**
  - **POST**：`{CENTER_URL}/batches`，body 必须是 `{"batch": <原始批次>}`（中心 `BatchRequest` 要求外层包 `batch`）
  - **第 8 步判确认**（按 §0.3 的表）：

```python
def can_ack(resp) -> bool:
    status = resp.get("status")
    code   = resp.get("reason_code")
    if status == "accepted":
        return True
    if status == "duplicate" and code == "DUPLICATE_BATCH":
        return True          # 内容一致，中心已审计
    return False             # 含 BATCH_CONTENT_CONFLICT、各种 INVALID_*、网络失败
```

  - 网络异常 / 超时 / 中心 5xx → **保持 pending**，`attempts += 1`，写 `last_error`，按 `RETRY_INTERVAL` 退避
  - 定时轮询未确认批次重试（第 9 步）
- ⚠️ **不要**为了绕开什么而在网关侧改写批次结构——中心现在收的就是契约扁平格式。
- **传输层注意**：QoS 1 和 HTTP 超时都会重试，按 `device_id + batch_id` 幂等，不依赖"恰好一次"（任务书 P0042）
- **需要谁的文件**：A 的中心（✅ 已到位）；`device_simulator/publish.py`（**D11，仍未做**）

#### S22 · 联调前置：把公钥导入中心 ✅ 已完成
- **背景**：中心**没有** `POST /devices`，也没有导入脚本。`devices` 表空着的话，任何批次都会被判 `UNKNOWN_DEVICE`。
- **产出**：[tools/import_registry.py](<tools/import_registry.py>)，三种用法：

```powershell
# 核对指纹（不写库）—— 就是 D8 要交的东西
.venv\Scripts\python.exe tools\import_registry.py --fingerprint-only

# 只看会导入什么
.venv\Scripts\python.exe tools\import_registry.py --dry-run

# 真正导入
.venv\Scripts\python.exe tools\import_registry.py
```

- ⚠️ 必须和中心服务**用同一个 `center_platform/data/center.db`**，所以要在仓库根目录跑
- **建议**：把这个需求同时提给队长，正式的导入入口应该由中心侧提供

##### D8 指纹核对表（2026-10-09 生成，请与队长、队员A 逐条确认）

| 设备ID | 类型 | 公钥前 16 位 | 公钥 SM3 指纹 |
|---|---|---|---|
| `DR-001` | drone | `92220bc68136096d` | `0e150c34a9a477c2836f19aa5ca5390b62e65c1e42c3e4150e86d5f46f7b4353` |
| `LK-001` | lock | `f3dc2ad4863e1f0a` | `23cc83d4916328b618b59e4f85aacf712ef873b4a2c53739fd9bc218ac37d6a0` |
| `NM-001` | navigation_mark | `a4a2e19083676714` | `2c96729981f5301f89f5bc43a364463022ed6cf39749b4907a864fb5b2035b18` |
| `SB-001` | survey_boat | `a20462d9a30b2388` | `99b5e49dd2f80b30a1f7cd52f93f8711f7c25fd5966307ccf546e44096d61b1b` |
| `SL-001` | slope | `73426c215e3e57d6` | `ddc4c8c082d10de7aa1067417263482b3bb51df73ede4d668771693383d1f6fa` |
| `WE-001` | weather | `3df617ba393fdc6d` | `81ed9b1c050933175701139daae52322405edcb42979a6d29784cb5486f6c4d9` |
| `WL-001` | water_level | `57a00d1312aaf656` | `bf41f4821258a959730e1975fc2f618e3fa25a2c256f6c36b37ed38e2d3aa885` |


#### S23 · （可选）`tests/manual/mock_center.py`
- **干什么**：标准库 `http.server` 写个本地 stub，按契约返回 `accepted`/`rejected`/`duplicate`，`CENTER_URL` 指向它
- **用途**：不用起真中心也能验 acked 判定、退避重试、按序补传

#### S24 · 三端联调
- **在哪**：四个 Windows CMD 终端，都在仓库根目录

| 终端 | 命令 | 谁 |
|---|---|---|
| 1 | `mosquitto -c mosquitto.conf -v` | B |
| 2 | `.venv\Scripts\python.exe -m uvicorn center_platform.main:app --host 127.0.0.1 --port 8000` | 队长 |
| 3 | `.venv\Scripts\python.exe -m edge_gateway.main` | **B** |
| 4 | `.venv\Scripts\python.exe -m device_simulator.publish --devices 1 --rate 1 --batch-size 10` | **A** |

- **先跑 S22 导入公钥**，否则全是 `UNKNOWN_DEVICE`
- **验收**：任务书第 50 行——至少一台设备的合法批次在中心出现
- **需要谁的文件**：**D11**（A 的 `publish.py`）、**D10**（A 的 `keys/WL-001.json`）
- ⚠️ **别跑** `python -m device_simulator.register --devices 1`——`register_devices()` 默认覆盖写 `test_data/registry_public.json`，会把 7 台设备压成 1 台。要重新生成只能用 `--devices 7`，且确认 `keys/` 下 7 个私钥都在（存在则加载、不重新生成）

---

### 阶段 P5 异常注入与对照

#### S25 · 写 `tests/inject_anomalies.py`
- **种子**：`test_data/normal_batch.json`（A 的 `sample_batch.json` 等旧样例**不能用**）
- **六类**：

| 类型 | 怎么做 | 预期中心 reason_code |
|---|---|---|
| 改值 | 改一条记录 `payload` 里一个值 | `INVALID_RECORD_HASH` + 序号 |
| 删除 | 删一条记录（头不动） | `INVALID_BATCH_RANGE`（count 不符） |
| 插入 | 插一条记录 | `INVALID_BATCH_RANGE` |
| 乱序 | 交换两条记录 | `INVALID_SEQUENCE` 或 `INVALID_HASH_CHAIN` |
| 伪造设备 | 改 `device_id` | `UNKNOWN_DEVICE` 或 `INVALID_DEVICE_IDENTITY` |
| 重放 | 复制历史批次 | `DUPLICATE_BATCH`（同内容）/ `BATCH_CONTENT_CONFLICT`（改了内容） |

- ⚠️ **铁律：改完不得重算签名、不得重签**（任务书 P0035 + `contract.md`）
- ⚠️ **必须给每个变异样本分配新的 `batch_id`**（例如从 `WL-001-000002` 起递增），否则会先撞上"③ 去重"拿到 `BATCH_CONTENT_CONFLICT`，**测不到逐条摘要定位**（见 §0.3 陷阱 1）。唯一例外是"重放"这一类，那正是你要故意触发去重
- **输出**：`test_data/anomalies/<name>.json` + `<name>.label.json`（类型、设备、批次、序号）
- **需要谁的文件**：A 的 `normal_batch.json`

#### S28 · 写 `tests/test_tamper.py`
- **干什么**：逐种发送 → 记录**网关初判 + 中心复核 + 异常序号** → 与 label 对照
- **验收**：审计记录可复现，**正常样本零误报**
- **需要谁的文件**：A 的中心（✅ 到位）

---

### 阶段 P6 离线与缓存篡改演练

#### S30 · `tests/test_offline.py`
- 六个场景：中心断开 / 补传 / 网关重启 / 缓存篡改 / 重复消息 / 跨设备交错
- **验收**：可重复运行并留有日志与真实标签

#### S32 · `tests/tamper_cache.py`
- 中心停机期间改 `edge_gateway/data/edge_cache.db` 里 `pending_batches.raw_json` 的某个监测值，**不动 signature**

#### S33 · 按任务书 P0027–P0032 六步走
| 步 | 动作 | 记录 |
|---|---|---|
| 1 | 全起，确认正常批次进中心 | 最后一个已确认序号 |
| 2 | 停中心，网关与模拟器跑 3–5 分钟 | pending 增长、设备端序号递增 |
| 3 | 停并重启网关 | pending 不丢；恢复中心看按序补传与 ack |
| 4 | 再停中心积累待传；跑 `tamper_cache.py` | —— |
| 5 | 恢复中心 | 中心独立检出，audits 记录设备/批次/**可定位序号**；异常批次不入合法记录 |
| 6 | 测超时、重复提交、跨设备并发 | 开始/结束时间、待传数、成功数、失败数、断点序号、截图 |

- ⚠️ 缓存篡改那条：中心会先撞"③ 去重"给出 `BATCH_CONTENT_CONFLICT`。**这本身就是"独立检出"的证据**，但报告里要写清中心是在哪一步拦下的
- **报告必须写清**（P0033）：网关缓存被改后，**网关此前的"初验通过"不能代替中心复核**

---

### 阶段 P7 交付

#### S40 · 配合队长 100 设备测试
- 导出网关日志 → 收数、转发数、资源占用原始 CSV
- 任务书第 79 行：98% 检测率、P95 ≤ 1 秒、100 台 30 分钟都是**目标值**，结论必须用真实执行结果

#### S41 · 文档与部署复核
- 网关流程图、运行说明、配置样例、报告段落
- 写进 `README.md` 的**独立一节**（网关配置项 + 四终端启动顺序），别重排别人的段落
- 10月16–17日交队长；10月18日另一台电脑按 README 重新部署；10月19日先提交并核对文件能打开

---

## 3. 九步 → 代码落点

| 九步 | 落在哪 |
|---|---|
| 1 记 `gateway_received_at` | `main.py` 工作线程 + `storage.py` 对应列 |
| 2 限大小、解析 JSON、失败也审计、不崩溃 | `main.py`（`MAX_MESSAGE_BYTES` + try/except） |
| 3 查预登记公钥，不用消息自带公钥 | `validator.py` + `config.REGISTRY_PATH` |
| 4 调公共验证 | `common_crypto.batch.verify_batch`（**A 已写好，直接复用**） |
| 5 原文+初判入库，重复不生成第二条 | `storage.py` 事务 + 唯一索引 |
| 6 正常入队；失败送中心 | `POST /batches`（中心自动写 audits） |
| 7 逐设备按序提交，不同设备可交错 | `forward.py` 按 `batch_no` 排序 |
| 8 三条件才标确认 | `forward.py` 的 `can_ack()`（§0.3） |
| 9 定时重试，重启从 DB 恢复 | `forward.py` 轮询 + `main.py` 启动恢复 |

---

## 4. 验收清单

- [x] S02：配置项可由环境变量覆盖（已验证 3 项）
- [x] S03：`mosquitto -c mosquitto.conf -v` 能起，MQTT 最小收发打通
- [x] S04：`python -m pytest` 不再因 MQTT 脚本收集失败（**D13 未修，仍会因缺 `httpx2` 失败**）
- [x] S05/S06：正常批次入队、重复送达不生成第二条、链尾正确推进、初验失败进 `audit_pending` 且不污染链尾
- [x] S10：A 的 `normal_batch.json` 过 `validator.verify()` → `valid=True`
- [x] S15/S16：网关只接收一次并打印 ID；QoS1 重发被唯一约束挡住
- [x] S20：**端到端实测通过** —— 中心停机期间每轮只重试 1 批（不抢跑），恢复后每轮确认 1 批、严格按序
- [x] S22：7 台设备都进了中心 `devices` 表
- [ ] S06 补充：显式跑一次"网关重启后 pending 不丢"（要用 `main.py` 做真实重启，建议写进 S30）
- [ ] S25：六类异常样本齐全，各带 `label.json`，且都用**新 batch_id**
- [ ] S28：label 与中心 audits 对得上，**正常样本零误报**
- [ ] S33：缓存篡改被中心独立拦下并留下审计
- [ ] S41：README 写清配置项与四终端启动顺序
- [ ] 私钥 / `.venv` / `*.db` / 含个人路径的日志**没进 Git**

---

## 5. 提交与协作规矩

- 分支：所有自己的代码提交到 `feature/edge-gateway`，**不要直接推 `main`**
- 每天先 `git fetch origin && git merge origin/main`，再干活
- 合并前必须跑 `python -m pytest`（任务书第 76 行）
- 字段有调整时，**同一个提交**里改公共模型 + 三端代码 + 样本 + 测试（任务书第 10 行）
- 问题记录格式：**复现命令、期望、实际、日志**
- 不提交：私钥、`.venv`、SQLite 数据库、含个人路径的日志
- 共享文件（`README.md`、`requirements.txt`）只加不改别人的段落

---

## 6. 卡住时先查什么

| 现象 | 先查 |
|---|---|
| 设备发了但没收到 | Mosquitto 是否启动、broker IP、1883 端口、主题 `pinglu/batches/+`、Windows 防火墙 |
| 全部批次都是 `UNKNOWN_DEVICE` | 你跑过 S22 吗？中心 `devices` 表是空的 |
| 验签失败 | 公钥是否来自 `registry_public.json`（不是消息自带）；`keys/` 里的私钥是否与登记公钥配套（**D10**） |
| 回的是 `BATCH_CONTENT_CONFLICT` 而不是摘要错误 | 你复用了同一个 `batch_id`——去重在验证之前（§0.3 陷阱 1） |
| 回的是 `INVALID_BATCH_SEQUENCE` | 同设备批次号没递增，或你的补传没按 `batch_no` 排序 |
| 回的是 `INVALID_TIMESTAMP` | 新批次 `start_time` 不晚于该设备上一批最后一条记录时间 |
| 回的是 `INVALID_BATCH_STRUCTURE` | 字段多一个少一个；`payload` 键与设备类型不匹配；`batch_id` 不是 `设备ID-六位正整数` |
| 断网后少数据 | SQLite 事务是否已提交；pending 是否只在 acked 后才变；重启后的队列扫描是否完整 |
| 中心收到两次 | QoS 1 和 HTTP 超时都会重试；按 `device_id + batch_id` 幂等，不依赖恰好一次 |
| `python -m pytest` 收集失败 | 缺 `httpx2`（**D13**） |
| 中心启动报「已有记录不是最终合同格式」 | 本机 `center_platform/data/center.db` 是旧数据，删掉重来 |
