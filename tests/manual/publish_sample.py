"""把一个样本批次通过 MQTT 发出去，模拟设备端发布。

不属于 pytest 测试，纯手工联调工具。

用法（仓库根目录）：
    .venv\\Scripts\\python.exe tests\\manual\\publish_sample.py
    .venv\\Scripts\\python.exe tests\\manual\\publish_sample.py --file test_data/normal_batch.json
    .venv\\Scripts\\python.exe tests\\manual\\publish_sample.py --count 2      # 连发两次，验证去重

主题默认按批次里的 device_id 生成：pinglu/batches/<device_id>
"""
import argparse
import json
import time
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FILE = ROOT / "test_data" / "normal_batch.json"


def main():
    parser = argparse.ArgumentParser(description="通过 MQTT 发布样本批次")
    parser.add_argument("--file", default=str(DEFAULT_FILE), help="批次 JSON 文件")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--topic", help="默认 pinglu/batches/<device_id>")
    parser.add_argument("--count", type=int, default=1, help="重复发送次数")
    parser.add_argument("--delay", type=float, default=1.0, help="每次间隔秒数")
    args = parser.parse_args()

    path = Path(args.file)
    text = path.read_text(encoding="utf-8")
    batch = json.loads(text)

    device_id = batch.get("device_id")
    if not device_id:
        raise SystemExit(f"{path} 里没有 device_id，无法确定主题")

    topic = args.topic or f"pinglu/batches/{device_id}"

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="publish-sample")
    client.connect(args.host, args.port, 60)
    client.loop_start()

    print(f"文件   : {path}")
    print(f"批次   : {batch.get('batch_id')}  记录数 {len(batch.get('records', []))}")
    print(f"主题   : {topic}")
    print(f"次数   : {args.count}")

    for index in range(args.count):
        info = client.publish(topic, text, qos=1)
        info.wait_for_publish(timeout=5)
        print(f"  第 {index + 1} 次已发出（qos=1, rc={info.rc}）")
        if index + 1 < args.count:
            time.sleep(args.delay)

    time.sleep(1)
    client.loop_stop()
    client.disconnect()
    print("发布完成")


if __name__ == "__main__":
    main()
