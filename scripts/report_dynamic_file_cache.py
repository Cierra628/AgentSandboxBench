"""Offline join of named-file sidecars and existing live guest/host page evidence."""
import argparse
import json
from pathlib import Path

from file_cache_metadata import account, decode as decode_file
from kernel_cache_metadata import decode as decode_guest
from live_task_cache import HOST_ROW


def damon_delta(before, after):
    keys = ('nr_reclaim_tried_regions', 'bytes_reclaim_tried_regions',
            'nr_reclaimed_regions', 'bytes_reclaimed_regions', 'nr_quota_exceeds')
    values, unavailable = {}, {}
    for key in keys:
        a, b = before['values'].get(key), after['values'].get(key)
        if a is None or b is None or b < a:
            values[key] = None
            unavailable[key] = 'missing counter or counter decreased'
        else:
            values[key] = b - a
    return dict(values=values, unavailable=unavailable,
        enabled_before=before['values'].get('enabled'), enabled_after=after['values'].get('enabled'),
        attribution='guest-wide region-operation statistics, not a file eviction cause or freed host bytes')


def analyze(live, sidecars):
    if not sidecars.is_dir():
        return dict(status='UNAVAILABLE', observations=[],
                    reason='No named-file or runtime DAMON sidecars were saved; old PFN lifetimes cannot recover file identity')
    marker = json.loads((live/'marker.json').read_text())
    declaration = marker.get('file_cache_query')
    if not declaration:
        raise ValueError('no named-file declaration in exporter identity')
    observations = []
    for line in (live/'samples.jsonl').read_text().splitlines():
        sample = json.loads(line)
        receipts, queries = [], []
        for publication in (sample['guest_publication_before'], sample):
            number = publication['publication']
            receipt = json.loads((sidecars/f'file-{number:04}.json').read_text())
            if (receipt['publication'] != number or receipt['sandbox_id'] != marker['sandbox_id'] or
                    receipt['boot_id'] != marker['boot_id']):
                raise ValueError('file-cache sidecar belongs to another publication/guest')
            if receipt['file_cache']['path'] != declaration['path']:
                raise ValueError('file-cache path differs from declared target')
            receipts.append(receipt)
            if receipt['file_cache']['status'] == 'UNAVAILABLE':
                queries.append(None)
                continue
            query = decode_file((sidecars/f'file-{number:04}.bin').read_bytes())
            header = query[0]
            if not (publication['guest_start_ns'] <= header['start_ns'] <= header['end_ns'] <= publication['guest_end_ns']):
                raise ValueError('file-cache query is outside its publication interval')
            if header != receipt['file_cache']['header']:
                raise ValueError('file-cache receipt differs from binary header')
            queries.append(query)
        observation = dict(point=sample['point'], publications=[r['publication'] for r in receipts],
            file_query_before=receipts[0], file_query_after=receipts[1],
            damon_delta=damon_delta(receipts[0]['damon'], receipts[1]['damon']))
        if any(q is None for q in queries):
            observation.update(status='UNAVAILABLE', current_file_cache_after_bytes=None,
                confirmed_host_union_bytes=None, reason='target file unavailable in at least one query')
        elif receipts[0]['file_cache']['identity'] != receipts[1]['file_cache']['identity']:
            observation.update(status='UNAVAILABLE', current_file_cache_after_bytes=None,
                confirmed_host_union_bytes=None, reason='file content metadata or identity changed between queries')
        else:
            prefix = live/f"pair-{sample['point']:04}"
            guest = [decode_guest(Path(str(prefix)+suffix).read_bytes())
                     for suffix in ('-before.bin', '-after.bin')]
            raw = Path(str(prefix)+'-host.bin').read_bytes()
            host = [dict(zip(('guest_pfn', 'first', 'middle', 'last'), row))
                    for row in HOST_ROW.iter_unpack(raw)]
            try:
                observation.update(account(*queries, *guest, host))
            except ValueError as error:
                observation.update(status='UNAVAILABLE', current_file_cache_after_bytes=None,
                    confirmed_host_union_bytes=None, reason=str(error))
        observations.append(observation)
    available = [o for o in observations if o['status'] != 'UNAVAILABLE']
    return dict(version='named-file-pair-v1',
        status='UNAVAILABLE' if not available else 'PARTIAL' if any(o['status'] != 'OBSERVED' for o in observations) else 'OBSERVED',
        observations=observations, sandbox_id=marker['sandbox_id'], boot_id=marker['boot_id'],
        scope='declared file only; current lifetimes, not the initial written PFNs',
        validation='offline evidence join; acquisition behavior requires separate live validation',
        old_fixed_initial_check='unchanged; not replaced by this analysis')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-directory', required=True, type=Path)
    parser.add_argument('--sidecars', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    value = analyze(args.live_directory, args.sidecars)
    with args.output.open('x') as output:
        json.dump(value, output, indent=2)
        output.write('\n')
    print('status='+value['status']+' result='+str(args.output))


if __name__ == '__main__':
    main()
