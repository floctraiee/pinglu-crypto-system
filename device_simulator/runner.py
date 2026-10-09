"""多设备模拟运行入口：先持久化生成，再可选地按设备顺序发送待发批次。"""
import argparse
import os
import time
from pathlib import Path

from .batch import create_next_batch, pending_batches, record_mqtt_result
from .publish import publish_qos1, validate_for_publish
from .register import register_devices


def flush_pending(db_path, registry, broker, port, username=None, password=None, timeout=15, publish=publish_qos1):
    """每台设备按批次号顺序发送；同设备失败即停止，绝不跳过旧批次。"""
    results = []
    current_device = None
    blocked_devices = set()
    for item in pending_batches(db_path):
        device_id = item["device_id"]
        if device_id in blocked_devices:
            continue
        topic, payload = validate_for_publish(item["batch"], registry)
        try:
            mid = publish(broker, port, topic, payload, username, password, timeout)
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            record_mqtt_result(db_path, device_id, item["batch_id"], False, exc)
            blocked_devices.add(device_id)
            results.append((device_id, item["batch_id"], "pending", str(exc)))
        else:
            record_mqtt_result(db_path, device_id, item["batch_id"], True)
            results.append((device_id, item["batch_id"], "broker_confirmed", str(mid)))
        current_device = device_id
    return results


def generate_round(registry, keys_dir, db_path, batch_size, seed):
    """每台已登记设备生成一批；批次先落盘，网络是否可用不影响此步骤。"""
    output = []
    keys_dir = Path(keys_dir)
    for device_id in sorted(registry):
        registration = registry[device_id]
        private_key = __import__("json").loads(
            (keys_dir / f"{device_id}.json").read_text(encoding="utf-8")
        )["private_key"]
        batch = create_next_batch(
            registration, private_key, batch_size=batch_size, db_path=db_path, seed=seed
        )
        output.append(batch)
    return output


def parse_args():
    parser = argparse.ArgumentParser(description="平陆运河多设备离线生成与 MQTT 待发队列工具")
    parser.add_argument("--devices", type=int, default=7, help="设备数量，按七类循环")
    parser.add_argument("--rate", type=float, default=1.0, help="每台设备每秒生成的记录数")
    parser.add_argument("--batch-size", type=int, default=10, help="每台设备每批记录数")
    parser.add_argument("--seed", type=int, default=2026, help="模拟 payload 的可复现随机种子")
    parser.add_argument("--rounds", type=int, default=1, help="生成轮数；每轮每台设备生成一批")
    parser.add_argument("--keys-dir", type=Path, required=True, help="设备私钥目录（必须显式指定）")
    parser.add_argument("--registry", type=Path, required=True, help="公开登记表输出路径（必须显式指定）")
    parser.add_argument("--db-path", type=Path, required=True, help="模拟器状态数据库（必须显式指定）")
    parser.add_argument("--broker", help="可选 Broker；提供后才尝试发送已保存待发批次")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username")
    parser.add_argument("--password-env")
    parser.add_argument("--timeout", type=int, default=15)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.devices < 1 or args.batch_size < 1 or args.rounds < 1 or args.rate <= 0:
        raise ValueError("--devices、--batch-size、--rounds 必须为正整数，--rate 必须大于 0")
    if not 1 <= args.port <= 65535 or args.timeout < 1:
        raise ValueError("端口或超时参数无效")
    password = None
    if args.password_env:
        password = os.environ.get(args.password_env)
        if password is None:
            raise ValueError(f"未设置密码环境变量：{args.password_env}")
    if password is not None and not args.username:
        raise ValueError("提供密码时必须提供 --username")

    registry = register_devices(args.devices, args.keys_dir, args.registry)
    interval = args.batch_size / args.rate
    print(f"已加载 {len(registry)} 台固定测试设备；rate={args.rate} 条/设备/秒，批间隔={interval:g} 秒")
    for round_number in range(args.rounds):
        batches = generate_round(registry, args.keys_dir, args.db_path, args.batch_size, args.seed)
        for batch in batches:
            print(f"已生成并保存 {batch['device_id']} {batch['batch_id']} seq {batch['start_sequence']}~{batch['end_sequence']}，MQTT=pending，中心=not_requested")
        if args.broker:
            for result in flush_pending(args.db_path, registry, args.broker, args.port, args.username, password, args.timeout):
                print(f"MQTT {result[2]} {result[0]} {result[1]}：{result[3]}")
        if round_number + 1 < args.rounds:
            time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        raise SystemExit(f"模拟器失败：{exc}")
