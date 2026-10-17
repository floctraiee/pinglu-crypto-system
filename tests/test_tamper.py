"""异常注入对照实验：检测率 / 定位准确率 / 误报率。

判定用的是 common_crypto.batch.verify_batch —— 与中心、网关完全同一个函数
（center_platform/validator.py 与 edge_gateway/validator.py 都只是它的包装），
所以本地跑出来的指标等价于送到中心的指标，且不会污染中心数据库。

运行
----
    .venv\\Scripts\\python.exe -m pytest tests/test_tamper.py -v -s
"""
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common_crypto.batch import verify_batch  # noqa: E402
from common_crypto.hash_chain import ZERO_HASH  # noqa: E402

CASES_DIR = ROOT / "tests" / "anomaly_cases"
REPORT_PATH = ROOT / "tests" / "anomaly_report.md"
PER_CLASS = 100

TARGET_DETECTION = 0.98
TARGET_LOCALIZATION = 0.98
TARGET_FALSE_POSITIVE = 0.02

_REGISTRY = json.loads(
    (ROOT / "test_data" / "registry_public.json").read_text(encoding="utf-8")
)


def _ensure_cases():
    """样本不存在就先按默认规模生成一份（700 份，约需 30~60 秒）。"""
    label = CASES_DIR / "label.json"
    if label.exists() and len(json.loads(label.read_text(encoding="utf-8"))) >= PER_CLASS * 7:
        return
    subprocess.run(
        [sys.executable, str(ROOT / "tests" / "inject_anomalies.py"),
         "--per-class", str(PER_CLASS), "--out", str(CASES_DIR)],
        check=True, cwd=str(ROOT),
    )


def _verify(batch):
    registration = _REGISTRY.get(batch.get("device_id"))
    return verify_batch(batch, registration, ZERO_HASH, 1)


@pytest.fixture(scope="module")
def evaluation():
    _ensure_cases()
    labels = json.loads((CASES_DIR / "label.json").read_text(encoding="utf-8"))

    stats = defaultdict(lambda: {"total": 0, "detected": 0, "localized": 0,
                                 "reasons": defaultdict(int)})
    for item in labels:
        batch = json.loads((CASES_DIR / item["file"]).read_text(encoding="utf-8"))
        result = _verify(batch)
        row = stats[item["category"]]
        row["total"] += 1

        if item["category"] == "normal":
            if not result["valid"]:
                row["detected"] += 1          # 正常样本被判异常 = 误报
                row["reasons"][result["reason_code"]] += 1
            continue

        if not result["valid"]:
            row["detected"] += 1
        row["reasons"][result["reason_code"]] += 1

        if result["reason_code"] != item["expect_reason"]:
            continue
        granularity = item["granularity"]
        sequences = result["affected_sequences"]
        if granularity == "exact":
            if sequences == [item["expect_sequence"]]:
                row["localized"] += 1
        elif granularity == "contains":
            if item["expect_sequence"] in sequences:
                row["localized"] += 1
        else:  # empty：头部级异常不应冤枉任何一条记录
            if sequences == []:
                row["localized"] += 1

    return dict(stats)


def _pct(numerator, denominator):
    return numerator / denominator if denominator else 0.0


def test_metrics_meet_targets(evaluation):
    abnormal = {k: v for k, v in evaluation.items() if k != "normal"}
    normal = evaluation.get("normal", {"total": 0, "detected": 0})

    total_abnormal = sum(v["total"] for v in abnormal.values())
    total_detected = sum(v["detected"] for v in abnormal.values())
    total_localized = sum(v["localized"] for v in abnormal.values())

    detection = _pct(total_detected, total_abnormal)
    localization = _pct(total_localized, total_abnormal)
    false_positive = _pct(normal["detected"], normal["total"])

    lines = ["# 异常注入对照实验结果", "",
             f"样本总数：正常 {normal['total']} 份，异常 {total_abnormal} 份", "",
             "| 类别 | 样本 | 检出 | 定位正确 | 实际 reason_code |",
             "|---|---|---|---|---|"]
    for name in sorted(abnormal):
        row = abnormal[name]
        reasons = ", ".join(f"{k}×{v}" for k, v in sorted(row["reasons"].items()))
        lines.append(
            f"| {name} | {row['total']} | {row['detected']} | {row['localized']} | {reasons} |"
        )
    lines += ["",
              "| 指标 | 实测 | 目标 | 结论 |",
              "|---|---|---|---|",
              f"| 检测率 | {detection:.2%} | >= {TARGET_DETECTION:.0%} | "
              f"{'达标' if detection >= TARGET_DETECTION else '未达标'} |",
              f"| 定位准确率 | {localization:.2%} | >= {TARGET_LOCALIZATION:.0%} | "
              f"{'达标' if localization >= TARGET_LOCALIZATION else '未达标'} |",
              f"| 误报率 | {false_positive:.2%} | <= {TARGET_FALSE_POSITIVE:.0%} | "
              f"{'达标' if false_positive <= TARGET_FALSE_POSITIVE else '未达标'} |",
              ""]
    report = "\n".join(lines)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print("\n" + report)

    assert detection >= TARGET_DETECTION, f"检测率 {detection:.2%} 低于目标"
    assert localization >= TARGET_LOCALIZATION, f"定位准确率 {localization:.2%} 低于目标"
    assert false_positive <= TARGET_FALSE_POSITIVE, f"误报率 {false_positive:.2%} 高于目标"


def test_normal_samples_have_no_record_errors(evaluation):
    """正常样本不应报出任何记录级错误。"""
    normal = evaluation.get("normal", {"total": 0, "detected": 0})
    assert normal["detected"] == 0, f"{normal['detected']} 份正常样本被误判"