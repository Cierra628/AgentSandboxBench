"""Targeted old regular-file cache ownership during non-faulting mincore lookup.

Linux 6.19.9+ research entry only; never part of the periodic sampler.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time

from host_file_monitor import mincore_file

ROOT = Path(__file__).resolve().parents[1]
CGROOT = Path('/sys/fs/cgroup')


def layout():
    assert os.uname().release == '6.19.9+', 'This research probe follows the inspected host source'
    types = json.loads(subprocess.check_output([
        'bpftool', '-j', 'btf', 'dump', 'file', '/sys/kernel/btf/vmlinux', 'format', 'raw'], timeout=25))['types']
    by_id = {t['id']: t for t in types}

    def member(t, name):
        for m in t.get('members', []):
            if m['name'] == name:
                return m['bits_offset'] // 8
            if m['name'] == '(anon)' and by_id[m['type_id']]['kind'] in ('STRUCT', 'UNION'):
                value = member(by_id[m['type_id']], name)
                if value is not None:
                    return m['bits_offset'] // 8 + value
        return None

    fields = {'folio': ['mapping', 'index', 'memcg_data', 'flags', '_flags_1'],
              'address_space': ['host'], 'inode': ['i_ino', 'i_sb'], 'super_block': ['s_dev'],
              'mem_cgroup': ['css'], 'cgroup_subsys_state': ['flags', 'parent', 'cgroup'],
              'cgroup': ['kn'], 'kernfs_node': ['id']}
    offsets = {}
    for name, names in fields.items():
        struct = next(t for t in types if t.get('kind') == 'STRUCT' and t.get('name') == name)
        for field in names:
            offsets[(name + '_' + field).upper()] = member(struct, field)
    flags = next(t for t in types if t.get('kind') == 'ENUM' and t.get('name') == 'pageflags')
    for name in ('PG_head', 'PG_uptodate'):
        offsets[name.upper()] = next(v['val'] for v in flags['values'] if v['name'] == name)
    assert all(v is not None for v in offsets.values())
    return offsets


def program(info, pid, offsets):
    text = r'''
BEGIN { printf("READY\n"); }
tracepoint:syscalls:sys_enter_mincore /pid == TARGET_PID/ {
 @active[tid]=1; @started=1; @totals[5]=count();
}
tracepoint:syscalls:sys_exit_mincore /pid == TARGET_PID && @active[tid]/ {
 if (args->ret != 0) { @totals[6]=count(); }
 delete(@active[tid]);
}
kprobe:filemap_get_entry /pid == TARGET_PID && @active[tid]/ {
 $mapping=(uint64)arg0;
 $inode=*(uint64 *)($mapping+ADDRESS_SPACE_HOST);
 $sb=*(uint64 *)($inode+INODE_I_SB);
 if (*(uint64 *)($inode+INODE_I_INO)==TARGET_INODE &&
     *(uint32 *)($sb+SUPER_BLOCK_S_DEV)==TARGET_DEVICE) {
  $index=(uint64)arg1;
  if (@pending[tid] || $index != @next) { @totals[3]=count(); }
  @next=$index+1; @pending[tid]=$index+1; @mapping[tid]=$mapping;
  @totals[1]=count();
 }
}
kretprobe:filemap_get_entry /pid == TARGET_PID && @pending[tid]/ {
 $index=@pending[tid]-1; $mapping=@mapping[tid]; $f=(uint64)retval;
 @totals[2]=count();
 if (!$f) { @states[0]=count(); }
 else if ($f & 1) { @states[1]=count(); }
 else {
  $flags=*(uint64 *)($f+FOLIO_FLAGS);
  if (!($flags & (1 << PG_UPTODATE))) { @states[3]=count(); }
  else {
   @states[2]=count();
   $order=0;
   if ($flags & (1 << PG_HEAD)) { $order=*(uint64 *)($f+FOLIO__FLAGS_1) & 255; }
   $base=*(uint64 *)($f+FOLIO_INDEX);
   $data=*(uint64 *)($f+FOLIO_MEMCG_DATA);
   $raw=0; $online=0; $depth=0; $css=0; $rawcss=0; $rawflags=0;
   $valid=*(uint64 *)($f+FOLIO_MAPPING)==$mapping && $order<=18 &&
          $base<=$index && $index<$base+((uint64)1 << $order) && $data && !($data & 7);
   if ($valid) {
    $css=($data & ~7)+MEM_CGROUP_CSS; $rawcss=$css;
    $cg=*(uint64 *)($css+CGROUP_SUBSYS_STATE_CGROUP);
    $kn=*(uint64 *)($cg+CGROUP_KN); $raw=*(uint64 *)($kn+KERNFS_NODE_ID);
    $rawflags=*(uint32 *)($css+CGROUP_SUBSYS_STATE_FLAGS);
    while ($css && $depth<64 && !$online) {
     if (*(uint32 *)($css+CGROUP_SUBSYS_STATE_FLAGS) & 2) {
      $cg=*(uint64 *)($css+CGROUP_SUBSYS_STATE_CGROUP);
      $kn=*(uint64 *)($cg+CGROUP_KN); $online=*(uint64 *)($kn+KERNFS_NODE_ID);
     } else { $css=*(uint64 *)($css+CGROUP_SUBSYS_STATE_PARENT); }
     $depth=$depth+1;
    }
   }
   if ($valid && $raw && $online && $data==*(uint64 *)($f+FOLIO_MEMCG_DATA) &&
       *(uint64 *)($f+FOLIO_MAPPING)==$mapping &&
       $base==*(uint64 *)($f+FOLIO_INDEX) &&
       ($rawflags & (uint64)2)==((uint64)*(uint32 *)($rawcss+CGROUP_SUBSYS_STATE_FLAGS) & (uint64)2) &&
       (*(uint32 *)($css+CGROUP_SUBSYS_STATE_FLAGS) & 2)) {
    @owners[$raw,$online,$rawflags & 2]=count();
   } else { @unknown[1]=count(); }
  }
 }
 delete(@pending[tid]); delete(@mapping[tid]);
}
RAW_EVENTS
interval:s:60 { printf("DEADLINE\n"); exit(); }
END {
 print(@totals); print(@states); print(@owners); print(@unknown); print(@changes);
 clear(@totals); clear(@states); clear(@owners); clear(@unknown); clear(@changes);
 clear(@active); clear(@pending); clear(@mapping); clear(@next); clear(@started);
 printf("DONE\n");
}
'''
    events = []
    for number, event in ((1, 'add_to'), (2, 'delete_from')):
        events.append(r'''
rawtracepoint:mm_filemap_EVENT_page_cache /@started/ {
 $f=(uint64)arg0; $mapping=*(uint64 *)($f+FOLIO_MAPPING);
 $inode=*(uint64 *)($mapping+ADDRESS_SPACE_HOST); $sb=*(uint64 *)($inode+INODE_I_SB);
 if (*(uint64 *)($inode+INODE_I_INO)==TARGET_INODE &&
     *(uint32 *)($sb+SUPER_BLOCK_S_DEV)==TARGET_DEVICE) { @changes[NUMBER]=count(); }
}
'''.replace('EVENT', event).replace('NUMBER', str(number)))
    text = text.replace('RAW_EVENTS', '\n'.join(events))
    values = dict(offsets, TARGET_PID=pid, TARGET_INODE=info.st_ino,
                  TARGET_DEVICE=(os.major(info.st_dev) << 20) | os.minor(info.st_dev))
    for name in sorted(values, key=len, reverse=True):
        text = text.replace(name, str(values[name]))
    return text


def parse(text, error):
    assert 'READY\n' in text and 'DONE\n' in text and 'DEADLINE' not in text
    assert not re.search(r'lost\s+[1-9]|ERROR', text + error, re.I), error
    result = {}
    for name in ('totals', 'states', 'unknown', 'changes'):
        result[name] = {int(k): int(v) for k, v in re.findall(r'@' + name + r'\[(\d+)\]:\s*(\d+)', text)}
    result['owners'] = [dict(raw_group_id=int(raw), online_group_id=int(online),
                             raw_group_online=bool(int(active)), pages=int(pages))
                        for raw, online, active, pages in re.findall(
                            r'@owners\[(\d+),\s*(\d+),\s*(\d+)\]:\s*(\d+)', text)]
    return result


def group_ids(group):
    return {p.stat().st_ino for p in (group, *[p for p in group.rglob('*') if p.is_dir()])}


def summarize(probe, row, inside_ids):
    pages = row['pages']; resident = row['resident_pages']; totals = probe['totals']
    assert totals.get(1, 0) == totals.get(2, 0) == pages, 'Every file index needs one paired lookup'
    assert not totals.get(3, 0) and not totals.get(6, 0), 'Index order or syscall error'
    assert sum(probe['states'].values()) == pages
    assert probe['states'].get(2, 0) == resident, 'Probe residency differs from mincore'
    known = sum(o['pages'] for o in probe['owners'])
    unknown = sum(probe['unknown'].values())
    assert known + unknown == resident
    assert not sum(probe['changes'].values()), 'Target cache changed during observation'
    inside = sum(o['pages'] for o in probe['owners'] if o['online_group_id'] in inside_ids)
    return dict(status='OBSERVED' if not unknown else 'INCOMPLETE', page_size=os.sysconf('SC_PAGE_SIZE'),
                resident_pages=resident, inside_pages=inside, outside_pages=known-inside,
                unknown_pages=unknown, inside_bytes=inside*os.sysconf('SC_PAGE_SIZE'),
                outside_bytes=(known-inside)*os.sysconf('SC_PAGE_SIZE'),
                unknown_bytes=unknown*os.sysconf('SC_PAGE_SIZE'), owners=probe['owners'])


def query(path, group, out):
    path = path.resolve(); group = group.resolve()
    assert group.is_relative_to(CGROOT) and group.is_dir()
    info = path.stat(); assert stat.S_ISREG(info.st_mode) and info.st_size > 0
    cap = int(next(l.split(':')[1] for l in Path('/proc/self/status').read_text().splitlines() if l.startswith('CapEff:')), 16)
    assert cap & (1 << 3), 'CAP_FOWNER required: mincore must not fabricate all-resident results'
    out.mkdir(parents=True, exist_ok=False)
    result = dict(status='FAIL', path=str(path), reference_group=str(group),
                  kernel=os.uname().release, scope='Current sequential observation only; no historical backfill')
    process = None; stream = error = None
    started = time.monotonic_ns()
    result['start_monotonic_ns'] = started
    try:
        ids = group_ids(group)
        before = mincore_file(path)
        offsets = layout(); (out/'layout.json').write_text(json.dumps(offsets, indent=2)+'\n')
        (out/'query.bt').write_text(program(info, os.getpid(), offsets))
        stream = (out/'probe.log').open('x'); error = (out/'probe.stderr').open('x')
        process = subprocess.Popen(['bpftrace', '-q', '-B', 'line', str(out/'query.bt')], stdout=stream, stderr=error)
        deadline = time.monotonic()+30
        while 'READY\n' not in (out/'probe.log').read_text():
            if process.poll() is not None:
                raise RuntimeError('Probe failed: '+(out/'probe.stderr').read_text())
            if time.monotonic() > deadline:
                raise TimeoutError('Probe preparation timed out')
            time.sleep(.05)
        begin = time.monotonic_ns(); observed = mincore_file(path)
        result['query_ns'] = time.monotonic_ns()-begin
        result.update(query_start_monotonic_ns=begin, query_end_monotonic_ns=begin+result['query_ns'])
        process.send_signal(signal.SIGINT); process.wait(timeout=15)
        assert process.returncode == 0
        stream.close(); error.close(); stream = error = None
        parsed = parse((out/'probe.log').read_text(), (out/'probe.stderr').read_text())
        after = mincore_file(path)
        assert all((r['device'],r['inode'],r['size'],r['mtime_ns']) ==
                   (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns) and r['stable'] for r in (before,observed,after))
        assert before['resident_pages'] == observed['resident_pages'] == after['resident_pages']
        assert group_ids(group) == ids, 'Reference group tree changed'
        result.update(summarize(parsed, observed, ids), reference_group_ids=sorted(ids),
                      residency_before=before, residency_observed=observed, residency_after=after, probe_counts=parsed)
        for owner in result['owners']:
            owner['online_path'] = next((str(p) for p in (CGROOT, *CGROOT.rglob('*'))
                                       if p.is_dir() and p.stat().st_ino == owner['online_group_id']), None)
    except BaseException as exc:
        result.update(status='FAIL', error=repr(exc))
        raise
    finally:
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        if stream: stream.close()
        if error: error.close()
        result['probe_reaped'] = process is not None and process.poll() is not None
        result['total_ns'] = time.monotonic_ns()-started
        result['end_monotonic_ns'] = started+result['total_ns']
        (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


def _group_alive(pid):
    try: os.killpg(pid, 0)
    except ProcessLookupError: return False
    return True


def _signal_group(pid, sig):
    try: os.killpg(pid, sig)
    except ProcessLookupError: pass  # The owned group may finish during cleanup.


def _stop_group(worker, grace):
    """Only this new session: allow query's finally to reap its probe first."""
    if _group_alive(worker.pid):
        _signal_group(worker.pid, signal.SIGTERM)
    try: worker.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        _signal_group(worker.pid, signal.SIGKILL)
        worker.wait(timeout=5)
    # A worker may exit before an inherited descendant; never leave its probe running.
    if _group_alive(worker.pid):
        _signal_group(worker.pid, signal.SIGKILL)
    return not _group_alive(worker.pid)


