"""将已生成并本地验签的扁平批次以 MQTT QoS 1 发给边缘网关。

本模块不生成批次、不更新 simulator.db，也不把 MQTT PUBACK 误当作中心
accepted。中心转发及其终态确认由边缘网关负责。
"""
import argparse
import json
import os
import sys
from pathlib import Path

from common_crypto.batch import verify_batch
from common_crypto.canonical import canonical_bytes


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BATCH = ROOT / "test_data" / "normal_batch.json"
DEFAULT_REGISTRY = ROOT / "test_data" / "registry_public.json"


def load_json(path):
    """以 UTF-8 读取 JSON；不修改来源文件。"""
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def validate_for_publish(batch, registry):
    """使用公开登记表验签并检查样例的合同结构，返回 MQTT 主题和原文。"""
    if not isinstance(batch, dict):
        raise ValueError("批次必须是 JSON 对象")

    device_id = batch.get("device_id")
    if not isinstance(device_id, str) or not device_id:
        raise ValueError("批次缺少合法 device_id")
    if not isinstance(registry, dict):
        raise ValueError("公开登记表必须是对象")

    registration = registry.get(device_id)
    if not isinstance(registration, dict):
        raise ValueError(f"公开登记表未登记设备：{device_id}")
    if "private_key" in registration:
        raise ValueError("公开登记表不得包含 private_key")

    records = batch.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("批次 records 必须是非空列表")

    # 此处只做设备端本地样例验证；跨批次链尾仍必须由 B/中心的可信状态决定。
    validation = verify_batch(
        batch,
        registration,
        previous_hash=records[0].get("previous_hash"),
        start_sequence=records[0].get("sequence"),
    )
    if not validation["valid"]:
        raise ValueError(
            "批次未通过本地合同/签名检查："
            f"{validation['reason_code']}；{'；'.join(validation['errors'])}"
        )

    # canonical_bytes 是唯一允许的 JSON 编码实现：UTF-8、无空白、键排序、拒绝 NaN。
    payload = canonical_bytes(batch)
    return f"pinglu/batches/{device_id}", payload


def publish_qos1(broker, port, topic, payload, username=None, password=None, timeout=15):
    """发布一次 QoS 1、retain=False 消息；返回 MQTT 消息编号。"""
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise RuntimeError("缺少 paho-mqtt，请先安装 requirements.txt") from exc

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="pinglu-device-simulator",
        protocol=mqtt.MQTTv311,
    )
    if username is not None:
        client.username_pw_set(username, password)

    try:
        client.connect(broker, port, keepalive=30)
        client.loop_start()
        info = client.publish(topic, payload=payload, qos=1, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT 发布未被客户端接受，rc={info.rc}")
        # paho 的 wait_for_publish 成功时返回 None，因此以发布状态判断。
        info.wait_for_publish(timeout=timeout)
        if not info.is_published():
            raise TimeoutError("等待 MQTT PUBACK 超时")
        return info.mid
    finally:
        client.loop_stop()
        client.disconnect()


def parse_args():
    parser = argparse.ArgumentParser(description="发布已验签的平陆运河扁平批次")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--sample", action="store_true", help="使用 test_data/normal_batch.json")
    source.add_argument("--batch-file", type=Path, help="指定已生成的扁平批次 JSON 文件")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY, help="公开登记表路径")
    parser.add_argument("--broker", help="B 提供的 MQTT Broker 主机/IP；非 dry-run 时必填")
    parser.add_argument("--port", type=int, default=1883, help="MQTT Broker 端口，默认 1883")
    parser.add_argument("--username", help="可选 MQTT 用户名")
    parser.add_argument("--password-env", help="保存 MQTT 密码的环境变量名")
    parser.add_argument("--timeout", type=int, default=15, help="等待 PUBACK 的秒数")
    parser.add_argument("--dry-run", action="store_true", help="仅验签和显示主题，不连接 Broker")
    return parser.parse_args()


def main():
    args = parse_args()
    batch_path = DEFAULT_BATCH if args.sample or args.batch_file is None else args.batch_file
    batch = load_json(batch_path)
    registry = load_json(args.registry)
    topic, payload = validate_for_publish(batch, registry)

    print(f"本地验签与合同检查通过：{batch['batch_id']}")
    print(f"MQTT 主题：{topic}")
    print(f"消息字节数：{len(payload)}（完整扁平批次，无 HTTP 包装）")

    if args.dry_run:
        print("dry-run：未连接 Broker，未发送消息。")
        return
    if not args.broker:
        raise ValueError("--broker 为必填项；未知 Broker 时请使用 --dry-run")
    if not 1 <= args.port <= 65535:
        raise ValueError("--port 必须在 1 到 65535 之间")
    if args.timeout < 1:
        raise ValueError("--timeout 必须是正整数")

    password = None
    if args.password_env:
        password = os.environ.get(args.password_env)
        if password is None:
            raise ValueError(f"未设置密码环境变量：{args.password_env}")
    if password is not None and args.username is None:
        raise ValueError("提供 --password-env 时也必须提供 --username")

    mid = publish_qos1(
        args.broker, args.port, topic, payload,
        username=args.username, password=password, timeout=args.timeout,
    )
    print(f"MQTT QoS 1 PUBACK 已收到，mid={mid}。")
    print("这只证明 Broker 已确认；B 仍须等待中心明确持久化终态后才能确认待传任务。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"发布失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
