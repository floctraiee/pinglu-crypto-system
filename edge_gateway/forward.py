"""把队列里的批次送到中心，并按回复判定是否确认。

九步运行里对应第 6、7、8、9 步：

  第 6 步  初验失败的原文也要送中心复核（中心对非 accepted 结果自动写 audits）
  第 7 步  逐设备按 batch_no 升序推进，一台设备一次只发一批，后续批次不抢跑
  第 8 步  只有中心明确 accepted / duplicate(内容一致) 才算确认；
           rejected 记为终态并留证；网络失败、超时、5xx 一律保留待传
  第 9 步  由定时轮询驱动，重启后从数据库恢复，不依赖内存计数

只用标准库（http.client + json），不引入额外依赖。

连接复用
--------
网关是按批串行转发的（第 7 步要求同设备按序、不抢跑），如果每批都新建 TCP 连接，
100 台 × 每批一次握手会白吃掉 10 ms/批。所以这里按（主机, 端口）缓存一个
http.client.HTTPConnection，在同一线程内复用；连接被服务端关闭时自动重连一次。
"""
import http.client
import json
import threading
from urllib.parse import urlsplit

from . import config, storage

#: 每个线程一份 HTTP 连接缓存（网关的转发线程只有一个，但测试里可能多线程）
_HTTP_LOCAL = threading.local()


