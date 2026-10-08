from common_crypto.batch import verify_batch
from common_crypto.merkle import merkle_root, merkle_proof, verify_proof
from common_crypto.batch import encoded


ALLOWED_HEADER_FIELDS = {
    "schema_version",
    "device_id",
    "device_type",
    "batch_id",
    "sequence_start",
    "sequence_end",
    "record_count",
    "timestamp_start",
    "timestamp_end",
    "previous_hash",
    "merkle_root",
}


def normalize_reason(errors):
    if not errors:
        return "OK"

    text = "；".join(str(x) for x in errors)

    if "SM2" in text:
        return "INVALID_SIGNATURE"

    if "设备" in text or "device" in text.lower():
        return "UNKNOWN_DEVICE"

    if "摘要" in text or "hash" in text.lower():
        return "INVALID_RECORD_HASH"

    if "Merkle" in text or "merkle" in text.lower():
        return "INVALID_MERKLE_ROOT"

    if "序号" in text or "sequence" in text.lower():
        return "INVALID_SEQUENCE"

    if "批次" in text:
        return "INVALID_BATCH"

    return "VALIDATION_FAILED"


def affected_sequences(record_errors):
    result = []

    for item in record_errors or []:
        if isinstance(item, dict):
            if "sequence" in item:
                result.append(item["sequence"])
            elif "index" in item:
                result.append(item["index"])

    return sorted(set(result))


def validate_batch(batch, registration, previous_hash, start_sequence):
    """
    中心独立验证批次。

    registration 必须来自中心 devices 表，
    不信任批次自己携带的公钥。
    """

    if not registration:
        return {
            "valid": False,
            "reason_code": "UNKNOWN_DEVICE",
            "errors": ["device is not registered"],
            "record_errors": [],
            "affected_sequences": [],
        }

    try:
        result = verify_batch(
            batch,
            registration,
            previous_hash=previous_hash,
            start_sequence=start_sequence,
        )
    except Exception as exc:
        return {
            "valid": False,
            "reason_code": "VALIDATION_EXCEPTION",
            "errors": [str(exc)],
            "record_errors": [],
            "affected_sequences": [],
        }

    errors = result.get("errors", [])
    record_errors = result.get("record_errors", [])

    affected = affected_sequences(record_errors)

    return {
        "valid": bool(result.get("valid")),
        "reason_code": normalize_reason(errors + record_errors),
        "errors": errors,
        "record_errors": record_errors,
        "affected_sequences": affected,
    }


def get_merkle_proof(batch, sequence):
    records = sorted(
        batch["records"],
        key=lambda item: item["sequence"],
    )

    sequences = [record["sequence"] for record in records]

    if sequence not in sequences:
        raise ValueError("sequence not found")

    index = sequences.index(sequence)
    hashes = [record["record_hash"] for record in records]

    proof = merkle_proof(hashes, index)
    root = merkle_root(hashes)

    record_hash = records[index]["record_hash"]

    verified = verify_proof(
        record_hash,
        proof,
        root,
    )

    return {
        "sequence": sequence,
        "record_hash": record_hash,
        "proof": proof,
        "root": root,
        "verified": verified,
    }