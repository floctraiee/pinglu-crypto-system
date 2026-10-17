"""生成《作品设计报告》初稿 DOCX。

所有实测数字均取自本仓库内的证据文件（CSV/JSON/TXT），文件头注释里列出对应文件。
匿名要求：正文不出现团队名称、成员姓名、单位、导师、联系方式；
封面只留作品名称与版本，实名信息按官方模板单独提交。
"""

from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parent
FIG_DIR = ROOT / "报告图_初稿"
OUT = ROOT / "作品设计报告_初稿_20261017.docx"

CN_BODY = "宋体"
CN_HEAD = "黑体"
EN_BODY = "Times New Roman"
INK = RGBColor(0x1B, 0x27, 0x33)
MUTED = RGBColor(0x55, 0x65, 0x75)
ACCENT = RGBColor(0x1F, 0x5F, 0xA8)

BODY_PT = 10.5   # 五号
TABLE_PT = 9


# ------------------------------------------------------------------ 基础工具
def set_run(run, size=BODY_PT, bold=False, cn=CN_BODY, en=EN_BODY, color=INK, italic=False):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    run.font.name = en
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    fonts.set(qn("w:ascii"), en)
    fonts.set(qn("w:hAnsi"), en)
    fonts.set(qn("w:eastAsia"), cn)
    return run


def para(doc, text="", size=BODY_PT, bold=False, color=INK, align=None,
         space_before=0, space_after=6, first_indent=None, line=1.5, cn=CN_BODY):
    p = doc.add_paragraph()
    fmt = p.paragraph_format
    fmt.space_before = Pt(space_before)
    fmt.space_after = Pt(space_after)
    fmt.line_spacing = line
    if first_indent is not None:
        fmt.first_line_indent = Cm(first_indent)
    if align is not None:
        p.alignment = align
    if text:
        set_run(p.add_run(text), size=size, bold=bold, color=color, cn=cn)
    return p


def heading(doc, text, level=1):
    sizes = {1: 15, 2: 12.5, 3: 11}
    before = {1: 16, 2: 12, 3: 8}
    after = {1: 8, 2: 6, 3: 4}

    p = doc.add_paragraph()
    p.style = doc.styles[f"Heading {level}"]
    fmt = p.paragraph_format
    fmt.space_before = Pt(before[level])
    fmt.space_after = Pt(after[level])
    fmt.line_spacing = 1.35
    fmt.keep_with_next = True
    set_run(p.add_run(text), size=sizes[level], bold=True, cn=CN_HEAD, color=INK)
    return p


def caption(doc, text):
    return para(doc, text, size=9, color=MUTED, align=WD_ALIGN_PARAGRAPH.CENTER,
                space_before=2, space_after=10)


def bullet(doc, text, size=BODY_PT):
    p = doc.add_paragraph(style="List Bullet")
    fmt = p.paragraph_format
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(3)
    fmt.line_spacing = 1.45
    fmt.left_indent = Cm(0.75)
    set_run(p.add_run(text), size=size)
    return p


def shade(cell, hex_color):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def table(doc, headers, rows, widths=None, align_center=True, header_fill="EAF0F7"):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False

    hdr = t.rows[0].cells
    for index, text in enumerate(headers):
        hdr[index].text = ""
        p = hdr[index].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        set_run(p.add_run(str(text)), size=TABLE_PT, bold=True, cn=CN_HEAD)
        shade(hdr[index], header_fill)

    for row in rows:
        cells = t.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = ""
            p = cells[index].paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if (align_center and index > 0) \
                else WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.2
            set_run(p.add_run(str(value)), size=TABLE_PT)

    if widths:
        for index, width in enumerate(widths):
            for row in t.rows:
                row.cells[index].width = Cm(width)

    # 表后补充空段落，避免与后续正文贴太近
    para(doc, "", size=6, space_after=2)
    return t


def figure(doc, filename, width_cm=16.0):
    path = FIG_DIR / filename
    if not path.exists():
        para(doc, f"（缺图：{filename}）", size=9, color=MUTED)
        return
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(str(path), width=Cm(width_cm))


def add_page_number_footer(section):
    footer = section.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run(p.add_run("— "), size=9, color=MUTED)
    run = p.add_run()
    set_run(run, size=9, color=MUTED)
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_end)
    set_run(p.add_run(" —"), size=9, color=MUTED)


# ------------------------------------------------------------------ 文档
doc = Document()

normal = doc.styles["Normal"]
normal.font.name = EN_BODY
normal.font.size = Pt(BODY_PT)
normal.element.rPr.rFonts.set(qn("w:eastAsia"), CN_BODY)

section = doc.sections[0]
section.page_width = Cm(21.0)
section.page_height = Cm(29.7)
section.top_margin = Cm(2.4)
section.bottom_margin = Cm(2.2)
section.left_margin = Cm(2.6)
section.right_margin = Cm(2.4)
add_page_number_footer(section)

# 关闭文档网格：python-docx 默认模板带 <w:docGrid linePitch="360">，
# 部分渲染器会按字符网格排版，导致正文整体右移、右边距失效。
_sect = section._sectPr
for _grid in _sect.findall(qn("w:docGrid")):
    _grid.set(qn("w:type"), "none")
    _grid.set(qn("w:linePitch"), "360")
    if _grid.get(qn("w:charSpace")) is not None:
        del _grid.attrib[qn("w:charSpace")]

# ------------------------------------------------------------------ 封面
para(doc, "", space_after=40)
para(doc, "平陆运河多源感知数据", size=24, bold=True, cn=CN_HEAD,
     align=WD_ALIGN_PARAGRAPH.CENTER, space_after=4, line=1.3)
para(doc, "国密可信采集与篡改溯源系统", size=24, bold=True, cn=CN_HEAD,
     align=WD_ALIGN_PARAGRAPH.CENTER, space_after=18, line=1.3)
para(doc, "作 品 设 计 报 告", size=16, bold=False, cn=CN_HEAD,
     align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10, line=1.3, color=ACCENT)
