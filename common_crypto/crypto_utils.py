import secrets
from gmssl import sm2, sm3


def sm3_hex(data: bytes) -> str:
    """计算 SM3 摘要，返回十六进制字符串。"""
    return sm3.sm3_hash(list(data))


def _create_sm2(private_key="", public_key=""):
    """统一创建密码对象。"""
    obj = sm2.CryptSM2(private_key=private_key, public_key="")
    obj.public_key = public_key
    return obj


def generate_keypair():
    """生成密钥对，依次返回私钥、公钥。"""
    n = int(sm2.default_ecc_table["n"], 16)
    d = secrets.randbelow(n - 2) + 1
    private_key = f"{d:064x}"

    obj = _create_sm2(private_key=private_key)
    public_key = obj._kg(d, sm2.default_ecc_table["g"])
    return private_key, public_key


def sign_message(data: bytes, private_key: str, public_key: str):
    """使用 SM2 和 SM3 对数据签名。"""
    obj = _create_sm2(private_key, public_key)
    n = int(sm2.default_ecc_table["n"], 16)

    while True:
        k = secrets.randbelow(n - 1) + 1
        signature = obj.sign_with_sm3(data, f"{k:064x}")
        if signature is not None:
            return signature


def verify_message(data: bytes, signature: str, public_key: str):
    """使用公钥验签，通过返回 True，失败返回 False。"""
    obj = _create_sm2(public_key=public_key)
    try:
        return bool(obj.verify_with_sm3(signature, data))
    except (ValueError, TypeError, IndexError):
        return False