"""网关侧初验。

职责只有三件，密码学一律复用公共模块，不自己实现：

1. 按 device_id 从**预登记公钥文件**取公钥
   （contract.md §7：绝不能使用消息里自带的公钥作为信任来源）
2. 从 storage 取出该设备网关侧的可信链尾，注入契约验证器
3. 把契约验证器的结果整理成带 reason_code / affected_sequences 的结果对象

真正的契约校验在 common_crypto/batch.py::verify_batch，
它做完了字段严格检查、payload 与设备类型匹配、时区、经纬度范围、
签名、摘要、链、序号、数量、Merkle 根。
"""
import json
from pathlib import Path

from common_crypto.batch import verify_batch as contract_verify
from common_crypto.hash_chain import ZERO_HASH

from . import config


class RegistryError(RuntimeError):
    """预登记公钥文件不可用；启动阶段应当直接失败，不要带着空信任表跑。"""


#: 预登记文件缓存，按 mtime 失效，方便联调时替换登记文件而不用重启
_cache = {"path": None, "mtime": None, "data": None}


def load_registry(path=None, force=False):
    """读取 设备ID -> 登记信息 的公钥表。"""
    target = Path(path or config.REGISTRY_PATH)

    try:
        mtime = target.stat().st_mtime
    except OSError as exc:
        raise RegistryError(f"读不到预登记公钥文件 {target}：{exc}") from exc

    if (not force
            and _cache["data"] is not None
            and _cache["path"] == str(target)
            and _cache["mtime"] == mtime):
        return _cache["data"]

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistryError(f"预登记公钥文件 {target} 解析失败：{exc}") from exc

    if not isinstance(data, dict) or not data:
        raise RegistryError(f"预登记公钥文件 {target} 不是非空对象")

    _cache.update({"path": str(target), "mtime": mtime, "data": data})
    return data


def get_registration(device_id, registry=None):
    """按设备 ID 取预登记信息；没登记返回 None。"""
    if not isinstance(device_id, str) or not device_id:
        return None
    if registry is None:
        registry = load_registry()
    item = registry.get(device_id)
    return item if isinstance(item, dict) else None


def identify(batch):
    """尽力取出路由所需字段。

    只要拿得到 device_id 与 batch_id，这一批就能进持久队列
    （哪怕是未知设备或严重不合规，也要能送中心复核）。
    连这两个都取不到时返回 None，由调用方写网关审计表。
    """
    if not isinstance(batch, dict):
        return None

    device_id = batch.get("device_id")
    batch_id = batch.get("batch_id")

    if not isinstance(device_id, str) or not device_id:
        return None
    if not isinstance(batch_id, str) or not batch_id:
        return None

    return {
        "device_id": device_id,
        "batch_id": batch_id,
        "first_seq": batch.get("start_sequence"),
        "last_seq": batch.get("end_sequence"),
    }


def _result(valid, reason_code, errors=None, record_errors=None, affected=None):
    return {
        "valid": bool(valid),
        "reason_code": reason_code,
        "errors": list(errors or []),
        "record_errors": list(record_errors or []),
        "affected_sequences": list(affected or []),
    }


def verify(batch, registration, chain=None):
    """初验一批。

    registration 必须是预登记文件里的条目；
    chain 是 storage.get_device_chain(device_id) 的返回值（没有历史时全 0）。
    """
    if not registration:
        return _result(False, "UNKNOWN_DEVICE", ["设备未在网关预登记"])

    previous_hash = (chain or {}).get("last_hash") or ZERO_HASH
    start_sequence = int((chain or {}).get("last_sequence") or 0) + 1

    try:
        result = contract_verify(batch, registration, previous_hash, start_sequence)
    except Exception as exc:
        # 契约验证器本身出意外也不能让网关崩掉，按异常留证处理
        return _result(False, "VALIDATION_EXCEPTION", [f"{type(exc).__name__}: {exc}"])

    return {
        "valid": bool(result.get("valid")),
        "reason_code": result.get("reason_code", "VALIDATION_FAILED"),
        "errors": list(result.get("errors", [])),
        "record_errors": list(result.get("record_errors", [])),
        "affected_sequences": list(result.get("affected_sequences", [])),
    }


if __name__ == "__main__":
    registry = load_registry()
    print(f"预登记文件: {config.REGISTRY_PATH}")
    print(f"设备数量  : {len(registry)}")
    for device_id in sorted(registry):
        print(f"  {device_id:<10} {registry[device_id].get('device_type', '?')}")
