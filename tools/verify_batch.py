"""逐项核验一个批次，输出可交给队友/写进报告的分项结果。

对应 contract.md §9 的中心验证优先顺序，但只做**不依赖历史状态**的部分
（链尾衔接要由中心/网关用可信状态判断，这里只验批内）。

用法（仓库根目录）：
    .venv\\Scripts\\python.exe tools\\verify_batch.py
    .venv\\Scripts\\python.exe tools\\verify_batch.py --batch test_data/normal_batch.json
    .venv\\Scripts\\python.exe tools\\verify_batch.py --batch xxx.json --device WL-001

退出码：全部通过 0，任一失败 1。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common_crypto.batch import BATCH_FIELDS, RECORD_FIELDS  # noqa: E402
from common_crypto.canonical import canonical_bytes, signed_header  # noqa: E402
from common_crypto.hash_chain import ZERO_HASH, record_hash  # noqa: E402
from common_crypto.merkle import merkle_root  # noqa: E402
from common_crypto.sm import sm3_hex, verify_message  # noqa: E402

DEFAULT_BATCH = ROOT / "test_data" / "normal_batch.json"
DEFAULT_REGISTRY = ROOT / "test_data" / "registry_public.json"


class Report:
    def __init__(self):
        self.rows = []
        self.ok = True

    def check(self, name, passed, detail=""):
        self.rows.append((name, bool(passed), detail))
        if not passed:
            self.ok = False
        return passed

    def dump(self):
        width = max(len(r[0]) for r in self.rows)
        for name, passed, detail in self.rows:
            mark = "PASS" if passed else "FAIL"
            print(f"  [{mark}] {name:<{width}}  {detail}")
        print()
        print("  结论：" + ("全部通过" if self.ok else "存在未通过项"))


def main():
    parser = argparse.ArgumentParser(description="逐项核验批次")
    parser.add_argument("--batch", default=str(DEFAULT_BATCH))
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--device", help="默认取批次里的 device_id")
    parser.add_argument("--previous-hash", default=ZERO_HASH,
                        help="该设备上一批的最后一条摘要；首批为 64 个 0")
    parser.add_argument("--start-sequence", type=int,
                        help="该设备本批期望的起始序号；默认取批次自身")
    args = parser.parse_args()

    batch_path = Path(args.batch)
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    device_id = args.device or batch.get("device_id")

    report = Report()
    print(f"批次文件 : {batch_path}")
    print(f"设备     : {device_id}")
    print(f"批次号   : {batch.get('batch_id')}")
    print(f"公钥来源 : {args.registry}")
    print("-" * 100)

    # 1) 结构
    report.check("1 批次字段集合",
                 isinstance(batch, dict) and set(batch) == BATCH_FIELDS,
                 f"{len(batch) if isinstance(batch, dict) else '?'} 个字段，"
                 f"应为 {len(BATCH_FIELDS)} 个")

    # 2) 设备登记与公钥
    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    entry = registry.get(device_id) if isinstance(registry, dict) else None
    if not report.check("2 设备已在登记表", bool(entry)):
        report.dump()
        return 1

    public_key = entry["public_key"]
    fingerprint = sm3_hex(bytes.fromhex(public_key))
    report.check("2 设备类型一致", entry.get("device_type") == batch["records"][0]["device_type"],
                 f"登记={entry.get('device_type')} 记录={batch['records'][0]['device_type']}")
    print(f"  公钥     : {public_key[:32]}...")
    print(f"  公钥 SM3 : {fingerprint}")

    # 3) SM2 验签（签名只覆盖批次头九个字段）
    signature_ok = verify_message(public_key, canonical_bytes(signed_header(batch)),
                                  batch["signature"])
    report.check("3 SM2 验签（批次头九字段）", signature_ok,
                 f"signature={batch['signature'][:24]}...")

    records = batch["records"]

    # 4) 数量与序号范围
    report.check("4 记录数量与 count 一致", batch["count"] == len(records),
                 f"count={batch['count']} 实际={len(records)}")
    report.check("4 序号范围与首尾记录一致",
                 batch["start_sequence"] == records[0]["sequence"]
                 and batch["end_sequence"] == records[-1]["sequence"],
                 f"{batch['start_sequence']}~{batch['end_sequence']}")

    # 5) 逐条 SM3 摘要
    bad_hash = [r["sequence"] for r in records
                if set(r) != RECORD_FIELDS or r["record_hash"] != record_hash(r)]
    report.check("5 逐条 SM3 记录摘要", not bad_hash,
                 f"{len(records)} 条，异常序号 {bad_hash}" if bad_hash else f"{len(records)} 条全部匹配")

    # 6) SM3 哈希链
    chain_bad = []
    previous = args.previous_hash
    expected = args.start_sequence if args.start_sequence is not None else records[0]["sequence"]
    for index, record in enumerate(records):
        if record["previous_hash"] != previous:
            chain_bad.append(record["sequence"])
        if record["sequence"] != expected + index:
            chain_bad.append(record["sequence"])
        previous = record["record_hash"]
    report.check("6 SM3 哈希链连接与序号连续", not chain_bad,
                 f"首条 previous_hash={args.previous_hash[:16]}...，"
                 f"{len(records)} 条连续" if not chain_bad else f"异常序号 {sorted(set(chain_bad))}")

    # 7) Merkle 根
    computed = merkle_root([r["record_hash"] for r in records])
    report.check("7 Merkle 根", computed == batch["merkle_root"],
                 f"重算 {computed[:24]}... vs 批次 {batch['merkle_root'][:24]}...")

    print("-" * 100)
    report.dump()
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
