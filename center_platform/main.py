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
    batch = request.batch

    try:
        header = batch["header"]
        device_id = header["device_id"]
        batch_id = header["batch_id"]
    except (KeyError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={
                "status": "rejected",
                "reason_code": "invalid_batch_structure",
            },
        )

    # 1. 查询中心预注册设备
    registration = storage.get_device(device_id)

    if registration is None:
        return {
            "status": "rejected",
            "reason_code": "unknown_device",
            "device_id": device_id,
            "batch_id": batch_id,
            "affected_sequences": [],
        }

    # 2. 检查是否已经处理过这个批次
    existing = storage.batch_exists(device_id, batch_id)

    if existing:
        return {
            "status": "duplicate",
            "reason_code": "duplicate_batch",
            "device_id": device_id,
            "batch_id": batch_id,
            "affected_sequences": [],
        }

    # 3. 获取设备历史链尾
    last_record = storage.get_last_record(device_id)

    if last_record:
        previous_hash = last_record.get("record_hash")
        start_sequence = int(last_record.get("sequence", 0)) + 1
    else:
        previous_hash = "0" * 64
        start_sequence = 1

    # 4. 中心独立验证
    result = validate_batch(
        batch,
        registration,
        previous_hash,
        start_sequence,
    )

    if not result.get("valid"):
        record_errors = result.get("record_errors") or []

        affected = []

        for item in record_errors:
            if isinstance(item, dict):
                if "sequence" in item:
                    affected.append(item["sequence"])

        return {
            "status": "rejected",
            "reason_code": "validation_failed",
            "device_id": device_id,
            "batch_id": batch_id,
            "affected_sequences": affected,
            "errors": result.get("errors", []),
            "record_errors": record_errors,
        }

    # 5. 验证通过，事务写入数据库
    storage.insert_valid_batch(batch)

    return {
        "status": "accepted",
        "device_id": device_id,
        "batch_id": batch_id,
        "affected_sequences": [],
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