# -*- coding: utf-8 -*-
"""打包复赛“第二部分：相关支撑材料”（全队整合）。

用法（仓库根目录）：
    .venv\\Scripts\\python.exe 打包提交材料.py
    .venv\\Scripts\\python.exe 打包提交材料.py --include-cases   # 连 700 份异常样本一起打

结构（对应竞赛要求“文档、程序主要代码、测试代码、测试数据、运行结果截图、测试结果截图”）：
    00_支撑材料目录与说明.md
    01_源码与配置/          三端代码、测试代码、工具、配置样例、依赖清单
    02_运行说明与文档/      README、接口契约、P33 口径、验收清单、报告生成脚本
    03_测试数据/            公开登记表、正常批次、评估样本（可选含异常样本）
    04_测试结果/            冒烟/页面/端到端/检测率/压力/百台核对/pytest/签名对照
    05_报告插图/            报告用结构图（fig1–3）
    06_运行截图/            网页六块、终端联调、pytest 输出——由队员提供后放入

报告 docx 与审批签字页属于第一、第三部分，单独上传，不打进支撑材料。
"""

from __future__ import annotations

import argparse
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "pinglu-crypto-system-submit-20261018"
PKG_DIR = ROOT / "_tmp" / "submit_pkg"
STAGE = PKG_DIR / NAME
ZIP_PATH = PKG_DIR / f"{NAME}.zip"

SKIP_SUFFIX = (".bundle", ".zip", ".docx")
SKIP_KEYWORDS = ("队长任务书_全文提取",)

# 交付件中属于支撑材料的部分（报告 docx 与签字页单独上传，不放这里）
EXTRA_FILES = [
    ".env.example",
    "P33_实时延迟口径与数据_20261017.md",
    "验收清单_30分钟与断网恢复_20261018.md",
    "截图执行单_20261018.md",
    "剩余动作清单_20261018.md",
    "验证结果_全量pytest_20261018.txt",
    "验证结果_异常样本700例_20261018.txt",
    "验证结果_离线与缓存篡改_20261018.txt",
    "验证结果_百台环境核对_20261018.txt",
    "验证结果_三端重测_round5_20261018.txt",
    "验证结果_正式轮_30分钟_stats.json",
    "验证结果_正式轮_30分钟_audits.json",
    "验证结果_正式轮_30分钟.log",
    "验证结果_正式轮_监控.log",
    "验证结果_门禁与顺序_20261018.txt",
]

# 分类规则：按仓库相对路径前缀判断（先匹配先生效）
CATEGORY_RULES = [
    ("03_测试数据", ("test_data/",)),
    ("04_测试结果", ("验证结果_", "benchmark_signing", "docs/evidence/", "tests/anomaly_report.md")),
    ("05_报告插图", ("报告图_定稿/",)),
    ("02_运行说明与文档", (
        "README.md", ".env.example", "contract.md", "docs/", "P33_",
        "验收清单_", "截图执行单_", "剩余动作清单_", "报告_内容_定稿.py", "报告_生成脚本_定稿.py",
        "签字页_生成脚本.py", "打包提交材料.py",
    )),
]

IDENTITY_PATTERN = ("学院", "大学", "指导老师", "C:\\Users\\")


def tracked_files():
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if out.returncode != 0:
        raise SystemExit("git ls-files 失败：" + out.stderr.strip())
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def should_skip(rel: str) -> bool:
    return rel.endswith(SKIP_SUFFIX) or any(k in rel for k in SKIP_KEYWORDS)


def category_of(rel: str) -> str:
    for folder, prefixes in CATEGORY_RULES:
        if any(rel == p or rel.startswith(p) for p in prefixes):
            return folder
    return "01_源码与配置"


