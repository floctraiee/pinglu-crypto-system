import os

MQTT_BROKER = os.getenv("MQTT_BROKER", "127.0.0.1")
MQTT_PORT   = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC  = os.getenv("MQTT_TOPIC", "pinglu/batches/+")

CENTER_URL  = os.getenv("CENTER_URL", "http://127.0.0.1:8000")
DB_PATH     = os.getenv("DB_PATH", "edge_gateway/edge_cache.db")

RETRY_INTERVAL = int(os.getenv("RETRY_INTERVAL", "5"))
HTTP_TIMEOUT   = int(os.getenv("HTTP_TIMEOUT", "10"))