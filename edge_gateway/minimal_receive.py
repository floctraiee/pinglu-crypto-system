import json, os

def main():
    registry_path = "test_data/registry_public.json"
    batch_path = "test_data/normal_batch.json"

    if not os.path.exists(registry_path) or not os.path.exists(batch_path):
        print("❌ 找不到样本文件！请确认路径。"); return

    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)
        print(f"[OK] 注册文件加载成功，包含 {len(registry)} 个设备")

    with open(batch_path, "r", encoding="utf-8") as f:
        batch = json.load(f)

    print(f"device_id: {batch.get('device_id')}")
    print(f"batch_id:  {batch.get('batch_id')}")
    print(f"记录数:    {len(batch.get('records', []))} (count字段: {batch.get('count', 0)})")

if __name__ == "__main__":
    main()