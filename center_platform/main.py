from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import storage
from . import telemetry
from .validator import validate_batch, get_merkle_proof
from common_crypto.canonical import canonical_bytes, signed_header
from common_crypto.sm import verify_message


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


app = FastAPI(
    title="Pinglu Crypto Center Platform",
    version="1.0.0",
)

# 首页在 "/" 直接返回 index.html，静态资源（app.css / app.js）走这个挂载点。
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# 没有 static/index.html 时的兜底页面：保证浏览器打开永远是网页而不是 JSON。
FALLBACK_INDEX = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>多源感知数据可信采集 · 中心平台</title>
<style>
 body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:24px;color:#222;background:#fafafa}
 h1{font-size:20px;margin:0 0 6px}
 .sub{color:#777;font-size:13px;margin-bottom:18px}
 .cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px}
 .card{background:#fff;border:1px solid #e3e3e3;border-radius:8px;padding:12px 18px;min-width:120px}
 .card b{display:block;font-size:24px;line-height:1.2}
 .card span{color:#777;font-size:12px}
 table{border-collapse:collapse;width:100%;max-width:1000px;background:#fff}
 th,td{border:1px solid #e3e3e3;padding:7px 10px;font-size:14px;text-align:left}
 th{background:#f2f2f2}
 .ok{color:#0a7;font-weight:600} .no{color:#c00;font-weight:600}
 .note{margin-top:14px;font-size:12px;color:#888}
 a{color:#06c}
</style>
</head>
<body>
<h1>多源感知数据可信采集 · 中心平台</h1>
<div class="sub">SM2/SM3 · 摘要链 · Merkle 证明 · 独立复核　｜　<a href="/docs">接口文档 /docs</a></div>
<div class="cards">
  <div class="card"><b id="d">-</b><span>设备</span></div>
  <div class="card"><b id="b">-</b><span>批次</span></div>
  <div class="card"><b id="r">-</b><span>记录</span></div>
  <div class="card"><b id="a">-</b><span>审计</span></div>
</div>
<table id="t">
  <thead><tr><th>设备</th><th>批次号</th><th>状态</th><th>接收时间</th></tr></thead>
  <tbody><tr><td colspan="4">加载中…</td></tr></tbody>
</table>
<div class="note">本页为兜底页面：未找到 center_platform/static/index.html，完整六块面板请以带 static 目录的部署为准。</div>
<script>
function load(){
  fetch('/stats').then(function(r){return r.json()}).then(function(s){
    document.getElementById('d').textContent = s.devices;
    document.getElementById('b').textContent = s.batches;
    document.getElementById('r').textContent = s.records;
    document.getElementById('a').textContent = s.audits;
  }).catch(function(){});
  fetch('/batches').then(function(r){return r.json()}).then(function(list){
    var body = document.querySelector('#t tbody');
    if(!list.length){ body.innerHTML = '<tr><td colspan="4">暂无批次</td></tr>'; return; }
    body.innerHTML = list.map(function(x){
      var cls = x.status === 'accepted' ? 'ok' : 'no';
      return '<tr><td>' + (x.device_id||'') + '</td><td>' + (x.batch_id||'') +
             '</td><td class="' + cls + '">' + (x.status||'') +
             '</td><td>' + (x.received_at||'') + '</td></tr>';
    }).join('');
  }).catch(function(){});
}
load();
setInterval(load, 5000);
</script>
</body>
</html>
"""


class BatchRequest(BaseModel):
    batch: Dict[str, Any]


class VerifySignatureRequest(BaseModel):
    batch: Dict[str, Any]
    public_key: str


@app.on_event("startup")
def startup():
    storage.init_db()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "center_platform",
    }


@app.get("/")
def index():
    index_file = STATIC_DIR / "index.html"

    if index_file.exists():
        return FileResponse(index_file)

    # 静态资源缺失时（例如只克隆了代码、没拿到 static/ 的机器），
    # 仍然返回一个可用的页面，而不是让浏览器看到一串 JSON。
    return HTMLResponse(FALLBACK_INDEX)


@app.get("/devices")
def devices():
    return storage.list_devices()


@app.get("/batches")
def batches():
    return storage.list_batches()


@app.get("/batch-raw")
def batch_raw(batch_id: str, device_id: str):
    """只读返回入库时的原始批次，供证明页复核签名与记录摘要。"""
    stored = storage.get_batch(device_id, batch_id)

    if stored is None:
        raise HTTPException(status_code=404, detail="batch not found")

    return {
        "device_id": stored["device_id"],
        "batch_id": stored["batch_id"],
        "status": stored["status"],
        "received_at": stored["received_at"],
        "raw": stored["raw"],
    }


@app.get("/records")
def records(
    device_id: str | None = None,
    batch_id: str | None = None,
):
    return storage.list_records(
        device_id=device_id,
        batch_id=batch_id,
    )


@app.get("/audits")
def audits():
    """审计列表，并附上网关初验结论（若网关库可读）。

    中心复核与网关初验是两次独立判断：中心不采信网关结论，独立重验后才落 audit。
    页面上把两者并排列出，异常原因与边缘判定可以对照。
    """
    rows = storage.list_audits()

    try:
        verdicts = telemetry.gateway_edge_verdicts()
    except Exception:
        verdicts = {}

    for row in rows:
        key = (row.get("device_id"), row.get("batch_id"))
        verdict = verdicts.get(key)

        if verdict is None:
            row["edge_verdict"] = None
            row["edge_source"] = (
                "gateway_db_unavailable" if not verdicts else "gateway_not_recorded"
            )
        else:
            row["edge_verdict"] = verdict
            row["edge_source"] = "gateway_db"

    return rows


@app.get("/stats")
def stats():
    return storage.get_stats()


@app.get("/offline-status")
def offline_status():
    """离线页数据：网关只读状态或联调上报的待发队列，只读不写业务库。"""
    return telemetry.collect_offline_status()


@app.get("/performance")
def performance():
    """性能页数据：读签名对照 CSV；没有文件时如实返回缺失原因。"""
    return telemetry.collect_performance()


@app.post("/batches")
def receive_batch(request: BatchRequest):
    # 整个接收流程由 storage 在同一写事务内完成，避免并发时链尾失效。
    return storage.process_batch(request.batch, validate_batch)


@app.post("/verify-signature")
def verify_signature(request: VerifySignatureRequest):
    """证明页用的只读签名复核：由中心用可信公钥验签，前端不接触私钥。"""
    batch = request.batch

    try:
        header = signed_header(batch)
        payload = canonical_bytes(header)
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "verified": False,
            "note": f"批次头部字段不完整，无法复核签名：{exc}",
        }

    signature = batch.get("signature")

    if not isinstance(signature, str) or not signature:
        return {"verified": False, "note": "批次缺少 signature 字段。"}

    try:
        verified = verify_message(request.public_key, payload, signature)
    except (ValueError, TypeError) as exc:
        return {"verified": False, "note": f"公钥或签名格式不合法：{exc}"}

    note = (
        "用中心 devices 表中的预登记公钥，对"
        "canonical(批次九个签名字段) 重新验签。"
    )

    return {
        "verified": bool(verified),
        "note": note if verified else note + " 验签未通过，说明签名字段已被改动。",
    }


@app.get("/batches/{batch_id}/proof/{sequence}")
def proof(batch_id: str, sequence: int):
    batches_data = storage.list_batches()

    target = None

    for item in batches_data:
        if item.get("batch_id") == batch_id:
            target = item
            break

    if target is None:
        raise HTTPException(
            status_code=404,
            detail="batch not found",
        )

    # 直接读取数据库保存的完整原始批次
    stored = storage.get_batch(
        target["device_id"],
        batch_id,
    )

    if stored is None:
        raise HTTPException(
            status_code=404,
            detail="batch not found",
        )

    batch = stored.get("raw")

    if not isinstance(batch, dict):
        raise HTTPException(
            status_code=400,
            detail="invalid stored batch",
        )

    if "records" not in batch:
        raise HTTPException(
            status_code=400,
            detail="stored batch has no records",
        )

    try:
        result = get_merkle_proof(
            batch,
            sequence,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    return result

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "center_platform.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )