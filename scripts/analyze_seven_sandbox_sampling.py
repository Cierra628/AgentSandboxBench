#!/usr/bin/env python3
"""Replay saved cache-query rows and estimate seven-sandbox sampling costs offline."""

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import csv
import json
from itertools import combinations, pairwise
from math import fsum, prod
from pathlib import Path
import random
import statistics
import struct
from types import SimpleNamespace

from audit_provider_segments import independent_segments
from kernel_cache_metadata import MASK, PHYSICAL_MASK, PRESENT, SWAPPED, decode
from provider_slot_cache import (
    FILE,
    PAGE,
    UNIT,
    UNKNOWN,
    account_observed_provider,
    provider_context,
    recount_point,
    segment_sources,
)
from snapshot_cache_residency import SnapshotCache
from snapshot_layer_metadata import RawMemoryLayer


RATIOS = (1, 2, 5, 10, 25)
RATES = (9, 12, 16)  # microseconds per confirmed-PFN-equivalent
REPEATS = 200
SEED = 20261118
PAGE_PER_MIB = 2**20 // PAGE
ROW = struct.Struct("<4Q")
LAYERS = ("host", "backend", "lower")


@dataclass
class Candidate:
    direct: int
    lower: int
    version: str | None
    physical: set[int]


def score_row(guest, before, after, host, direct, lower):
    _, word, middle, last = host
    _, direct_offset, first_direct, last_direct = direct
    _, lower_offset, first_lower, last_lower = lower
    record = Candidate(direct_offset, lower_offset, None, set())
    if before[guest] != after.get(guest) or ((word ^ middle) | (middle ^ last)) & PHYSICAL_MASK:
        return record
    present = bool(word & PRESENT)
    file_mapping = present and bool(word & FILE)
    direct_confirmed = first_direct == last_direct and bool(first_direct & PRESENT)
    if file_mapping and direct_confirmed and (word & MASK) != (first_direct & MASK):
        return record
    current = not word & (PRESENT | SWAPPED) or file_mapping
    if present:
        record.physical.add(word & MASK)
    if current and direct_confirmed:
        record.physical.add(first_direct & MASK)
    if current and first_lower == last_lower and first_lower & PRESENT:
        record.physical.add(first_lower & MASK)
    if 0 in record.physical:
        raise ValueError("a confirmed page has no visible PFN")
    record.version = "current" if current else "old" if present else "unknown"
    return record