para(doc, "（初稿 · 待补官方模板要求的实名信息与签字页）", size=10, color=MUTED,
     align=WD_ALIGN_PARAGRAPH.CENTER, space_after=70)

table(doc,
      ["项目", "内容"],
      [
          ["文档版本", "v0.9（初稿）"],
          ["编制日期", "2026年10月17日"],
          ["文档状态", "技术内容与实测数据已齐；待补作品编号、实名信息、签字页"],
          ["数据来源", "本仓库内的实测证据文件（CSV / JSON / 日志），见附录 B"],
          ["匿名要求", "正文不含团队名称、成员姓名、单位、导师与联系方式"],
      ],
      widths=[3.6, 11.4], align_center=False)

para(doc, "", space_after=30)
para(doc,
     "本报告所有性能与准确性数字均来自真实执行的测试脚本输出，"
     "并逐项标注对应的证据文件名与统计口径；未执行或未收齐的项目在正文中明确标为待验证，"
     "不作估算、不以目标值代替实测值。",
     size=9.5, color=MUTED, align=WD_ALIGN_PARAGRAPH.JUSTIFY, first_indent=0.74)

doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

# ------------------------------------------------------------------ 目录
heading(doc, "目　录", level=1)
toc_items = [
    ("摘　要", 0),
    ("1　项目背景与目标", 0),
    ("2　系统总体设计", 0),
    ("3　密码学设计与实现", 0),
    ("4　核心功能实现", 0),
    ("5　测试方案", 0),
    ("6　实测结果", 0),
    ("7　局限与待完善", 0),
    ("8　结论", 0),
    ("附录 A　可复现的运行与测试命令", 0),
    ("附录 B　证据文件索引", 0),
]
for text, _ in toc_items:
    para(doc, text, size=11, space_after=5, line=1.6)

doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

# ------------------------------------------------------------------ 摘要
heading(doc, "摘　要", level=1)

