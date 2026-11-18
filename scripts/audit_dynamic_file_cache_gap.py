"""Recheck saved two-guest evidence without changing its original verdict."""
import argparse
import json
from pathlib import Path

from report_two_child_cache import check_targets, ROLES
from report_dynamic_file_cache import analyze


def audit(raw):
    run = json.loads((raw/'result.json').read_text())
    original = json.loads((raw/'target-check.json').read_text())
    recomputed = check_targets(raw, run)
    unchanged = (original['status'] == recomputed['status'] and original['errors'] == recomputed['errors'])
    guests = {}
    for role in ROLES:
        live = raw/'live-cache'/role
        marker = json.loads((live/'marker.json').read_text())
        if marker['sandbox_id'] != run['guests'][role]['sandbox_id']:
            raise ValueError('saved sandbox identity mismatch')
        guests[role] = dict(sandbox_id=marker['sandbox_id'],
            dynamic_file_cache=analyze(live, live/'file-query-download/file-query'),
            runtime_damon=dict(status='UNAVAILABLE', values=None,
                reason='Only startup command line was saved; no runtime DAMON sidecars in this batch'))
    return dict(status='PASS' if unchanged else 'FAIL', raw_directory=str(raw),
        historical_verdict_preserved=unchanged, original_status=original['status'],
        original_errors=original['errors'], recomputed_status=recomputed['status'],
        recomputed_errors=recomputed['errors'], guests=guests,
        interpretation='PASS means offline preservation checks passed, not that the original run passed')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--raw-directory', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    value = audit(args.raw_directory)
    with args.output.open('x') as output:
        json.dump(value, output, indent=2)
        output.write('\n')
    print('status='+value['status']+' result='+str(args.output))
    return 0 if value['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
