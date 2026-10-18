# 密码模块接口与正常样例说明

## 1. 接口

导入位置：common_crypto.sm

| 函数 | 输入 | 返回 |
|---|---|---|
| sm3_hex(data) | bytes 类型的原文 | 64位十六进制 SM3 摘要字符串 |
| generate_keypair() | 无 | (private_key, public_key)，私钥在前、公钥在后 |
| sign_message(private_key, public_key, data) | 十六进制私钥、公钥及 bytes 原文 | SM2 签名字符串 |
| verify_message(public_key, data, signature) | 十六进制公钥、bytes 原文及签名字符串 | 验签通过为 True；验签不通过为 False |

签名使用 sign_with_sm3，验签使用 verify_with_sm3。
调用时传入规范化原文字节，不要先自行计算 SM3 再传入。

## 2. 批次验签示例

在项目根目录运行，使用预先确认的公钥登记表：

```python
import json
from pathlib import Path

from common_crypto.canonical import canonical_bytes, signed_header
from common_crypto.sm import verify_message

folder = Path("test_data/verified_wl001")
batch = json.loads(
    (folder / "normal_batch.json").read_text(encoding="utf-8")
)
registry = json.loads(
    (folder / "registry_public.json").read_text(encoding="utf-8")
)

public_key = registry[batch["device_id"]]["public_key"]
data = canonical_bytes(signed_header(batch))

verified = verify_message(public_key, data, batch["signature"])
print("批次签名验证：", verified)
```

期望结果：批次签名验证： True。

此示例只检查批次签名。完整验证还需检查合同字段、
记录摘要、摘要链、Merkle 根及历史连续性。

## 3. 交接文件

正常原文：
test_data/verified_wl001/normal_batch.json

配套公开登记表：
test_data/verified_wl001/registry_public.json

设备：WL-001。
批次：WL-001-000001。
序号：1～10。

B 制作异常样本时，应复制正常原文后修改，
保留原签名，不对篡改结果重新签名。

私钥和本机状态数据库不属于交接文件。

## 4. 已有验证证据

## 4. 已有验证证据

当前合并版本本地测试：45 passed，14 subtests passed，3 warnings。
正式 WL-001 样例此前已通过三端联调。
上述测试结果不代表百设备 P95 延迟指标已达标。