# -*- coding: utf-8 -*-
"""生成「网关转发流程图」PNG，用于交付材料 B6。

用法（仓库根目录）：
    .venv\\Scripts\\python.exe tools\\draw_gateway_flow.py
输出：
    docs/网关转发流程图.png
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "网关转发流程图.png"

W, H = 1500, 2380
BG = (246, 248, 251)
INK = (32, 36, 44)
MUTED = (110, 116, 126)
ARROW = (120, 128, 140)

# 字体：优先雅黑，退回黑体
FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path(r"C:\Windows\Fonts\simsun.ttc"),
]


def font(size, bold=False):
    order = FONT_CANDIDATES[1:] + FONT_CANDIDATES[:1] if bold else FONT_CANDIDATES
    for path in order:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    raise SystemExit("找不到中文字体，无法绘制")


F_TITLE = font(40, bold=True)
F_SUB = font(20)
F_BOX_T = font(25, bold=True)
F_BOX = font(21)
F_SIDE = font(19)
F_FOOT = font(18)


def draw_arrow(draw, x, y1, y2, label=None):
    draw.line([(x, y1), (x, y2 - 12)], fill=ARROW, width=3)
    draw.polygon([(x - 8, y2 - 14), (x + 8, y2 - 14), (x, y2)], fill=ARROW)
    if label:
        draw.text((x + 14, (y1 + y2) // 2 - 12), label, font=F_SIDE, fill=MUTED)


def main():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # 标题
    d.text((60, 40), "边缘网关转发流程", font=F_TITLE, fill=INK)
    d.text((62, 92), "订阅 → 分片接收 → 持久化 → 分片转发 → 中心判定 → 落终态", font=F_SUB, fill=MUTED)
    d.line([(60, 132), (W - 60, 132)], fill=(220, 226, 234), width=2)

    cx = W // 2
    y = 168
    box_w = 980
    left = cx - box_w // 2

    def box(title, lines, accent=(70, 120, 200), fill=(255, 255, 255)):
        nonlocal y
        pad = 16
        lh = 30
        h = pad * 2 + 34 + lh * len(lines)
        d.rounded_rectangle([left, y, left + box_w, y + h], radius=12,
                            fill=fill, outline=accent, width=3)
        d.rounded_rectangle([left, y, left + 10, y + h], radius=5, fill=accent)
        d.text((left + 28, y + pad), title, font=F_BOX_T, fill=INK)
        yy = y + pad + 36
        for line in lines:
            d.text((left + 28, yy), line, font=F_BOX, fill=(60, 66, 76))
            yy += lh
        y += h
        return y

    def arrow(label=None):
        nonlocal y
        draw_arrow(d, cx, y + 8, y + 52, label)
        y += 56

    # ① 订阅
    box("① MQTT 订阅", [
        "topic: pinglu/batches/+    QoS 1",
        "client_id: edge-gateway（固定，避免多实例抢订阅）",
    ], accent=(90, 160, 220))

    arrow()

    # ② on_message
    box("② on_message —— 只做快速入队，零 IO", [
        "记录 gateway_received_at → 按 device_id 做 crc32 取模选内存队列",
        "不做解析、不写数据库：回调里任何耗时都会拖慢整个订阅",
    ], accent=(90, 160, 220))

    arrow("按 device_id 分片，每台设备恒定同队列")

    # ③ 接收工作线程
    box("③ 接收工作线程 × RECV_WORKERS（默认 8）", [
        "同一台设备永远落在同一个线程 → 链尾推进严格有序",
        "a. 大小 / UTF-8 / JSON 校验（失败写 gateway_audit，绝不崩）",
        "b. identify(device_id, batch_id)",
        "c. 取预登记公钥（只信任登记表，不用消息自带公钥）",
        "d. 契约初验：结构 → 设备登记 → SM2 验签 → 数量/范围",
        "   → 起止时间 → 逐条记录(SM3/链) → Merkle 根",
        "e. save_received：原文 + 初判同事务落库，提交成功才算持有",
    ], accent=(110, 170, 120))

    arrow()

    # ④ 持久化
    box("④ 本地持久队列 pending_batches（SQLite · WAL · synchronous=NORMAL）", [
        "status: pending / audit_pending / accepted / rejected / duplicate / conflict",
        "UNIQUE(device_id, batch_id) 挡住 QoS 1 重复投递",
        "线程内复用连接（连接池），避免每次 connect + PRAGMA",
    ], accent=(200, 150, 70))

    arrow("新批次入库即 wake_event.set()")

    # ⑤ 转发线程
    box("⑤ 转发线程 run_forever（事件驱动，不是定时轮询）", [
        "被 wake_event 立刻唤醒；无新批次时按 RETRY_INTERVAL 做重试扫描",
        "每轮执行一次 run_once()",
    ], accent=(160, 110, 200))

    arrow()

    # ⑥ run_once
    box("⑥ run_once —— 留证 + 分片并行转发", [
        "① 初验失败的原文（audit_pending）送中心复核，不参与顺序约束",
        "② 设备按序号取模分 FORWARD_SHARDS 片（默认 24），每片一个线程",
        "③ 同一设备严格按 batch_no 升序；只要有一批没拿到终态就停",
        "   —— 后续批次绝不允许抢在未确认的前一批之前确认（按序停等）",
    ], accent=(160, 110, 200))

    arrow("POST {CENTER_URL}/batches  body={batch}")

    # ⑦ 分类
    box("⑦ 中心回执分类 classify()", [
        "accepted            → 终态 accepted",
        "duplicate(DUPLICATE_BATCH) → 终态 duplicate（幂等）",
        "rejected / BATCH_CONTENT_CONFLICT → 终态 rejected / conflict + 留证",
        "传输失败 / 超时 / 5xx / 无法判定的回执 → 保留待传，下一轮重试",
    ], accent=(210, 100, 100))

    arrow()

    # ⑧ 落终态
    box("⑧ mark_result —— 落终态并记录实时延迟", [
        "latency_ms = 网关收到该批 → 拿到中心终态（毫秒精度）",
        "方案第七节 3 的「实时批次 P95 ≤ 1 秒」就按这一列统计",
        "离线补传批次不计入实时 P95",
    ], accent=(110, 170, 120))

    arrow()

    # ⑨ 状态文件
    box("⑨ 每 STATUS_INTERVAL（默认 5 秒）写出只读状态", [
        "edge_gateway/data/gateway_status.json（临时文件 + 原子替换）",
        "内容：每台设备缓存批数/记录数、已补传、待补传、断点序号、最后收发时间",
        "中心 /offline-status 优先读它；写出失败只打日志，不影响转发",
    ], accent=(90, 160, 220))

    # 右下角：三个关键设计
    info = [
        "三条硬性约束",
        "① 收到即落库：事务提交成功才算网关已持有这批数据，重启不丢",
        "② 按序停等：同设备未确认的批次会挡住后续批次，绝不越过",
        "③ 终态只判一次：accepted/duplicate/rejected/conflict 落库即定案，不重复判定",
    ]
    bx, by = 60, H - 210
    d.rounded_rectangle([bx, by, W - 60, H - 40], radius=12,
                        fill=(255, 252, 240), outline=(230, 200, 120), width=2)
    d.text((bx + 20, by + 14), info[0], font=F_BOX_T, fill=(150, 110, 30))
    yy = by + 52
    for line in info[1:]:
        d.text((bx + 20, yy), line, font=F_FOOT, fill=(90, 80, 60))
        yy += 34

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG")
    print("已生成:", OUT)
    print("尺寸  :", img.size)


if __name__ == "__main__":
    sys.exit(main())