para(doc,
     "针对内河航运多源感知数据在采集、传输与汇聚环节缺乏可验证完整性保障的问题，"
     "本作品设计并实现了一套“设备端签名—边缘网关初验与缓存—中心平台独立重验与溯源”的"
     "三层可信采集系统。设备端对每条感知记录计算 SM3 摘要并串联成链，按批次构造 Merkle 树，"
     "用 SM2 对仅含九个字段的批次头签名；中心平台不采信网关的通过结论，"
     "从自身设备登记表取预登记公钥独立重做全部密码学校验，"
     "并以单事务完成入库、以审计表留存全部异常证据。系统对外提供单条记录的 Merkle 证明"
     "与签名复核接口，使“某条记录确实处于已签名批次之下、且未被篡改”可被第三方独立验证。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

para(doc,
     "测试方面，本作品把“能否拦住异常、能否定位到记录、会不会误伤正常数据”"
     "三项能力量化为检测率、定位准确率与误报率，并配套可复现的样本生成与评分脚本；"
     "同时以 100 台设备、每台每秒 1 条、连续 30 分钟为目标规模完成压力测试。"
     "实测结果如下：",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

for text in [
    "检测能力：14 个标注用例覆盖 11 种错误码，严格检测率 100%（9/9），"
    "定位准确率 100%（5/5），误报率 0%（0/3）；",
    "签名性能：同一批 100 条记录、按每批 1/10/20 条三种粒度，"
    "单次签名均值 5.17/5.19/5.23 ms，单次验签均值 8.34/8.38/8.42 ms，"
    "报文大小 832/4489/8559 字节；",
    "压力测试：100 台设备、逻辑 30 分钟、合计 9000 批 180000 条记录全部被中心接受，"
    "0 拒绝、0 传输失败；中心侧端到端延迟 P95 为 79.21 ms；",
    "端到端延迟：“网关收到批次至中心给出终态”的 P95 为 109 ms，"
    "中心侧独立口径 P95 为 79.21 ms，两条链路均在 1 秒目标内。",
]:
    bullet(doc, text)

para(doc,
     "上述指标中，检测率与误报率目前基于本项目自建的标注样本，"
     "尚未替换为跨角色独立提供的异常样本与真实标签；三机局域网部署的闭环证据亦有待补齐。"
     "相关局限已在第 7 章逐条列明。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

para(doc, "", space_after=2)
para(doc, "关键词：国密算法；SM2；SM3；Merkle 树；可信采集；篡改溯源；独立验证",
     bold=True, size=BODY_PT)

doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

# ------------------------------------------------------------------ 1
heading(doc, "1　项目背景与目标", level=1)

heading(doc, "1.1　应用背景", level=2)
para(doc,
     "平陆运河沿线的水位、气象、航标、边坡、船闸、无人机与测量船等多源感知设备，"
     "持续产生时效性强、来源分散的观测数据。这些数据在进入上层应用前要经过设备、"
     "边缘网关与中心平台多级汇聚，任一环节都可能出现取值被改动、记录被删除、"
     "批次被整体替换或历史批次被重放等风险。传统做法依赖传输层加密与网关自检，"
     "其缺陷是：一旦数据落到网关之后，中心无法独立判断“这份数据是否仍是设备当初签署的那一份”。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

heading(doc, "1.2　建设目标", level=2)
for text in [
    "采集可信：设备端完成摘要、Merkle 根与 SM2 签名，私钥不出设备端；",
    "验证独立：中心从自身登记表取公钥，独立重做全部密码学校验，不采信网关结论；",
    "异常可溯：任何被拒批次都保留原始报文与判定结果，页面可见、可复核；",
    "单条可证：对任一已入库记录，可输出 Merkle 证明路径并当场复验；",
    "规模可用：满足 100 台设备、每台每秒 1 条、连续 30 分钟的吞吐与延迟要求。",
]:
    bullet(doc, text)

heading(doc, "1.3　关键约束", level=2)
para(doc,
     "为保证多人协作下三端行为一致，本项目以一份接口契约为唯一事实来源，"
     "锁定以下约束：所有哈希与签名对象按 UTF-8 编码；JSON 序列化固定使用"
     "键排序、非 ASCII 不转义、无多余空白的分隔符；测量值与经纬度使用格式化十进制字符串；"
     "时间统一携带 +08:00 时区；字段如有调整，必须在同一次提交内同步修改公共模型、"
     "三端代码、样本与测试。验证优先次序固定为：设备登记 → 批次签名 → 数量与起止序号 → "
     "每条记录摘要 → 摘要链连接 → Merkle 根 → 历史重放。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

# ------------------------------------------------------------------ 2
heading(doc, "2　系统总体设计", level=1)

heading(doc, "2.1　三层架构与数据流", level=2)
para(doc,
     "系统分为设备端、边缘网关、中心平台三层，另有面向验证结果的网页展示层。"
     "三层职责与数据流向如图 1 所示：设备端只负责“把数据签对”，网关只负责“先接住、"
     "按序送”，中心负责“独立判断并留证”。这样的划分使得任何一层都无需信任上一层——"
     "网关的初验结论仅作为参考信息展示，不参与中心的判定。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

figure(doc, "fig1_dataflow.png", 16.0)
caption(doc, "图 1　系统数据流与三层职责（含中心独立验证优先次序与四类持久化结果）")

heading(doc, "2.2　硬件部署与配置方式", level=2)
para(doc,
     "系统支持单机四终端联调与三机局域网部署两种形态。单机形态下依次启动消息代理、"
     "中心平台、边缘网关与设备模拟器；三机形态下中心以监听全部网卡的方式启动，"
     "设备端的消息代理地址指向网关所在主机，网关的中心地址指向中心所在主机，"
     "全部差异仅通过配置项或同名环境变量表达，不需要修改任何代码。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

figure(doc, "fig2_deployment.png", 16.0)
caption(doc, "图 2　三机部署形态与关键配置项")

table(doc,
      ["层", "主要进程 / 模块", "关键职责"],
      [
          ["设备端", "device_simulator（七类设备生成、批组、签名、发布）",
           "生成记录、计算摘要链与 Merkle 根、SM2 签名、按 QoS1 发布"],
          ["边缘网关", "mosquitto + edge_gateway（订阅、初验、本地缓存、按序补传）",
           "消息快速入队、契约初验、原文落库、按批次号升序转发、失败保留待传"],
          ["中心平台", "center_platform（FastAPI + SQLite + 网页）",
           "独立重验、单事务入库、审计留证、幂等去重、Merkle 证明与验签复核"],
      ],
      widths=[2.4, 6.6, 6.6], align_center=False)
caption(doc, "表 1　三层职责划分")

heading(doc, "2.3　一次批次提交的完整流程", level=2)
para(doc,
     "图 3 给出从设备生成到中心落定的完整流程。需要强调的是其中两条不可逆约束："
     "其一，已签名内容不得重签——只要改动了记录取值，要么该条记录摘要对不上，"
     "要么批次签名的 Merkle 根对不上，二者都会被明确报出；"
     "其二，中心不依赖网关——即使网关被判为“初验通过”，中心仍会独立重做全部校验。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

figure(doc, "fig3_flow.png", 16.0)
caption(doc, "图 3　一次批次提交的完整流程与四条关键约束")

heading(doc, "2.4　数据模型", level=2)
para(doc,
     "中心平台使用 SQLite 承载四张业务表。设计要点是：批次表以“设备号 + 批次号”建立唯一约束，"
     "从数据库层面保证幂等；记录表以“设备号 + 批次号 + 序号”建立唯一约束；"
     "审计表不设外键指向业务数据，确保被拒批次的数据即使未入库也能独立留存。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

table(doc,
      ["表名", "关键字段", "作用"],
      [
          ["devices", "device_id（主键）、device_type、public_key、fingerprint、经纬度",
           "设备身份与预登记公钥，是中心唯一的信任来源"],
          ["batches", "device_id + batch_id（唯一）、header、raw、signature、status、received_at",
           "已接受批次的签名头与原始报文，供证明与复核使用"],
          ["records", "device_id + batch_id + sequence（唯一）、record_hash、previous_hash、record",
           "逐条记录与其摘要，支撑链校验与按序号定位"],
          ["audits", "device_id、batch_id、status、reason_code、affected_sequences、raw",
           "全部拒绝与重复事件的原因、受影响序号与原始报文"],
      ],
      widths=[2.2, 8.2, 5.2], align_center=False)
caption(doc, "表 2　中心平台数据模型")

heading(doc, "2.5　对外接口", level=2)
para(doc,
     "中心平台提供 8 个必需接口与 4 个只读辅助接口，并把审计查询与网关闭环结论一并返回。"
     "前者覆盖健康检查、批次提交、设备/批次/记录/审计查询、单条 Merkle 证明与统计；"
     "后者用于离线补传状态、性能对照数据、原始批次回读与签名复核。"
     "所有接口均有在线文档，可直接在页面上提交一批 JSON 完成手工验证。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

table(doc,
      ["接口", "方法", "用途"],
      [
          ["/health", "GET", "健康检查"],
          ["/batches", "POST", "接收批次并返回 accepted / rejected / duplicate"],
          ["/devices、/batches、/records、/audits、/stats", "GET", "登记表、批次、记录、审计与统计查询"],
          ["/batches/{id}/proof/{sequence}", "GET", "单条 Merkle 证明：叶摘要、路径、根与复验结果"],
          ["/batch-raw", "GET", "按设备号与批次号回读原始批次，供复核签名"],
          ["/verify-signature", "POST", "用中心预登记公钥复核批次签名"],
          ["/offline-status", "GET", "离线补传：缓存数、断点、补传进度与实时延迟"],
          ["/performance", "GET", "签名批次对照数据及其来源文件信息"],
      ],
      widths=[6.6, 1.8, 7.2], align_center=False)
caption(doc, "表 3　中心平台接口清单")

# ------------------------------------------------------------------ 3
heading(doc, "3　密码学设计与实现", level=1)

heading(doc, "3.1　规范化编码：所有密码学操作的前提", level=2)
para(doc,
     "签名与摘要只有在“参与运算的字节完全一致”时才可复现。因此本项目只保留一处 JSON 编码实现，"
     "固定为：UTF-8 编码、键按字典序排序、非 ASCII 字符不转义、分隔符不含空白、禁止 NaN。"
     "所有摘要与签名都作用于该编码输出的字节串，避免不同语言或不同脚本因键顺序、"
     "空格或转义差异导致“本地通过、对方不通过”。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

heading(doc, "3.2　SM3 与记录摘要链", level=2)
para(doc,
     "每条记录的摘要按下式计算：对记录对象去除 record_hash 字段本身后做规范化编码，"
     "再取 SM3 摘要。每条记录的 previous_hash 指向同设备上一条记录的摘要，"
     "设备的首条记录以 64 个 0 作为前驱，跨批次不断链。由此形成一条只可追加、"
     "不可局部改写的记录链：任意一条记录的取值被改动，其自身摘要即不匹配；"
     "若同时刷新自身摘要，则后一条记录的前驱摘要不再匹配，断链位置即被精确定位。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

heading(doc, "3.3　Merkle 树与单条记录证明", level=2)
para(doc,
     "批次内的记录按序号升序构树：叶节点为 SM3(0x00 与记录摘要字节的拼接)，"
     "父节点为 SM3(0x01 与左右子节点摘要的拼接)，奇数层复制末节点，根以 64 位十六进制表示。"
     "叶子与内节点使用不同前缀字节，可防止把内节点当作叶子进行伪造。"
     "之所以采用 Merkle 树而非重新计算整批摘要，是为了支持“单条证明”："
     "只需提供该记录摘要、各层兄弟节点及其方向，即可在 O(log n) 个哈希内复算出根，"
     "与批次签名中锁定的根比对一致，即可证明该记录属于该已签名批次而无需暴露整批数据。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

heading(doc, "3.4　SM2 批次签名与签名范围", level=2)
para(doc,
     "批次签名只覆盖批次头部的九个字段：版本号、设备号、批次号、起止序号、记录条数、"
     "起止时间与 Merkle 根，不含记录数组本身，也不含签名字段。"
     "这一设计基于两点考虑：其一，记录内容已由 Merkle 根间接锁定，签名根等价于签名整批记录；"
     "其二，签名对象保持定长，使签名开销与批次大小解耦（实测见 6.3 节）。"
     "相应地，改动记录而不改动根、或改动根而不重新签名，都会在校验中被发现。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

heading(doc, "3.5　验证优先次序与错误码", level=2)
para(doc,
     "中心按固定次序校验，一旦某层失败即停止并给出该层的错误码，"
     "避免“多处同时出错”时给出误导性的结论。次序与对应错误码如表 4。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

table(doc,
      ["次序", "校验内容", "失败时的错误码", "受影响序号"],
      [
          ["1", "批次顶层字段集合是否为约定的 11 个字段", "INVALID_BATCH_STRUCTURE", "无"],
          ["2", "设备是否已在中心登记", "UNKNOWN_DEVICE", "无"],
          ["3", "批次头 SM2 签名是否有效", "INVALID_SIGNATURE", "无"],
          ["4", "记录条数与起止序号是否自洽", "INVALID_BATCH_RANGE", "不一致的序号"],
          ["5", "逐条记录摘要是否匹配", "INVALID_RECORD_HASH", "被改动的序号"],
          ["6", "记录摘要链是否连续", "INVALID_HASH_CHAIN", "断链处及其后续"],
          ["7", "重算 Merkle 根是否等于签名中的根", "INVALID_MERKLE_ROOT", "无（批次级）"],
          ["8", "序号是否接续中心可信链尾", "INVALID_SEQUENCE", "整批序号"],
          ["9", "批次号与时间是否严格递增", "INVALID_BATCH_SEQUENCE / INVALID_TIMESTAMP", "无"],
          ["10", "同批次号内容是否一致", "DUPLICATE_BATCH / BATCH_CONTENT_CONFLICT", "无"],
      ],
      widths=[1.0, 6.4, 6.0, 3.0], align_center=False)
caption(doc, "表 4　验证优先次序与错误码")

para(doc,
     "需要说明的是，第 6 项断链与第 4 项缺记录都会产生“级联”的受影响序号："
     "被改动记录之后、直到链重新自洽的连续区间，都会被如实列入。"
     "这是链式结构的正确行为，而非定位错误；报告 6.2 节的定位标签即按此语义标注。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

# ------------------------------------------------------------------ 4
heading(doc, "4　核心功能实现", level=1)

heading(doc, "4.1　设备端：生成、组批与签名", level=2)
para(doc,
     "设备端为七类设备提供统一的记录生成与批组能力，每类设备的载荷字段不同，"
     "但记录的公共字段、摘要规则与签名范围完全一致。生成过程支持按设备号与序号确定性复现，"
     "便于在报告中给出可重复的样本。批次号采用“设备号－六位序号”形式逐批递增；"
     "设备状态（链尾摘要、已用序号、批次号）持久化在本地库中，进程重启后继续推进，"
     "保证跨批次不断链。设备私钥仅保存在设备侧私钥目录，不进入版本库，也不随批次上传。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

heading(doc, "4.2　边缘网关：接住、初验、按序补传", level=2)
para(doc,
     "网关的订阅回调只做一件事——记录接收时刻并入内存队列，解析、初验与落库全部在后台线程完成，"
     "避免慢操作阻塞消息接收。后台流程为：限制报文长度、解析 JSON、识别设备与批次、"
     "从预登记文件取公钥做契约初验、把原文与初判一并写入本地库。"
     "转发环节按“同一设备按批次号升序、前一批未获得中心终态则后续不抢跑”的规则推进；"
     "只有中心明确返回接受、或返回“内容相同的重复”时，才把该批标记为已确认；"
     "网络失败与超时一律保留为待传，由重试节拍与“入库即唤醒”的事件驱动机制共同回收。"
     "初验未通过的报文不会被丢弃，而是作为待复核对象一并送交中心。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

heading(doc, "4.3　中心平台：独立重验、单事务入库与留证", level=2)
para(doc,
     "中心对每批数据的处理在同一个写事务内完成，顺序为：读取设备预登记公钥 → "
     "按固定次序完成表 4 的全部校验 → 通过则在同一事务内写入批次与其全部记录 → "
     "不通过则写入审计表并返回拒绝。把“查重—读链尾—校验—落库”放在一个事务内的原因，"
     "是为了避免并发提交时读到过期的链尾而误判；同时，只有事务提交后才向网关返回终态，"
     "使“网关认为已确认”与“中心确实已持久化”严格等价。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

para(doc,
     "对于重复提交，中心比对规范化编码后的完整报文：完全一致返回重复并且不重复计数，"
     "内容不同则记为冲突审计，两种情况都不会覆盖已入库数据。"
     "对于被拒批次，审计记录保存错误码、受影响序号区间与完整原始报文，"
     "使异常在网页上可见、可回溯复核。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

heading(doc, "4.4　网页：六块验证视图", level=2)
para(doc,
     "网页围绕“让人相信验证真的发生了”组织为六块，全部数据来自真实接口与数据库，不含写死的演示文本：",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

table(doc,
      ["区块", "内容", "数据来源"],
      [
          ["① 总览", "设备→网关→中心→溯源四段数据流、六项统计、通过/拒绝/重复分类",
           "统计、设备、批次、审计接口"],
          ["② 七类感知数据", "按设备类型分卡展示登记数、批次数、记录数与最新载荷，支持三级筛选",
           "记录与设备接口"],
          ["③ 异常审计", "设备、批次、受影响序号、异常原因、边缘判定、中心复核、时间、原文",
           "审计接口（关联网关初验结论）"],
          ["④ 批次细节与证明", "选择批次与序号后展示叶摘要、逐层兄弟节点与方向、签名根、验签结果",
           "证明接口与签名复核接口"],
          ["⑤ 离线恢复", "缓存批次数、待补传、补传进度、逐设备断点与待补传区间、实时延迟 P95",
           "网关缓存库（只读）"],
          ["⑥ 性能对比", "每批 1/10/20 条的签名次数、均值、P95、报文大小，并标注来源文件与日期",
           "签名对照数据接口"],
      ],
      widths=[2.6, 8.4, 4.6], align_center=False)
caption(doc, "表 5　网页六块的内容与数据来源")

para(doc,
     "在线期间页面每 5 秒刷新一次；当中心不可达时，页面保留最后一次成功读取的快照，"
     "并以明显的方式标注“以下为断网前快照、非实时”，避免在中心离线时仍宣称数据实时。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

# ------------------------------------------------------------------ 5
heading(doc, "5　测试方案", level=1)

heading(doc, "5.1　测试分层", level=2)
table(doc,
      ["层次", "验证对象", "判定方式"],
      [
          ["密码学单元", "摘要、摘要链、Merkle 根与证明、SM2 签名验签、七类载荷",
           "固定向量与往返一致性断言"],
          ["契约集成", "字段集合、错误码、拒绝与冲突、并发提交、跨批次续链", "逐条断言期望错误码"],
          ["中心端到端", "合法入库、未知设备、改值、删记录、伪造签名、重复、证明路径",
           "接口返回与数据库状态双向核对"],
          ["网关链路", "订阅、初验、缓存、按序转发、终态判定、延迟统计", "转发结果与本地队列状态"],
          ["页面接口", "六块所用接口的可用性与数据自洽", "脚本化断言，输出通过项与失败项"],
          ["规模与性能", "100 台 30 分钟压力、签名批次大小对照", "吞吐、延迟分位、报文大小"],
      ],
      widths=[2.4, 8.0, 5.2], align_center=False)
caption(doc, "表 6　测试分层")

heading(doc, "5.2　异常样本与真实标签", level=2)
para(doc,
     "为把“能否拦住、能否定位、会不会误伤”量化为可比指标，本项目提供样本生成脚本，"
     "以固定种子确定性地产出带真实标签的用例集。每个用例除完整批次报文外，还标注："
     "期望是否被拦截、可接受的终态集合、可接受的错误码集合、应被定位的序号集合，"
     "以及是否属于“歧义用例”（即因校验次序可能给出另一种同样合理的错误码）。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

table(doc,
      ["用例类型", "构造方式", "期望结果"],
      [
          ["合法批次", "从可信链尾正常续接", "accepted"],
          ["原样重复", "重复提交同一批次", "duplicate / DUPLICATE_BATCH"],
          ["改测量值", "改动一条记录的载荷且不重算摘要", "INVALID_RECORD_HASH，定位该序号"],
          ["改数量", "改动记录条数后重签", "INVALID_BATCH_RANGE"],
          ["断链", "改动一条记录的前驱摘要并重算", "INVALID_HASH_CHAIN，定位断链区间"],
          ["删记录", "删去中间一条记录", "INVALID_BATCH_RANGE / INVALID_SEQUENCE"],
          ["伪造签名", "签名置零", "INVALID_SIGNATURE"],
          ["改值并重算摘要", "改末条载荷并重算该条摘要后重签", "INVALID_MERKLE_ROOT"],
          ["未登记设备", "把设备号改为未登记值", "UNKNOWN_DEVICE"],
          ["序号跳号", "整批序号跳过若干号", "INVALID_SEQUENCE，定位整批序号"],
          ["历史重放", "复用已入库批次号但内容不同", "BATCH_CONTENT_CONFLICT"],
          ["未约定字段", "在批次顶层加入额外字段", "INVALID_BATCH_STRUCTURE"],
      ],
      widths=[3.0, 7.6, 5.0], align_center=False)
caption(doc, "表 7　异常样本类型与期望结果")

heading(doc, "5.3　指标定义与统计口径", level=2)
para(doc,
     "把“非正常样本”视为应被拦截的正类，把正常样本视为负类，三项指标定义如下：",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

for text in [
    "检测率：被正确判为拒绝或重复的异常样本数 ÷ 全部异常样本数。"
    "排除歧义用例的口径记为“严格检测率”，包含歧义用例的口径记为“宽松检测率”，两者同时报告。",
    "定位准确率：受影响序号集合与标签完全一致的异常样本数 ÷ 全部考察定位的异常样本数。"
    "采用严格集合相等判定，不做部分匹配。",
    "误报率：被误判为拒绝或重复的正常样本数 ÷ 全部正常样本数。",
]:
    bullet(doc, text)

para(doc,
     "延迟口径需要区分两段：中心侧的“端到端延迟”指从请求发出到中心返回持久化结果；"
     "网关侧的“实时延迟”指从网关收到批次到中心给出终态这两个时刻之差，"
     "该值由网关在本地库中记录，按批次统计分位。两条链路分别报告，不混用。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

heading(doc, "5.4　实验环境与可复现性", level=2)
para(doc,
     "实验在 Windows 单机环境完成，Python 3.14，密码运算依赖固定的国密算法库版本，"
     "依赖版本全部锁定。所有测试脚本均可从仓库根目录直接运行，"
     "运行时输出通过项计数并把证据落盘为 CSV / JSON / 文本；"
     "报告中每个数字都标注对应证据文件名（见附录 B），可按附录 A 的命令重新生成。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

para(doc,
     "为避免测试互相污染，验证脚本均使用独立的临时库文件，并在启动前自检库路径，"
     "一旦发现指向正式库即拒绝运行；中心库路径、网关缓存库路径与设备登记表路径均可通过环境变量切换，"
     "这也是三机部署时切换数据源的方式。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

# ------------------------------------------------------------------ 6
heading(doc, "6　实测结果", level=1)

heading(doc, "6.1　功能与接口验证", level=2)
table(doc,
      ["验证项", "范围", "结果", "证据文件"],
      [
          ["中心功能冒烟", "14 项接口与校验行为", "14/14 通过", "验证结果_中心功能冒烟_20261014.txt"],
          ["页面接口自洽", "六块所需接口与数据一致性", "26/26 通过", "验证结果_页面接口_20261014.txt"],
          ["单条证明可复验", "全部已入库记录逐条复验", "逐条通过", "同上"],
          ["签名复核", "正确公钥通过、篡改与错误公钥均失败", "符合预期", "同上"],
          ["网关至中心打通", "真实签名批次经网关转发至中心", "3/3 接受，0 拒绝", "验证结果_端到端打通_20261014.txt"],
      ],
      widths=[3.2, 5.2, 3.2, 5.0], align_center=False)
caption(doc, "表 8　功能与接口验证结果")

heading(doc, "6.2　检测能力", level=2)
table(doc,
      ["指标", "口径", "实测值", "分子/分母"],
      [
          ["严格检测率", "排除歧义用例", "100.00%", "9/9"],
          ["宽松检测率", "包含歧义用例", "100.00%", "11/11"],
          ["状态判定命中率", "终态与标签一致", "100.00%", "11/11"],
          ["错误码命中率", "错误码落在可接受集合内", "100.00%", "11/11"],
          ["定位准确率", "受影响序号集合完全一致", "100.00%", "5/5"],
          ["误报率", "正常样本被误拦", "0.00%", "0/3"],
          ["正常样本放行率", "正常样本被接受", "100.00%", "3/3"],
      ],
      widths=[3.6, 6.0, 3.0, 3.0], align_center=False)
caption(doc, "表 9　检测能力实测（用例集：14 个，覆盖 11 种错误码；证据文件见附录 B）")

para(doc,
     "需要强调三点口径说明。第一，表 9 的样本由本项目脚本生成并自带标签，"
     "尚未替换为跨角色独立提供的异常样本，因此该结果说明“系统实现与本项目定义的标签一致”，"
     "不等同于独立第三方评测结论。第二，断链与缺记录两类用例会产生级联受影响序号，"
     "标签按中心的实际语义标注为连续区间；其中被标为歧义的用例不计入严格检测率。"
     "第三，误报率的分母仅为 3 个正常样本，样本量偏小，结论的统计强度有限。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

heading(doc, "6.3　签名批次大小对照", level=2)
para(doc,
     "以同一批 100 条记录为原料，按每批 1 条、10 条、20 条三种粒度分别组批并测量，"
     "每个粒度累计执行 200 次以上真实签名与验签。结果如表 10。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

table(doc,
      ["每批条数", "批次数", "签名次数", "签名均值(ms)", "签名P95(ms)", "验签均值(ms)", "验签P95(ms)", "报文大小(B)"],
      [
          ["1", "100", "300", "5.166", "5.554", "8.340", "8.808", "832"],
          ["10", "10", "230", "5.191", "5.587", "8.375", "8.857", "4489"],
          ["20", "5", "220", "5.226", "5.734", "8.419", "8.946", "8559"],
      ],
      widths=[1.8, 1.6, 1.9, 2.2, 2.2, 2.2, 2.2, 2.1])
caption(doc, "表 10　签名批次大小对照（同一批 100 条记录；证据文件 benchmark_signing_20261014.csv）")

para(doc,
     "结论是三段式的。第一，单次签名与验签耗时基本不随批次大小变化——"
     "因为签名对象是固定长度的批次头九字段，不含记录数组，"
     "批次大小改变的是报文体积与批次数，而不是单次签名的工作量。"
     "第二，真正随批次大小变化的是“每 100 条记录需要签名多少次”与“报文总量”："
     "逐条签名需要 100 次签名与约 83 KB 报文，每批 20 条只需 5 次签名，"
     "但报文总量因每批重复携带头部与签名而上升。第三，因此工程上的取舍不是“批次越小越快”，"
     "而是在延迟与签名开销之间按设备出数速率选择批次大小。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

heading(doc, "6.4　100 台设备 30 分钟压力测试", level=2)
para(doc,
     "压力测试按目标规模组织：100 台设备，每台每秒产生 1 条记录，逻辑时长 30 分钟，"
     "即每台 1800 条、合计 180000 条。按每批 20 条切分为 9000 批，逐批完成签名后提交中心。"
     "由于正式登记表只含少量设备，规模测试使用脚本在临时目录内生成“正式设备 + 合成设备”共 100 台的"
     "临时登记表、私钥与干净中心库，不触碰正式数据。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

table(doc,
      ["统计项", "实测值", "说明"],
      [
          ["设备规模 / 逻辑时长", "100 台 / 30 分钟", "每台每秒 1 条，每批 20 条"],
          ["设备端生成记录数", "180000", "全部完成签名，生成阶段净耗时 413.43 s"],
          ["提交批次数", "9000", "提交阶段耗时 614.2 s，吞吐约 14.65 批/秒"],
          ["中心接受批次", "9000", "0 拒绝、0 重复、0 传输失败"],
          ["中心入库记录数", "180000", "与生成数一致，无丢批"],
          ["端到端延迟 均值", "68.14 ms", "请求发出至中心返回持久化结果"],
          ["端到端延迟 P50 / P95", "71.26 ms / 79.21 ms", "P95 为报告主要指标"],
          ["端到端延迟 P99 / 最大", "84.77 ms / 151.47 ms", "反映尾部抖动"],
      ],
      widths=[4.4, 5.0, 6.2], align_center=False)
caption(doc, "表 11　100 台 30 分钟压力测试结果（证据文件 验证结果_压力测试100台_20261014.csv）")

para(doc,
     "三点说明。第一，本次测试的批次大小为 20 条而非 10 条：取 10 条会使签名次数翻倍、"
     "单次运行时间显著拉长，而记录总量与设备规模均与目标一致，"
     "故在报告中明确标注该口径差异，避免与“每批 10 条”的目标混淆。"
     "第二，本表数字仅覆盖中心侧链路，不包含消息代理与网关之间的传输段，"
     "因此不能直接等同于“端到端全链路延迟”。第三，本轮未采集网关侧接收数与离线补传批次的分列统计，"
     "该项列为待验证。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

heading(doc, "6.5　端到端延迟的三个独立口径", level=2)
para(doc,
     "为交叉验证“延迟是否真的在一秒以内”，本项目从三个互不依赖的来源测量了同一类延迟：",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

table(doc,
      ["口径", "测量起点 → 终点", "样本", "P95", "来源"],
      [
          ["中心侧端到端", "请求发出 → 中心返回持久化结果", "9000 批", "79.21 ms", "压力测试 CSV"],
          ["网关侧实时延迟", "网关收到批次 → 中心给出终态", "3 批", "109 ms", "端到端打通证据"],
          ["网关侧实时延迟", "同上，由网关实现方独立测量", "—", "91 ms", "网关侧终端记录"],
      ],
      widths=[3.4, 6.4, 2.0, 2.2, 3.6], align_center=False)
caption(doc, "表 12　端到端延迟的三个独立口径")

para(doc,
     "三个口径的量级一致，且均远低于 1 秒目标，说明结论不是某一次测量的偶然结果。"
     "需要说明的是，网关侧的两个样本量很小（3 批），不能给出稳定的分位数估计，"
     "其价值在于“与中心侧口径互相印证”而非独立给出统计结论。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

heading(doc, "6.6　独立交叉验证", level=2)
para(doc,
     "同一批次分别由三套彼此独立的实现核验：设备端在发送前自检签名与摘要链；"
     "网关侧另有独立的批次核验工具，按字段集合、设备登记、类型一致、签名、条数与序号、"
     "逐条摘要、链连续与 Merkle 根逐项输出结论；中心侧则完全独立地重做一遍全部校验。"
     "三套实现对同一合法批次给出一致结论，对篡改批次的拒绝理由也相互吻合，"
     "这构成了“验证不是自我循环”的证据。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

# ------------------------------------------------------------------ 7
heading(doc, "7　局限与待完善", level=1)
para(doc,
     "本章如实列明当前工作的边界，供评审判断结论的适用范围。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

table(doc,
      ["局限", "具体表现", "影响"],
      [
          ["检测率样本来源单一", "用例由本项目脚本生成并自带标签，尚未替换为跨角色独立提供的异常样本与真实标签",
           "检测率结论说明“实现与自定标签一致”，不等同于第三方评测"],
          ["正常样本量偏小", "误报率分母仅 3 个正常样本", "误报率结论统计强度有限"],
          ["压力测试口径差异", "批次大小取 20 条而非 10 条", "签名次数与单批延迟与目标口径不完全一致"],
          ["B 端指标未纳入", "压力测试未采集网关侧接收数与离线补传批次的分列统计",
           "表 11 仅代表中心侧链路"],
          ["实时延迟样本少", "网关侧延迟样本仅 3 批", "无法给出稳定的分位数估计"],
          ["三机部署证据待补", "三机局域网形态的完整闭环证据仍在收集中",
           "部署形态结论需以补齐后的证据为准"],
          ["异常样本覆盖有限", "12 类构造方式覆盖常见篡改，但未穷举协议层攻击",
           "对更复杂攻击的检出能力未评估"],
          ["单机实验环境", "全部实验在单机、单一操作系统与单一库版本下完成",
           "跨平台与跨版本的稳定性未验证"],
      ],
      widths=[3.4, 7.4, 4.8], align_center=False)
caption(doc, "表 13　已识别局限")

para(doc,
     "后续工作的优先级为：先补齐跨角色独立提供的异常样本与真实标签，并扩大正常样本量，"
     "重新计算三项指标；再补齐网关侧接收数、离线补传分列统计与三机部署的完整证据；"
     "最后在第二台机器上按运行说明重新部署并复跑全部脚本，验证可复现性。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

# ------------------------------------------------------------------ 8
heading(doc, "8　结论", level=1)
para(doc,
     "本作品完成了一套可运行、可验证、可复现的三层可信采集系统：设备端以 SM3 摘要链与 "
     "Merkle 树组织数据并用 SM2 签名，网关承担接收、初验、缓存与按序补传，"
     "中心平台以预登记公钥独立重验、以单事务落库、以审计表留证，并向外界提供单条 Merkle 证明与签名复核。"
     "系统对“改值、删记录、断链、伪造签名、改值重算、未登记设备、序号跳号、历史重放、未约定字段”"
     "等异常均能拒绝并定位，且不误伤正常数据。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

para(doc,
     "在目标规模下，100 台设备 30 分钟产生的 180000 条记录全部被正确接受，"
     "中心侧端到端延迟 P95 为 79.21 ms，网关侧实时延迟 P95 为 109 ms，均显著优于 1 秒目标。"
     "签名开销与批次大小的关系表明：单次签名耗时基本恒定，工程取舍应落在批次划分而非算法优化上。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

para(doc,
     "需要重申的是，检测率与误报率目前基于自建标注样本，压力测试的批次粒度与目标口径存在差异，"
     "网关侧规模指标与三机部署证据尚待补齐。这些局限已在第 7 章列明，"
     "并在后续工作中按优先级逐项消除。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

# ------------------------------------------------------------------ 附录
doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
heading(doc, "附录 A　可复现的运行与测试命令", level=1)
para(doc,
     "以下命令均在仓库根目录执行。首次使用前建议先执行初始化脚本，"
     "并按输出的公钥指纹核对设备登记表。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

para(doc, "A.1　单机四终端启动", bold=True, space_before=6, space_after=4)
for text in [
    "终端 1：mosquitto -c mosquitto.conf -v",
    "终端 2：.venv\\Scripts\\python.exe -m uvicorn center_platform.main:app --host 127.0.0.1 --port 8000",
    "终端 3：.venv\\Scripts\\python.exe -m edge_gateway.main",
    "终端 4：.venv\\Scripts\\python.exe -m device_simulator.publish --broker 127.0.0.1 --port 1883 --sample",
]:
    bullet(doc, text, size=9.5)

para(doc, "A.2　测试与实验", bold=True, space_before=8, space_after=4)
table(doc,
      ["目的", "命令", "输出"],
      [
          ["单元与集成测试", "python -m pytest -q", "通过数"],
          ["中心功能冒烟", "python 验证脚本_中心功能冒烟.py", "验证结果_中心功能冒烟_*.txt"],
          ["页面接口", "python 验证脚本_页面接口.py http://127.0.0.1:8000", "验证结果_页面接口_*.txt"],
          ["检测率评估", "python tests/evaluate.py --make-samples --out test_data/eval_samples.json",
           "标注样本文件"],
          ["检测率评分", "python tests/evaluate.py --samples test_data/eval_samples.json --submit --url http://127.0.0.1:8000",
           "汇总输出与明细 CSV"],
          ["签名对照", "python tests/benchmark_signing.py --records 100", "benchmark_signing_*.csv"],
          ["规模压力", "python tests/stress_center.py --devices 100 --minutes 30 --batch-size 20 --url <中心地址>",
           "验证结果_压力测试*.csv/json"],
          ["网关至中心打通", "python 验证脚本_端到端打通.py --center <中心地址> --device WL-001",
           "转发结果与延迟统计"],
      ],
      widths=[3.0, 8.6, 4.0], align_center=False)

para(doc, "A.3　配置切换", bold=True, space_before=8, space_after=4)
for text in [
    "中心库路径：PINGLU_CENTER_DB",
    "网关缓存库路径：PINGLU_GATEWAY_DB（供离线页只读）",
    "网关侧：MQTT_BROKER、MQTT_PORT、CENTER_URL、DB_PATH、REGISTRY_PATH",
]:
    bullet(doc, text, size=9.5)

heading(doc, "附录 B　证据文件索引", level=1)
para(doc,
     "下列文件随源码一并提交，报告中每个数字均可在对应文件中查到原始记录。",
     first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

table(doc,
      ["报告位置", "证据文件", "内容"],
      [
          ["6.1 / 表 8", "验证结果_中心功能冒烟_20261014.txt", "中心 13 项行为的通过计数"],
          ["6.1 / 表 8", "验证结果_页面接口_20261014.txt", "六块接口与证明复验的逐项结果"],
          ["6.2 / 表 9", "验证结果_检测率评估_20261014.json / .csv / .txt", "逐用例期望与实际的对照与指标"],
          ["6.3 / 表 10", "benchmark_signing_20261014.csv", "三种批次粒度的签名与验签统计"],
          ["6.4 / 表 11", "验证结果_压力测试100台_20261014.csv / .json / .txt", "规模、吞吐、延迟分位"],
          ["6.5 / 表 12", "验证结果_端到端打通_20261014.txt", "网关至中心打通与延迟统计"],
          ["2.1 / 表 1", "报告图_初稿/fig1_dataflow.png", "系统数据流与验证次序示意图"],
          ["2.2 / 图 2", "报告图_初稿/fig2_deployment.png", "三机部署与配置项"],
          ["2.3 / 图 3", "报告图_初稿/fig3_flow.png", "批次提交完整流程"],
      ],
      widths=[2.8, 7.4, 5.4], align_center=False)

para(doc, "", space_after=10)
para(doc,
     "说明：本稿为技术初稿，实名信息、作品编号与签字页按官方模板另行填写；"
     "按匿名的要求，正文与图示中不含团队名称、成员姓名、单位与导师信息。",
     size=9, color=MUTED, first_indent=0.74, align=WD_ALIGN_PARAGRAPH.JUSTIFY)

doc.save(OUT)
print("已生成:", OUT.name, OUT.stat().st_size, "B")
