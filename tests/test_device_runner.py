import json

from common_crypto.hash_chain import verify_chain
from device_simulator.batch import pending_batches
from device_simulator.runner import flush_pending, generate_round
from device_simulator.register import register_devices


def test_seven_devices_restart_and_offline_recovery(tmp_path):
    keys = tmp_path / "keys"
    registry_path = tmp_path / "registry.json"
    database = tmp_path / "simulator.db"
    registry = register_devices(7, keys, registry_path)

    # 两轮离线生成：网络不可达不影响落盘、链和下一批次。
    first = generate_round(registry, keys, database, batch_size=2, seed=7)
    second = generate_round(registry, keys, database, batch_size=2, seed=7)
    assert len(first) == len(second) == 7
    for a, b in zip(first, second):
        assert a["end_sequence"] + 1 == b["start_sequence"]
        assert b["records"][0]["previous_hash"] == a["records"][-1]["record_hash"]
        assert verify_chain(a["records"] + b["records"]) == []

    # 模拟进程重启后，再生成一轮，必须继续使用原有 SQLite 状态。
    third = generate_round(registry, keys, database, batch_size=2, seed=7)
    assert all(batch["batch_id"].endswith("000003") for batch in third)
    assert all(batch["start_sequence"] == 5 for batch in third)

    calls = []
    def unavailable(*_args):
        raise OSError("test broker unavailable")
    failed = flush_pending(database, registry, "test.invalid", 1883, publish=unavailable)
    assert len(failed) == 7  # 每台设备的首个待发批失败，其后的同设备批次不跳过。
    assert len(pending_batches(database)) == 21

    def available(_broker, _port, topic, payload, *_args):
        calls.append((topic, json.loads(payload.decode("utf-8"))["batch_id"]))
        return len(calls)
    recovered = flush_pending(database, registry, "test", 1883, publish=available)
    assert len(recovered) == 21
    assert not pending_batches(database)
    for device_id in registry:
        sent = [batch_id for topic, batch_id in calls if topic == f"pinglu/batches/{device_id}"]
        assert sent == [f"{device_id}-000001", f"{device_id}-000002", f"{device_id}-000003"]