def _reset_connection():
    conn = getattr(_HTTP_LOCAL, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _HTTP_LOCAL.conn = None
    _HTTP_LOCAL.key = None


def _shared_connection(base_url, timeout):
    """取出（或建立）本线程复用的连接，返回 (conn, 请求路径)。"""
    parts = urlsplit(base_url)
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    key = (base_url, timeout)
    conn = getattr(_HTTP_LOCAL, "conn", None)
    if conn is not None and getattr(_HTTP_LOCAL, "key", None) == key:
        return conn, parts.path.rstrip("/") + "/batches"

    _reset_connection()
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    _HTTP_LOCAL.conn = conn
    _HTTP_LOCAL.key = key
    return conn, parts.path.rstrip("/") + "/batches"


def _post_once(conn, path, body):
    """发一次请求，返回 (status, text)；异常原样抛出。"""
    conn.request(
        "POST",
        path,
        body=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    response = conn.getresponse()
    return response.status, response.read().decode("utf-8")


def post_batch(batch, center_url=None, timeout=None):
    """POST {CENTER_URL}/batches，body 必须是 {"batch": <原始批次>}。"""
    base = (center_url or config.CENTER_URL).rstrip("/")
    timeout = timeout or config.HTTP_TIMEOUT
    body = json.dumps({"batch": batch}, ensure_ascii=False).encode("utf-8")

    try:
        conn, path = _shared_connection(base, timeout)
        status, text = _post_once(conn, path, body)
    except Exception as exc:  # 连接可能已被服务端按 keep-alive 超时关闭
        _reset_connection()
        try:
            conn, path = _shared_connection(base, timeout)
            status, text = _post_once(conn, path, body)
        except Exception as exc2:
            _reset_connection()
            return {"ok": False, "error": f"{type(exc2).__name__}: {exc2}（首次: {type(exc).__name__}）"}

    if status >= 400:
        return {"ok": False, "code": status, "error": f"HTTP {status}: {text[:200]}"}

    try:
        return {"ok": True, "code": status, "response": json.loads(text)}
    except ValueError as exc:
        return {"ok": False, "error": f"中心返回的不是 JSON：{exc}；原文 {text[:200]!r}"}


def classify(response):
    """把中心回复映射成终态；返回 None 表示"没看懂，继续重试"。"""
    if not isinstance(response, dict):
        return None

    status = response.get("status")
    code = response.get("reason_code")

    if status == "accepted":
        return storage.STATUS_ACCEPTED

    if status == "duplicate":
        # 内容一致才算确认；duplicate 但内容不同时中心会回 BATCH_CONTENT_CONFLICT
        return storage.STATUS_DUPLICATE if code == "DUPLICATE_BATCH" else None

    if status == "rejected":
        if code == "BATCH_CONTENT_CONFLICT":
            return storage.STATUS_CONFLICT
        return storage.STATUS_REJECTED

    return None


def forward_one(row, center_url=None, timeout=None):
    """发送一批。返回 (终态 或 None, 中心回复或错误信息)。"""
    device_id = row["device_id"]
    batch_id = row["batch_id"]

    try:
        batch = json.loads(row["raw_json"])
    except ValueError as exc:
        # 本地存的原文本坏了，重试也没意义，直接留证
        storage.mark_result(device_id, batch_id, storage.STATUS_REJECTED,
                            "LOCAL_RAW_JSON_BROKEN",
                            {"status": "rejected", "reason_code": "LOCAL_RAW_JSON_BROKEN",
                             "detail": str(exc)})
        return storage.STATUS_REJECTED, {"error": f"本地原文解析失败：{exc}"}

    outcome = post_batch(batch, center_url, timeout)

    if not outcome.get("ok"):
        # 传输失败、超时、5xx：保留待传，下一轮重试
        storage.record_attempt(device_id, batch_id, outcome.get("error"))
        return None, outcome

    response = outcome["response"]
    status = classify(response)

    if status is None:
        storage.record_attempt(device_id, batch_id,
                               f"无法判定的中心回复：{json.dumps(response, ensure_ascii=False)[:200]}")
        return None, outcome

    storage.mark_result(device_id, batch_id, status,
                        response.get("reason_code"), response)
    return status, response


def run_once(center_url=None, timeout=None, max_audit=10, max_per_device=None):
    """跑一轮转发，返回统计。"""
    if max_per_device is None:
        max_per_device = config.MAX_PER_DEVICE_PER_ROUND

    summary = {
        "audit_sent": 0,
        "sent": 0,
        "accepted": 0,
        "rejected": 0,
        "duplicate": 0,
        "conflict": 0,
        "retry": 0,
    }

    def record(status):
        summary["sent"] += 1
        if status:
            summary[status] = summary.get(status, 0) + 1
        else:
            summary["retry"] += 1

    # 第 6 步：初验失败的原文送中心复核，不参与同设备的顺序约束
    for row in storage.list_audit_pending()[:max_audit]:
        status, _ = forward_one(row, center_url, timeout)
        summary["audit_sent"] += 1
        if status:
            summary[status] = summary.get(status, 0) + 1
        else:
            summary["retry"] += 1

    # 第 7 步：逐设备按 batch_no 升序推进。
    #
    # 同一设备一轮内可以连发多批（串行发送，顺序天然不乱），但只要有一批
    # 没拿到中心终态就立刻停下——后续批次绝不允许抢在未确认的前一批之前确认。
    #
    # 之所以要连发而不是"每设备每轮只发一批"：100 台设备 × 每秒 1 条 ÷ 每批 10 条
    # = 10 批/秒，如果每台每轮只发一批，任何一次积压都要等下一轮才能清，会越堆越多。
    for device_id in storage.pending_device_ids():
        for _ in range(max_per_device):
            row = storage.next_pending(device_id)
            if row is None:
                break

            status, _ = forward_one(row, center_url, timeout)
            record(status)

            if not status:
                break       # 这一批还没确认，该设备后面的批次等下一轮

    return summary


def run_forever(stop_event, wake_event=None, center_url=None, timeout=None,
                interval=None):
    """第 9 步：持续转发未确认批次。

    延迟要求（方案第七节 3）：以网关收到批次为起点、中心完成验证为终点，
    实时批次 P95 不高于 1 秒。所以这里**不能只靠定时轮询**——worker 线程每
    入库一批就 set 一次 wake_event，本函数被立刻唤醒并马上转发；interval 只
    作为"没有新批次时"的重试节拍（用于清掉之前发送失败的批次）。
    """
    interval = interval or config.RETRY_INTERVAL

    while not stop_event.is_set():
        try:
            summary = run_once(center_url, timeout)
            if any(summary.values()):
                print(f"[转发] 留证 {summary['audit_sent']} 批，"
                      f"发送 {summary['sent']} 批，"
                      f"确认 {summary['accepted']}，重复 {summary['duplicate']}，"
                      f"拒绝 {summary['rejected']}，冲突 {summary['conflict']}，"
                      f"待重试 {summary['retry']}")
        except Exception as exc:
            print(f"[转发] 本轮异常：{type(exc).__name__}: {exc}")

        if wake_event is not None:
            # 有新批次就立刻返回，否则最多等 interval 秒做一次重试扫描
            wake_event.wait(interval)
            wake_event.clear()
        else:
            stop_event.wait(interval)


if __name__ == "__main__":
    # 单次转发，便于离线演练时手动补传：
    #   .venv\Scripts\python.exe -m edge_gateway.forward
    storage.init_db()
    result = run_once()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("状态统计：", storage.count_by_status())
