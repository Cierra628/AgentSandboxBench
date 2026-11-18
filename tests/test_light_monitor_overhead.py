"""Saved tiny timing JSON and locally owned collector children; no VM or devices."""
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from analyze_light_monitor_overhead import analyze
from sandbox_runtime_monitor import MEMFIELDS,RuntimeMonitor,stop


def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))


class TimingBreakdownTests(unittest.TestCase):
    def test_prettier_only_summary_has_exactly_three_pairs(self):
        from run_light_monitor_controls import summarize
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);runs=[]
            for number in (1,2,3):
                for mode in ('on','off'):
                    runs.append(dict(task='prettier6604',round=number,mode=mode,patch='same',original_tests=dict(failed=2),
                        tool_seconds=1,frozen_replay_seconds=2,controller_seconds=3))
            write(root/'runs.json',runs);value=summarize(root,('prettier6604',))
            self.assertEqual(value['run_count'],6);self.assertEqual(len(value['pairs']),3)
            self.assertEqual(set(value['tasks']),{'prettier6604'})
            with self.assertRaises(AssertionError):summarize(root)

    def test_duplicate_task_selection_is_rejected_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'must-not-create'
            proc=subprocess.run([sys.executable,'-B',ROOT/'scripts/run_light_monitor_controls.py','--output',out,
                '--tasks','prettier6604','prettier6604'],capture_output=True,text=True,timeout=5)
            self.assertNotEqual(proc.returncode,0);self.assertIn('tasks must not repeat',proc.stderr)
            self.assertFalse(out.exists())

    def fixture(self,root):
        runs=[]
        for number in (1,2,3):
            for mode in ('off','on'):
                raw=root/f'{number}-{mode}'
                events=[dict(stage='cold-start',start_monotonic_ns=0,duration_ns=100,exit_code=0),
                    dict(stage='segment-replay',start_monotonic_ns=200,duration_ns=100,exit_code=0),
                    dict(stage='controller-call-0',start_monotonic_ns=210,duration_ns=80,exit_code=0)]
                if mode=='on':events += [dict(stage='monitor-ready',start_monotonic_ns=100,duration_ns=30,exit_code=0),
                    dict(stage='monitor-collect-owned',start_monotonic_ns=100,duration_ns=250,exit_code=0)]
                write(raw/'events.json',events);write(raw/'result.json',dict(status='PASS',controller_elapsed_ns=400))
                runs.append(dict(task='prettier6604',round=number,mode=mode,raw=str(raw),status='PASS',controller_seconds=400/1e9))
        path=root/'runs.json';write(path,runs);return path,runs

    def test_nested_and_background_time_are_not_added_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,runs=self.fixture(Path(tmp));v=analyze(path)
            self.assertEqual(v['status'],'PASS_OFFLINE_TIMING_BREAKDOWN');self.assertEqual(v['live_actions'],0)
            for r in v['runs']:
                self.assertEqual(sum(r['categories_ns'].values()),400)
                self.assertEqual(r['categories_ns']['segments'],100)
            for p in v['pairs']:
                self.assertEqual(p['controller_difference_ns'],0)
                self.assertEqual(p['categories_difference_ns']['monitor_control'],30)
                self.assertEqual(p['categories_difference_ns']['unlogged'],-30)
                self.assertEqual(sum(p['categories_difference_ns'].values()),0)

    def test_overlaps_uncovered_nested_calls_and_missing_pairs_fail(self):
        for case in ('overlap','uncovered','missing'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as tmp:
                path,runs=self.fixture(Path(tmp))
                raw=Path(runs[0]['raw']);events=json.loads((raw/'events.json').read_text())
                if case=='overlap':events.append(dict(stage='other',start_monotonic_ns=10,duration_ns=10,exit_code=0))
                if case=='uncovered':events[-1]['start_monotonic_ns']=400
                if case=='missing':write(path,runs[:-1])
                write(raw/'events.json',events)
                with self.assertRaises(AssertionError):analyze(path)


class LightControlTests(unittest.TestCase):
    def session(self,root,*,tamper=False):
        raw=root/'raw';raw.mkdir();workspace=root/'workspace';workspace.mkdir();proc=root/'proc';proc.mkdir()
        values={k:0 for k in MEMFIELDS};values.update(MemTotal=4096,MemAvailable=1024,Cached=512,Shmem=128)
        (proc/'meminfo').write_text(''.join(f'{k}: {v} kB\n' for k,v in values.items()));calls=[]
        def cli(label,*argv,timeout=15):
            calls.append(label)
            if argv[0]=='upload':
                self.assertEqual(sorted(p.name for p in Path(argv[2]).iterdir()),['asb-runtime-monitor.py','guest_memory_collector.py'])
                shutil.copytree(argv[2],argv[3],dirs_exist_ok=True);return ''
            if argv[0]=='download':shutil.copytree(argv[2],Path(argv[3])/Path(argv[2]).name);return ''
            command=list(argv[2:])
            if 'collect' in command:command+=['--proc-root',str(proc)]
            value=subprocess.check_output(command,text=True,timeout=timeout).strip()
            if tamper and label=='monitor-finish':
                receipt=json.loads(value);receipt['pid']+=1;return json.dumps(receipt)
            return value
        return raw,workspace,calls,cli

    def test_light_bundles_two_files_and_combines_finish_without_weakening_reaping(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw,workspace,calls,cli=self.session(Path(tmp))
            with patch('sandbox_runtime_monitor.GUEST_ROOT',str(workspace/'state')),patch('sandbox_runtime_monitor.SCRIPT',str(workspace/'asb-runtime-monitor.py')):
                monitor=RuntimeMonitor(raw,cli,container='must-not-contact-docker',light=True)
                try:
                    for sid,reuse in (('parent',False),('restored',True)):
                        monitor.start(sid,'backbone','setup',reuse_code=reuse);time.sleep(.12)
                        monitor.mark(sid,'official-task-tests');time.sleep(.12);monitor.stop(sid,'cleanup')
                        session=monitor.sessions[-1];self.assertTrue(session['collector_reaped'])
                        self.assertFalse(session['code_reused_from_snapshot'])
                        self.assertFalse(Path('/proc',str(session['receipt']['pid'])).exists())
                        with (raw/session['raw_directory']/'guest-memory.tsv').open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
                        self.assertEqual(rows[0]['phase'],'setup');self.assertEqual(rows[-1]['phase'],'cleanup')
                        self.assertTrue(any(r['phase']=='official-task-tests' for r in rows))
                        self.assertTrue(all(r['Cached_kib']=='512' and r['Shmem_kib']=='128' for r in rows))
                        self.assertEqual(session['receipt']['interval_seconds'],.1)
                    self.assertEqual(calls.count('monitor-upload-bundle'),2);self.assertEqual(calls.count('monitor-finish'),2)
                    self.assertEqual(len(calls),12)  # 6/session including the concurrent collect command.
                    for label in ('monitor-upload','monitor-directory','monitor-stop','monitor-reaped'):self.assertNotIn(label,calls)
                    self.assertFalse(monitor.workers);self.assertFalse(monitor.active)
                    self.assertFalse((raw/'host-vmm').exists());self.assertEqual(monitor.code_sha256,{})
                finally:
                    for sid in list(monitor.active):monitor.stop(sid)

    def test_finish_identity_mismatch_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw,workspace,calls,cli=self.session(Path(tmp),tamper=True)
            with patch('sandbox_runtime_monitor.GUEST_ROOT',str(workspace/'state')),patch('sandbox_runtime_monitor.SCRIPT',str(workspace/'asb-runtime-monitor.py')):
                monitor=RuntimeMonitor(raw,cli,light=True);monitor.start('owned','backbone')
                pid=monitor.active['owned']['identity']['pid']
                with self.assertRaises(AssertionError):monitor.stop('owned')
                self.assertEqual(monitor.sessions[0]['status'],'FAIL');self.assertNotIn('collector_reaped',monitor.sessions[0])
                self.assertFalse(Path('/proc',str(pid)).exists());self.assertFalse(monitor.workers)

    def test_finish_does_not_accept_a_zombie_as_reaped(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);identity=dict(pid=123456789,start_ticks='1',boot_id='test')
            write(directory/'identity.json',identity);write(directory/'collector-status.json',dict(status='PASS',**identity))
            original=Path.exists
            def exists(path):return True if str(path)=='/proc/123456789/stat' else original(path)
            with patch('sandbox_runtime_monitor.process_identity',return_value=identity),patch.object(Path,'exists',exists),\
                 patch.object(Path,'read_bytes',return_value=b'python3\0collect\0'+str(directory).encode()+b'\0'),\
                 patch('sandbox_runtime_monitor.os.kill') as kill,patch('sandbox_runtime_monitor.time.monotonic',side_effect=[0,6]),redirect_stdout(io.StringIO()):
                with self.assertRaises(TimeoutError):stop(directory,wait_reaped=True)
                kill.assert_called_once()


if __name__=='__main__':unittest.main()
