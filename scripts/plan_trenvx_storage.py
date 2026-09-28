"""Conservative retained-archive budget; read-only except the optional report."""
import argparse
import json
from pathlib import Path
import shutil

GIB = 1024**3
ROOT = Path(__file__).resolve().parents[1]


def budget(free_bytes, remaining_runs):
    if remaining_runs < 1:
        raise ValueError('remaining_runs must be positive')
    # Keep the pair driver's 40 GiB launch margin after all retained archives.
    # 6 GiB per arm is a planning allowance, not a bound on compressed size.
    required = (40 + 6 * remaining_runs) * GIB
    return dict(status='READY' if free_bytes >= required else 'BLOCKED',
                free_bytes=free_bytes, remaining_runs=remaining_runs,
                launch_margin_bytes=40 * GIB, retained_bytes_per_run=6 * GIB,
                required_free_bytes=required, shortfall_bytes=max(0, required-free_bytes),
                scope='planning estimate; runtime disk guard remains required')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pairs', type=int, default=5)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.pairs < 1:
        parser.error('--pairs must be positive')
    result = budget(shutil.disk_usage(ROOT).free, 2 * args.pairs)
    if args.output:
        # Never overwrite a previous plan or experimental result.
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
    print(json.dumps(result))
    return 0 if result['status'] == 'READY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
