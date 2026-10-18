"""MQTT 最小订阅测试（任务书 10月1–2日验收项：订阅者能收到测试消息）。

本文件**不参与 pytest 收集**：文件名不以 test_ 开头，且所有连接动作都在
``main()`` 里，import 本模块不会碰网络。

本机用法（仓库根目录）：
    .venv\\Scripts\\python.exe tests\\manual\\mqtt_sub_smoke.py
    .venv\\Scripts\\python.exe tests\\manual\\mqtt_sub_smoke.py --topic "pinglu/batches/+"

三台电脑联调时用 --host 指向电脑2的局域网 IP。
"""
import argparse

import paho.mqtt.client as mqtt


def main():
    parser = argparse.ArgumentParser(description="MQTT 最小订阅测试")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--topic", default="pinglu/batches/+")
    args = parser.parse_args()

    def on_connect(client, userdata, flags, reason_code, properties):
        print(f"订阅端已连接: {reason_code}")
        client.subscribe(args.topic)

    def on_message(client, userdata, msg):
        text = msg.payload.decode("utf-8", "replace")
        print(f"收到: {msg.topic} -> {text}")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(args.host, args.port, 60)
    print("订阅端启动，等待消息... Ctrl+C 退出")

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n订阅端退出")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
