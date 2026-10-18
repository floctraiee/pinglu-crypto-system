# -*- coding: utf-8 -*-
"""把 anomaly_samples 整理成可交付的异常样本证据包。

产出（都在 tests/anomaly_samples/ 下）：
  index.json       每例一条：type / device_id / batch_id / 被改序号 /
                   预期 reason_code / 是否歧义 / 网关初判 / 中心复核
  verify_log.txt   可复现命令 + 逐例判定日志（网关初判 + 中心复核）

用法（仓库根目录）：
    .venv\\Scripts\\python.exe tests\\inject_anomalies.py --per-class 3 --out tests\\anomaly_samples
    .venv\\Scripts\\python.exe tests\\make_anomaly_pack.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from center_platform.validator import validate_batch  # noqa: E402
from common_crypto.hash_chain import ZERO_HASH  # noqa: E402
from edge_gateway import validator as gw  # noqa: E402

SAMPLES = ROOT / "tests" / "anomaly_samples"
REGISTRY = json.loads(
    (ROOT / "test_data" / "registry_public.json").read_text(encoding="utf-8")
)


def main():
    label_path = SAMPLES / "label.json"
    if not label_path.exists():
        raise SystemExit(
            f"找不到 {label_path}；请先跑：\n"
            r"  .venv\Scripts\python.exe tests\inject_anomalies.py --per-class 3 --out tests\anomaly_samples"
        )

    labels = json.loads(label_path.read_text(encoding="utf-8"))
    index = []
    lines = []

    lines.append("# 异常注入样本 —— 网关初判 + 中心复核日志")
    lines.append("")
    lines.append("## 可复现命令")
    lines.append("")
    lines.append("```")
    lines.append("cd /d <仓库根目录>")
    lines.append(
        r".venv\Scripts\python.exe tests\inject_anomalies.py --per-class 3 --out tests\anomaly_samples"
    )
    lines.append(r".venv\Scripts\python.exe tests\make_anomaly_pack.py")
    lines.append("```")
    lines.append("")
    lines.append("## 样本约定")
    lines.append("")
    lines.append("- 所有样本均为**新 batch_id**，与任何真机批次不冲突。")
    lines.append("- **`signature` 类**：篡改被签字段，**不重签**（用于命中签名检查）。")
    lines.append(
        "- **`batch_count`、`merkle_root` 类**：篡改后**重新签名**，"
        "用于验证签名之后的检查项依然生效。"
    )
    lines.append(
        "- **`hash_chain` 类**：改 `previous_hash` 并重算该条 `record_hash`，用于精确命中链检查。"
    )
    lines.append("- **`record_payload`、`structure` 类**：**未重签**（前者只改记录内容，后者删字段）。")
    lines.append("")
    lines.append("## 逐例结果")
    lines.append("")

    for item in labels:
        batch = json.loads((SAMPLES / item["file"]).read_text(encoding="utf-8"))
        device_id = batch.get("device_id")
        batch_id = batch.get("batch_id")
        registration = REGISTRY.get(device_id)

        gw_result = gw.verify(batch, registration, None)
        ct_result = validate_batch(batch, registration, ZERO_HASH, 1)

        ok_gw = gw_result["reason_code"] == item["expect_reason"]
        ok_ct = ct_result["reason_code"] == item["expect_reason"]

        index.append({
            "type": item["category"],
            "file": item["file"],
            "device_id": device_id,
            "batch_id": batch_id,
            "tampered_sequence": item["expect_sequence"],
            "expect_reason_code": item["expect_reason"],
            "ambiguity": item["granularity"],
            "gateway_reason_code": gw_result["reason_code"],
            "center_reason_code": ct_result["reason_code"],
            "gateway_matches": ok_gw,
            "center_matches": ok_ct,
        })

        lines.append("### " + item["file"])
        lines.append("")
        lines.append("- type: `" + item["category"] + "`")
        lines.append("- device_id: `" + str(device_id) + "`   batch_id: `" + str(batch_id) + "`")
        lines.append(
            "- 被改序号: `" + str(item["expect_sequence"]) + "`   歧义粒度: `"
            + item["granularity"] + "`"
        )
        lines.append("- 预期 reason_code: `" + item["expect_reason"] + "`")
        lines.append(
            "- **网关初判**: `" + gw_result["reason_code"] + "`  "
            + ("PASS" if ok_gw else "FAIL")
        )
        lines.append(
            "- **中心复核**: `" + ct_result["reason_code"] + "`  "
            + ("PASS" if ok_ct else "FAIL")
        )
        affected = gw_result.get("affected_sequences") or []
        if affected:
            lines.append("- 定位序号: `" + str(affected) + "`")
        lines.append("")

    (SAMPLES / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (SAMPLES / "verify_log.txt").write_text("\n".join(lines), encoding="utf-8")

    total = len(index)
    gw_ok = sum(1 for x in index if x["gateway_matches"])
    ct_ok = sum(1 for x in index if x["center_matches"])
    print("样本总数 :", total)
    print("网关初判 :", gw_ok, "/", total, "命中预期")
    print("中心复核 :", ct_ok, "/", total, "命中预期")
    print("输出     :", SAMPLES / "index.json")
    print("           ", SAMPLES / "verify_log.txt")
    return 0 if gw_ok == total and ct_ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
