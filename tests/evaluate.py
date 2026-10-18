"""把系统输出与真实标签对比，算检测率、定位准确率、误报率。

用法
----
    rem 1) 生成带真实标签的样本（含合法与各类异常）
    python tests/evaluate.py --make-samples --out test_data/eval_samples.json

    rem 2) 逐个提交中心并读回判定，最后评分
    python tests/evaluate.py --samples test_data/eval_samples.json --submit ^
        --url http://127.0.0.1:8000 ^
        --results test_data/eval_results.json ^
        --out-csv docs/evidence/evaluate_20261014.csv ^
        --out-json docs/evidence/evaluate_20261014.json

    rem 3) 离线评分：标签 + 已有结果文件（不连中心）
    python tests/evaluate.py --samples test_data/eval_samples.json ^
        --results test_data/eval_results.json --out-csv result.csv

    rem 4) 直接读中心库 audits 表评分
    python tests/evaluate.py --samples test_data/eval_samples.json --from-db

指标口径（报告里必须写明）
------------------------
把「非 normal 的样本」视为应被拦截的正类（positive），normal 样本为负类（negative）。

    检测率     = 被正确判为 rejected/duplicate 的异常样本 / 全部异常样本
    误报率     = 被误判为 rejected/duplicate 的正常样本 / 全部正常样本
    定位准确率 = 受影响序号集合完全正确的异常样本 / 全部异常样本

定位采用**严格集合相等**：`affected_sequences` 必须与标签的 `expected_sequences`
完全一致（去重、顺序无关）。标签里 `expected_sequences` 为 null 表示该用例不考察定位，
不进入定位准确率的分母。

`ambiguous` 标记的用例（例如删记录可能被 INVALID_BATCH_RANGE 拦住，也可能被更早的
序号检查拦住）不进入严格检测率，只进入宽松检测率。两个数都打印，报告里注明取哪个。

断链、缺记录这类错误会**级联**：被改的那条之后的记录 previous_hash 全部对不上，
中心会把该条到批末的序号都列进 `affected_sequences`。这属于正确行为，
定位标签按中心的实际语义写成序号区间，而不是只写被改的那一条。

样本文件格式（schema: pinglu-eval-samples/1）
--------------------------------------------
{
  "device_id": "SL-001",
  "cases": [
    {
      "case_id": "tamper_value-04",
      "kind": "tamper_value",
      "expect_detected": true,
      "expected_status": ["rejected"],
      "expected_reason_codes": ["INVALID_RECORD_HASH"],
      "expected_sequences": [2],
      "ambiguous": false,
      "batch_id": "SL-001-000004",
      "batch": { ... 完整批次，供提交 ... }
    }
  ]
}
"""

import argparse
import csv
import json
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，中文表格会报 UnicodeEncodeError；统一改成 UTF-8。
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

REGISTRY_PATH = ROOT / "test_data" / "registry_public.json"
KEYS_DIR = ROOT / "keys"
ZERO_HASH = "0" * 64

# 这些状态算"系统拦住了"
DETECTED_STATUSES = {"rejected", "duplicate"}

KINDS = (
    "normal",                # 合法批次，应 accepted
    "duplicate",             # 原样重复提交
    "tamper_value",          # 改测量值，摘要不重算
    "tamper_count",          # 改数量后重签
    "tamper_previous_hash",  # 断链
    "missing_record",        # 删记录，数量/区间对不上
    "forged_signature",      # 签名置零
    "recomputed_hash",       # 改值并重算摘要，但签名根对不上
    "unknown_device",        # 未登记设备
    "sequence_gap",          # 序号跳号
    "batch_number_replay",   # 历史重放：复用已入库批次号
    "extra_field",           # 未约定字段
)


