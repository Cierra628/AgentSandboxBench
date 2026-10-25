"""Explain saved provider residency without changing cache admission or metrics."""
import csv
import json
from pathlib import Path
import struct

from kernel_cache_metadata import decode
from provider_slot_cache import PAGE, account_provider
from report_provider_initial_pages import initial_pages
from report_provider_offset_candidates import classify_sources
from report_provider_segment_unknown import classify

FIELDS = ('current_cache_bytes', 'old_snapshot_only_bytes', 'unused_allocation_bytes',
    'source_absent_both_bytes', 'mixed_sources_bytes', 'unresolved_bytes')
LABELS = ('当前缓存副本', '仅旧快照副本', '已分配、尚未用于请求',
    '来源前后不在缓存表', '混合来源', '剩余未知')
COLORS = ('#218c68', '#8262ac', '#c7ccd1', '#67a7c4', '#e3ae47', '#d86464')


def partition(resident, current, old, unused, sources):
    """Physical sets, not arithmetic subtraction of independently counted totals."""
    unknown = resident - current - old
    assert current <= resident and old <= resident and unused <= unknown
    other = sources.get('all_sources_absent_both', set())
    mixed = set().union(*(sources.get(k, set()) for k in
        ('mixed_absent_and_stable_cache', 'mixed_absent_and_new_cache', 'other_with_absent_source')))
    assert other | mixed <= unknown
    categories = [current, old - current, unused, other, mixed]
    seen = set()
    for values in categories:
        assert not seen & values, 'provider residency classifications overlap'
        seen.update(values)
    categories.append(resident - seen)
    assert set().union(*categories) == resident
    return dict(zip(FIELDS, categories, strict=True))


def point_breakdown(directory, saved, slots, identity):
    prefix = directory / f"pair-{saved['point']:04}"
    read = lambda suffix: Path(str(prefix) + suffix).read_bytes()
    rows = struct.Struct('<4Q')
    before, after = read('-before.bin'), read('-after.bin')
    raw = json.loads(read('-provider.json'))
    args = (before, after, *(list(rows.iter_unpack(read(s))) for s in
        ('-host.bin', '-backend.bin', '-lower.bin')), raw['first'], raw['last'])
    legacy, segments, details = {}, {}, {}
    account_provider(*args, details=legacy)
    account_provider(*args, segmented=True, details=segments)
    original = classify(*args, details=details)
    _, trace = classify_sources(details, json.loads(read('-maps.json')), slots, identity,
        decode(before)[1], decode(after)[1])
    sources = {}
    for row in trace['pages']:
        sources.setdefault(row['classification'], set()).add(row['pfn'])
    initial = initial_pages(raw['first'], raw['last'])
    current, old = legacy['current'] | segments['current'], legacy['old'] | segments['old']
    sets = partition(legacy['resident'], current, old, initial['whole_initial'], sources)
    result = dict(point=saved['point'], timestamp_ns=saved['timestamp_ns'],
        **{key: len(value) * PAGE for key, value in sets.items()},
        whole_resident_bytes=len(legacy['resident']) * PAGE,
        boundary_pages=len(initial['boundary']), current_old_overlap_bytes=len(current & old) * PAGE,
        original_unattributed_bytes=original['unattributed_pages'] * PAGE)
    assert result['whole_resident_bytes'] == saved['provider_whole_allocation_resident_bytes']
    assert result['original_unattributed_bytes'] == saved['provider_segment_unattributed_resident_bytes']
    assert len(current) * PAGE == saved['host_provider_combined_current_bytes']
    assert len(old) * PAGE == saved['host_provider_combined_old_snapshot_bytes']
    assert len(legacy['three'] | current) * PAGE == saved['host_four_segment_current_union_bytes']
    assert sum(result[key] for key in FIELDS) == result['whole_resident_bytes']
    reasons = {}
    for pfn in sets['unresolved_bytes']:
        kinds = details['reasons'][pfn]
        key = next(iter(kinds)) if len(kinds) == 1 else 'multiple_unknown_aliases'
        reasons[key] = reasons.get(key, 0) + PAGE
    result['unresolved_reason_bytes'] = dict(sorted(reasons.items()))
    assert sum(reasons.values()) == result['unresolved_bytes']
    return result


