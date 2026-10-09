"""边缘网关：把签名批次可靠地从 MQTT 收到、初验、持久化并送到中心。

模块分工：
    config.py     全部可调项（broker / 中心 URL / SQLite 路径 / 重试与超时）
    storage.py    SQLite 持久队列 pending_batches 与网关侧可信链尾 device_chain
    validator.py  复用公共契约验证器 common_crypto.batch.verify_batch
    main.py       订阅 pinglu/batches/+，回调只快速入队，工作线程做校验与入库
    forward.py    逐设备按序转发到中心 POST /batches，按回复判定是否确认

启动：
    .venv\\Scripts\\python.exe -m edge_gateway.main
"""
