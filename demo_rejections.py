"""异常与篡改验收：改值、删记录、伪造签名、未知设备、重复提交、历史重放逐条打进中心。

用法：
    python demo_rejections.py                 # 需要中心已在 127.0.0.1:8000 运行
    python demo_rejections.py --url http://127.0.0.1:8000

设计要点：
- 中心是「链尾驱动」的：批次序号必须接上已入库的下一序号，时间必须晚于上一条记录。
  所以每条用例都从中心实测到的链尾出发构造，否则校验会先卡在序号/时间上，
  测不到真正想测的签名、摘要、根和历史重放。
- 每条篡改用例用**新的批次号**，避免中心的重复判定（BATCH_CONTENT_CONFLICT）
  先于密码学校验触发。用例 01 与 07 专门考察重复与冲突判定。
- 结论写入 test_data/rejection_result.json，并打印新增审计证据。
"""

import argparse
import copy
import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from common_crypto.canonical import canonical_bytes, signed_header
from common_crypto.hash_chain import record_hash
from common_crypto.merkle import merkle_root
from common_crypto.sm import sign_message
from device_simulator.batch import create_next_batch
from device_simulator.generator import make_records


ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = ROOT / "test_data" / "registry_public.json"
KEYS_DIR = ROOT / "keys"
REJECT_SIM_DB = ROOT / "simulator_reject.db"   # 与正式演示状态分开，避免污染

DEVICE_ID = "SL-001"


def load_device(device_id):
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    registration = registry[device_id]
    saved = json.loads((KEYS_DIR / f"{device_id}.json").read_text(encoding="utf-8"))

    if saved["public_key"] != registration["public_key"]:
        raise ValueError(f"{device_id} 密钥与登记表不一致")

    return registration, saved["private_key"]


