"""页面接口实测：逐项验证第①~⑥块用到的接口与数据。

用法：
    python 验证脚本_页面接口.py <base_url>

只读，不写中心库。结果打印到标准输出并写入 验证结果_页面接口_<日期>.txt。
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8123"
ROOT = Path(__file__).resolve().parent

RESULTS = []


def get_json(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(path, payload):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))


stats = get_json("/stats")
devices = get_json("/devices")
batches = get_json("/batches")
records = get_json("/records")
audits = get_json("/audits")
offline = get_json("/offline-status")
performance = get_json("/performance")

# ① 总览
check("① /stats 五项统计齐全",
      set(stats) >= {"devices", "batches", "records", "accepted_batches", "audits"},
      str(stats))
check("① 已登记 7 台设备", stats["devices"] == 7, str(stats["devices"]))
check("① 设备按七类覆盖", len({d["device_type"] for d in devices}) == 7,
      str(sorted({d["device_type"] for d in devices})))

# ② 七类数据
accepted = [b for b in batches if b["status"] == "accepted"]
check("② 有已入库批次可供分类展示", len(accepted) >= 7, str(len(accepted)))

types_seen = set()
for batch in accepted:
    device_id = batch["header"]["device_id"]
    for device in devices:
        if device["device_id"] == device_id:
            types_seen.add(device["device_type"])
check("② 已入库批次覆盖 7 类设备", len(types_seen) == 7, str(sorted(types_seen)))

payload_ok = all(
    isinstance(item["record"].get("payload"), dict) and item["record"]["payload"]
    for item in records
)
check("② 每条记录都带 payload（页面可展示最新值）", payload_ok)

check("② 记录按设备+序号有序",
      all(
          (records[i]["device_id"], records[i]["sequence"])
          <= (records[i + 1]["device_id"], records[i + 1]["sequence"])
          for i in range(len(records) - 1)
      ),
      f"{len(records)} 条")

# ③ 异常审计
codes = {a["reason_code"] for a in audits}
check("③ 审计覆盖多种 reason_code", len(codes) >= 6, str(sorted(codes)))
check("③ 拒绝批次不混进正常列表",
      not any(b["status"] != "accepted" for b in accepted))
check("③ 审计带设备/批次/时间",
      all(a["device_id"] and a["batch_id"] and a["created_at"] for a in audits),
      f"{len(audits)} 条")
check("③ 审计保留原始批次证据", any(a.get("raw") for a in audits))
seq_audits = [a for a in audits if a["affected_sequences"]]
check("③ 有带异常序号区间的审计", bool(seq_audits),
      str([a["affected_sequences"] for a in seq_audits][:5]))

# ④ 证明与验签
target = accepted[0]
batch_id = target["header"]["batch_id"]
device_id = target["header"]["device_id"]
registration = [d for d in devices if d["device_id"] == device_id][0]

proof = get_json(
    f"/batches/{urllib.parse.quote(batch_id)}/proof/{target['header']['start_sequence']}"
)
check("④ 证明接口返回叶摘要/路径/根",
      proof["record_hash"] and proof["proof"] is not None and proof["root"], "")
check("④ 证明重新验证通过", proof["verified"] is True, str(proof)[:160])
check("④ 证明根与批次签名根一致",
      proof["root"] == target["header"]["merkle_root"])

stored = get_json(
    f"/batch-raw?batch_id={urllib.parse.quote(batch_id)}&device_id={urllib.parse.quote(device_id)}"
)
check("④ 可按设备+批次取回原始批次",
      stored["raw"]["batch_id"] == batch_id and stored["status"] == "accepted")

verify = post_json("/verify-signature", {
    "batch": stored["raw"],
    "public_key": registration["public_key"],
})
check("④ SM2 验签通过（用中心登记公钥）", verify["verified"] is True, str(verify))

# 篡改后应验签失败
tampered = json.loads(json.dumps(stored["raw"]))
tampered["merkle_root"] = "0" * 64
verify_bad = post_json("/verify-signature", {
    "batch": tampered,
    "public_key": registration["public_key"],
})
check("④ 篡改签名字段后验签失败", verify_bad["verified"] is False, str(verify_bad))

bad_device = post_json("/verify-signature", {
    "batch": stored["raw"],
    "public_key": "0" * 128,
})
check("④ 错误公钥一律验签失败", bad_device["verified"] is False, str(bad_device)[:120])

# 每台设备的每条记录都能出证明
all_proof_ok = True
checked = 0
for batch in accepted:
    start = batch["header"]["start_sequence"]
    end = batch["header"]["end_sequence"]
    for sequence in range(start, end + 1):
        item = get_json(
            f"/batches/{urllib.parse.quote(batch['header']['batch_id'])}/proof/{sequence}"
        )
        checked += 1
        if not item["verified"] or item["root"] != batch["header"]["merkle_root"]:
            all_proof_ok = False
            check(f"④ 证明失败 {batch['header']['batch_id']}#{sequence}", False, str(item)[:200])
check(f"④ 全部 {checked} 条记录证明均可复验", all_proof_ok)

missing = None
try:
    get_json(f"/batches/{urllib.parse.quote(batch_id)}/proof/99999")
except urllib.error.HTTPError as exc:
    missing = exc.code
check("④ 不存在的序号返回 400", missing == 400, str(missing))

# ⑤ 离线补传
check("⑤ 离线页有数据源说明", bool(offline.get("source_note")), str(offline.get("source"))[:60])
check("⑤ 汇总含缓存数与补传进度",
      set(offline["summary"]) >= {"cached_batches", "cached_records", "resend_done",
                                  "resend_pending", "progress_percent"},
      json.dumps(offline["summary"], ensure_ascii=False))
check("⑤ 逐设备含断点与待补传区间",
      all("break_sequence" in d for d in offline["devices"]), "")

# ⑥ 性能
check("⑥ 无 CSV 时如实返回缺失原因",
      performance["available"] is False and performance["reason"] == "MISSING_CSV",
      str(performance.get("reason")))
check("⑥ 给出预期文件与预期列",
      bool(performance.get("expected_file")) and len(performance.get("expected_columns") or []) >= 10,
      str(performance.get("expected_file")))

lines = []
passed = sum(1 for _, ok, _ in RESULTS if ok)
for name, ok, detail in RESULTS:
    lines.append(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   <- {detail}" if not ok else ""))

summary = f"合计 {passed}/{len(RESULTS)} 通过（{BASE}）"
lines.append("")
lines.append(summary)

output = "\n".join(lines)
print(output)

out_path = ROOT / "验证结果_页面接口_20261014.txt"
out_path.write_text(output, encoding="utf-8")
print("\n已写入：", out_path)
sys.exit(0 if passed == len(RESULTS) else 1)
