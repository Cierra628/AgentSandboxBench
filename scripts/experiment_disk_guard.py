"""Bound project writers and stop their process group before the disk reserve is used."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

RESERVE_BYTES = 20 * 1024**3
LOCK = Path(__file__).resolve().parents[1]/'.artifacts/trenvx-disk-writer.lock'


@contextmanager
def writer_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def guarded_run(argv, *, directory, log, timeout, reserve=RESERVE_BYTES,
                interval=.25, free_bytes=None):
    free_bytes = free_bytes or (lambda: shutil.disk_usage(directory).free)
    started = time.monotonic()
    record = dict(command=list(map(str, argv)), reserve_bytes=reserve,
                  interval_seconds=interval, status='FAIL', sample_count=0)
    process = None
    try:
        available = free_bytes()
        record['observed_min_free_bytes'] = available
        if available <= reserve:
            raise RuntimeError(f'disk reserve reached before launch: {available} <= {reserve}')
        process = subprocess.Popen(list(map(str, argv)), stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        record['process_group'] = process.pid
        while True:
            available = free_bytes()
            record['sample_count'] += 1
            record['observed_min_free_bytes'] = min(record['observed_min_free_bytes'], available)
            if available <= reserve:
                raise RuntimeError(f'disk reserve reached: {available} <= {reserve}')
            if time.monotonic() - started >= timeout:
                raise TimeoutError(f'guarded command exceeded {timeout} seconds')
            code = process.poll()
            if code is not None:
                record['exit_code'] = code
                if code:
                    raise subprocess.CalledProcessError(code, argv)
                record['status'] = 'PASS'
                break
            time.sleep(interval)
    except BaseException as exc:
        record['error'] = repr(exc)
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)
            record['owned_process_group_killed'] = True
        raise
    finally:
        record['elapsed_seconds'] = time.monotonic() - started
        with (Path(directory)/'disk-guard.jsonl').open('a') as stream:
            stream.write(json.dumps(record) + '\n')