def post_json(url, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def get_json(url, path):
    with urllib.request.urlopen(url + path, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def reset_device(device_id, sim_db):
    """只清掉指定设备在中心的批次/记录与模拟器状态，保证本脚本可重复运行。"""
    from center_platform import storage

    connection = storage.get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM records WHERE device_id = ?", (device_id,))
        connection.execute("DELETE FROM batches WHERE device_id = ?", (device_id,))
        connection.commit()
    finally:
        connection.close()

    connection = sqlite3.connect(str(sim_db))
    try:
        connection.execute("DELETE FROM generated_batches WHERE device_id = ?", (device_id,))
        connection.execute("DELETE FROM simulator_state WHERE device_id = ?", (device_id,))
        connection.commit()
    finally:
        connection.close()

    print(f"已清空 {device_id} 在中心与模拟器里的历史批次，开始异常验收")
    print()


def chain_tail(url, device_id):
    """读中心已入库的该设备链尾：最后一条记录的摘要与序号（没有则返回 0 号、零摘要）。"""
    records = get_json(url, f"/records?device_id={device_id}")

    if not records:
        return {"sequence": 0, "hash": "0" * 64, "timestamp": None}

    last = max(records, key=lambda item: item["sequence"])

    return {
        "sequence": last["sequence"],
        "hash": last["record_hash"],
        "timestamp": last["record"]["timestamp"],
    }


def next_batch_from_tail(registration, tail, count=5, batch_number=None):
    """按中心链尾造一批结构自洽的记录（尚未签名）。"""
    if batch_number is None:
        batch_number = 1

    batch_id = f"{registration['device_id']}-{batch_number:06d}"

    if tail["timestamp"] is None:
        start_time = datetime.now().astimezone().replace(microsecond=0)
    else:
        start_time = datetime.fromisoformat(tail["timestamp"]) + timedelta(seconds=1)

    records = make_records(
        registration=registration,
        batch_id=batch_id,
        start_sequence=tail["sequence"] + 1,
        previous_hash=tail["hash"],
        start_time=start_time,
        count=count,
    )

    return {
        "version": 1,
        "device_id": registration["device_id"],
        "batch_id": batch_id,
        "start_sequence": records[0]["sequence"],
        "end_sequence": records[-1]["sequence"],
        "count": len(records),
        "start_time": records[0]["timestamp"],
        "end_time": records[-1]["timestamp"],
        "merkle_root": merkle_root([r["record_hash"] for r in records]),
        "records": records,
    }


def sign(batch, private_key, public_key):
    batch["signature"] = sign_message(
        private_key, public_key, canonical_bytes(signed_header(batch))
    )
    return batch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    registration, private_key = load_device(DEVICE_ID)
    public_key = registration["public_key"]

    def submit(batch):
        return post_json(f"{args.url}/batches", {"batch": batch})[1]

    def fresh_batch(count=5, batch_number=1):
        tail = chain_tail(args.url, DEVICE_ID)
        return next_batch_from_tail(registration, tail, count=count, batch_number=batch_number)

    cases = []
    reset_device(DEVICE_ID, REJECT_SIM_DB)
    stats_before = get_json(args.url, "/stats")
    audits_before = len(get_json(args.url, "/audits"))

    # 基准批次：用模拟器正常生成并入库，作为重复提交与历史重放的靶子
    baseline = create_next_batch(registration, private_key, batch_size=5, db_path=REJECT_SIM_DB)
    baseline_result = submit(baseline)
    assert baseline_result["status"] == "accepted", baseline_result

    def record_case(number, name, given, expected, actual):
        cases.append({
            "编号": number, "名称": name, "输入": given,
            "期望": expected, "实际": actual,
        })

    # 用例 01：原样重复提交
    record_case("异常-01", "原样重复提交", "与已入库批次逐字节相同",
                "duplicate / DUPLICATE_BATCH", submit(copy.deepcopy(baseline)))

    # 用例 02：改测量值、不重算摘要（新批次号）
    tampered = fresh_batch(batch_number=2)
    target = tampered["records"][1]
    field = next(iter(target["payload"]))
    target["payload"][field] = "999.99"
    sign(tampered, private_key, public_key)
    record_case("异常-02", "篡改测量值",
                f"第 {target['sequence']} 条 {field} 改为 999.99，摘要未重算",
                "rejected / INVALID_RECORD_HASH",
                submit(tampered))

    # 用例 03：删掉中间一条记录，其余自洽（新批次号）
    deleted = fresh_batch(batch_number=3)
    removed = deleted["records"].pop(1)
    sign(deleted, private_key, public_key)
    record_case("异常-03", f"删除第 {removed['sequence']} 条记录",
                "删掉中间一条，起止序号与数量随之对不上",
                "rejected / INVALID_BATCH_RANGE",
                submit(deleted))

    # 用例 04：伪造签名（新批次号）
    forged = fresh_batch(batch_number=4)
    sign(forged, private_key, public_key)
    forged["signature"] = "0" * 128
    record_case("异常-04", "伪造签名", "内容自洽、签名整段替换为全 0",
                "rejected / INVALID_SIGNATURE", submit(forged))

    # 用例 05：记录全部自洽，只把签名的 Merkle 根换成别的值（新批次号）
    # 注意：改某条记录并重算摘要会同时让下一条 previous_hash 断链，
    # 那样先命中的是 INVALID_HASH_CHAIN（校验次序：签名→数量序号时间→每条摘要→链→根）。
    wrong_root = fresh_batch(batch_number=5)
    correct_root = wrong_root["merkle_root"]
    wrong_root["merkle_root"] = "0" * 64
    sign(wrong_root, private_key, public_key)
    record_case("异常-05", "签名的 Merkle 根与记录不符",
                "记录与链都自洽，只把 merkle_root 改成全 0 后用真实私钥签名",
                "rejected / INVALID_MERKLE_ROOT",
                submit(wrong_root))

    # 用例 05b：改值后重算摘要但不动链（第 2 条），应当命中链校验
    broken = fresh_batch(batch_number=6)
    target = broken["records"][1]
    target["payload"][next(iter(target["payload"]))] = "888.88"
    target["record_hash"] = record_hash(target)
    sign(broken, private_key, public_key)
    record_case("异常-05b", "改值后重算摘要但不修链",
                f"第 {target['sequence']} 条改值并重算 record_hash，"
                f"第 {target['sequence'] + 1} 条的 previous_hash 因此对不上",
                "rejected / INVALID_HASH_CHAIN",
                submit(broken))

    # 用例 05c：改末条记录摘要，链不受影响，只剩根对不上
    tail_tamper = fresh_batch(batch_number=7)
    last = tail_tamper["records"][-1]
    last["payload"][next(iter(last["payload"]))] = "666.66"
    last["record_hash"] = record_hash(last)
    sign(tail_tamper, private_key, public_key)
    record_case("异常-05c", "只改末条记录摘要（链不受影响）",
                "末条改值并重算 record_hash，前面链仍然连续，只剩下 Merkle 根对不上",
                "rejected / INVALID_MERKLE_ROOT",
                submit(tail_tamper))

    # 用例 06：未登记设备（结构自洽、签名有效，只是设备没登记）
    unknown = fresh_batch(batch_number=8)
    unknown["device_id"] = "XX-999"
    unknown["batch_id"] = "XX-999-000001"
    previous = "0" * 64
    for record in unknown["records"]:
        record["device_id"] = "XX-999"
        record["batch_id"] = "XX-999-000001"
        record["previous_hash"] = previous
        record["record_hash"] = record_hash(record)
        previous = record["record_hash"]
    unknown["merkle_root"] = merkle_root([r["record_hash"] for r in unknown["records"]])
    sign(unknown, private_key, public_key)
    record_case("异常-06", "未登记设备提交",
                "device_id 改为未登记的 XX-999，结构自洽、签名有效",
                "rejected / UNKNOWN_DEVICE", submit(unknown))

    # 用例 07：历史重放（沿用已入库批次号，内容不同）
    stale = copy.deepcopy(baseline)
    record = stale["records"][0]
    record["payload"][next(iter(record["payload"]))] = "777.77"
    record["record_hash"] = record_hash(record)
    stale["merkle_root"] = merkle_root([r["record_hash"] for r in stale["records"]])
    sign(stale, private_key, public_key)
    record_case("异常-07", "历史重放：同批次号不同内容",
                "沿用已入库的批次号，改一条测量值并重签",
                "rejected / BATCH_CONTENT_CONFLICT", submit(stale))

    # 对照 08：接着链尾生成的正常批次必须通过
    healthy = fresh_batch(batch_number=10)
    sign(healthy, private_key, public_key)
    record_case("对照-08", "接着链尾的第二批（应当通过）",
                f"序号 {healthy['start_sequence']}~{healthy['end_sequence']}，"
                f"previous_hash 接中心链尾",
                "accepted / OK", submit(healthy))

    stats_after = get_json(args.url, "/stats")
    audits = get_json(args.url, "/audits")

    print(f"中心：{args.url}")
    print(f"提交前统计：{json.dumps(stats_before, ensure_ascii=False)}")
    print()
    ok = True

    for case in cases:
        actual = case["实际"]
        got = f"{actual.get('status')} / {actual.get('reason_code')}"
        expected = case["期望"].split("（")[0].strip()
        hit = any(part.strip() in got for part in expected.split("或"))
        ok = ok and hit
        sequences = actual.get("affected_sequences") or []
        print(f"{case['编号']} {case['名称']}")
        print(f"        输入：{case['输入']}")
        print(f"        期望：{case['期望']}")
        print(f"        实际：{got}"
              + (f"，affected_sequences={sequences}" if sequences else "")
              + ("  [符合]" if hit else "  [不符合]"))
        print()

    print(f"提交后统计：{json.dumps(stats_after, ensure_ascii=False)}")
    new_audits = audits[:len(audits) - audits_before]
    print(f"新增审计事件：{len(new_audits)} 条")
    for audit in reversed(new_audits):
        print(f"  - {audit.get('reason_code'):<24} batch={audit.get('batch_id')} "
              f"序号={audit.get('affected_sequences')}")

    output = ROOT / "test_data" / "rejection_result.json"
    output.write_text(
        json.dumps({
            "stats_before": stats_before, "stats_after": stats_after,
            "audits_added": len(new_audits), "cases": cases,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print()
    print("结果已写入：", output)
    print("全部用例符合预期" if ok else "有用例不符合预期，见上")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
