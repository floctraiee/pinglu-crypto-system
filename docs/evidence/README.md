# 证据归档目录（docs/evidence）

本目录是**签名对照与性能类证据 CSV 的默认归档位置**；`tests/benchmark_signing.py` 不指定
`--out` 时就把 CSV 写到这里，中心页面第⑥块“性能对比”也会优先读这里。

## 页面读取顺序

1. `docs/evidence/benchmark_signing*.csv`
2. 仓库根目录 `benchmark_signing*.csv`

按文件名排序取**第一个**匹配。因此同一时间只应保留**一份** `benchmark_signing_*.csv`，
否则页面可能读到旧文件。重新测量后请把旧文件删掉或改名归档。

## 命名与生成

- 命名：`benchmark_signing_<YYYYMMDD>.csv`
- 生成：`python tests/benchmark_signing.py --records 100`
- 必需列：`device_id / device_scale / batch_size / batches / signatures / sign_mean_ms /
  sign_p95_ms / verify_mean_ms / verify_p95_ms / message_bytes / tested_at`
  （完整定义见 `center_platform/telemetry.py` 的 `PERFORMANCE_COLUMNS`）

## 现状

历史那份 `benchmark_signing_20261014.csv`（每批 1 / 10 / 20 条对照，设备 SL-001）
目前仍在**仓库根目录**，页面会按上面的顺序在根目录找到它。
下次重测时用默认 `--out` 生成到本目录即可；确认新结果无误后，把根目录那份删掉，
保证页面只读到最新一份。

## 不要放这里

- 设备私钥、`*.db`、`keys/` 相关文件
- 含个人路径或身份的日志
- 与性能页无关的临时文件（`*.tmp` 已在 `.gitignore` 中忽略）

其余实验证据（`验证结果_*.txt/.csv/.json`、`P33_实时延迟口径与数据_*.md`）仍放在仓库根目录，
报告附录 B 以文件名引用。
