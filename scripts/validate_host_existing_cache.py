"""One sparse self-owned file, two live cgroups; no sandbox or task payload I/O."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from host_existing_cache_owner import query, CGROOT

ROOT = Path(__file__).resolve().parents[1]
PAGE = os.sysconf('SC_PAGE_SIZE')


def worker(role, out):
    path = out/'fixture.bin'
    if role == 'a':
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.ftruncate(fd, 2*2**20 + PAGE)
        offset = 0
    else:
        fd = os.open(path, os.O_RDWR)
        offset = 2**20
    try:
        assert os.pwrite(fd, b'X'*PAGE, offset) == PAGE
    finally:
        os.close(fd)
    group = next(l[3:] for l in Path('/proc/self/cgroup').read_text().splitlines() if l.startswith('0::'))
    (out/(role+'-ready.json')).write_text(json.dumps(dict(pid=os.getpid(),group=group))+'\n')
    deadline = time.monotonic()+100
    while not (out/'release').exists():
        if time.monotonic() > deadline:
            raise TimeoutError('Fixture owner deadline reached')
        time.sleep(.05)


def run(out):
    out.mkdir(parents=True, exist_ok=False)
    name = 'asboldcache'+out.name.lower().replace('-', '')
    slice_name = name+'.slice'
    workers = []; streams = []; units = []
    result = dict(status='FAIL', new_sandbox_actions=0, actual_fixture_written_bytes=2*PAGE)
    try:
        for role in ('a', 'b'):
            unit = name+role+'.service'; units.append(unit)
            log = (out/(role+'.log')).open('x'); streams.append(log)
            workers.append(subprocess.Popen([
                'systemd-run','--quiet','--wait','--pipe','--collect','--unit='+unit,
                '--slice='+slice_name,'--property=MemoryAccounting=yes','--property=RuntimeMaxSec=110',
                sys.executable,'-B',str(Path(__file__).resolve()),'--worker',role,'--output',str(out)],
                stdout=log,stderr=subprocess.STDOUT))
            deadline = time.monotonic()+15
            while not (out/(role+'-ready.json')).exists():
                assert workers[-1].poll() is None, 'Fixture owner failed before setup'
                if time.monotonic() > deadline: raise TimeoutError('Fixture setup deadline')
                time.sleep(.05)
        groups = [CGROOT/json.loads((out/(r+'-ready.json')).read_text())['group'].lstrip('/') for r in ('a','b')]
        ids = [p.stat().st_ino for p in groups]
        observed = query(out/'fixture.bin', groups[0], out/'query')
        assert observed['status'] == 'OBSERVED'
        assert observed['inside_bytes'] == observed['outside_bytes'] == PAGE
        assert observed['unknown_bytes'] == 0 and observed['resident_pages'] == 2
        assert {o['online_group_id'] for o in observed['owners']} == set(ids)
        assert all(o['raw_group_online'] and o['raw_group_id']==o['online_group_id'] for o in observed['owners'])
        assert observed['residency_observed']['pages'] == 513
        result.update(status='PASS', reference_group=str(groups[0]), owner_group_ids=ids,
                      inside_bytes=observed['inside_bytes'], outside_bytes=observed['outside_bytes'],
                      unknown_bytes=observed['unknown_bytes'], hole_pages=511,
                      query_ns=observed['query_ns'], total_query_ns=observed['total_ns'])
    except BaseException as exc:
        result['error'] = repr(exc)
        raise
    finally:
        (out/'release').touch()
        for proc, unit in zip(workers, units):
            try: proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                subprocess.run(['systemctl','stop',unit],timeout=15,check=True)
                proc.wait(timeout=5)
        for stream in streams: stream.close()
        subprocess.run(['systemctl','stop',slice_name],timeout=15,check=True)
        result['workers_exited_zero'] = all(p.returncode == 0 for p in workers)
        result['owned_groups_absent'] = not (CGROOT/slice_name).exists()
        if result['status'] == 'PASS':
            assert result['workers_exited_zero'] and result['owned_groups_absent']
            (out/'fixture.bin').unlink()
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--worker',choices=('a','b'))
    args = parser.parse_args()
    if args.worker:
        worker(args.worker,args.output)
    else:
        out=args.output or ROOT/'.artifacts/host-existing-cache-fixture'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid()))
        run(out.resolve())


if __name__ == '__main__':
    main()
