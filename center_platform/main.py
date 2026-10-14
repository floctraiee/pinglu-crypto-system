from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
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

    return {
        "service": "center_platform",
        "message": "Center platform is running",
        "docs": "/docs",
    }


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
    return storage.list_audits()


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