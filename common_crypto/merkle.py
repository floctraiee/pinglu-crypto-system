from .sm import sm3_hex


def leaf_node(record_hash):
    # 叶子加 00 标记，内部节点加 01 标记
    return sm3_hex(b"\x00" + bytes.fromhex(record_hash))


def parent_node(left, right):
    return sm3_hex(
        b"\x01" + bytes.fromhex(left) + bytes.fromhex(right)
    )


def merkle_root(hashes):
    """根据一批记录摘要计算 Merkle 根。"""
    if not hashes:
        raise ValueError("批次不能为空")

    level = [leaf_node(h) for h in hashes]

    while len(level) > 1:
        # 节点数量为奇数时，复制最后一个节点
        if len(level) % 2:
            level = level + [level[-1]]

        level = [
            parent_node(level[i], level[i + 1])
            for i in range(0, len(level), 2)
        ]

    return level[0]


def merkle_proof(hashes, index):
    """为指定位置的记录生成证明路径，index 从 0 开始。"""
    if not 0 <= index < len(hashes):
        raise ValueError("记录位置超出范围")

    level = [leaf_node(h) for h in hashes]
    proof = []

    while len(level) > 1:
        if len(level) % 2:
            level = level + [level[-1]]

        sibling = index ^ 1
        proof.append({
            "side": "left" if index % 2 else "right",
            "hash": level[sibling],
        })

        level = [
            parent_node(level[i], level[i + 1])
            for i in range(0, len(level), 2)
        ]
        index //= 2

    return proof


def verify_proof(record_hash, proof, root):
    """检查记录摘要能否通过证明路径得到指定的根。"""
    current = leaf_node(record_hash)

    for step in proof:
        if step["side"] == "left":
            current = parent_node(step["hash"], current)
        elif step["side"] == "right":
            current = parent_node(current, step["hash"])
        else:
            return False

    return current == root