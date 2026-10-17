"""按公开登记表重建一个中心库，供实测使用（不会动正式库）。

用法：
    python 验证脚本_建中心库.py <目标库路径> [登记表路径]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

TARGET = Path(sys.argv[1]).resolve()
REGISTRY = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "test_data" / "registry_public.json"

TARGET.parent.mkdir(parents=True, exist_ok=True)

# 先尽力删掉旧库（含 WAL/SHM 附属文件）；被文件权限挡住时退化为"清空到干净起点"。
wiped = True

for suffix in ("", "-wal", "-shm"):
    stale = Path(str(TARGET) + suffix)
    if not stale.exists():
        continue
    try:
        stale.unlink()
    except OSError:
        wiped = False

from center_platform import storage  # noqa: E402

storage.DB_PATH = TARGET
storage.init_db()

if not wiped:
    # 中心库还在：只把业务数据清空，避免同名批次撞 BATCH_CONTENT_CONFLICT。
    import sqlite3

    connection = sqlite3.connect(str(TARGET))

    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM records")
        connection.execute("DELETE FROM batches")
        connection.execute("DELETE FROM audits")
        connection.commit()
        print("旧库无法删除，已就地清空 records / batches / audits")
    finally:
        connection.close()

registry = json.loads(REGISTRY.read_text(encoding="utf-8"))

for device_id, item in registry.items():
    storage.register_device(
        device_id,
        item["device_type"],
        item["public_key"],
        longitude=item.get("longitude"),
        latitude=item.get("latitude"),
    )

stats = storage.get_stats()
print(f"已按登记表重建中心库：{TARGET}")
print(f"登记设备 {stats['devices']} 台，批次 {stats['batches']}，记录 {stats['records']}，审计 {stats['audits']}")
