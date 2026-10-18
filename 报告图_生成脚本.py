"""生成报告用的示意图（架构数据流 / 三机部署 / 软件流程）。

输出到 报告图_初稿/ 目录，供 python-docx 嵌入。
纯 PIL 绘制；中文字体用系统 msyh/simhei。
所有元素按画布边界计算，避免裁切与文字重叠。
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "报告图_初稿"
OUT.mkdir(exist_ok=True)

FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
]

TITLE_FONT = BODY_FONT = SMALL_FONT = TINY_FONT = None

for candidate in FONT_CANDIDATES:
    if candidate.exists():
        try:
            TITLE_FONT = ImageFont.truetype(str(candidate), 30)
            BODY_FONT = ImageFont.truetype(str(candidate), 21)
            SMALL_FONT = ImageFont.truetype(str(candidate), 17)
            TINY_FONT = ImageFont.truetype(str(candidate), 15)
            break
        except OSError:
            continue

INK = (28, 39, 51)
MUTED = (102, 119, 137)
LINE = (196, 206, 218)
BG = (255, 255, 255)
BLUE = (31, 95, 168)
GREEN = (31, 122, 77)
AMBER = (181, 106, 18)
LIGHT_BLUE = (232, 240, 250)
LIGHT_GREEN = (234, 243, 236)
LIGHT_AMBER = (253, 243, 230)
LIGHT_GREY = (246, 248, 250)


def canvas(width, height):
    image = Image.new("RGB", (width, height), BG)
    return image, ImageDraw.Draw(image)


def text_size(draw, text, font):
    left, top, right, bottom = draw.multiline_textbbox(
        (0, 0), text, font=font, spacing=4, align="center"
    )
    return right - left, bottom - top


def centered(draw, box, text, font, fill=INK):
    x0, y0, x1, y1 = box
    width, height = text_size(draw, text, font)
    draw.multiline_text(
        ((x0 + x1 - width) / 2, (y0 + y1 - height) / 2),
        text, font=font, fill=fill, spacing=4, align="center",
    )


def arrow(draw, start, end, color=MUTED, width=3, head=11):
    draw.line([start, end], fill=color, width=width)
    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    length = max((dx * dx + dy * dy) ** 0.5, 1)
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    left = (x1 - ux * head + px * head * 0.6, y1 - uy * head + py * head * 0.6)
    right = (x1 - ux * head - px * head * 0.6, y1 - uy * head - py * head * 0.6)
    draw.polygon([end, left, right], fill=color)


def gap_label(draw, left, right, y, text, color=MUTED):
    """在两块之间居中放一行说明，宽度不足时自动缩小字号。"""
    font = TINY_FONT

    for candidate in (TINY_FONT, ImageFont.truetype(str(FONT_CANDIDATES[0]), 13)):
        width, _ = text_size(draw, text, candidate)
        if width <= (right - left) - 6:
            font = candidate
            break

    width, _ = text_size(draw, text, font)
    draw.text(((left + right - width) / 2, y), text, font=font, fill=color)


# ---------------------------------------------------------------- 图 1
def figure_dataflow():
    width, height = 1600, 790
    image, draw = canvas(width, height)

    draw.text((48, 30), "图 1  系统数据流与三层职责", font=TITLE_FONT, fill=INK)
    draw.text(
        (48, 74),
        "设备端签名 → 边缘网关初验与缓存 → 中心独立重验、审计与溯源证明",
        font=SMALL_FONT, fill=MUTED,
    )

    top, bottom = 140, 330
    boxes = [
        (60, top, 380, bottom, "设备端（感知设备 / 模拟器）",
         ["七类设备 · SM3 摘要链", "Merkle 根 · SM2 批次签名", "私钥不出设备端"], LIGHT_BLUE, BLUE),
        (480, top, 800, bottom, "边缘网关（MQTT 订阅）",
         ["快速入队 · 契约初验", "本地 SQLite 缓存", "按序补传 · 只读状态"], LIGHT_AMBER, AMBER),
        (900, top, 1220, bottom, "中心平台（FastAPI）",
         ["独立重验 · 单事务入库", "审计留证 · 幂等去重", "Merkle 证明 · 验签复核"], LIGHT_GREEN, GREEN),
        (1320, top, 1560, bottom, "网页",
         ["六块展示", "溯源查询", "断网快照"], LIGHT_GREY, LINE),
    ]

    for left, top_, right, bottom_, title, lines, fill, accent in boxes:
        draw.rounded_rectangle((left, top_, right, bottom_), radius=10,
                               fill=fill, outline=accent, width=2)
        inner = (left + 10, top_ + 10, right - 10, bottom_ - 10)
        centered(draw, inner, title + "\n" + "\n".join(lines), SMALL_FONT, INK)

    arrow_y = 235
    arrow(draw, (380, arrow_y), (480, arrow_y))
    arrow(draw, (800, arrow_y), (900, arrow_y))
    arrow(draw, (1220, arrow_y), (1320, arrow_y))

    gap_label(draw, 380, 480, arrow_y - 26, "MQTT QoS1")
    gap_label(draw, 800, 900, arrow_y - 26, "HTTP POST")
    gap_label(draw, 1220, 1320, arrow_y - 26, "HTTP GET")

    draw.text((60, 366), "中心独立验证优先次序（不采信网关的通过状态）",
              font=BODY_FONT, fill=INK)

    steps = ["设备登记", "批次签名", "数量与序号", "每条摘要",
             "链连接", "Merkle 根", "历史重放"]
    left0, step_y = 60, 412
    step_w, step_h, gap = 190, 62, 20

    for index, text in enumerate(steps):
        left = left0 + index * (step_w + gap)
        accent = AMBER if index == len(steps) - 1 else GREEN
        fill = LIGHT_AMBER if index == len(steps) - 1 else LIGHT_GREEN
        draw.rounded_rectangle((left, step_y, left + step_w, step_y + step_h),
                               radius=9, fill=fill, outline=accent, width=2)
        centered(draw, (left + 4, step_y, left + step_w - 4, step_y + step_h),
                 text, TINY_FONT, INK)
        if index < len(steps) - 1:
            arrow(draw, (left + step_w + 2, step_y + step_h / 2),
                  (left + step_w + gap - 2, step_y + step_h / 2),
                  color=MUTED, width=2, head=7)

    draw.text((60, 508), "持久化结果与网关动作", font=BODY_FONT, fill=INK)

    outcomes = [
        (60, "accepted", "中心已入库，网关确认完成", GREEN, LIGHT_GREEN),
        (440, "duplicate", "内容相同，不重复计数", BLUE, LIGHT_BLUE),
        (820, "rejected", "写审计留原文，网关停止重试", AMBER, LIGHT_AMBER),
        (1200, "proof", "单条 Merkle 证明 + 验签复核", GREEN, LIGHT_GREEN),
    ]

    for left, title, desc, color, fill in outcomes:
        draw.rounded_rectangle((left, 554, left + 340, 668), radius=10,
                               fill=fill, outline=color, width=2)
        draw.text((left + 18, 566), title, font=BODY_FONT, fill=color)
        draw.text((left + 18, 608), desc, font=TINY_FONT, fill=INK)

    path = OUT / "fig1_dataflow.png"
    image.save(path, dpi=(200, 200))
    return path


# ---------------------------------------------------------------- 图 2
def figure_deployment():
    width, height = 1560, 790
    image, draw = canvas(width, height)

    draw.text((48, 30), "图 2  三机部署与关键配置项", font=TITLE_FONT, fill=INK)
    draw.text(
        (48, 74),
        "中心以 --host 0.0.0.0 监听；设备端 broker 指向电脑2；网关的中心 URL 指向电脑3",
        font=SMALL_FONT, fill=MUTED,
    )

    machines = [
        (60, "电脑 1 · 设备端", "device_simulator", [
            "七类设备轮换生成记录",
            "SM3 链 + Merkle + SM2 签名",
            "MQTT QoS1 发布批次",
        ], BLUE, LIGHT_BLUE),
        (570, "电脑 2 · 边缘网关", "mosquitto + edge_gateway", [
            "Mosquitto 提供 1883 端口",
            "订阅 pinglu/batches/+",
            "初验、缓存、按序补传",
        ], AMBER, LIGHT_AMBER),
        (1080, "电脑 3 · 中心平台", "center_platform + 网页", [
            "独立重验与单事务入库",
            "审计、Merkle 证明、验签复核",
            "网页六块与接口文档",
        ], GREEN, LIGHT_GREEN),
    ]

    for left, title, subtitle, lines, accent, fill in machines:
        draw.rounded_rectangle((left, 136, left + 420, 360), radius=12,
                               fill=fill, outline=accent, width=2)
        draw.text((left + 20, 154), title, font=BODY_FONT, fill=accent)
        draw.text((left + 20, 186), subtitle, font=TINY_FONT, fill=MUTED)
        for index, line in enumerate(lines):
            draw.text((left + 20, 226 + index * 34), "· " + line, font=SMALL_FONT, fill=INK)

    arrow_y = 250
    arrow(draw, (480, arrow_y), (570, arrow_y))
    arrow(draw, (990, arrow_y), (1080, arrow_y))
    gap_label(draw, 480, 570, arrow_y - 26, "MQTT 1883")
    gap_label(draw, 990, 1080, arrow_y - 26, "HTTP 8000")

    draw.text((60, 396), "关键配置项（均可用同名环境变量覆盖，无需改代码）",
              font=BODY_FONT, fill=INK)

    rows = [
        ("MQTT_BROKER / MQTT_PORT", "电脑2 的 IP / 1883", "设备端、网关"),
        ("CENTER_URL", "http://电脑3IP:8000", "网关"),
        ("DB_PATH / REGISTRY_PATH", "本地缓存库 / 公开登记表", "网关"),
        ("PINGLU_CENTER_DB", "中心库路径（异机部署切换用）", "中心"),
        ("PINGLU_GATEWAY_DB", "网关缓存库路径（离线页只读）", "中心"),
    ]

    table_top = 442
    row_h = 42
    table_bottom = table_top + row_h * (len(rows) + 1)
    draw.rounded_rectangle((60, table_top, 1500, table_bottom), radius=10,
                           fill=LIGHT_GREY, outline=LINE, width=2)

    for x, text in [(86, "配置项"), (620, "取值"), (1100, "生效方")]:
        draw.text((x, table_top + 12), text, font=TINY_FONT, fill=MUTED)

    for index, (name, value, who) in enumerate(rows, start=1):
        y = table_top + row_h * index
        draw.line([(60, y), (1500, y)], fill=LINE, width=1)
        draw.text((86, y + 11), name, font=TINY_FONT, fill=INK)
        draw.text((620, y + 11), value, font=TINY_FONT, fill=INK)
        draw.text((1100, y + 11), who, font=TINY_FONT, fill=INK)

    path = OUT / "fig2_deployment.png"
    image.save(path, dpi=(200, 200))
    return path


# ---------------------------------------------------------------- 图 3
def figure_flow():
    width, height = 1560, 990
    image, draw = canvas(width, height)

    draw.text((48, 30), "图 3  一次批次提交的完整流程", font=TITLE_FONT, fill=INK)
    draw.text(
        (48, 74),
        "任一环节失败都保留原文与判定，页面可见；网关初验失败仍交中心复核",
        font=SMALL_FONT, fill=MUTED,
    )

    lanes = [
        ("设备端", BLUE, LIGHT_BLUE,
         ["生成记录", "算记录摘要", "构 Merkle 根", "SM2 签名", "MQTT 发布"]),
        ("边缘网关", AMBER, LIGHT_AMBER,
         ["入队", "解析与限长", "契约初验", "落库缓存", "按序转发"]),
        ("中心平台", GREEN, LIGHT_GREEN,
         ["结构检查", "查公钥", "签名与序号", "摘要与链", "Merkle 根", "入库或审计"]),
    ]

    lane_top = 124
    lane_h = 200
    start_x = 180
    lane_right = 1500

    for lane_index, (name, accent, fill, steps) in enumerate(lanes):
        top = lane_top + lane_index * lane_h
        draw.rounded_rectangle((50, top, 1510, top + lane_h - 40), radius=12,
                               fill=fill, outline=accent, width=2)
        draw.text((70, top + 14), name, font=BODY_FONT, fill=accent)

        count = len(steps)
        gap = 12
        cell_w = int((lane_right - start_x - (count - 1) * gap) / count)

        for step_index, step in enumerate(steps):
            left = start_x + step_index * (cell_w + gap)
            draw.rounded_rectangle((left, top + 54, left + cell_w, top + 126),
                                   radius=9, fill=BG, outline=accent, width=2)
            centered(draw, (left + 4, top + 54, left + cell_w - 4, top + 126),
                     step, TINY_FONT, INK)

            if step_index < count - 1:
                arrow(draw, (left + cell_w + 1, top + 90),
                      (left + cell_w + gap - 1, top + 90),
                      color=accent, width=2, head=6)

    mid_x = start_x + 2 * 260
    arrow(draw, (760, 284), (760, 324), color=MUTED)
    gap_label(draw, 700, 890, 292, "MQTT QoS1")
    arrow(draw, (760, 484), (760, 524), color=MUTED)
    gap_label(draw, 690, 990, 492, "HTTP POST /batches")

    draw.text((50, 736), "关键约束", font=BODY_FONT, fill=INK)

    constraints = [
        "① 已签名内容不得重签：改值后摘要或根必然对不上，中心分别回报 INVALID_RECORD_HASH / INVALID_MERKLE_ROOT",
        "② 中心不信网关：公钥只从中心 devices 表取，重复校验签名、序号区间、摘要链、Merkle 根与历史批次号",
        "③ 幂等：device_id + batch_id 唯一；内容相同返回 duplicate 不重复计数，内容不同记冲突审计",
        "④ 顺序：同一设备按批次号升序推进，前一批未确认时后续批次不抢跑",
    ]

    for index, text in enumerate(constraints):
        draw.text((50, 780 + index * 40), text, font=SMALL_FONT, fill=MUTED)

    path = OUT / "fig3_flow.png"
    image.save(path, dpi=(200, 200))
    return path


if __name__ == "__main__":
    for path in (figure_dataflow(), figure_deployment(), figure_flow()):
        print("已生成:", path.name, path.stat().st_size, "B")