def copy_into(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-cases", action="store_true",
                        help="连 tests/anomaly_cases 的 700 份样本一起打（默认不打）")
    args = parser.parse_args()

    if STAGE.exists():
        for path in sorted(STAGE.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        STAGE.rmdir()
    STAGE.mkdir(parents=True)

    mapping = {}
    for rel in tracked_files():
        if should_skip(rel):
            continue
        src = ROOT / rel
        if not src.is_file():
            continue
        folder = category_of(rel)
        copy_into(src, STAGE / folder / rel)
        mapping[rel] = folder
    print(f"git 跟踪文件：{len(mapping)} 个")

    for rel in EXTRA_FILES:
        src = ROOT / rel
        if not src.is_file():
            print(f"  [跳过] 缺失：{rel}")
            continue
        folder = category_of(rel)
        copy_into(src, STAGE / folder / rel)
        mapping[rel] = folder
        print(f"  交付件：{rel} → {folder}")

    fig_dir = ROOT / "报告图_定稿"
    if fig_dir.is_dir():
        for path in fig_dir.glob("*.png"):
            copy_into(path, STAGE / "05_报告插图" / path.name)
        print("  报告插图：05_报告插图/")

    if args.include_cases:
        cases_dir = ROOT / "tests" / "anomaly_cases"
        if cases_dir.is_dir():
            count = 0
            for path in cases_dir.rglob("*"):
                if path.is_file():
                    copy_into(path, STAGE / "03_测试数据" / "anomaly_cases" / path.relative_to(cases_dir))
                    count += 1
            print(f"  异常样本：03_测试数据/anomaly_cases/（{count} 个文件）")

    # 截图目录：优先收仓库根 screenshots/ 下的 PNG；没有则留占位说明
    shot_dir = STAGE / "06_运行截图"
    shot_dir.mkdir(exist_ok=True)
    src_shots = ROOT / "screenshots"
    shots = sorted(src_shots.glob("*.png")) if src_shots.is_dir() else []
    for path in shots:
        copy_into(path, shot_dir / path.name)
    if shots:
        print(f"  运行截图：{len(shots)} 张（来自 screenshots/）")
        if len(shots) < 6:
            print("  [提醒] 截图少于 6 张，按《截图执行单》应至少 6 张（建议 8 张）")
    else:
        print("  [提醒] 未找到 screenshots/ 下的截图，06_运行截图 只有占位说明")
    (shot_dir / "截图清单与放置说明.md").write_text(
        "# 运行截图（第二部分必交）\n\n"
        "按竞赛要求，支撑材料需含“运行结果截图、测试结果截图”。建议命名与内容：\n\n"
        "| 文件名 | 内容 |\n|---|---|\n"
        "| 01_总览.png | 中心首页：设备→网关→中心→溯源数据流 + 六项统计 |\n"
        "| 02_七类感知数据.png | 七类设备分卡、批次数与最新载荷 |\n"
        "| 03_异常审计.png | 被拒批次：设备/批次/受影响序号/原因码/中心复核 |\n"
        "| 04_Merkle证明.png | 单条记录：叶摘要、路径、根、SM2 验签结果 |\n"
        "| 05_离线恢复.png | 缓存数、断点、补传进度、实时 P95 |\n"
        "| 06_性能对比.png | 每批 1/10/20 条的签名次数、均值、P95、报文大小 |\n"
        "| 07_四终端联调.png | MQTT/中心/网关/模拟器四个终端同时运行 |\n"
        "| 08_pytest.png | `python -m pytest -q` 结果行 |\n\n"
        "匿名要求：截图里不得出现姓名、单位、导师、本机用户名路径；"
        "浏览器地址栏只保留 `127.0.0.1:8000` 或 `<IP>:8000`。\n",
        encoding="utf-8",
    )

    # 说明与清单
    files = [p for p in STAGE.rglob("*") if p.is_file()]
    total_mb = round(sum(p.stat().st_size for p in files) / 1024 / 1024, 2)
    by_folder = {}
    for path in files:
        folder = path.relative_to(STAGE).parts[0]
        by_folder.setdefault(folder, []).append(path)

    lines = [
        "# 支撑材料目录与说明",
        "",
        f"生成时间：{datetime.now():%Y-%m-%d %H:%M}　文件数：{len(files)}　总大小：{total_mb} MB（上限 4GB）",
        "",
        "> 本包是“第二部分：相关支撑材料”，由队长收集并整合三位队员的代码、文档、测试数据与结果。",
        "> 作品设计报告（第一部分）与审批及签字页（第三部分）单独上传，不在本包内。",
        "",
        "## 快速运行（Windows，仓库根目录）",
        "",
        "```bat",
        "python -m venv .venv",
        ".venv\\Scripts\\python.exe -m pip install -r requirements.txt",
        ".venv\\Scripts\\python.exe init_project.py        :: 生成七类密钥与登记表、重建中心库",
        ".venv\\Scripts\\python.exe -m pytest -q            :: 全套测试",
        "```",
        "",
        "四终端联调顺序、三机部署与配置项见 `02_运行说明与文档/README.md` 与 `.env.example`。",
        "",
        "## 目录",
        "",
    ]
    for folder in sorted(by_folder):
        folder_files = sorted(by_folder[folder], key=lambda p: str(p))
        size_mb = round(sum(p.stat().st_size for p in folder_files) / 1024 / 1024, 2)
        lines.append(f"### {folder}（{len(folder_files)} 个文件，{size_mb} MB）")
        lines.append("")
        for path in folder_files[:40]:
            lines.append(f"- `{path.relative_to(STAGE / folder)}`")
        if len(folder_files) > 40:
            lines.append(f"- …… 其余 {len(folder_files) - 40} 个文件")
        lines.append("")

    lines += [
        "## 口径与限制（引用数据时请照此表述）",
        "",
        "- 中心侧直连压测：100 台 / 18000 批 / 180000 条，P95 78.07 ms，在中心库 `DELETE + FULL` 下测得；",
        "- 三端实时延迟：无积压窗口 312 批 P95 740 ms（目标 ≤1000 ms，已达标）；过载窗口 P95 174.6 s 为排队所致，不作实时指标；",
        "- 三端持续吞吐：约 90 条/秒，**未达** 100 条/秒目标，瓶颈在发送端批次组织与网关转发节拍；",
        "- 异常检测：自造 14 例（严格 9/9、宽松 11/11、定位 5/5、误报 0/3）+ 协作者独立 700 例（600/600 检出与定位、0/100 误报）；",
        "- WL-001 仅有历史签名样例与其登记公钥，本机无配套私钥；现签演示请用 NM/SL/WE/LK/DR/SB 六台。",
        "",
        "## 匿名声明",
        "",
        "本包内代码、文档、数据与路径均不含团队名称、成员姓名、单位、导师与联系方式；",
        "设备私钥（`keys/`）、SQLite 数据库与虚拟环境未包含在内。",
        "",
    ]
    (STAGE / "00_支撑材料目录与说明.md").write_text("\n".join(lines), encoding="utf-8")
    files = [p for p in STAGE.rglob("*") if p.is_file()]

    # ---- 自检 ----
    bad = [p for p in files if p.suffix in (".db", ".bundle", ".zip")
           or p.name.endswith((".db-wal", ".db-shm")) or "\\keys\\" in str(p)]
    leaks = []
    for path in files:
        if path.suffix.lower() not in (".md", ".txt", ".py", ".json", ".csv", ".cmd", ".conf",
                                       ".js", ".html", ".css"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for word in IDENTITY_PATTERN:
            if word in text:
                leaks.append((str(path.relative_to(STAGE)), word))
                break

    print("\n==== 自检 ====")
    print("私钥/数据库/压缩包：", "未混入 OK" if not bad else
          "混入 " + ", ".join(str(p.relative_to(STAGE)) for p in bad))
    print("匿名词：", "未发现 OK" if not leaks else
          "\n".join(f"  {path}（{word}）" for path, word in leaks))
    print(f"总大小：{total_mb} MB（上限 4GB）")
    print("分类统计：", {k: len(v) for k, v in sorted(by_folder.items())})

    ZIP_PATH.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.relative_to(STAGE))
    zip_mb = round(ZIP_PATH.stat().st_size / 1024 / 1024, 2)
    print(f"\n已生成：{ZIP_PATH}（{zip_mb} MB）")
    print(f"解压后目录：{STAGE}")


if __name__ == "__main__":
    main()
