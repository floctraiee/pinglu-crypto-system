"""断网补传与恢复演练（任务书表 2 的 R3）。

设计说明
--------
1. 桩中心（_StubCenter）在本进程内起一个 HTTP 服务，内部直接调用
   center_platform.validator.validate_batch —— 判定逻辑与真中心完全一致
   （center_platform/storage.process_batch 就是它的包装），但不需要 uvicorn、
   不需要跨机网络，因此测试可重复、能进 CI。
2. 用 NM-001 生成批次：本地 keys/ 里只有 DR/LK/NM/SB/SL/WE 六台设备的私钥与
   登记表配套（WL-001 的新私钥在 A 那边，本地没有）。
3. 网关的库通过 monkeypatch config.DB_PATH 指到 tmp_path，绝不触碰真实的
   edge_gateway/data/edge_cache.db。

覆盖三段场景
------------
① 中心不可达 → 批次保留 pending、attempts 递增、同设备只有队首批在重试
② 中心恢复   → 按序补传、全部 accepted、中心侧序号连续、网关链尾推进
③ 重复投递   → 同一批再收一次不会产生第二条待传任务（QoS 1 幂等）
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from center_platform.validator import validate_batch  # noqa: E402
from common_crypto.hash_chain import ZERO_HASH  # noqa: E402
from device_simulator.batch import create_next_batch  # noqa: E402
from edge_gateway import config, forward, storage  # noqa: E402

DEVICE = "NM-001"
BATCH_SIZE = 10
ROUNDS = 3

REGISTRY = json.loads(
    (ROOT / "test_data" / "registry_public.json").read_text(encoding="utf-8")
)

# 设备私钥不随仓库分发（.gitignore 忽略 keys/）。缺失时整个模块跳过，
# 使交付包在干净环境中 pytest 仍能全绿（skipped 而非 error）。
_KEYS_PATH = ROOT / "keys" / f"{DEVICE}.json"
KEYS = json.loads(_KEYS_PATH.read_text(encoding="utf-8")) if _KEYS_PATH.exists() else None

pytestmark = pytest.mark.skipif(
    KEYS is None,
    reason=f"缺少设备私钥 {DEVICE}.json（keys/ 不随仓库分发），跳过断网补传演练",
)

#: 指向一个必然拒绝连接的端口，用来模拟"中心不可达"
UNREACHABLE = "http://127.0.0.1:1"

#: 模拟"网关初验通过"的结果，直接喂给 save_received
#: （初验逻辑本身由 tests/test_tamper.py 覆盖，这里只测转发与重试机制）
VALID_EDGE_RESULT = {
    "valid": True,
    "reason_code": "OK",
    "errors": [],
    "record_errors": [],
    "affected_sequences": [],
}


class _StubCenter:
    """本地桩中心：内部直接调用 validate_batch，行为与真中心一致。"""

    def __init__(self):
        self.received = []      # 中心真正接受的批次号，按到达顺序
        self._chains = {}       # device_id -> (previous_hash, next_sequence)
        self._seen = {}         # (device_id, batch_id) -> 判定结果
        self._server = None
        self._thread = None
        self.url = None

    # ---- 判定逻辑（等价于 center_platform.storage.process_batch 的判定部分）----
    def handle(self, batch):
        device_id = batch["device_id"]
        batch_id = batch["batch_id"]
        key = (device_id, batch_id)

        # 幂等：同批次号且已受理过 → duplicate
        if key in self._seen:
            return {"status": "duplicate", "reason_code": "DUPLICATE_BATCH",
                    "device_id": device_id, "batch_id": batch_id,
                    "affected_sequences": []}

        previous_hash, next_sequence = self._chains.get(device_id, (ZERO_HASH, 1))
        result = validate_batch(
            batch, REGISTRY.get(device_id), previous_hash, next_sequence
        )

        status = "accepted" if result["valid"] else "rejected"
        if result["valid"]:
            self._chains[device_id] = (
                batch["records"][-1]["record_hash"],
                batch["end_sequence"] + 1,
            )
            self.received.append(batch_id)

        self._seen[key] = (status, result["reason_code"])
        return {"status": status, "reason_code": result["reason_code"],
                "device_id": device_id, "batch_id": batch_id,
                "affected_sequences": result["affected_sequences"]}

    # ---- HTTP 外壳 ----
    def start(self):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                batch = json.loads(self.rfile.read(length))["batch"]
                payload = json.dumps(
                    stub.handle(batch), ensure_ascii=False
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):     # 静音访问日志
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


# --------------------------------------------------------------------- fixtures

@pytest.fixture
def gateway_db(tmp_path, monkeypatch):
    """把网关的库指到临时文件，绝不碰真实的 edge_gateway/data/edge_cache.db。"""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "edge_cache.db")
    storage.init_db()
    assert storage.count_by_status() == {}, "临时库应为空"
    return tmp_path


@pytest.fixture
def three_batches(tmp_path):
    """用 NM-001 生成 3 个链上连续的合法批次（seq 1~10 / 11~20 / 21~30）。"""
    state_db = tmp_path / "simulator.db"
    batches = []
    for _ in range(ROUNDS):
        batches.append(
            create_next_batch(
                REGISTRY[DEVICE], KEYS["private_key"],
                batch_size=BATCH_SIZE, db_path=state_db, seed=2026,
            )
        )
    assert [b["batch_id"] for b in batches] == [
        f"{DEVICE}-{n:06d}" for n in (1, 2, 3)
    ]
    return batches


@pytest.fixture
def stub_center():
    center = _StubCenter().start()
    yield center
    center.stop()


def _ingest(batches):
    """按"MQTT 收到一批 → 初验通过 → 落库"的路径写入，等价于网关主流程第 3~5 步。"""
    for batch in batches:
        raw = json.dumps(batch, ensure_ascii=False)
        result = storage.save_received(batch, raw, dict(VALID_EDGE_RESULT))
        assert result["inserted"] is True
        assert result["status"] == storage.STATUS_PENDING


# ----------------------------------------------------------------------- tests

def test_offline_keeps_queue_and_retries_only_head(gateway_db, three_batches):
    """① 中心不可达：队列保留、只有队首批重试、后两批一次都没试过。"""
    _ingest(three_batches)
    assert storage.count_by_status() == {storage.STATUS_PENDING: ROUNDS}

    for _ in range(2):
        summary = forward.run_once(center_url=UNREACHABLE, timeout=2)
        assert summary["sent"] == 1, "断网时每轮只应尝试队首批"
        assert summary["retry"] == 1
        assert summary["accepted"] == 0

    head = storage.next_pending(DEVICE)
    assert head["batch_id"] == three_batches[0]["batch_id"]
    assert head["attempts"] == 2
    assert head["last_error"], "应记下失败原因"
    assert head["status"] == storage.STATUS_PENDING

    for batch in three_batches[1:]:
        row = storage.get_batch(DEVICE, batch["batch_id"])
        assert row["status"] == storage.STATUS_PENDING
        assert row["attempts"] == 0, "未确认之前，后续批次不允许被尝试"


def test_recovery_delivers_in_order(gateway_db, three_batches, stub_center):
    """② 中心恢复：按序补传、全部 accepted、中心侧序号连续、网关链尾推进。"""
    _ingest(three_batches)

    # 先断网两轮，制造积压
    for _ in range(2):
        forward.run_once(center_url=UNREACHABLE, timeout=2)
    assert storage.count_by_status() == {storage.STATUS_PENDING: ROUNDS}

    # 中心恢复
    summary = forward.run_once(center_url=stub_center.url, timeout=5)
    assert summary["accepted"] == ROUNDS, summary
    assert summary["retry"] == 0
    assert summary["sent"] == ROUNDS

    # 队列清空，全部 accepted
    assert storage.count_by_status() == {storage.STATUS_ACCEPTED: ROUNDS}

    # 中心收到的顺序与批次号
    assert stub_center.received == [b["batch_id"] for b in three_batches]

    # 中心侧序号连续无缺口
    assert [b["start_sequence"] for b in three_batches] == [1, 11, 21]
    assert [b["end_sequence"] for b in three_batches] == [10, 20, 30]

    # 网关链尾推进到最后一批
    chain = storage.get_device_chain(DEVICE)
    assert chain["last_sequence"] == 30

           # 延迟样本：默认口径排除重试批次（方案第 7.3 节：离线补传不计入实时 P95）
    # 注意：本用例为了制造积压，让批次在队列里停留了数秒，因此这里测出的延迟
    # 必然超过 1s 目标 —— P95 ≤1s 的达标验证属于在线联调（10/14 实测 337ms），
    # 不在本用例断言范围内。
    realtime = storage.latency_stats()
    assert realtime["count"] == ROUNDS - 1, "重试过的批次不应计入实时 P95"
    assert realtime["p95_ms"] > 0, "应记录到延迟样本"

    with_retried = storage.latency_stats(include_retried=True)
    assert with_retried["count"] == ROUNDS, "含重试口径应覆盖全部 3 批"
def test_duplicate_delivery_is_idempotent(gateway_db, three_batches):
    """③ 同一批被重复收到（QoS 1 重传）→ 不产生第二条待传任务。"""
    _ingest(three_batches)

    raw = json.dumps(three_batches[0], ensure_ascii=False)
    again = storage.save_received(three_batches[0], raw, dict(VALID_EDGE_RESULT))
    assert again["inserted"] is False
    assert again["status"] == storage.STATUS_PENDING

    assert storage.count_by_status() == {storage.STATUS_PENDING: ROUNDS}
    assert len(storage.list_pending(DEVICE)) == ROUNDS