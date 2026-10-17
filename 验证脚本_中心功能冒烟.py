"""工作区内的功能冒烟验证：不依赖系统临时目录。

用法：
    python 验证脚本_中心功能冒烟.py

只在自己的临时库上运行，绝不碰 center_platform/data/center.db。
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

SMOKE_DB = ROOT / "_tmp" / "smoke_center.db"
SMOKE_DB.parent.mkdir(parents=True, exist_ok=True)

# 关键：必须在 import center_platform.storage **之前**设好环境变量。
# storage 在模块级就会按 DB_PATH 建库；如果等 import 之后再改 storage.DB_PATH，
# 模块级那次 init_db() 已经作用在默认库（正式库）上了，会污染正式数据。
os.environ["PINGLU_CENTER_DB"] = str(SMOKE_DB)

from center_platform import storage  # noqa: E402

# 双保险：真跑之前再确认路径指向临时库，否则直接拒绝运行。
if Path(storage.DB_PATH).resolve() != SMOKE_DB.resolve():
    raise SystemExit(
        f"拒绝对非临时库运行：storage.DB_PATH={storage.DB_PATH}，"
        f"期望 {SMOKE_DB}"
    )

for suffix in ("", "-wal", "-shm"):
    stale = Path(str(SMOKE_DB) + suffix)
    if stale.exists():
        stale.unlink()

storage.init_db()

from fastapi.testclient import TestClient  # noqa: E402

from center_platform.main import app  # noqa: E402
from common_crypto.batch import PAYLOAD_FIELDS  # noqa: E402
from common_crypto.canonical import canonical_bytes, signed_header  # noqa: E402
from common_crypto.hash_chain import ZERO_HASH, record_hash  # noqa: E402
from common_crypto.merkle import merkle_root  # noqa: E402
from common_crypto.sm import generate_keypair, sign_message  # noqa: E402

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))


def payload_for(device_type, index):
    return {f: f"{10 + index}.{index:02d}" for f in PAYLOAD_FIELDS[device_type]}


def make_records(device_id, device_type, start=1, count=3, previous=ZERO_HASH,
                 batch_id=None, start_time="2026-10-14T10:00:00+08:00"):
    if batch_id is None:
        batch_id = f"{device_id}-000001"
    base = datetime.fromisoformat(start_time)
    records = []
    previous_hash = previous
    for offset in range(count):
        sequence = start + offset
        record = {
            "version": 1, "device_id": device_id, "device_type": device_type,
            "timestamp": (base + timedelta(seconds=offset)).isoformat(),
            "longitude": "108.500000", "latitude": "22.000000",
            "sequence": sequence, "batch_id": batch_id,
            "payload": payload_for(device_type, sequence), "status": "normal",
            "previous_hash": previous_hash,
        }
        record["record_hash"] = record_hash(record)
        previous_hash = record["record_hash"]
        records.append(record)
    return records, previous_hash


def make_batch(device_id, device_type, private_key, public_key, records=None, **kw):
    if records is None:
        records, _ = make_records(device_id, device_type, **kw)
    batch = {
        "version": 1, "device_id": device_id, "batch_id": records[0]["batch_id"],
        "start_sequence": records[0]["sequence"], "end_sequence": records[-1]["sequence"],
        "count": len(records), "start_time": records[0]["timestamp"],
        "end_time": records[-1]["timestamp"],
        "merkle_root": merkle_root([r["record_hash"] for r in records]),
        "records": records,
    }
    batch["signature"] = sign_message(
        private_key, public_key, canonical_bytes(signed_header(batch))
    )
    return batch


def fresh_center(tag="case"):
    """每个用例一个独立库，避免 batch_id 唯一约束串味。"""
    db = ROOT / "_tmp" / f"smoke_{tag}.db"
    if db.exists():
        db.unlink()
    storage.DB_PATH = db
    storage.init_db()
    private_key, public_key = generate_keypair()
    storage.register_device("WL-001", "water_level", public_key,
                            longitude="108.500000", latitude="22.000000")
    return TestClient(app), private_key, public_key


# 1 健康检查
c, private_key, public_key = fresh_center("health")
health = c.get("/health").json()
check("GET /health", health.get("status") == "ok", str(health))

# 2 合法批次入库 + 证明 + 重复
c, private_key, public_key = fresh_center("valid")
batch = make_batch("WL-001", "water_level", private_key, public_key)
r = c.post("/batches", json={"batch": batch}).json()
check("合法批次 accepted", r.get("status") == "accepted" and r.get("reason_code") == "OK", str(r))
stats = c.get("/stats").json()
check("入库 3 条记录", stats.get("records") == 3, str(stats))

r2 = c.post("/batches", json={"batch": batch}).json()
check("重复批次 duplicate", r2.get("status") == "duplicate", str(r2))
check("重复不计入", c.get("/stats").json()["records"] == 3)

conflict = make_batch("WL-001", "water_level", private_key, public_key)
conflict["records"][0]["payload"]["water_level_m"] = "0.01"
rc = c.post("/batches", json={"batch": conflict}).json()
check("同号改内容 BATCH_CONTENT_CONFLICT",
      rc.get("status") == "rejected" and rc.get("reason_code") == "BATCH_CONTENT_CONFLICT", str(rc))

proof = c.get(f"/batches/{batch['batch_id']}/proof/2").json()
check("Merkle 证明可复验",
      proof.get("verified") is True and proof.get("root") == batch["merkle_root"], str(proof)[:200])

# 3 未知设备
c, private_key, public_key = fresh_center("unknown")
bad = make_batch("XX-999", "water_level", private_key, public_key)
rb = c.post("/batches", json={"batch": bad}).json()
check("未知设备 rejected/UNKNOWN_DEVICE",
      rb.get("status") == "rejected" and rb.get("reason_code") == "UNKNOWN_DEVICE", str(rb))

# 4 改值定位
c, private_key, public_key = fresh_center("tamper")
tampered = make_batch("WL-001", "water_level", private_key, public_key)
tampered["records"][1]["payload"]["water_level_m"] = "999.00"
rt = c.post("/batches", json={"batch": tampered}).json()
check("改值 rejected/INVALID_RECORD_HASH 且定位序号",
      rt.get("status") == "rejected" and rt.get("reason_code") == "INVALID_RECORD_HASH"
      and rt.get("affected_sequences") == [2], str(rt))

# 5 伪造签名
c, private_key, public_key = fresh_center("forged")
forged = make_batch("WL-001", "water_level", private_key, public_key)
forged["signature"] = "0" * 128
rf = c.post("/batches", json={"batch": forged}).json()
check("伪造签名 rejected/INVALID_SIGNATURE",
      rf.get("status") == "rejected" and rf.get("reason_code") == "INVALID_SIGNATURE", str(rf))
audits = c.get("/audits").json()
check("审计保留原始批次", any(a.get("raw") for a in audits), str(audits)[:200])

# 6 续链批次
c, private_key, public_key = fresh_center("chain")
first = make_batch("WL-001", "water_level", private_key, public_key)
c.post("/batches", json={"batch": first})
records, _ = make_records("WL-001", "water_level", start=4, count=2,
                          previous=first["records"][-1]["record_hash"],
                          batch_id="WL-001-000002",
                          start_time="2026-10-14T10:00:10+08:00")
second = make_batch("WL-001", "water_level", private_key, public_key, records=records)
r9 = c.post("/batches", json={"batch": second}).json()
check("第二批续链 accepted", r9.get("status") == "accepted", str(r9))
check("累计 5 条记录", c.get("/stats").json()["records"] == 5)

# 7 七类设备登记与提交
c, _, _ = fresh_center("seven")
types = {"water_level": "WL", "weather": "WE", "navigation_mark": "NM", "slope": "SL",
         "lock": "LK", "drone": "DR", "survey_boat": "SB"}
seven_ok = True
for device_type, prefix in types.items():
    device_id = f"{prefix}-001"
    pk, pub = generate_keypair()
    storage.register_device(device_id, device_type, pub,
                            longitude="108.500000", latitude="22.000000")
    recs, _ = make_records(device_id, device_type, start=1, count=2,
                           batch_id=f"{device_id}-000001",
                           start_time="2026-10-14T11:00:00+08:00")
    b = make_batch(device_id, device_type, pk, pub, records=recs)
    res = c.post("/batches", json={"batch": b}).json()
    if res.get("status") != "accepted":
        seven_ok = False
        check(f"七类设备 {device_id} accepted", False, str(res))
check("七类设备全部可登记提交", seven_ok and c.get("/stats").json()["records"] == 14,
      str(c.get("/stats").json()))

lines = []
passed = sum(1 for _, ok, _ in RESULTS if ok)
for name, ok, detail in RESULTS:
    lines.append(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  <- {detail}" if not ok else ""))
lines.append(f"\n合计 {passed}/{len(RESULTS)} 通过")

(ROOT / "_smoke_out.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
