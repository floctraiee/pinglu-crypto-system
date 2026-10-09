"""中心独立验证；registration 只能来自中心 devices 表。"""
from common_crypto.batch import verify_batch
from common_crypto.merkle import merkle_proof, verify_proof


def validate_batch(batch, registration, previous_hash, start_sequence):
    return verify_batch(batch, registration, previous_hash, start_sequence)


def get_merkle_proof(batch, sequence):
    # 使用已验证入库时的原始顺序及已签名的根，不再另算一个根来证明自身。
    records = batch["records"]
    sequences = [r["sequence"] for r in records]
    if sequence not in sequences:
        raise ValueError("sequence not found")
    index = sequences.index(sequence)
    hashes = [r["record_hash"] for r in records]
    proof = merkle_proof(hashes, index)
    root = batch["merkle_root"]
    return {
        "sequence": sequence, "record_hash": hashes[index], "proof": proof,
        "root": root, "verified": verify_proof(hashes[index], proof, root),
    }
