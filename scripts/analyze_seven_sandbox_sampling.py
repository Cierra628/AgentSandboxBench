#!/usr/bin/env python3
"""Rebuild the seven-sandbox sampling assessment tables from the handoff packet."""

import argparse
import csv
from decimal import Decimal
from pathlib import Path


RATIOS = (1, 2, 5, 10, 25)
RATES = (9, 12, 16)  # microseconds per confirmed-PFN-equivalent
BRANCHES = 7
PAGES_PER_MIB = 256  # 4096-byte pages
MISSING = "缺逐页数据"


def csv_rows(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def markdown_rows(section):
    for line in section.splitlines():
        if line.startswith("|") and line.endswith("|"):
            yield [cell.strip() for cell in line[1:-1].split("|")]


def load_evidence(packet):
    points_path = packet / "snapshot-query-isolation-20261118/points.csv"
    points = sorted(csv_rows(points_path), key=lambda row: int(row["point"]))
    if [int(row["point"]) for row in points] != list(range(5)):
        raise ValueError(f"expected points 0 through 4 in {points_path}")

    stage_path = packet / "four-source-stage-budget-20261118/REPORT.md"
    report = stage_path.read_text(encoding="utf-8")
    layers_section = report.split("## 五个恢复点：逐层成本\n", 1)[1]
    layers_section, totals_section = layers_section.split("## 与整点时间对账\n", 1)
    totals_section = totals_section.split("## 不计位置成本", 1)[0]
    physical_ms = {}
    rounds = {}
    for cells in markdown_rows(layers_section):
        point, sep, round_id = cells[0].partition("／")
        if sep and point.isdecimal() and round_id.isdecimal():
            point = int(point)
            rounds[point] = int(round_id)
            physical_ms[point, cells[1]] = Decimal(cells[4])
    full_ms = {
        int(cells[0]): Decimal(cells[5])
        for cells in markdown_rows(totals_section)
        if cells[0].isdecimal()
    }
    return points, physical_ms, rounds, full_ms


def print_table(headers, rows):
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        print("| " + " | ".join(map(str, row)) + " |")
    print()


def seconds_triplet(milliseconds):
    return " / ".join(f"{value / 1000:.4f}" for value in milliseconds)


def build_tables(packet):
    saved, physical_ms, rounds, full_ms = load_evidence(packet)
    inputs, costs, budget = [], [], []
    for row in saved:
        point, candidates, confirmed, words, missing, extra = (
            int(row[key]) for key in ("point", "rows", "pfns", "words", "missing", "extra")
        )
        if candidates < 1 or confirmed < 1 or words != 4 * candidates or missing or extra:
            raise ValueError(f"invalid reference counts for point {point}")
        label = f"{point}／{rounds[point]}"
        direct = physical_ms[point, "直接后备"]
        lower = physical_ms[point, "快照下层"]
        provider = physical_ms[point, "provider"]
        fixed = full_ms[point] - direct - lower - provider
        if min(fixed, direct, lower, provider) < 0:
            raise ValueError(f"negative cost for point {point}")
        ratio = Decimal(confirmed) / candidates
        five_percent_mib = Decimal(confirmed) / (20 * PAGES_PER_MIB)
        inputs.append((label, f"{candidates:,}", f"{confirmed:,}", f"{five_percent_mib:.3f}"))
        costs.append((label, full_ms[point], fixed, provider, direct + lower, f"{ratio:.4f}",
                      f"{1000 * (direct + lower) / candidates:.2f}",
                      " / ".join(f"{rate * ratio:.2f}" for rate in RATES)))

        for percent in RATIOS:
            count = (candidates * percent + 99) // 100
            variable = [rate * ratio * count / 1000 for rate in RATES]
            single = [fixed + cost + provider for cost in variable]
            # Serial work in the shared-device design pays for the provider pair once.
            serial = [BRANCHES * (fixed + cost) + provider for cost in variable]
            ideal_wall = single  # seven identical branches and no contention or batch tail
            budget.append((label, f"{percent}%", f"—（计划 {count:,} / {candidates:,}）",
                           MISSING, MISSING, MISSING, MISSING, seconds_triplet(single),
                           seconds_triplet(serial), seconds_triplet(ideal_wall), "未证实"))

    print_table(("采样点／共同轮", "查询前候选条数", "全量确认 PFN 数", "5% 门槛（MiB）"), inputs)
    print_table(("采样点／共同轮", "历史整点 ms", "每分支保留 F ms", "共享 P ms",
                 "两层原查询 ms", "确认 PFN／候选", "两层实测均摊 µs／候选",
                 "9 / 12 / 16 档换算 µs／候选"), costs)
    print_table(("采样点／共同轮", "抽样比例", "实抽数 / 候选数", "绝对相对误差中位数",
                 "绝对相对误差 P95", "有符号偏差", "绝对误差 P95（MiB）", "单实例 s",
                 "七份串行 s", "七实例整轮 s（理想情景）", "整轮是否低于 1 秒"), budget)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path, help="unpacked handoff-evidence-20261118 directory")
    args = parser.parse_args()
    try:
        build_tables(args.evidence)
    except (OSError, ValueError, KeyError, IndexError) as error:
        parser.exit(1, f"Cannot rebuild tables from {args.evidence}: {error}\n")


if __name__ == "__main__":
    main()
