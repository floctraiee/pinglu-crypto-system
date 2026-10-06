import paho.mqtt.client as mqtt
import time

def on_connect(client, userdata, flags, reason_code, properties):
    print(f"发布端已连接: {reason_code}")

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect

client.connect("127.0.0.1", 1883, 60)
client.loop_start()

print("发布消息...")
client.publish("pinglu/batches/test", "hello_from_python")
time.sleep(2)  # 等2秒确保消息发出

client.loop_stop()
client.disconnect()
print("发布完成")