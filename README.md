# pinglu-crypto-system

## A端：设备模拟与密码模块

负责设备登记、SM2签名、SM3摘要链、Merkle树、批次生成及MQTT QoS 1发布。

支持水位、气象、航标、边坡、船闸、无人机、测量船七类设备；
100台测试表示扩展模拟设备数量，不是增加设备类型。

### 当前联调样例

当前使用以WL-001身份重新生成并签名的样例，其密钥沿用已验证的原WL-900密钥。

- 正常批次:`test_data/verified_wl001/normal_batch.json`
- 配套公开登记表:`test_data/verified_wl001/registry_public.json`

上述两个公开文件随仓库提供，可用于本地验签复现。
私钥和模拟器状态数据库不随仓库提供。

### 本地检查

在项目根目录、已安装项目依赖的Python环境中执行：

```powershell
python -m device_simulator.publish --batch-file .\test_data\verified_wl001\normal_batch.json --registry .\test_data\verified_wl001\registry_public.json --dry-run
```

预期显示本地验签与合同检查通过，批次为WL-001-000001，
主题为pinglu/batches/WL-001。

dry-run不连接Broker、不发送消息，显示未连接Broker属于预期行为。

运行前请激活项目虚拟环境；Windows也可将命令开头的
python 替换为 .\.venv\Scripts\python.exe。

### 独立生成设备数据

以下命令创建独立测试目录，生成七类设备各一台、每台三批数据，
共21批、210条记录。不连接 MQTT，不影响既有联调密钥和数据库。

```powershell
$runDir = Join-Path $env:TEMP ("pinglu-demo-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $runDir | Out-Null

python -m device_simulator.runner `
  --devices 7 `
  --rate 1 `
  --batch-size 10 `
  --rounds 3 `
  --keys-dir "$runDir\keys" `
  --registry "$runDir\registry_public.json" `
  --db-path "$runDir\simulator.db"

Write-Host "测试资料目录：$runDir"
```

预期每台设备生成批次000001～000003，序号分别为1～10、
11～20、21～30；状态为 MQTT=pending、中心=not_requested。

此目录使用新生成的测试密钥，不能直接替换真实联调的登记表。

### MQTT发布

先确认B已启动网关、加载配套公开登记表，并核实Broker当前IP。
以下命令中的地址为此前联调地址：

```powershell
python -m device_simulator.publish --batch-file .\test_data\verified_wl001\normal_batch.json --registry .\test_data\verified_wl001\registry_public.json --broker 172.24.76.45 --port 1883 --timeout 15
```

MQTT PUBACK只表示Broker已确认接收。
中心接收结果及网关任务完成状态需要另外核对。
当前联调应显式指定这两个文件，不使用默认--sample。

### 已验证结果

- 当前合并版本全套测试：45 passed、14 subtests passed，3条警告。
- 单设备重启续链：4批、40条记录检查通过。
- 100台设备三轮本地测试：300批、3000条记录，
  批次校验、记录摘要和跨批次摘要链检查全部通过。
- 正式WL-001样例已完成真实三端联调。
- 另一次百设备真实短测中，300批获得Broker确认，
  中心接受300批、保存3000条记录。

本地校验、真实短测和30分钟性能验收属于不同验证。
目前尚未确认百设备连续30分钟测试达标；P95延迟指标尚未达标，
不能将已有功能测试结果表述为性能验收通过。

更详细的A端验证记录见 `docs/a_crypto_simulation_notes.md`。