def provider_paths(first, last):
    """Yield stable provider PFNs and the direct offsets needed for each proof."""
    for left, right in zip(first["rows"], last["rows"], strict=True):
        if left["name"] != right["name"] or left["first_address"] != right["first_address"]:
            raise ValueError("provider frame identity changed")
        headers = [frame[edge] for frame in (left, right) for edge in ("before", "after")]
        header = headers[0]
        address, capacity, offset, length, state = header[2:7]
        if any(row[6] == 5 for row in headers):
            continue
        stable_header = all(row == header for row in headers) and header[1] % 2 == 0
        legacy = stable_header and state == 3 and 0 < length <= capacity
        same_allocation = all(row[2:4] == header[2:4] for row in headers)
        segments = segment_sources(left, right, capacity) if same_allocation else []
        for index, (word, end) in enumerate(zip(left["pagemap"], right["pagemap"], strict=True)):
            start = left["first_address"] + index * PAGE - address
            if not word & PRESENT or not word & MASK or word != end or not 0 <= start <= capacity - PAGE:
                continue
            if legacy and start + PAGE <= length:
                begin = offset + start
                yield word & MASK, frozenset(range(begin // PAGE, (begin + PAGE - 1) // PAGE + 1))
            pieces = segments[start // UNIT : start // UNIT + PAGE // UNIT]
            if len(pieces) == PAGE // UNIT and None not in pieces:
                yield word & MASK, frozenset(
                    key for begin in pieces for key in range(begin // PAGE, (begin + UNIT - 1) // PAGE + 1)
                )


def current_offsets(records, aliases):
    observed = Counter()
    states = defaultdict(set)
    for row in records:
        if row.direct != UNKNOWN:
            observed[row.direct] += 1
            if row.version is not None:
                states[row.direct].add(row.version)
    # An unqueried alias could disagree with this version, so defer that offset.
    return {offset for offset, kinds in states.items() if kinds == {"current"} and observed[offset] == len(aliases[offset])}


def scalar_runs(offsets):
    ordered = sorted(set(offsets) - {UNKNOWN})
    return len(ordered) - sum(right == left + 1 for left, right in pairwise(ordered))


def inclusion_probability(proofs, size, count):
    """Compute the chance that at least one complete set of required rows is drawn."""
    if all(len(proof) == 1 for proof in proofs):
        misses = size - count
        return 1 - prod((misses - i) / (size - i) for i in range(len(proofs)))

    def probability_all(required):
        return prod((count - i) / (size - i) for i in range(len(required)))

    return fsum(
        (-1) ** (order + 1) * probability_all(frozenset.union(*subset))
        for order in range(1, len(proofs) + 1)
        for subset in combinations(proofs, order)
    )


def exact_weights(proofs_by_pfn, size, count):
    weights = {}
    for physical, proofs in proofs_by_pfn.items():
        probability = inclusion_probability(tuple(proofs), size, count)
        if probability <= 0:
            raise ValueError("a full-query PFN has zero probability under the sample design")
        weights[physical] = 1 / probability
    return weights


def p95(values):
    return sorted(values)[(95 * len(values) + 99) // 100 - 1]


def print_table(headers, rows):
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        print("| " + " | ".join(map(str, row)) + " |")
    print()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify_offsets(raw, prefix, guests, direct, lower):
    identity = read_json(raw / "snapshot-backend.json")["identity"]
    maps = read_json(raw / f"{prefix}-maps.json")
    reader = SimpleNamespace(slots=read_json(raw / "ram-attestation.json")["slots"],
                             identity=(identity["device"], identity["inode"]))
    reader.regions = maps["before"]
    offsets_before = list(SnapshotCache.offsets(reader, guests))
    if offsets_before != [row[1] for row in direct]:
        raise ValueError("archived direct offsets differ from pre-query maps")
    reader.regions = maps["after"]
    offsets_after = SnapshotCache.offsets(reader, guests)
    for index, (before, after) in enumerate(zip(offsets_before, offsets_after, strict=True)):
        if before != after and (direct[index][3] or lower[index][3]):
            raise ValueError("offset changed but the final query word was retained")

    directory = raw / "lower-metadata"
    metadata = read_json(directory / "metadata.json")
    backend = read_json(raw / "lower-query/snapshot-backend.json")
    if (backend["identity"]["device"], backend["identity"]["inode"], backend["mapping_length"]) != (
        metadata["device"], metadata["inode"], metadata["size_bytes"]
    ):
        raise ValueError("archived lower metadata and reader identity disagree")
    layer = RawMemoryLayer.from_saved(directory)
    if list(layer.offsets([row[1] for row in direct])) != [row[1] for row in lower]:
        raise ValueError("archived lower offsets differ from the saved index")


def build_point(raw, saved, reference):
    point = saved["point"]
    prefix = f"pair-{point:04d}"
    before, after = ((raw / f"{prefix}-{edge}.bin").read_bytes() for edge in ("before", "after"))
    header, left = decode(before)
    _, right = decode(after)
    guest_ids = sorted(guest for guest, lifetime in left.items() if lifetime)
    rows = [list(ROW.iter_unpack((raw / f"{prefix}-{layer}.bin").read_bytes())) for layer in LAYERS]
    if any([row[0] for row in layer] != guest_ids for layer in rows):
        raise ValueError(f"point {point}: query rows do not match the initial candidate list")
    verify_offsets(raw, prefix, guest_ids, rows[1], rows[2])

    frames = read_json(raw / f"{prefix}-provider.json")
    first, last = frames["first"], frames["last"]
    population = [score_row(guest, left, right, *values) for guest, *values in zip(guest_ids, *rows, strict=True)]
    aliases = defaultdict(set)
    proofs = defaultdict(set)
    for i, record in enumerate(population):
        if record.direct != UNKNOWN:
            aliases[record.direct].add(i)
        for physical in record.physical:
            proofs[physical].add(frozenset((i,)))
    three = set(proofs)
    paths = set(provider_paths(first, last))
    full_current = current_offsets(population, aliases)
    provider = set()
    for physical, offsets in paths:
        if offsets <= full_current:
            provider.add(physical)
            proofs[physical].add(frozenset().union(*(aliases[key] for key in offsets)))

    recounted, full = recount_point(raw, saved)
    _, _, _, online_three = provider_context(before, after, *rows)
    independent, audit = independent_segments(before, after, *rows, first, last, three)
    if (three != online_three or set(proofs) != full or audit != full
            or any(saved[key] != value for key, value in independent.items())
            or len(full) != int(reference["pfns"]) or len(guest_ids) != int(reference["rows"])
            or int(reference["words"]) != 4 * len(guest_ids) or int(reference["missing"]) or int(reference["extra"])):
        raise ValueError(f"point {point}: full PFN sets or receipts do not agree")
    return header, population, paths, aliases, proofs, provider, three, recounted, (before, after, rows, first, last)


def stage_costs(saved):
    stages = saved["host_stage_costs"]

    def ms(*names):
        return sum(stages[name]["elapsed_ns"] for name in names) / 1e6

    full = saved["host_elapsed_ns"] / 1e6
    guest = ms("guest_before", "guest_after")
    vmm = ms("vmm_first", "vmm_middle", "vmm_last")
    shared = ms("provider_first", "provider_last")
    direct = ms("backend_query_first", "backend_query_last")
    lower = ms("lower_query_first", "lower_query_last")
    fixed = full - shared - direct - lower
    return full, fixed, guest, vmm, shared, direct, lower


def seconds(values):
    return " / ".join(f"{value / 1000:.4f}" for value in values)


def evaluation(point, percent, population, aliases, paths, proofs, reference, costs):
    size = len(population)
    count = (size * percent + 99) // 100
    rng = random.Random(SEED + 100 * point + percent)
    full_direct = scalar_runs(row.direct for row in population)
    full_lower = scalar_runs(row.lower for row in population)
    truth = len(proofs)
    weights = exact_weights(proofs, size, count)
    error, oracle_error, run_calls, run_wall = [], [], [], []
    _, fixed, _, _, shared, direct, lower = costs

    for repeat in range(REPEATS):
        indices = sorted(rng.sample(range(size), count))
        sample = [population[i] for i in indices]
        physical = {pfn for row in sample for pfn in row.physical}
        current = current_offsets(sample, aliases)
        physical.update(pfn for pfn, offsets in paths if offsets <= current)
        if repeat == 0 and all(len(group) == 1 for group in aliases.values()):
            before, after, rows, first, last = reference
            subset = [[layer[i] for i in indices] for layer in rows]
            _, replayed = account_observed_provider(before, after, *subset, first, last)
            if replayed != physical:
                raise ValueError(f"point {point}, {percent}%: sampled PFN set differs from archived accounting rule")
        error.append((len(physical) * size / count - truth) / truth)
        oracle_error.append(abs(fsum(weights[pfn] for pfn in physical) - truth) / truth)
        sampled_direct = scalar_runs(row.direct for row in sample)
        sampled_lower = scalar_runs(row.lower for row in sample)
        run_calls.append(2 * (sampled_direct + sampled_lower))
        run_wall.append(fixed + shared + direct * sampled_direct / full_direct + lower * sampled_lower / full_lower)

    absolute = list(map(abs, error))
    variable = [rate * truth * count / size / 1000 for rate in RATES]
    single = [fixed + shared + cost for cost in variable]
    serial = [7 * (fixed + cost) + shared for cost in variable]
    return (
        count, statistics.median(absolute), p95(absolute), statistics.mean(error), p95(oracle_error),
        p95(run_calls), 2 * (full_direct + full_lower), p95(run_wall), single, serial,
    )


def build_tables(packet, raw):
    with (packet / "snapshot-query-isolation-20261118/points.csv").open(encoding="utf-8", newline="") as stream:
        references = {int(row["point"]): row for row in csv.DictReader(stream)}
    saved_points = sorted(map(json.loads, (raw / "samples.jsonl").read_text().splitlines()), key=lambda row: row["point"])
    if [row["point"] for row in saved_points] != list(range(5)):
        raise ValueError("expected five distinct saved points, 0–4")

    inputs, stage_rows, budget, runs = [], [], [], []
    ratio_summary = defaultdict(list)
    for saved in saved_points:
        point, round_id = saved["point"], saved["shared_round"]["round"]
        label = f"{point}／{round_id}"
        header, population, paths, aliases, proofs, provider, three, recounted, reference = build_point(raw, saved, references[point])
        total, size = len(proofs), len(population)
        added = provider - three
        joint = sum(all(len(proof) > 1 for proof in proofs[pfn]) for pfn in added)
        inputs.append((label, f"`pair-{point:04d}-before.bin`", header["records"], header["stale_records"], size,
                       len(three), len(added), joint, total,
                       recounted["host_four_segment_unconfirmed_guest_capacity_bytes"] // PAGE))
        costs = stage_costs(saved)
        full, fixed, guest, vmm, shared, direct, lower = costs
        factor = total / size
        stage_rows.append((label, f"{full:.3f}", f"{fixed:.3f}", f"{guest:.3f}", f"{vmm:.3f}", f"{shared:.3f}",
                           f"{direct + lower:.3f}", f"{factor:.4f}", " / ".join(f"{rate * factor:.2f}" for rate in RATES)))
        for percent in RATIOS:
            count, median, tail, bias, oracle, calls, full_calls, run_wall, single, serial = evaluation(
                point, percent, population, aliases, paths, proofs, reference, costs
            )
            budget.append((label, f"{percent}%", f"{count:,} / {size:,}", f"{median:.3%}", f"{tail:.3%}",
                           f"{bias:+.3%}", f"{tail * total / PAGE_PER_MIB:.3f}", seconds(single),
                           seconds(serial), seconds(single), "未证实"))
            runs.append((label, f"{percent}%", f"{calls:,} / {full_calls:,}", f"{run_wall / 1000:.4f}", f"{oracle:.3%}"))
            ratio_summary[percent].append((tail, point, max(single), run_wall))

    print_table(("点／轮", "候选文件", "表头 records", "stale", "有效候选 N", "前三层 PFN",
                 "设备 provider 新增 PFN", "其中需联查候选", "四来源去重 T", "未确认 guest 页"), inputs)
    print_table(("点／轮", "历史整点 ms", "分支保留 F ms", "guest 双导出 ms", "VMM 全槽 ms",
                 "共享 P ms", "后备＋下层原查询 ms", "确认 PFN／候选", "9 / 12 / 16 档换算 µs／候选"), stage_rows)
    print_table(("点／轮", "抽样比例", "实抽数 / 候选数", "绝对相对误差中位数",
                 "绝对相对误差 P95", "有符号偏差", "绝对误差 P95（MiB）", "单实例 s",
                 "七份串行 s", "七实例整轮 s（理想）", "实际整轮是否低于 1 秒"), budget)
    print_table(("点／轮", "抽样比例", "标量 mincore 调用 P95 / 原全量",
                 "按段计费理想时间 P95（s）", "全量去偏对照误差 P95"), runs)
    summary = []
    for percent, values in ratio_summary.items():
        worst = max(values)
        summary.append((f"{percent}%", worst[1], f"{worst[0]:.3%}", "通过" if worst[0] <= .05 else "未通过",
                        f"{max(value[2] for value in values) / 1000:.4f}", f"{max(value[3] for value in values) / 1000:.4f}"))
    print_table(("比例", "最差误差点", "最差绝对相对误差 P95", "五点精度",
                 "按页理想上表最大值（s）", "按段理想 P95 最大值（s）"), summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path, help="unpacked handoff-evidence-20261118 directory")
    parser.add_argument("--raw", type=Path, required=True, help="saved restored-g1 directory")
    args = parser.parse_args()
    try:
        build_tables(args.evidence, args.raw)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Cannot replay the saved inputs: {error}\n")


if __name__ == "__main__":
    main()