def query_bounded(path, group, out, *, timeout=30, grace=15):
    """Explicit old archive only; parent bounds the worker and its BPF descendants."""
    begin = time.monotonic_ns()
    result = dict(status='NOT_REQUESTED' if path is None else 'ERROR',
        path=str(Path(path).resolve()) if path is not None else None,
        reference_group=str(Path(group).resolve()), reference_group_ids=None,
        inside_bytes=None, outside_bytes=None, unknown_bytes=None, resident_bytes=None,
        observation=None, start_monotonic_ns=begin, timeout_seconds=timeout,
        worker_pid=None, worker_returncode=None, worker_reaped=None, worker_group_released=None)
    if path is None:
        result['end_monotonic_ns'] = time.monotonic_ns()
        return result
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    worker = None
    try:
        with (out/'worker.stdout').open('x') as stdout, (out/'worker.stderr').open('x') as stderr:
            worker = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), result['path'],
                '--group', result['reference_group'], '--output', str(out/'observation')],
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True)
            result['worker_pid'] = worker.pid
            try:
                worker.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result.update(status='TIMEOUT', error='old cache query worker timed out')
            finally:
                result['worker_group_released'] = _stop_group(worker, grace)
                result.update(worker_returncode=worker.returncode, worker_reaped=worker.returncode is not None)
        saved = out/'observation/result.json'
        if saved.exists(): result['observation'] = json.loads(saved.read_text())
        if result['status'] == 'TIMEOUT': return result
        value = result['observation']
        if not value or value.get('status') not in ('OBSERVED', 'INCOMPLETE'):
            raise ValueError('query failed; see observation/result.json and worker.stderr')
        if worker.returncode != (0 if value['status'] == 'OBSERVED' else 1):
            raise ValueError('query exit status differs from saved observation')
        if not result['worker_group_released'] or not value.get('probe_reaped'):
            raise ValueError('query descendants or probe not released')
        if value['path'] != result['path'] or value['reference_group'] != result['reference_group']:
            raise ValueError('query file or reference group differs')
        row = value['residency_observed']; page_size = value['page_size']
        capacities = [value[k] for k in ('inside_bytes', 'outside_bytes', 'unknown_bytes')]
        if any(not isinstance(v, int) or v < 0 or v % page_size for v in capacities):
            raise ValueError('invalid owner capacities')
        if sum(capacities) != row['resident_bytes'] or row['resident_bytes'] != value['resident_pages']*page_size:
            raise ValueError('owner capacities differ from saved residency')
        if (value['status'] == 'OBSERVED') != (value['unknown_bytes'] == 0):
            raise ValueError('query completeness differs from unknown capacity')
        if not (begin <= value['start_monotonic_ns'] <= value['query_start_monotonic_ns']
                <= value['query_end_monotonic_ns'] <= value['end_monotonic_ns'] <= time.monotonic_ns()):
            raise ValueError('query time bounds differ')
        result.update(status=value['status'], reference_group_ids=value['reference_group_ids'],
            resident_bytes=row['resident_bytes'], **{k:value[k] for k in ('inside_bytes', 'outside_bytes', 'unknown_bytes')})
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        result.update(status='ERROR', error=repr(exc))
    finally:
        result['end_monotonic_ns'] = time.monotonic_ns()
        (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path); parser.add_argument('--group', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    def timed_out(signum, frame):
        raise TimeoutError('Owned query deadline reached')
    signal.signal(signal.SIGTERM, timed_out); signal.signal(signal.SIGINT, timed_out)
    signal.signal(signal.SIGALRM, timed_out); signal.alarm(110)
    try:
        result = query(args.file, args.group, args.output)
        print('status='+result['status']+' result_dir='+str(args.output), flush=True)
        return 0 if result['status'] == 'OBSERVED' else 1
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
