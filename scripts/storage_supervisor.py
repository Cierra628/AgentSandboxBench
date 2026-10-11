"""Stop owned writers while allowing bounded resource/image disposal to finish."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid

from experiment_disk_guard import RESERVE_BYTES
from storage_lifecycle import SAFE_PHASES, read_state
from storage_observer import StorageObserver


def own_state(path, pid, token, raw_root):
    state = read_state(path)
    if not isinstance(state, dict): return {}
    if state.get('owner_pid') != pid or state.get('token') != token:
        return {}
    if not isinstance(state.get('phase'), str) or not isinstance(state.get('writers_stopped'), bool):
        return {}
    try: raw = Path(state.get('raw_dir', '')).resolve()
    except (TypeError, OSError): return {}
    if raw.parent != Path(raw_root).resolve() or not raw.name.endswith('-'+str(pid)):
        return {}
    return state


def recover_success_images(raw, raw_root):
    """Reuse all original deletion guards; failed/uncertain/checkpoint runs are retained."""
    from archive_trenvx_run_data import discard_success_images
    raw = Path(raw).resolve()
    if raw.parent != Path(raw_root).resolve():
        raise ValueError('recovery cannot touch a run outside the owned result root')
    cfg = json.loads((raw/'config.json').read_text()); result = json.loads((raw/'result.json').read_text())
    if not cfg.get('discard_success_images') or cfg.get('checkpoint_after') or cfg.get('keep_data'):
        return dict(status='RETAINED', reason='no ordinary-success image deletion authorization')
    if result.get('status') != 'PASS' or not result.get('full_trajectory_verified'):
        return dict(status='RETAINED', reason='failed or incomplete task')
    if cfg.get('official_task') and result.get('checks', {}).get('official_task_tests') != 'PASS':
        return dict(status='RETAINED', reason='official task check missing')
    group = cfg.get('cgroup')
    if group != 'asb-trenvx-'+raw.name or (Path('/sys/fs/cgroup')/group).exists():
        return dict(status='RETAINED', reason='cgroup ownership or removal not confirmed')
    if not (raw/'data').exists():
        receipt = read_state(raw/'image-cleanup.json')
        confirmed = (not (raw/'data').is_symlink() and isinstance(receipt, dict)
                     and receipt.get('source_removed') is True and receipt.get('run_id') == raw.name
                     and receipt.get('mode') == 'successful-exp1-images-discarded')
        return dict(status='PASS' if confirmed else 'RETAINED', reason='private data absent; matching completion receipt required')
    if any((raw/name).exists() for name in ('image-cleanup.json', 'data.tar.gz', 'data.tar.gz.partial')):
        return dict(status='RETAINED', reason='partial/existing retention record requires inspection')
    manifest = discard_success_images(raw, root=raw_root)
    return dict(status='PASS', source_removed=manifest['source_removed'],
                source_allocated_bytes=manifest['source_allocated_bytes'],
                limitation='image recovery only; original task and workflow status are unchanged')


def supervised_run(argv, *, out, label, env, filesystem, raw_root, timeout=900,
                   cleanup_timeout=180, interval=.25, reserve=RESERVE_BYTES,
                   free_bytes=None, diagnostics=False):
    out = Path(out); filesystem = Path(filesystem)
    free_bytes = free_bytes or (lambda: shutil.disk_usage(filesystem).free)
    state_path = out/(label+'-lifecycle.json'); stop_path = Path(str(state_path)+'.stop.json')
    if state_path.exists() or stop_path.exists(): raise FileExistsError('existing lifecycle/stop record')
    token = uuid.uuid4().hex
    child_env = dict(env, ASB_STORAGE_LIFECYCLE=str(state_path), ASB_STORAGE_TOKEN=token,
                     ASB_STORAGE_DIAGNOSTICS='1' if diagnostics else '0')
    started = time.monotonic(); process = None; observer = None; error = None; stop_time = None
    record = dict(status='FAIL', reserve_bytes=reserve, sample_count=0,
                  diagnostic_mode=diagnostics, cleanup_timeout_seconds=cleanup_timeout)

    def request_stop(reason):
        nonlocal stop_time
        if stop_time is not None: return
        stop_time = time.monotonic(); record['stop_reason'] = reason
        try:
            with stop_path.open('x') as stream:
                json.dump(dict(reason=reason, timestamp_ns=time.time_ns()), stream); stream.write('\n')
        except OSError as exc:
            record['stop_record_error'] = repr(exc)

    def stop_owned_writer():
        state = own_state(state_path, process.pid, token, raw_root)
        safe = state.get('writers_stopped') is True and state.get('phase') in SAFE_PHASES
        if not safe and not record.get('term_sent'):
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            record['term_sent'] = True
        elif safe:
            record['cleanup_allowed_to_finish'] = True
        if time.monotonic()-stop_time >= cleanup_timeout:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=10); record['forced_kill'] = True

    try:
        available = free_bytes(); record['observed_min_free_bytes'] = available
        if available <= reserve: raise RuntimeError('disk reserve reached before launch')
        if diagnostics:
            observer = StorageObserver(out/(label+'-storage'), filesystem, roots=[out], lifecycle=state_path)
            observer.start()
        with (out/(label+'.log')).open('xb') as log:
            process = subprocess.Popen(list(map(str, argv)), env=child_env, cwd=filesystem,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            record['owned_process_group'] = process.pid
            while process.poll() is None:
                available = free_bytes(); record['sample_count'] += 1
                record['observed_min_free_bytes'] = min(record['observed_min_free_bytes'], available)
                if available <= reserve: request_stop('disk reserve reached')
                elif time.monotonic()-started >= timeout: request_stop('run timeout')
                if stop_time is not None: stop_owned_writer()
                time.sleep(interval)
            if record.get('stop_reason'): raise RuntimeError(record['stop_reason'])
            if process.returncode: raise RuntimeError(f'{label} exited {process.returncode}; see private log')
            record['status'] = 'PASS'
    except BaseException as exc:
        error = exc; record['error'] = repr(exc)
    finally:
        # Repeated terminal signals must not interrupt the independent cleanup window.
        previous = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            if process is not None and process.poll() is None:
                request_stop('supervisor interrupted')
                while process.poll() is None:
                    stop_owned_writer(); time.sleep(interval)
            if process is not None:
                record['exit_code'] = process.returncode
                state = own_state(state_path, process.pid, token, raw_root)
                record['final_lifecycle'] = state
                if error and state.get('writers_stopped') and state.get('phase') in SAFE_PHASES and state.get('raw_dir'):
                    # Run recovery in an independent child, after the writer's confirmed exit.
                    destination = out/(label+'-recovery.json')
                    with (out/(label+'-recovery.log')).open('x') as recovery_log:
                        worker = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()),
                            '--recover', state['raw_dir'], '--raw-root', str(raw_root), '--output', str(destination)],
                            stdout=recovery_log, stderr=subprocess.STDOUT, start_new_session=True)
                        try: worker.wait(timeout=cleanup_timeout)
                        except subprocess.TimeoutExpired:
                            try: os.killpg(worker.pid, signal.SIGKILL)
                            except ProcessLookupError: pass
                            worker.wait(timeout=10)
                    record['recovery'] = read_state(destination) or dict(status='FAIL', reason='recovery did not finish; data retained')
        except Exception as exc:
            record['cleanup_error'] = repr(exc)
            if error is None: error = exc
        finally:
            if observer:
                try: record['storage_observer'] = observer.stop()
                except Exception as exc:
                    record['storage_observer'] = dict(status='FAIL', error=repr(exc))
                if record['storage_observer']['status'] != 'PASS' and error is None:
                    error = RuntimeError('storage diagnostics incomplete; see private record')
            if error: record['status'] = 'FAIL'; record['error'] = repr(error)
            record['elapsed_seconds'] = time.monotonic()-started
            try:
                with (out/(label+'-invocation.json')).open('x') as stream:
                    json.dump(record, stream, indent=2); stream.write('\n')
            finally:
                for sig, handler in previous.items(): signal.signal(sig, handler)
    if error: raise error
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recover', type=Path, required=True)
    parser.add_argument('--raw-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); os.umask(0o077)
    try: result = recover_success_images(args.recover, args.raw_root)
    except Exception as exc: result = dict(status='FAIL', reason=repr(exc), policy='preserve uncertain data')
    with args.output.open('x') as stream: json.dump(result, stream, indent=2); stream.write('\n')
    raise SystemExit(0 if result['status'] == 'PASS' else 1)
