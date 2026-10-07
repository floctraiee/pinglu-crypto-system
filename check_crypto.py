from common_crypto.sm import (
    sm3_hex,
    generate_keypair,
    sign_message,
    verify_message,
)

assert sm3_hex(b"abc") == (
    "66c7f0f462eeedd9d1f2d46bdc10e4e2"
    "4167c4875cf2f7a2297da02b8f4ba8e0"
)

private_key, public_key = generate_keypair()
message = b"device_001|batch_001|water_level=3.25"

signature = sign_message(private_key, public_key, message)

assert verify_message(public_key, message, signature)
assert not verify_message(public_key, b"changed data", signature)

_, other_public_key = generate_keypair()
assert not verify_message(other_public_key, message, signature)

print("SM3 摘要：通过")
print("SM2 正常验签：通过")
print("修改数据检测：通过")
print("错误公钥检测：通过")
print("公共密码模块验证通过")