def build_breakdown(directories, groups, names):
    sessions = {}
    for directory, group, name in zip(directories, groups, names, strict=True):
        selected = [p for p in group if 'host_four_segment_current_union_bytes' in p]
        if not selected:
            sessions[name] = dict(status='NOT_APPLICABLE', points=[],
                reason='No segment-based provider observations; no zero residency inferred.')
            continue
        slots = json.loads((directory / 'ram-attestation.json').read_text())['slots']
        identity = json.loads((directory / 'snapshot-backend.json').read_text())['identity']
        provider = json.loads((directory / 'provider/identity.json').read_text())
        assert all(s['negotiated_flags'] & 4 and s['content_validity'] == 'before_overwrite'
            and s['segments']['schema'] == 1 for s in provider['slots'])
        sessions[name] = dict(status='PASS' if len(selected) == len(group) else 'PARTIAL',
            points=[point_breakdown(directory, p, slots, identity) for p in selected],
            unclassified_points=[p['point'] for p in group if p not in selected])
    return dict(schema=1, page_size=PAGE, sessions=sessions,
        category_fields=list(FIELDS),
        scope='Mutually exclusive whole resident provider PFNs per point; boundary pages separate.',
        meanings=dict(source_absent_both_bytes='All source guest pages absent from both endpoint cache tables; not continuous absence or a memory type.',
            mixed_sources_bytes='At least one absent source and other sources; not wholly non-cache.',
            old_snapshot_only_bytes='Old buffer copies excluding PFNs also confirmed current.'),
        original_metrics='UNCHANGED', current_physical_union='UNCHANGED')


def write_breakdown(breakdown, output):
    """Add a readable table and a separate chart; retain the original cache plots."""
    points = [(name, p) for name, session in breakdown['sessions'].items() for p in session['points']]
    with (output / 'provider-buffer.csv').open('w') as stream:
        fields = ['session', 'point', 'timestamp_ns', *FIELDS, 'whole_resident_bytes',
            'boundary_pages', 'current_old_overlap_bytes', 'original_unattributed_bytes']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(dict(session=name, **{k: v for k, v in p.items() if k != 'unresolved_reason_bytes'})
            for name, p in points)
    if not points:
        return
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    active = [(name, s['points']) for name, s in breakdown['sessions'].items() if s['points']]
    # Matplotlib exposes the installed Noto CJK collection under its JP name;
    # the same font includes the Chinese glyphs used by these labels.
    with plt.rc_context({'font.family': 'Noto Sans CJK JP', 'svg.hashsalt': 'provider-residency-v1'}):
        fig, axes = plt.subplots(len(active), 1, figsize=(11, 4.5 * len(active)), squeeze=False)
        for ax, (name, rows) in zip(axes[:, 0], active, strict=True):
            bottom = [0.] * len(rows)
            for key, label, color in zip(FIELDS, LABELS, COLORS, strict=True):
                values = [p[key] / 2**20 for p in rows]
                ax.bar(range(len(rows)), values, bottom=bottom, label=label, color=color, width=.72)
                bottom = [a + b for a, b in zip(bottom, values, strict=True)]
            session = {'parent': '保存前', 'restored': '恢复后'}.get(name, name)
            ax.set(title=f'后端缓冲驻留分类：{session}', ylabel='主机驻留内存（MiB）',
                xlabel='采样点', xticks=range(len(rows)), xticklabels=[p['point'] for p in rows])
            ax.set_ylim(0, max(bottom) * 1.04)
            ax.grid(axis='y', alpha=.2)
        fig.legend(*axes[0, 0].get_legend_handles_labels(), loc='upper center', ncol=3,
            bbox_to_anchor=(.5, 1), frameon=False, fontsize=10)
        fig.text(.5, .015, '仅统计完整分配页；边界页另列，不加入此图。', ha='center', fontsize=10)
        fig.tight_layout(rect=(0, .045, 1, .86))
        fig.savefig(output / 'provider-buffer.svg', metadata={'Date': None})
        fig.savefig(output / 'provider-buffer.png')
        plt.close(fig)
