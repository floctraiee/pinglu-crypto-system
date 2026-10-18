"""本地缓存篡改检测：证明"网关本地库不可信，判定权在中心"。

场景
----
1. 网关收到一批并落库（pending）
2. 直接改 edge_cache.db 里 raw_json 的第 5 条 payload（模拟磁盘/数据库被改）
3. 网关转发时发出去的就是【被篡改后的原文】
4. 桩中心（内部真调用 validate_batch）用登记公钥验 → INVALID_RECORD_HASH
5. 网关记成 rejected，且 center_response 的 affected_sequences 精确指向第 5 条

桩中心与断网补传测试共用（tests/test_offline.py::_StubCenter），
所以判定逻辑与真中心一致，且完全本地、可重复。
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from device_simulator.batch import create_next_batch  # noqa: E402
from edge_gateway import config, forward, storage  # noqa: E402

from tests.test_offline import (  # noqa: E402
    DEVICE,
    KEYS,
    REGISTRY,
    VALID_EDGE_RESULT,
    _StubCenter,
)

pytestmark = pytest.mark.skipif(
    KEYS is None,
    reason=f"缺少设备私钥 {DEVICE}.json（keys/ 不随仓库分发），跳过缓存篡改检测",
)

TAMPERED_SEQUENCE = 5


@pytest.fixture
def gateway_db(tmp_path, monkeypatch):
    """把网关的库指到临时文件，绝不碰真实的 edge_gateway/data/edge_cache.db。"""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "edge_cache.db")
    storage.init_db()
    return tmp_path


@pytest.fixture
def one_batch(tmp_path):
    """用 NM-001 生成一个合法批次（本地私钥与登记表配套的设备）。

    设备私钥不随仓库分发（.gitignore 忽略 keys/）。这里再守一道：
    即使 pytestmark 因为某些原因没生效，也会 skip 而不是 TypeError。
    """
    if KEYS is None:
        pytest.skip(f"缺少设备私钥 {DEVICE}.json（keys/ 不随仓库分发），跳过缓存篡改检测")
    return create_next_batch(
        REGISTRY[DEVICE], KEYS["private_key"], batch_size=10,
        db_path=tmp_path / "simulator.db", seed=2026,
    )


@pytest.fixture
def stub_center():
    center = _StubCenter().start()
    yield center
    center.stop()


def _tamper_payload_in_db(device_id, batch_id, sequence, new_value):
    """直接改库里 raw_json 里指定序号的 payload —— 绕过网关的所有内存状态。

    注意：这里故意【不】重算 record_hash、也【不】重新签名，
    因为真实的磁盘/数据库级篡改不会帮我们做这些。
    """
    conn = sqlite3.connect(str(config.DB_PATH))
    try:
        row = conn.execute(
            "SELECT raw_json FROM pending_batches WHERE device_id=? AND batch_id=?",
            (device_id, batch_id),
        ).fetchone()
        assert row, "库里找不到这一批"

        batch = json.loads(row[0])
        for record in batch["records"]:
            if record["sequence"] == sequence:
                field = sorted(record["payload"])[0]
                record["payload"][field] = new_value
                break
        else:
            raise AssertionError(f"批次里没有序号 {sequence}")

        conn.execute(
            "UPDATE pending_batches SET raw_json=? WHERE device_id=? AND batch_id=?",
            (json.dumps(batch, ensure_ascii=False), device_id, batch_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_tampered_cache_is_rejected_with_sequence(gateway_db, one_batch, stub_center):
    """篡改本地缓存 → 中心拒绝，并精确指出被改的序号。"""
    raw = json.dumps(one_batch, ensure_ascii=False)
    saved = storage.save_received(one_batch, raw, dict(VALID_EDGE_RESULT))
    assert saved["inserted"] is True
    assert saved["status"] == storage.STATUS_PENDING

    # 篡改库里的第 5 条
    _tamper_payload_in_db(DEVICE, one_batch["batch_id"], TAMPERED_SEQUENCE, "999.9")

    # 确认库里读回来的确实已被改（证明篡改生效，不是测了个寂寞）
    stored = json.loads(storage.get_raw_batch(DEVICE, one_batch["batch_id"]))
    tampered = next(
        r for r in stored["records"] if r["sequence"] == TAMPERED_SEQUENCE
    )
    assert "999.9" in tampered["payload"].values()

    # 转发
    summary = forward.run_once(center_url=stub_center.url, timeout=5)
    assert summary["rejected"] == 1, summary
    assert summary["accepted"] == 0
    assert summary["sent"] == 1
    assert stub_center.received == [], "被篡改的批次不应被中心接受"

    # 网关侧落成终态 rejected，并保留中心的判定依据
    row = storage.get_batch(DEVICE, one_batch["batch_id"])
    assert row["status"] == storage.STATUS_REJECTED
    assert row["center_reason_code"] == "INVALID_RECORD_HASH"

    response = json.loads(row["center_response"])
    assert response["affected_sequences"] == [TAMPERED_SEQUENCE], response


def test_untampered_cache_is_accepted(gateway_db, one_batch, stub_center):
    """对照组：同样流程但不改库 → 中心接受。排除"不管改不改都被拒"的假阳性。"""
    raw = json.dumps(one_batch, ensure_ascii=False)
    storage.save_received(one_batch, raw, dict(VALID_EDGE_RESULT))

    summary = forward.run_once(center_url=stub_center.url, timeout=5)
    assert summary["accepted"] == 1, summary
    assert summary["rejected"] == 0
    assert stub_center.received == [one_batch["batch_id"]]

    row = storage.get_batch(DEVICE, one_batch["batch_id"])
    assert row["status"] == storage.STATUS_ACCEPTED