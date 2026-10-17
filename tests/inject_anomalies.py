"""异常注入样本生成器：6 类篡改 × N 份 + N 份正常对照。

用途
----
供 tests/test_tamper.py 统计「检测率 / 定位准确率 / 误报率」。

为什么默认基准设备是 NM-001
---------------------------
本地 keys/ 有 7 把私钥，但只有 6 把与 test_data/registry_public.json 配套：
WL-001 的登记公钥已换成 26aeb5c9…（A 机器上的新钥匙），本地 keys/WL-001.json
还是旧私钥。所以默认取 NM-001，可用 --device 换成 DR-001/LK-001/SB-001/SL-001/WE-001。

每类只改一处，且刻意绕开更早的检查
----------------------------------
verify_batch 判定顺序（reason_code 取第一个失败项）：
    结构 → 设备登记 → 签名 → 数量/范围 → 起止时间
         → 逐条记录（身份 → 序号 → 摘要 → 链 → 时间） → Merkle 根

1. 改批次头（count / merkle_root）后必须【重新签名】，否则先撞 INVALID_SIGNATURE；
2. 想命中 INVALID_HASH_CHAIN，必须同时重算那一条的 record_hash，否则先撞 INVALID_RECORD_HASH；
3. 想命中 INVALID_SIGNATURE，就【绝对不能】重新签名。

输出结构
--------
    <out>/normal/<n>.json          正常对照样本
    <out>/<类别>/<n>.json          异常样本
    <out>/label.json               每份样本的期望结果，供测试逐条对照
"""
import argparse
import copy
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 支持两种跑法：python -m tests.inject_anomalies / python tests/inject_anomalies.py
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common_crypto.canonical import canonical_bytes, signed_header  # noqa: E402
from common_crypto.hash_chain import record_hash  # noqa: E402
from common_crypto.sm import sign_message, verify_message  # noqa: E402
from device_simulator.batch import create_next_batch  # noqa: E402

#: 目录名 -> 该类的期望 reason_code
CATEGORIES = {
    "record_payload": "INVALID_RECORD_HASH",
    "batch_count": "INVALID_BATCH_RANGE",
    "merkle_root": "INVALID_MERKLE_ROOT",
    "hash_chain": "INVALID_HASH_CHAIN",
    "signature": "INVALID_SIGNATURE",
    "structure": "INVALID_BATCH_STRUCTURE",
}

#: 期望定位粒度：
#:   exact    —— affected_sequences 必须【精确等于】[被改序号]
#:   contains —— 必须【包含】被改序号，允许后继被牵连
#:               （断链后，下一条的 previous_hash 指向被改记录的旧摘要，
#:                 所以下一条也会报同一类错误 —— 这是摘要链的正确传播行为）
#:   empty    —— 头部级异常，不应报出任何具体序号
GRANULARITY = {
    "record_payload": "exact",
    "batch_count": "empty",
    "merkle_root": "empty",
    "hash_chain": "contains",
    "signature": "empty",
    "structure": "empty",
}


def _resign(batch, private_key, public_key):
    """改完批次头之后重新签名。

    注意：这是测试夹具在"自己人手里有私钥"的前提下模拟头部被改但记录未同步的情况，
    真实攻击者没有私钥，这一步做不出来 —— 报告里要写清楚这个前提。
    """
    batch["signature"] = sign_message(
        private_key, public_key, canonical_bytes(signed_header(batch))
    )
    return batch


def inject_record_payload(batch, key, index=4):
    """① 改一条记录的 payload，不动 record_hash。

    records 不在签名范围内，所以无需重签名；record_hash 由记录内容算出，
    改了 payload 就对不上 → INVALID_RECORD_HASH。
    """
    b = copy.deepcopy(batch)
    record = b["records"][index]
    field = sorted(record["payload"])[0]
    new_value = "9.9" if record["payload"][field] != "9.9" else "1.1"
    record["payload"][field] = new_value
    return b, record["sequence"]


def inject_batch_count(batch, key, index=0):
    """② 把批次头的 count 改大 1，并重新签名。→ INVALID_BATCH_RANGE"""
    b = copy.deepcopy(batch)
    b["count"] = b["count"] + 1
    return _resign(b, key["private_key"], key["public_key"]), None


def inject_merkle_root(batch, key, index=0):
    """③ 改 Merkle 根，并重新签名。记录完好，所以走到最后才失败。→ INVALID_MERKLE_ROOT"""
    b = copy.deepcopy(batch)
    b["merkle_root"] = "f" * 64 if b["merkle_root"] != "f" * 64 else "0" * 63 + "1"
    return _resign(b, key["private_key"], key["public_key"]), None


def inject_hash_chain(batch, key, index=3):
    """④ 改一条记录的 previous_hash，并重算它自己的 record_hash。

    重算 record_hash 是为了让它先通过"摘要自洽"检查，
    这样才会停在"摘要链断开" → INVALID_HASH_CHAIN。
    """
    b = copy.deepcopy(batch)
    record = b["records"][index]
    record["previous_hash"] = (
        "a" * 64 if record["previous_hash"] != "a" * 64 else "b" * 64
    )
    record["record_hash"] = record_hash(record)
    return b, record["sequence"]


