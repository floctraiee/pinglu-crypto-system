import paho.mqtt.client as mqtt

def on_connect(client, userdata, flags, reason_code, properties):
    print(f"订阅端已连接: {reason_code}")
    client.subscribe("pinglu/batches/+")

def on_message(client, userdata, msg):
    print(f"收到: {msg.topic} -> {msg.payload.decode()}")

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message

client.connect("127.0.0.1", 1883, 60)
print("订阅端启动，等待消息...")
client.loop_forever()