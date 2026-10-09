"""MQTT 最小发布测试（任务书 10月1–2日验收项：同一条消息能发出并能收到）。

本文件**不参与 pytest 收集**：文件名不以 test_ 开头，且所有连接动作都在
``main()`` 里，import 本模块不会碰网络。

本机用法（仓库根目录）：
    .venv\\Scripts\\python.exe tests\\manual\\mqtt_pub_smoke.py
    .venv\\Scripts\\python.exe tests\\manual\\mqtt_pub_smoke.py --topic pinglu/batches/WL-001

三台电脑联调时用 --host 指向电脑2的局域网 IP。
"""
import argparse
import time

import paho.mqtt.client as mqtt


def on_connect(client, userdata, flags, reason_code, properties):
    print(f"发布端已连接: {reason_code}")


def main():
    parser = argparse.ArgumentParser(description="MQTT 最小发布测试")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--topic", default="pinglu/batches/test")
    parser.add_argument("--message", default="hello_from_python")
    parser.add_argument("--wait", type=float, default=2.0,
                        help="发出后等待秒数，确保消息真正送达")
    args = parser.parse_args()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect

    client.connect(args.host, args.port, 60)
    client.loop_start()

    print(f"发布消息到 {args.topic} ...")
    client.publish(args.topic, args.message)
    time.sleep(args.wait)

    client.loop_stop()
    client.disconnect()
    print("发布完成")


if __name__ == "__main__":
    main()
