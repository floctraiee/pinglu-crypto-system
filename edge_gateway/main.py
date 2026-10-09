"""边缘网关主程序。

订阅 pinglu/batches/+；on_message **只把消息塞进内存队列就返回**，
解析、初验、落库在后台工作线程做，转发在另一个线程按序进行
（任务书表 2 R1：回调只做快速入队，数据库和重试在工作线程处理）。

启动（仓库根目录，四个终端里的第 3 个）：
    .venv\\Scripts\\python.exe -m edge_gateway.main

常用参数：
    --broker 192.168.1.20     三机联调时指向电脑2
    --center http://192.168.1.30:8000
"""
import argparse
import json
import queue
import threading

import paho.mqtt.client as mqtt

from . import config, forward, storage, validator


class Gateway:
    """接收队列 + 工作线程 + 转发线程。"""

    def __init__(self, center_url=None, http_timeout=None,
                 forward_interval=None, queue_size=1000):
        self.center_url = center_url
        self.http_timeout = http_timeout
        self.forward_interval = forward_interval

        self.inbox = queue.Queue(maxsize=queue_size)
        self.stop_event = threading.Event()
        self.threads = []

        self.stats = {
            "received": 0,        # 收到的 MQTT 消息数
            "queued": 0,          # 首次入库的批次数
            "duplicate": 0,       # QoS 1 重传被唯一约束挡住的次数
            "pending": 0,         # 初验通过、待转发
            "audit_pending": 0,   # 初验未通过、待送中心复核
            "audit": 0,           # 连身份都取不到、只写了本地审计
            "dropped": 0,         # 内存队列满而丢弃
        }

    # ------------------------------------------------------------ 接收

    def enqueue(self, topic, payload):
        """九步第 1 步：记下接收时间并快速入队。这里不做任何 IO。"""
        received_at = storage.now_iso()
        self.stats["received"] += 1

        try:
            self.inbox.put_nowait((topic, payload, received_at))
        except queue.Full:
            self.stats["dropped"] += 1
            storage.save_audit(
                "queue_full", topic=topic,
                detail=f"内存队列已满（{self.inbox.maxsize}），该消息被丢弃",
                gateway_received_at=received_at,
            )
            print("[队列] 内存队列已满，丢弃一条消息")

    def _worker(self):
        while not self.stop_event.is_set():
            try:
                topic, payload, received_at = self.inbox.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                self.handle(topic, payload, received_at)
            except Exception as exc:
                # 任何意外都不能让订阅程序崩溃
                storage.save_audit(
                    "worker_exception", topic=topic,
                    detail=f"{type(exc).__name__}: {exc}",
                    gateway_received_at=received_at,
                )
                print(f"[异常] 处理消息出错：{type(exc).__name__}: {exc}")
            finally:
                self.inbox.task_done()

    def handle(self, topic, payload, received_at):
        """九步第 2、3、4、5、6 步。"""
        # 第 2 步：限制消息大小，解析 JSON，失败也存审计日志
        if len(payload) > config.MAX_MESSAGE_BYTES:
            storage.save_audit(
                "oversized", topic=topic,
                detail=f"{len(payload)} 字节，超过上限 {config.MAX_MESSAGE_BYTES}",
                gateway_received_at=received_at,
            )
            self.stats["audit"] += 1
            print(f"[审计] 消息超长（{len(payload)} 字节），已记录并丢弃")
            return

        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            storage.save_audit("decode_error", topic=topic, detail=str(exc),
                               gateway_received_at=received_at)
            self.stats["audit"] += 1
            print(f"[审计] UTF-8 解码失败：{exc}")
            return

        try:
            batch = json.loads(text)
        except ValueError as exc:
            storage.save_audit("json_error", topic=topic, detail=str(exc),
                               raw_text=text, gateway_received_at=received_at)
            self.stats["audit"] += 1
            print(f"[审计] JSON 解析失败：{exc}")
            return

        identity = validator.identify(batch)
        if identity is None:
            storage.save_audit(
                "missing_identity", topic=topic,
                detail="缺少可用的 device_id 或 batch_id，无法路由",
                raw_text=text, gateway_received_at=received_at,
            )
            self.stats["audit"] += 1
            print("[审计] 消息缺少 device_id 或 batch_id，已保存原文")
            return

        device_id = identity["device_id"]
        batch_id = identity["batch_id"]

        # 第 3 步：公钥只从预登记文件取；第 4 步：契约验证器初验
        registration = validator.get_registration(device_id)
        chain = storage.get_device_chain(device_id)
        result = validator.verify(batch, registration, chain)

        # 第 5 步：原文 + 初判一起落库，提交成功才算网关已持有
        saved = storage.save_received(batch, text, result,
                                      gateway_received_at=received_at)

        if not saved["inserted"]:
            self.stats["duplicate"] += 1
            print(f"[重复] {device_id} {batch_id} 已存在"
                  f"（状态 {saved['status']}），未生成新的待传任务")
            return

        self.stats["queued"] += 1
        span = f"seq {identity['first_seq']}~{identity['last_seq']}"

        if result["valid"]:
            self.stats["pending"] += 1
            print(f"[接收] {device_id} {batch_id} {span} 初验通过，已入待传队列")
        else:
            # 第 6 步：初验失败也要把原文与初判送中心复核，不能悄悄丢弃
            self.stats["audit_pending"] += 1
            seqs = result["affected_sequences"]
            suffix = f"，异常序号 {seqs}" if seqs else ""
            print(f"[接收] {device_id} {batch_id} 初验未通过"
                  f"（{result['reason_code']}{suffix}），原文待送中心复核")

    # ------------------------------------------------------------ 线程

    def start(self):
        worker = threading.Thread(target=self._worker,
                                  name="gateway-worker", daemon=True)
        forwarder = threading.Thread(
            target=forward.run_forever,
            args=(self.stop_event, self.center_url,
                  self.http_timeout, self.forward_interval),
            name="gateway-forward", daemon=True,
        )
        worker.start()
        forwarder.start()
        self.threads = [worker, forwarder]

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=3)


