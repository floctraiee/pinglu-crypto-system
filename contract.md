\# 三人共同接口约定



\## 1. 编码与序列化规则



\- 所有哈希及签名对象均使用 UTF-8 编码。

\- JSON 使用：

&#x20; - sort\_keys=True

&#x20; - ensure\_ascii=False

&#x20; - separators=(",", ":")

\- 测量值和经纬度使用格式化后的十进制字符串。

\- 时间统一带 +08:00 时区。



\## 2. 记录



记录必须包含：



\- version

\- device\_id

\- device\_type

\- timestamp

\- longitude

\- latitude

\- sequence

\- batch\_id

\- payload

\- status

\- previous\_hash

\- record\_hash



其中：



\- version=1

\- payload 为设备类型对应的字符串键值对象。



\## 3. 记录摘要与链



record\_hash：



SM3(canonical(record 去除 record\_hash))



设备第一条记录：



\- previous\_hash 为 64 个 0



后续每条记录：



\- previous\_hash 接上一条记录的 record\_hash



跨批次不断链。



\## 4. Merkle 树



\- 按 sequence 升序排列。

\- 叶节点：

&#x20; SM3(0x00 || bytes.fromhex(record\_hash))

\- 父节点：

&#x20; SM3(0x01 || 左子节点 || 右子节点)

\- 奇数节点时复制末节点。

\- Merkle 根使用 64 位十六进制表达。



\## 5. 批次



批次必须包含：



\- version

\- device\_id

\- batch\_id

\- start\_sequence

\- end\_sequence

\- count

\- start\_time

\- end\_time

\- merkle\_root

\- records

\- signature



batch\_id 格式：



设备 ID-六位批次号



每台设备的批次号递增。



\## 6. SM2 签名



签名对象只保留：



\- version

\- device\_id

\- batch\_id

\- 起止序号

\- count

\- 起止时间

\- merkle\_root



使用：



SM2 sign\_with\_sm3(canonical(上述批次字段))



signature 本身不参与签名。



设备私钥不得离开设备端。



\## 7. 设备身份



设备公钥预先登记：



device\_id -> public\_key



中心收到批次后，不能接受批次自行提供或替换信任公钥。



登记文件由 A 生成，B 和队长负责核对指纹。



\## 8. 中心返回



验证结果：



\- status=accepted

\- status=rejected

\- status=duplicate



同时返回：



\- reason\_code

\- device\_id

\- batch\_id

\- affected\_sequences



只有中心明确持久化结果后，网关才能标记任务已确认。



\## 9. 中心验证优先顺序



1\. 设备登记

2\. 批次签名

3\. 数量、起止时间和序号

4\. 每条摘要

5\. 链连接

6\. Merkle 根

7\. 历史重放



采用严格字段检查，不接受未约定字段。



验证失败时保留原始批次和审计证据。



已签名内容被修改后不得重新签名。



网关初验失败仍需向中心提交异常原文或独立审计事件，由中心独立复核。

