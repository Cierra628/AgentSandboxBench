"""Opt-in asynchronous space/I/O observations; no clearing caches or controlling writers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from storage_lifecycle import read_state


def filesystem_sample(path):
    info = os.statvfs(path)
    return dict(device=os.stat(path).st_dev, total_bytes=info.f_blocks*info.f_frsize,
                free_bytes=info.f_bfree*info.f_frsize,
                available_bytes=info.f_bavail*info.f_frsize,
                reserved_or_unavailable_bytes=(info.f_bfree-info.f_bavail)*info.f_frsize)


def directory_sample(paths):
    seen = set(); allocated = logical = count = 0; errors = []
    for directory in paths:
        if directory.is_symlink():
            errors.append('root symlink excluded: '+str(directory)); continue
        try: device = directory.stat().st_dev
        except OSError as exc:
            errors.append(type(exc).__name__); continue
        def same_filesystem(path):
            try: return not path.is_symlink() and path.lstat().st_dev == device
            except OSError as exc:
                errors.append(type(exc).__name__); return False
        for base, dirs, files in os.walk(directory, followlinks=False,
                onerror=lambda exc:errors.append(type(exc).__name__)):
            dirs[:] = [name for name in dirs if same_filesystem(Path(base)/name)]
            for name in files:
                try:
                    item = (Path(base)/name).lstat(); key = (item.st_dev, item.st_ino)
                    if item.st_dev != device: continue
                    if key in seen: continue
                    seen.add(key); allocated += item.st_blocks*512; logical += item.st_size; count += 1
                except OSError as exc: errors.append(type(exc).__name__)
    return dict(allocated_bytes=allocated, logical_bytes=logical, inode_count=count,
                errors=errors, scope='file-entry st_blocks, directory inode blocks excluded; no symlink or cross-filesystem traversal; not atomic or reflink-exclusive')


def process_samples(proc=Path('/proc')):
    rows = {}; unreadable = 0
    for path in proc.glob('[0-9]*'):
        if path.name == str(os.getpid()): continue
        try:
            fields = (path/'stat').read_text().rsplit(')', 1)[1].split()
            values = dict(line.split(':', 1) for line in (path/'io').read_text().splitlines())
            row = dict(pid=int(path.name), parent_pid=int(fields[1]), start_ticks=int(fields[19]),
                       comm=(path/'comm').read_text().strip(), uid=path.stat().st_uid,
                       write_bytes=int(values['write_bytes']), cancelled_write_bytes=int(values['cancelled_write_bytes']))
            if (path/'stat').read_text().rsplit(')', 1)[1].split()[19] != fields[19]: continue
            rows[int(path.name)] = row
        except (OSError, KeyError, ValueError): unreadable += 1
    return rows, unreadable


def writer_deltas(before, after, owner_pid=None):
    result = []
    for pid, row in after.items():
        previous = before.get(pid)
        known = previous is not None and previous['start_ticks'] == row['start_ticks']
        delta = row['write_bytes']-previous['write_bytes'] if known else None
        cancelled = row['cancelled_write_bytes']-previous['cancelled_write_bytes'] if known else None
        if known and delta == 0 and cancelled == 0: continue
        cursor = pid; visited = set()
        while cursor in after and cursor != owner_pid and cursor not in visited:
            visited.add(cursor); cursor = after[cursor]['parent_pid']
        result.append(dict(row, write_delta_bytes=delta if delta is None or delta >= 0 else None,
                           cancelled_write_delta_bytes=cancelled,
                           owned_tree=owner_pid is not None and cursor == owner_pid,
                           interval_status='MEASURED' if known and delta >= 0 else 'NEW_OR_CHANGED_PROCESS'))
    return sorted(result, key=lambda row:row['write_delta_bytes'] or 0, reverse=True)[:10]


class StorageObserver:
    def __init__(self, out, filesystem, *, roots=(), lifecycle=None, interval=1., directory_interval=5.):
        self.out = Path(out); self.filesystem = Path(filesystem); self.roots = list(map(Path, roots))
        self.lifecycle = Path(lifecycle) if lifecycle else None
        self.interval = interval; self.directory_interval = directory_interval
        self.finished = threading.Event(); self.samples = 0; self.error = None
        self.scan_ns = 0; self.minimum = None; self.previous_processes = {}
        self.latest_directories = None; self.latest_scan = 0
        self.thread = threading.Thread(target=self.collect, daemon=True)

    def start(self):
        self.out.mkdir(parents=True, exist_ok=False)
        code = self.out/'code'; code.mkdir()
        hashes = {}
        for name in ('storage_observer.py', 'storage_lifecycle.py'):
            data = Path(__file__).with_name(name).read_bytes()
            (code/name).write_bytes(data); hashes[name] = hashlib.sha256(data).hexdigest()
        with (self.out/'source-sha256.json').open('x') as stream: json.dump(hashes, stream, indent=2)
        self.thread.start()

    def sample(self, stream):
        begin = time.monotonic_ns(); phase = read_state(self.lifecycle) if self.lifecycle else {}
        fs = filesystem_sample(self.filesystem)
        roots = self.roots.copy()
        if phase.get('raw_dir'): roots.append(Path(phase['raw_dir']))
        scanned = self.latest_directories is None or (begin-self.latest_scan)/1e9 >= self.directory_interval
        if scanned:
            self.latest_directories = directory_sample(roots); self.latest_scan = begin
        processes, unreadable = process_samples()
        writers = writer_deltas(self.previous_processes, processes, phase.get('owner_pid'))
        self.previous_processes = processes
        memory = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            fields = line.split()
            if fields[0] in ('Dirty:', 'Writeback:'): memory[fields[0][:-1]] = int(fields[1])*1024
        elapsed = time.monotonic_ns()-begin; self.scan_ns += elapsed
        self.minimum = min(self.minimum, fs['available_bytes']) if self.minimum is not None else fs['available_bytes']
        row = dict(timestamp_ns=time.time_ns(), monotonic_ns=begin, filesystem=fs,
                   lifecycle=phase, directories=self.latest_directories,
                   directory_scan_monotonic_ns=self.latest_scan, directory_scanned_this_sample=scanned,
                   writers=writers, process_read_errors=unreadable, host_writeback_bytes=memory,
                   scan_elapsed_ns=elapsed)
        stream.write(json.dumps(row)+'\n'); stream.flush(); self.samples += 1

    def collect(self):
        try:
            with (self.out/'samples.jsonl').open('x') as stream:
                self.sample(stream)
                while not self.finished.wait(self.interval): self.sample(stream)
                self.sample(stream)
        except Exception as exc: self.error = repr(exc)

    def stop(self):
        self.finished.set(); self.thread.join(timeout=10)
        result = dict(status='PASS' if not self.error and not self.thread.is_alive() and self.samples >= 2 else 'FAIL',
            samples=self.samples, observed_min_available_bytes=self.minimum, scan_elapsed_ns=self.scan_ns,
            interval_seconds=self.interval, directory_interval_seconds=self.directory_interval,
            error=self.error, unit='bytes; timestamps/durations ns',
            limitations=['sampled extrema, not exact instantaneous minima',
                'directory scans and filesystem reads are not atomic; cached directory scans carry their timestamp',
                'process I/O is across all devices; exited/between-sample writers can be missed',
                'I/O byte deltas are not disk allocation deltas; unexplained changes have no assigned cause',
                'diagnostic sampling cost is recorded; task/monitoring impact is NOT_MEASURED'])
        with (self.out/'result.json').open('x') as stream: json.dump(result, stream, indent=2); stream.write('\n')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--duration', type=float, default=15)
    parser.add_argument('--root', action='append', type=Path, default=[])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]; out = args.output.resolve()
    if not 0 < args.duration <= 60 or out.exists() or not out.is_relative_to(root/'.artifacts'):
        parser.error('duration must be 0..60s and output must be new inside project .artifacts')
    os.umask(0o077); sampler = StorageObserver(out, root, roots=args.root)
    sampler.start()
    try: time.sleep(args.duration)
    finally: result = sampler.stop()
    print('status='+result['status']+' result_dir='+str(out))
    return 0 if result['status']=='PASS' else 1


if __name__ == '__main__': raise SystemExit(main())