def build_client(gateway):
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=config.MQTT_CLIENT_ID,
    )

    def on_connect(client, userdata, flags, reason_code, properties):
        print(f"[MQTT] 已连接 {config.MQTT_BROKER}:{config.MQTT_PORT}：{reason_code}")
        client.subscribe(config.MQTT_TOPIC, qos=1)
        print(f"[MQTT] 已订阅 {config.MQTT_TOPIC}（QoS 1）")

    def on_disconnect(client, userdata, flags, reason_code, properties):
        print(f"[MQTT] 连接断开：{reason_code}")

    def on_message(client, userdata, message):
        userdata.enqueue(message.topic, message.payload)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.user_data_set(gateway)
    return client


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="边缘网关")
    parser.add_argument("--broker", help="MQTT broker 地址（默认取环境变量 MQTT_BROKER）")
    parser.add_argument("--port", type=int, help="MQTT broker 端口")
    parser.add_argument("--topic", help="订阅主题")
    parser.add_argument("--center", help="中心平台地址")
    parser.add_argument("--db", help="本地 SQLite 路径")
    parser.add_argument("--interval", type=int, help="转发轮询间隔（秒）")
    return parser.parse_args(argv)


def apply_overrides(args):
    """命令行参数覆盖配置模块常量；其余模块都在调用时读 config，因此即时生效。"""
    if args.broker:
        config.MQTT_BROKER = args.broker
    if args.port:
        config.MQTT_PORT = args.port
    if args.topic:
        config.MQTT_TOPIC = args.topic
    if args.center:
        config.CENTER_URL = args.center.rstrip("/")
    if args.db:
        config.DB_PATH = args.db
    if args.interval:
        config.RETRY_INTERVAL = args.interval


def main(argv=None):
    args = parse_args(argv)
    apply_overrides(args)

    print("=" * 72)
    for key, value in config.describe().items():
        print(f"  {key} = {value}")
    print("=" * 72)

    storage.init_db()

    try:
        registry = validator.load_registry()
    except validator.RegistryError as exc:
        print(f"[致命] {exc}")
        print("       先跑 tools/import_registry.py 确认登记文件可用，再启动网关。")
        return 2
    print(f"[启动] 预登记设备 {len(registry)} 台：{', '.join(sorted(registry))}")

    recovered = storage.list_unconfirmed()
    print(f"[启动] 从数据库恢复未确认批次 {len(recovered)} 条；"
          f"当前队列 {storage.count_by_status() or '空'}")

    gateway = Gateway(center_url=config.CENTER_URL,
                      http_timeout=config.HTTP_TIMEOUT,
                      forward_interval=config.RETRY_INTERVAL)
    gateway.start()

    client = build_client(gateway)
    try:
        client.connect(config.MQTT_BROKER, config.MQTT_PORT, config.MQTT_KEEPALIVE)
    except OSError as exc:
        print(f"[致命] 连不上 MQTT broker {config.MQTT_BROKER}:{config.MQTT_PORT}：{exc}")
        gateway.stop()
        return 2

    print("[启动] 网关就绪，Ctrl+C 退出")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[退出] 收到中断信号")
    finally:
        gateway.stop()
        client.disconnect()
        print(f"[退出] 本次统计 {gateway.stats}")
        print(f"[退出] 队列状态 {storage.count_by_status()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