def inject_signature(batch, key, index=0):
    """⑤ 改一个被签名的批次头字段（start_time 后移 1 秒），但【不】重新签名。"""
    b = copy.deepcopy(batch)
    stamp = datetime.fromisoformat(b["start_time"]) + timedelta(seconds=1)
    b["start_time"] = stamp.isoformat()
    return b, None


def inject_structure(batch, key, index=0):
    """⑥ 删掉一个必需字段 → 结构检查直接失败。"""
    b = copy.deepcopy(batch)
    b.pop("version", None)
    return b, None


INJECTORS = {
    "record_payload": inject_record_payload,
    "batch_count": inject_batch_count,
    "merkle_root": inject_merkle_root,
    "hash_chain": inject_hash_chain,
    "signature": inject_signature,
    "structure": inject_structure,
}


def build_base(registration, private_key, batch_size, seed, scratch_db):
    """生成一份干净的 seq 1~10 批次。

    状态库放在工作区内（--out 的上一级），不用系统临时目录 —— 某些受限环境下
    系统临时目录不可写，SQLite 会报 "unable to open database file"。
    每次调用前删掉，保证批次号从 000001、previous_hash 从全 0 开始。
    """
    scratch_db = Path(scratch_db)
    for suffix in ("", "-wal", "-shm"):
        leftover = Path(str(scratch_db) + suffix)
        if leftover.exists():
            leftover.unlink()
    return create_next_batch(
        registration,
        private_key,
        batch_size=batch_size,
        db_path=scratch_db,
        seed=seed,
    )


def main():
    parser = argparse.ArgumentParser(description="生成 6 类异常注入样本")
    parser.add_argument("--device", default="NM-001", help="基准设备（本地私钥须与登记表配套）")
    parser.add_argument("--keys-dir", default=str(ROOT / "keys"), help="设备私钥目录")
    parser.add_argument("--registry", default=str(ROOT / "test_data" / "registry_public.json"))
    parser.add_argument("--out", default=str(ROOT / "tests" / "anomaly_cases"))
    parser.add_argument("--per-class", type=int, default=100, help="每类样本数")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=2026, help="基准随机种子")
    args = parser.parse_args()

    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    if args.device not in registry:
        raise SystemExit(f"登记表里没有设备 {args.device}；可选：{', '.join(sorted(registry))}")
    registration = registry[args.device]

    key_path = Path(args.keys_dir) / f"{args.device}.json"
    if not key_path.exists():
        raise SystemExit(f"找不到私钥文件 {key_path}")
    key = json.loads(key_path.read_text(encoding="utf-8"))

    # 先自查：这把私钥必须和登记表里的公钥配套，否则生成的样本全都验不过，指标会失真
    probe = b"anomaly-generator self check"
    if not verify_message(
        registration["public_key"], probe,
        sign_message(key["private_key"], key["public_key"], probe),
    ) or key["public_key"] != registration["public_key"]:
        raise SystemExit(
            f"{args.device} 的本地私钥与登记表公钥不配套，请换一台设备或更新登记表。"
        )

    out = Path(args.out)
    if out.exists():
        for child in sorted(out.rglob("*"), reverse=True):
            child.unlink() if child.is_file() else child.rmdir()
    out.mkdir(parents=True, exist_ok=True)
    for name in CATEGORIES:
        (out / name).mkdir(parents=True, exist_ok=True)
    (out / "normal").mkdir(parents=True, exist_ok=True)

    # 生成用的状态库放在工作区内，跑完删掉
    scratch = out.parent / "_anomaly_scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    scratch_db = scratch / "base.db"

    labels = []
    seed = args.seed

    # 正常对照：种子从 seed+100000 起，和异常样本不重叠
    for n in range(args.per_class):
        batch = build_base(registration, key["private_key"], args.batch_size,
                           seed + 100000 + n, scratch_db)
        rel = f"normal/{n:04d}.json"
        (out / rel).write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
        labels.append({
            "file": rel, "category": "normal", "expect_reason": "OK",
            "expect_sequence": None, "granularity": "none",
        })

    for name, injector in INJECTORS.items():
        for n in range(args.per_class):
            base = build_base(registration, key["private_key"], args.batch_size,
                              seed + n, scratch_db)
            tampered, sequence = injector(base, key)
            rel = f"{name}/{n:04d}.json"
            (out / rel).write_text(
                json.dumps(tampered, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            labels.append({
                "file": rel, "category": name,
                "expect_reason": CATEGORIES[name],
                "expect_sequence": sequence,
                "granularity": GRANULARITY[name],
            })

    (out / "label.json").write_text(
        json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"基准设备   : {args.device}")
    print(f"输出目录   : {out}")
    print(f"正常样本   : {args.per_class} 份")
    for name in CATEGORIES:
        print(f"{name:<16}: {args.per_class} 份  -> 期望 {CATEGORIES[name]}")
    print(f"合计       : {len(labels)} 份，label.json 已写出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())