"""边缘网关配置。

每一项目都可以用**同名环境变量**覆盖，业务代码只读这里的常量，
因此换到三台电脑联调时不需要改任何代码，只改环境变量或启动前 set。

查看当前生效的配置：
    .venv\\Scripts\\python.exe -m edge_gateway.config
"""
import os
from pathlib import Path

# 仓库根目录（本文件在 <root>/edge_gateway/config.py）
ROOT = Path(__file__).resolve().parents[1]


def _env_str(name, default):
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_int(name, default):
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是整数，当前值：{raw!r}") from exc


def _env_path(name, default):
    """环境变量给了相对路径时按当前工作目录解析，否则用默认绝对路径。"""
    raw = os.getenv(name)
    if raw in (None, ""):
        return Path(default)
    path = Path(raw)
    return path if path.is_absolute() else (Path.cwd() / path)


# ---- MQTT broker ----------------------------------------------------------
MQTT_BROKER = _env_str("MQTT_BROKER", "127.0.0.1")
MQTT_PORT = _env_int("MQTT_PORT", 1883)
MQTT_TOPIC = _env_str("MQTT_TOPIC", "pinglu/batches/+")
MQTT_CLIENT_ID = _env_str("MQTT_CLIENT_ID", "edge-gateway")
MQTT_KEEPALIVE = _env_int("MQTT_KEEPALIVE", 60)

# ---- 中心平台 -------------------------------------------------------------
CENTER_URL = _env_str("CENTER_URL", "http://127.0.0.1:8000").rstrip("/")

# ---- 本地持久化与预登记公钥 -----------------------------------------------
DB_PATH = _env_path("DB_PATH", ROOT / "edge_gateway" / "data" / "edge_cache.db")
REGISTRY_PATH = _env_path("REGISTRY_PATH", ROOT / "test_data" / "registry_public.json")

# ---- 重试与消息大小 -------------------------------------------------------
RETRY_INTERVAL = _env_int("RETRY_INTERVAL", 5)        # 转发重试轮询间隔（秒）
HTTP_TIMEOUT = _env_int("HTTP_TIMEOUT", 10)           # 单次 POST 超时（秒）
MAX_MESSAGE_BYTES = _env_int("MAX_MESSAGE_BYTES", 1048576)  # 单条 MQTT 消息上限


def describe():
    """返回当前生效的配置，便于联调时确认环境变量是否生效。"""
    return {
        "MQTT_BROKER": MQTT_BROKER,
        "MQTT_PORT": MQTT_PORT,
        "MQTT_TOPIC": MQTT_TOPIC,
        "MQTT_CLIENT_ID": MQTT_CLIENT_ID,
        "MQTT_KEEPALIVE": MQTT_KEEPALIVE,
        "CENTER_URL": CENTER_URL,
        "DB_PATH": str(DB_PATH),
        "REGISTRY_PATH": str(REGISTRY_PATH),
        "RETRY_INTERVAL": RETRY_INTERVAL,
        "HTTP_TIMEOUT": HTTP_TIMEOUT,
        "MAX_MESSAGE_BYTES": MAX_MESSAGE_BYTES,
    }


if __name__ == "__main__":
    for key, value in describe().items():
        print(f"{key} = {value}")
