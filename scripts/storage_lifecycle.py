"""Run-owned phase receipts and cooperative stop requests; no service operations."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import time

SAFE_PHASES = {'resource-cleanup', 'image-discard', 'retained', 'complete'}


def read_state(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def phase_event(out, phase, event, **extra):
    if os.environ.get('ASB_STORAGE_DIAGNOSTICS') != '1':
        return
    row = dict(timestamp_ns=time.time_ns(), monotonic_ns=time.monotonic_ns(),
               pid=os.getpid(), phase=phase, event=event, **extra)
    with (Path(out)/'storage-events.jsonl').open('a') as stream:
        stream.write(json.dumps(row)+'\n')


@contextmanager
def storage_phase(out, phase):
    phase_event(out, phase, 'begin')
    try:
        yield
    except BaseException:
        phase_event(out, phase, 'end', status='FAIL')
        raise
    else:
        phase_event(out, phase, 'end', status='PASS')


class Lifecycle:
    def __init__(self, out):
        self.out = Path(out).resolve()
        self.path = Path(os.environ.get('ASB_STORAGE_LIFECYCLE', self.out/'lifecycle.json'))
        self.stop_path = Path(str(self.path)+'.stop.json')
        self.token = os.environ.get('ASB_STORAGE_TOKEN')
        self.phase = 'initializing'
        self.writers_stopped = False
        self.stop_signal = None
        self.update('initializing')

    def update(self, phase, *, writers_stopped=False):
        self.phase = phase; self.writers_stopped = writers_stopped
        row = dict(version=1, owner_pid=os.getpid(), token=self.token,
                   raw_dir=str(self.out), phase=phase, writers_stopped=writers_stopped,
                   stop_signal=self.stop_signal, timestamp_ns=time.time_ns())
        temporary = self.path.with_name(self.path.name+'.tmp-'+str(os.getpid()))
        with temporary.open('w') as stream:
            json.dump(row, stream); stream.write('\n')
        os.replace(temporary, self.path)
        phase_event(self.out, phase, 'state', writers_stopped=writers_stopped)

    def stop_requested(self):
        return self.stop_signal is not None or bool(read_state(self.stop_path))

    def on_signal(self, signum, frame):
        self.stop_signal = signum
        if not (self.token and self.writers_stopped and self.phase in SAFE_PHASES):
            raise RuntimeError(f'owned writer stop requested: signal {signum}')
        # Resource/image disposal has a separate deadline in the parent supervisor.

    @contextmanager
    def signals(self):
        previous = {sig: signal.signal(sig, self.on_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            yield
        finally:
            for sig, handler in previous.items(): signal.signal(sig, handler)