def now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def load_registry():
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def load_key(device_id):
    return json.loads((KEYS_DIR / f"{device_id}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 提交与读取

def post_batch(url, batch):
    request = urllib.request.Request(
        url.rstrip("/") + "/batches",
        data=json.dumps({"batch": batch}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"status": "http_error", "reason_code": f"HTTP_{exc.code}"}


def load_results_from_db(db_path):
    """从中心库 audits 表读回判定，按 batch_id 建索引。"""
    import sqlite3

    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row

    try:
        rows = connection.execute(
            """
            SELECT device_id, batch_id, status, reason_code,
                   affected_sequences, created_at
            FROM audits
            ORDER BY id
            """
        ).fetchall()
    finally:
        connection.close()

    results = {}

    for row in rows:
        sequences = []

        if row["affected_sequences"]:
            try:
                sequences = json.loads(row["affected_sequences"])
            except (TypeError, ValueError):
                sequences = []

        # 同一批次可能被审计多次（重复提交后再冲突），保留最后一条。
        results[row["batch_id"]] = {
            "status": row["status"],
            "reason_code": row["reason_code"],
            "affected_sequences": sequences,
            "created_at": row["created_at"],
            "source": "center_db.audits",
        }

    return results


# ---------------------------------------------------------------- 造带标签样本

def build_samples(device_id, batch_size, pieces, out_path=None):
    """为指定设备造一组从链尾续上的合法/异常批次，并附真实标签。

    每个用例都占一个独立批次号，避免中心先卡在重复/冲突判定上，
    从而测不到签名、摘要、链、根这些真正的密码学检查。
    异常用例一律从当前链尾续上，保证先撞到真正要测的那一层检查。
    """
    from common_crypto.canonical import canonical_bytes, signed_header
    from common_crypto.hash_chain import record_hash
    from common_crypto.merkle import merkle_root
    from common_crypto.sm import sign_message
    from device_simulator.generator import make_records

    registry = load_registry()

    if device_id not in registry:
        raise SystemExit(f"{device_id} 不在登记表中：{REGISTRY_PATH}")

    registration = registry[device_id]
    private_key = load_key(device_id)["private_key"]
    public_key = registration["public_key"]

    start = datetime(2026, 10, 14, 14, 0, 0, tzinfo=timezone(timedelta(hours=8)))

    cases = []
    number_box = {"value": 1}

    def take_number():
        value = number_box["value"]
        number_box["value"] += 1
        return value

    def new_batch(number, sequence_start, count, previous):
        batch_id = f"{device_id}-{number:06d}"
        records = make_records(
            registration=registration,
            batch_id=batch_id,
            start_sequence=sequence_start,
            previous_hash=previous,
            start_time=start + timedelta(minutes=number),
            count=count,
            seed=2026,
        )
        batch = {
            "version": 1,
            "device_id": device_id,
            "batch_id": batch_id,
            "start_sequence": records[0]["sequence"],
            "end_sequence": records[-1]["sequence"],
            "count": len(records),
            "start_time": records[0]["timestamp"],
            "end_time": records[-1]["timestamp"],
            "merkle_root": merkle_root([r["record_hash"] for r in records]),
            "records": records,
        }
        batch["signature"] = sign_message(
            private_key, public_key, canonical_bytes(signed_header(batch))
        )
        return batch

    def resign(batch):
        batch["signature"] = sign_message(
            private_key, public_key, canonical_bytes(signed_header(batch))
        )
        return batch

    def rebatch(batch, new_batch_id, resequence_to=None):
        """换批次号（可选同时改序号），并重算每条摘要与根。"""
        batch["batch_id"] = new_batch_id

        for offset, record in enumerate(batch["records"]):
            record["batch_id"] = new_batch_id
            if resequence_to is not None:
                record["sequence"] = resequence_to + offset
            record["record_hash"] = record_hash(record)

        batch["start_sequence"] = batch["records"][0]["sequence"]
        batch["end_sequence"] = batch["records"][-1]["sequence"]
        batch["merkle_root"] = merkle_root(
            [r["record_hash"] for r in batch["records"]]
        )
        return resign(batch)

    def sequence_range(batch):
        """该批次覆盖的序号区间，用于断链/缺记录这类会级联报错的定位标签。"""
        return list(range(batch["start_sequence"], batch["end_sequence"] + 1))

    def add(case_id, kind, batch, expect_detected, expected_status,
            expected_reasons, expected_sequences, ambiguous=False):
        cases.append({
            "case_id": case_id,
            "kind": kind,
            "expect_detected": expect_detected,
            "expected_status": list(expected_status),
            "expected_reason_codes": list(expected_reasons),
            "expected_sequences": expected_sequences,
            "ambiguous": ambiguous,
            "batch_id": batch["batch_id"],
            "batch": batch,
        })

    # ---- 先铺若干 normal，供 duplicate / batch_number_replay 参照 ----
    normal_count = max(3, sum(1 for piece in pieces if piece == "normal"))

    chain_sequence = 1
    chain_hash = ZERO_HASH
    normals = []

    for _ in range(normal_count):
        number = take_number()
        batch = new_batch(number, chain_sequence, batch_size, chain_hash)
        add(
            f"normal-{number:02d}", "normal", batch,
            expect_detected=False,
            expected_status=["accepted"],
            expected_reasons=["OK"],
            expected_sequences=None,
        )
        normals.append(batch)
        chain_sequence = batch["end_sequence"] + 1
        chain_hash = batch["records"][-1]["record_hash"]

    # ---- 逐个异常用例 ----
    for piece in pieces:
        if piece == "normal":
            continue

        number = take_number()
        case_id = f"{piece}-{number:02d}"

        if piece == "duplicate":
            add(
                case_id, "duplicate", json.loads(json.dumps(normals[-1])),
                expect_detected=True,
                expected_status=["duplicate"],
                expected_reasons=["DUPLICATE_BATCH"],
                expected_sequences=None,
            )
            continue

        batch = new_batch(number, chain_sequence, batch_size, chain_hash)
        expected_sequences = None
        ambiguous = False
        statuses = ["rejected"]
        reasons = []

        if piece == "tamper_value":
            target = batch["records"][1]
            field = list(target["payload"])[0]
            target["payload"][field] = "999.00"
            reasons = ["INVALID_RECORD_HASH"]
            expected_sequences = [target["sequence"]]

        elif piece == "tamper_count":
            batch["count"] = batch_size + 1
            resign(batch)
            reasons = ["INVALID_BATCH_RANGE"]

        elif piece == "tamper_previous_hash":
            target = batch["records"][1]
            target["previous_hash"] = "1" * 64
            target["record_hash"] = record_hash(target)
            batch["merkle_root"] = merkle_root(
                [r["record_hash"] for r in batch["records"]]
            )
            resign(batch)
            reasons = ["INVALID_HASH_CHAIN", "INVALID_RECORD_HASH"]
            # 断链会级联：被改的那条摘要变了，紧跟其后的一条 previous_hash 就对不上，
            # 再往后又各自自洽，所以中心只列这两条。
            expected_sequences = sequence_range(batch)[1:3]
            ambiguous = True

        elif piece == "missing_record":
            # 删掉第 2 条：数量与序号区间仍旧对不上，且剩余记录的摘要链断开。
            # 中心的语义是报"剩余记录里链对不上的那些条"。
            kept_start = batch["records"][0]["sequence"]
            batch["records"] = batch["records"][:1] + batch["records"][2:]
            batch["merkle_root"] = merkle_root(
                [r["record_hash"] for r in batch["records"]]
            )
            resign(batch)
            reasons = ["INVALID_BATCH_RANGE", "INVALID_SEQUENCE", "INVALID_HASH_CHAIN"]
            expected_sequences = [
                record["sequence"]
                for record in batch["records"]
                if record["sequence"] != kept_start
            ]
            ambiguous = True

        elif piece == "forged_signature":
            batch["signature"] = "0" * 128
            reasons = ["INVALID_SIGNATURE"]

        elif piece == "recomputed_hash":
            # 改最后一条并重算摘要：链仍然自洽，只有签名的 Merkle 根对不上，
            # 这样才能真正测到"签名的根"这一层检查。
            target = batch["records"][-1]
            field = list(target["payload"])[0]
            target["payload"][field] = "999.00"
            target["record_hash"] = record_hash(target)
            resign(batch)
            reasons = ["INVALID_MERKLE_ROOT"]
            # 根是批次级校验：改完摘要后每条记录自身都自洽，
            # 所以中心不指认具体序号，affected_sequences 为空是正确的。
            expected_sequences = []

        elif piece == "unknown_device":
            rebatch(batch, f"XX-999-{number:06d}")
            for record in batch["records"]:
                record["device_id"] = "XX-999"
                record["record_hash"] = record_hash(record)
            batch["device_id"] = "XX-999"
            batch["merkle_root"] = merkle_root(
                [r["record_hash"] for r in batch["records"]]
            )
            batch["signature"] = "0" * 128
            reasons = ["UNKNOWN_DEVICE"]

        elif piece == "sequence_gap":
            rebatch(batch, batch["batch_id"], resequence_to=chain_sequence + 50)
            reasons = ["INVALID_SEQUENCE"]
            # 整个批次的序号都不接中心链尾，中心把该批全部序号列为受影响。
            expected_sequences = sequence_range(batch)

        elif piece == "batch_number_replay":
            # 复用已入库的批次号，但内容不同 → 历史重放。
            replayed = int(normals[-1]["batch_id"].rsplit("-", 1)[1])
            rebatch(batch, f"{device_id}-{replayed:06d}")
            statuses = ["rejected", "duplicate"]
            reasons = [
                "BATCH_CONTENT_CONFLICT",
                "INVALID_BATCH_SEQUENCE",
                "DUPLICATE_BATCH",
            ]

        elif piece == "extra_field":
            batch["public_key"] = public_key
            reasons = ["INVALID_BATCH_STRUCTURE"]

        else:
            raise SystemExit(f"未实现的用例类型：{piece}")

        add(
            case_id, piece, batch,
            expect_detected=True,
            expected_status=statuses,
            expected_reasons=reasons,
            expected_sequences=expected_sequences,
            ambiguous=ambiguous,
        )

    payload = {
        "schema": "pinglu-eval-samples/1",
        "generated_at": now_iso(),
        "device_id": device_id,
        "batch_size": batch_size,
        "case_count": len(cases),
        "cases": cases,
    }

    if out_path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return payload


# ---------------------------------------------------------------- 评分

def normalize_sequences(value):
    if value is None:
        return None

    if not isinstance(value, (list, tuple)):
        return None

    return sorted({int(item) for item in value})


def score(samples, results):
    """按标签与结果算指标，返回明细与汇总。

    `results` 优先按 case_id 索引（提交时用），退化时按 batch_id 索引（读审计表时用）。
    必须优先 case_id：duplicate 类用例会复用 normal 的 batch_id，
    按 batch_id 索引会把两次结果互相覆盖。
    """
    details = []
    counts = Counter()

    for case in samples["cases"]:
        case_id = case["case_id"]
        batch_id = case.get("batch_id")
        result = results.get(case_id)

        if result is None and case_id not in results:
            result = results.get(batch_id)

        actual_status = (result or {}).get("status")
        actual_reason = (result or {}).get("reason_code")
        actual_sequences = normalize_sequences(
            (result or {}).get("affected_sequences")
        )

        expected_statuses = set(case.get("expected_status") or [])
        expected_reasons = set(case.get("expected_reason_codes") or [])
        expected_sequences = normalize_sequences(case.get("expected_sequences"))

        is_anomaly = case["kind"] != "normal"
        ambiguous = bool(case.get("ambiguous"))

        status_hit = actual_status in expected_statuses if expected_statuses else None
        reason_hit = actual_reason in expected_reasons if expected_reasons else None
        detected = actual_status in DETECTED_STATUSES

        if is_anomaly:
            counts["anomaly_total"] += 1
            if not ambiguous:
                counts["anomaly_strict_total"] += 1
                if detected:
                    counts["anomaly_strict_detected"] += 1
            if detected:
                counts["anomaly_detected"] += 1
            if status_hit:
                counts["anomaly_status_hit"] += 1
            if reason_hit:
                counts["anomaly_reason_hit"] += 1
        else:
            counts["normal_total"] += 1
            if detected:
                counts["normal_detected"] += 1
            if actual_status == "accepted":
                counts["normal_accepted"] += 1

        localization = None
        if is_anomaly and expected_sequences is not None:
            counts["localization_total"] += 1
            localization = actual_sequences == expected_sequences
            if localization:
                counts["localization_hit"] += 1

        details.append({
            "case_id": case_id,
            "kind": case["kind"],
            "batch_id": batch_id,
            "expect_detected": case["expect_detected"],
            "actual_status": actual_status,
            "actual_reason_code": actual_reason,
            "expected_reason_codes": sorted(expected_reasons),
            "status_hit": status_hit,
            "reason_hit": reason_hit,
            "detected": detected,
            "ambiguous": ambiguous,
            "expected_sequences": expected_sequences,
            "actual_sequences": actual_sequences,
            "localization_hit": localization,
            "missing_result": result is None,
        })

    def rate(hit_key, total_key):
        total = counts[total_key]
        return round(counts[hit_key] * 100.0 / total, 2) if total else None

    summary = {
        "anomaly_total": counts["anomaly_total"],
        "anomaly_strict_total": counts["anomaly_strict_total"],
        "anomaly_detected": counts["anomaly_detected"],
        "anomaly_status_hit": counts["anomaly_status_hit"],
        "anomaly_reason_hit": counts["anomaly_reason_hit"],
        "normal_total": counts["normal_total"],
        "normal_detected": counts["normal_detected"],
        "normal_accepted": counts["normal_accepted"],
        "localization_total": counts["localization_total"],
        "localization_hit": counts["localization_hit"],
        "detection_rate_strict_percent": rate(
            "anomaly_strict_detected", "anomaly_strict_total"
        ),
        "detection_rate_lenient_percent": rate(
            "anomaly_detected", "anomaly_total"
        ),
        "reason_match_percent": rate("anomaly_reason_hit", "anomaly_total"),
        "status_match_percent": rate("anomaly_status_hit", "anomaly_total"),
        "localization_accuracy_percent": rate(
            "localization_hit", "localization_total"
        ),
        "false_positive_rate_percent": rate("normal_detected", "normal_total"),
        "normal_accept_rate_percent": rate("normal_accepted", "normal_total"),
        "missing_results": sum(1 for item in details if item["missing_result"]),
    }

    return details, summary


def fmt_pct(value):
    return "—" if value is None else f"{value:.2f}%"


def print_report(details, summary, samples):
    say = print
    say("")
    say("=" * 82)
    say("系统输出与真实标签对比")
    say(
        f"schema={samples.get('schema')}  设备={samples.get('device_id')}  "
        f"用例数={samples.get('case_count')}"
    )
    say("=" * 82)
    say("")
    say(f"{'用例':<26}{'类型':<22}{'期望':<8}{'实际状态':<12}{'原因码':<26}{'命中'}")
    say("-" * 82)

    for item in details:
        if item["missing_result"]:
            mark = "无结果"
        elif item["status_hit"] is False:
            mark = "状态不符"
        elif item["reason_hit"] is False:
            mark = "原因不符"
        elif item["localization_hit"] is False:
            mark = "定位不符"
        else:
            mark = "OK"

        say(
            f"{item['case_id']:<26}{item['kind']:<22}"
            f"{('拦截' if item['expect_detected'] else '放行'):<8}"
            f"{str(item['actual_status']):<12}{str(item['actual_reason_code']):<26}{mark}"
        )

    say("")
    say("-" * 82)
    say("指标（口径见文件头注释）")
    say("-" * 82)
    say(f"  异常样本总数                 {summary['anomaly_total']}")
    say(f"  其中严格口径（排除 ambiguous） {summary['anomaly_strict_total']}")
    say(f"  严格检测率                   {fmt_pct(summary['detection_rate_strict_percent'])}")
    say(f"  宽松检测率（含 ambiguous）    {fmt_pct(summary['detection_rate_lenient_percent'])}")
    say(f"  状态判定命中率               {fmt_pct(summary['status_match_percent'])}")
    say(f"  原因码命中率                 {fmt_pct(summary['reason_match_percent'])}")
    say(
        f"  定位准确率（序号集合全对）    "
        f"{fmt_pct(summary['localization_accuracy_percent'])}"
        f"（{summary['localization_hit']}/{summary['localization_total']}）"
    )
    say(
        f"  误报率                       "
        f"{fmt_pct(summary['false_positive_rate_percent'])}"
        f"（{summary['normal_detected']}/{summary['normal_total']}）"
    )
    say(f"  正常样本放行率               {fmt_pct(summary['normal_accept_rate_percent'])}")

    if summary["missing_results"]:
        say("")
        say(
            f"  注意：有 {summary['missing_results']} 个用例没取到系统结果，"
            "指标不可信；请确认样本已提交或结果文件完整。"
        )

    say("")


def write_csv(details, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    columns = [
        "case_id", "kind", "batch_id", "expect_detected", "actual_status",
        "actual_reason_code", "expected_reason_codes", "status_hit", "reason_hit",
        "detected", "ambiguous", "expected_sequences", "actual_sequences",
        "localization_hit", "missing_result",
    ]

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()

        for item in details:
            row = dict(item)
            row["expected_reason_codes"] = "|".join(item["expected_reason_codes"] or [])
            row["expected_sequences"] = json.dumps(item["expected_sequences"])
            row["actual_sequences"] = json.dumps(item["actual_sequences"])
            writer.writerow({key: row.get(key) for key in columns})

    return path


def main():
    parser = argparse.ArgumentParser(
        description="系统输出 vs 真实标签：检测率 / 定位准确率 / 误报率",
    )
    parser.add_argument("--make-samples", action="store_true",
                        help="只生成带标签的样本，不评分")
    parser.add_argument("--samples", type=Path,
                        default=ROOT / "test_data" / "eval_samples.json",
                        help="标签样本文件")
    parser.add_argument("--results", type=Path, default=None,
                        help="已有结果 JSON，离线评分用")
    parser.add_argument("--from-db", action="store_true",
                        help="从中心库 audits 表读回判定结果")
    parser.add_argument("--db", type=Path,
                        default=ROOT / "center_platform" / "data" / "center.db")
    parser.add_argument("--submit", action="store_true",
                        help="把样本逐个 POST 到中心，并把返回结果存成 --results")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--device", default="SL-001", help="造样本用的设备 ID")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--pieces", default=None,
                        help="逗号分隔的用例类型；默认覆盖 KINDS 全量")
    parser.add_argument("--out", type=Path, default=None, help="样本输出路径")
    parser.add_argument("--out-csv", type=Path, default=None, help="明细 CSV 输出")
    parser.add_argument("--out-json", type=Path, default=None, help="汇总 JSON 输出")
    args = parser.parse_args()

    if args.make_samples:
        pieces = (
            [item.strip() for item in args.pieces.split(",")]
            if args.pieces else list(KINDS)
        )
        unknown = [item for item in pieces if item not in KINDS]

        if unknown:
            raise SystemExit(f"未知用例类型：{unknown}；可选：{list(KINDS)}")

        out = args.out or (ROOT / "test_data" / "eval_samples.json")
        payload = build_samples(args.device, args.batch_size, pieces, out)
        print(f"已生成 {payload['case_count']} 个带标签用例 → {out}")

        for kind, count in Counter(case["kind"] for case in payload["cases"]).items():
            print(f"  {kind:<22} {count}")

        return 0

    if not args.samples.exists():
        raise SystemExit(
            f"样本文件不存在：{args.samples}\n"
            "先运行：python tests/evaluate.py --make-samples"
        )

    samples = json.loads(args.samples.read_text(encoding="utf-8"))
    results = {}

    if args.submit:
        print(f"逐个提交到中心 {args.url} …")

        for case in samples["cases"]:
            status, body = post_batch(args.url, case["batch"])
            # 按 case_id 记录：duplicate 用例与 normal 共用 batch_id，
            # 只用 batch_id 会互相覆盖。
            results[case["case_id"]] = {
                "status": body.get("status"),
                "reason_code": body.get("reason_code"),
                "affected_sequences": body.get("affected_sequences") or [],
                "http_status": status,
                "batch_id": case["batch_id"],
                "source": "live_center",
            }
            print(
                f"  {case['case_id']:<26} → "
                f"{body.get('status')} / {body.get('reason_code')}"
            )

        target = args.results or (ROOT / "test_data" / "eval_results.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({
                "schema": "pinglu-eval-results/1",
                "collected_at": now_iso(),
                "url": args.url,
                "results": results,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"已保存结果 → {target}")

    elif args.results:
        payload = json.loads(args.results.read_text(encoding="utf-8"))
        results = payload.get("results", payload)

        # 兼容以 batch_id 为键的旧结果文件：能对上的 case_id 补一份 case_id 键。
        by_case = {case["case_id"]: case["batch_id"] for case in samples["cases"]}

        for case_id, batch_id in by_case.items():
            if case_id in results:
                continue
            if batch_id in results and batch_id not in by_case:
                results[case_id] = results[batch_id]

    elif args.from_db:
        results = load_results_from_db(args.db)
        print(f"从中心库读到 {len(results)} 条审计判定：{args.db}")

    else:
        raise SystemExit(
            "请指定结果来源：--submit（连中心提交）、"
            "--results（结果文件）或 --from-db（读中心库）"
        )

    details, summary = score(samples, results)
    print_report(details, summary, samples)

    if args.out_csv:
        print(f"明细 CSV 已写出：{write_csv(details, args.out_csv)}")

    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(
            json.dumps({
                "schema": "pinglu-evaluate/1",
                "evaluated_at": now_iso(),
                "samples": str(args.samples),
                "summary": summary,
                "details": details,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"汇总 JSON 已写出：{args.out_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
