from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import storage
from .validator import validate_batch, get_merkle_proof


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


app = FastAPI(
    title="Pinglu Crypto Center Platform",
    version="1.0.0",
)


class BatchRequest(BaseModel):
    batch: Dict[str, Any]


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


@app.post("/batches")
def receive_batch(request: BatchRequest):
    # 整个接收流程由 storage 在同一写事务内完成，避免并发时链尾失效。
    return storage.process_batch(request.batch, validate_batch)


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