# pinglu-crypto-system

## A端：设备模拟与密码模块

负责设备登记、SM2签名、SM3摘要链、Merkle树、批次生成及MQTT QoS 1发布。

支持水位、气象、航标、边坡、船闸、无人机、测量船七类设备；
100台测试表示扩展模拟设备数量，不是增加设备类型。

### 当前联调样例

当前使用以WL-001身份重新生成并签名的样例，其密钥沿用已验证的原WL-900密钥。

- 正常批次：`test_data/migration_wl900_to_wl001/wl001_new_sample.json`
- 配套公开登记表：`test_data/migration_wl900_to_wl001/registry_public.json`

上述路径为A端本地资料，不随Git仓库提供。
在其他电脑复现该样例前，需要单独取得这两个公开文件。
私钥、模拟器状态数据库不随文件交接。

### 本地检查

在项目根目录、已安装项目依赖的Python环境中执行：

```powershell
python -m device_simulator.publish --batch-file .\test_data\migration_wl900_to_wl001\wl001_new_sample.json --registry .\test_data\migration_wl900_to_wl001\registry_public.json --dry-run
```

预期显示本地验签与合同检查通过，批次为WL-001-000001，
主题为pinglu/batches/WL-001。

dry-run不连接Broker、不发送消息，显示未连接Broker属于预期行为。

### MQTT发布

先确认B已启动网关、加载配套公开登记表，并核实Broker当前IP。
以下命令中的地址为此前联调地址：

```powershell
python -m device_simulator.publish --batch-file .\test_data\migration_wl900_to_wl001\wl001_new_sample.json --registry .\test_data\migration_wl900_to_wl001\registry_public.json --broker 172.24.76.45 --port 1883 --timeout 15
```

MQTT PUBACK只表示Broker已确认接收。
中心接收结果及网关任务完成状态需要另外核对。
当前联调应显式指定这两个文件，不使用默认--sample。

### 已验证结果

- 修改后全套测试：32 passed、14 subtests passed，3条弃用警告。
- 单设备重启续链：4批、40条记录检查通过。
- 100台设备三轮本地测试：300批、3000条记录。
- 上述100台测试的批次校验、记录摘要和跨批次摘要链检查全部通过。
- 已完成一次真实三端联调。

100台三轮测试未发送MQTT，不代表已完成100台运行30分钟、
真实断网恢复补传或端到端P95延迟测试。

更详细的A端验证记录见 `docs/a_crypto_simulation_notes.md`。