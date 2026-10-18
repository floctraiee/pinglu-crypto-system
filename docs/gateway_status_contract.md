# 网关只读状态文件约定 `gateway_status.json`

> B 端网关按本约定写出只读状态文件，中心平台 `/offline-status`（离线补传页）自动识别并优先采用它；
> 没有这个文件时才降级读模拟器的 `simulator.db`（只能反映"发了多少"，反映不了"缓存多少、断点在哪、补传进度"）。

## 1. 写在哪

按以下顺序查找，命中第一个即用：

| 顺序 | 路径 | 说明 |
|---|---|---|
| 1 | `$PINGLU_GATEWAY_STATUS` | 环境变量显式指定（异机部署/验证时用） |
| 2 | `edge_gateway/data/gateway_status.json` | **默认位置**，与网关缓存库同目录 |
| 3 | `edge_gateway/gateway_status.json` | 兼容位置 |

B 端用 `edge_gateway/status.py::write()` 写出，默认落到第 2 条。
可用 `PINGLU_STATUS_PATH` 覆盖写出位置。

## 2. 结构

```json
{
  "generated_at": "2026-10-18T18:50:00.000+08:00",
  "source": "edge_gateway.status",
  "queue": {"accepted": 19800},
  "latency": {"count": 19800, "min_ms": 76.0, "median_ms": 670.0,
              "p95_ms": 2447.0, "max_ms": 8163.0,
              "target_ms": 1000.0, "p95_within_target": false},
  "devices": [
    {
      "device_id": "WL-001",
      "device_type": "water_level",
      "cached_batches": 198,
      "cached_records": 1980,
      "resend_done": 198,
      "resend_pending": 0,
      "break_sequence": 1980,
      "last_gateway_received_at": "2026-10-18T18:05:12.345+08:00",
      "last_center_ack_at": "2026-10-18T18:05:13.012+08:00",
      "note": "已全部确认"
    }
  ]
}
```

## 3. 字段（`devices[]`，中心只强制要求 `device_id`）

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `device_id` | string | ✅ | 设备编号；空字符串或缺失的条目会被中心丢弃 |
| `device_type` | string | | 设备类型：`water_level` / `weather` / `navigation_mark` / `slope` / `lock` / `drone` / `survey_boat` |
| `cached_batches` | int | | 网关本地缓存的批次数 |
| `cached_records` | int | | 网关本地缓存的记录数 |
| `resend_done` | int | | 已补传完成（`accepted` / `duplicate`）的批次数 |
| `resend_pending` | int | | 仍需补传（`pending` / `audit_pending`）的批次数 |
| `break_sequence` | int \| null | | 网关侧可信链尾序号，中心据此判断"补到哪一条" |
| `last_gateway_received_at` | string \| null | | 该设备最后一批的网关接收时间（ISO 8601，`+08:00`） |
| `last_center_ack_at` | string \| null | | 该设备最后一批的中心确认时间 |
| `note` | string \| null | | 人读说明，例如"待补传 N 批" |

**中心侧汇总（`summary`）由这些字段自动算：**

| 汇总字段 | 计算方式 |
|---|---|
| `cached_batches` / `cached_records` | 所有设备求和 |
| `resend_done` / `resend_pending` | 所有设备求和 |
| `devices_with_backlog` | `resend_pending > 0` 的设备数 |
| `progress_percent` | `resend_done / (resend_done + resend_pending) × 100` |

## 4. 一致性要求

- **原子写入**：先写 `gateway_status.json.tmp`，再 `rename` 覆盖。中心任何时刻读到的都是完整 JSON。
- **刷新频率**：默认每 `STATUS_INTERVAL = 5` 秒一次（可环境变量覆盖）。太频繁会白扫一遍 `pending_batches`。
- **写出失败不影响转发**：`forward.run_forever` 里写出异常只打日志，不中断转发线程。

## 5. 校验方法

```bat
:: 网关跑起来后，另开窗口
type edge_gateway\data\gateway_status.json

:: 中心读到的结果
powershell -NoProfile -ExecutionPolicy Bypass -Command "(Invoke-WebRequest 'http://127.0.0.1:8000/offline-status' -UseBasicParsing).Content"
```

中心返回里的 `source` 应为 `gateway_status_file`（**不是** `simulator_db`），
`source_note` 应形如 `来自 B 端网关只读状态文件：edge_gateway\data\gateway_status.json`。

## 6. 实现位置

| 文件 | 作用 |
|---|---|
| `edge_gateway/status.py` | `collect()` 汇总、`write()` 原子落盘 |
| `edge_gateway/config.py` | `STATUS_PATH`、`STATUS_INTERVAL` |
| `edge_gateway/forward.py` | `run_forever` 里按 `STATUS_INTERVAL` 调 `status.write()` |
| `center_platform/telemetry.py` | `_read_gateway_status()` 读取并归一化 |
