from gmssl import sm3

actual = sm3.sm3_hash(list(b"abc"))
expected = (
	"66c7f0f462eeedd9d1f2d46bdc10e4e2"
	"4167c4875cf2f7a2297da02b8f4ba8e0"
)

print("计算结果：", actual)
assert actual == expected, "SM3 验证失败"
print("SM3 验证通过")
