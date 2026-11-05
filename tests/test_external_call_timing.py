"""Offline timing boundaries: mocked commands, temporary files and simulated waits only."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_external_workload as controller


class Clock:
    def __init__(self):self.now=1000
    def __call__(self):return self.now
    def advance(self,nanoseconds):self.now+=nanoseconds


class ExternalCallTimingTests(unittest.TestCase):
    def test_slow_stdout_and_events_do_not_extend_command_time(self):
        clock=Clock();events=[];write_text=Path.write_text;write_events=controller._write_events
        class WaitingLock:
            def __enter__(self):clock.advance(5)
            def __exit__(self,*args):pass
        def command(*args,**kwargs):
            clock.advance(10);return SimpleNamespace(returncode=0,stdout=' output\n')
        def stdout_write(path,*args,**kwargs):
            if path.suffix=='.stdout':clock.advance(20)
            return write_text(path,*args,**kwargs)
        def event_write(out,values):clock.advance(30);write_events(out,values)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            with patch.object(controller.time,'monotonic_ns',clock),patch.object(controller.subprocess,'run',side_effect=command),patch.object(Path,'write_text',stdout_write),patch.object(controller,'_write_events',side_effect=event_write):
                value=controller.logged_call(out,{},events,WaitingLock(),0,'replay',['mock'])
                row=events[0]
                self.assertEqual(value,'output')
                self.assertEqual(row['command_duration_ns'],10)
                self.assertEqual(row['stdout_log_duration_ns'],20)
                self.assertEqual(row['event_lock_wait_ns'],5)
                self.assertEqual(row['duration_ns'],35)  # Original boundary, before events.json write.
                self.assertEqual(row['events_log_duration_ns'],30)
                self.assertEqual(row['call_elapsed_ns'],65)
                self.assertTrue(row['command_returned'] and row['stdout_log_completed'] and row['events_log_completed'])
                self.assertNotIn('events_log_duration_ns',json.loads((out/'events.json').read_text())[0])
                controller._write_events(out,events)  # Controller final flush persists the last write receipt.
                saved=json.loads((out/'events.json').read_text())
                self.assertEqual(saved,events)
                summary=controller.call_timing_summary(events,30)
                self.assertEqual(summary['replay'],row)
                self.assertEqual(summary['final_events_flush_ns'],30)
                self.assertEqual(summary['stdout_log_cumulative_ns'],20)
                self.assertEqual(summary['events_log_cumulative_ns'],30)
            self.assertEqual((out/'000-replay.stdout').read_text(),' output\n')

    def test_nonzero_and_timeout_keep_different_return_states(self):
        for timeout in (False,True):
            with self.subTest(timeout=timeout),tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp);events=[];clock=Clock()
                def command(*args,**kwargs):
                    kwargs['stderr'].write('diagnostic\n');clock.advance(11)
                    if timeout:raise subprocess.TimeoutExpired('mock',1)
                    return SimpleNamespace(returncode=7,stdout='failed output\n')
                with patch.object(controller.time,'monotonic_ns',clock),patch.object(controller.subprocess,'run',side_effect=command):
                    with self.assertRaises(subprocess.TimeoutExpired if timeout else RuntimeError):
                        controller.logged_call(out,{},events,threading.Lock(),0,'replay',['mock'],1)
                row=events[0];self.assertEqual(row['command_duration_ns'],11)
                self.assertEqual(row['exit_code'],None if timeout else 7)
                self.assertEqual(row['command_returned'],not timeout)
                self.assertEqual(row['stdout_log_completed'],not timeout)
                self.assertEqual(row['stdout_log_duration_ns'],None if timeout else 0)
                self.assertTrue(row['events_log_completed'])
                self.assertEqual((out/'000-replay.stderr').read_text(),'diagnostic\n')
                self.assertEqual((out/'000-replay.stdout').exists(),not timeout)

    def test_failed_local_writes_remain_failures_with_command_receipt(self):
        for target in ('stdout','events'):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp);events=[];original=Path.write_text
                def write(path,*args,**kwargs):
                    if path.suffix=='.stdout' and target=='stdout' or path.name=='events.json' and target=='events':
                        raise OSError('simulated '+target+' failure')
                    return original(path,*args,**kwargs)
                with patch.object(controller.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='ok')),patch.object(Path,'write_text',write):
                    with self.assertRaisesRegex(OSError,'simulated '+target):
                        controller.logged_call(out,{},events,threading.Lock(),0,'replay',['mock'])
                row=events[0];self.assertEqual(row['exit_code'],0);self.assertTrue(row['command_returned'])
                self.assertEqual(row['stdout_log_completed'],target!='stdout')
                self.assertEqual(row['events_log_completed'],target!='events')
                controller._write_events(out,events)
                self.assertEqual(json.loads((out/'events.json').read_text()),events)

    def test_parallel_logging_keeps_both_receipts_and_outputs(self):
        events=[];lock=threading.Lock();barrier=threading.Barrier(2);errors=[]
        def command(argv,**kwargs):
            barrier.wait(timeout=2)
            return SimpleNamespace(returncode=0,stdout=argv[0])
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            def worker(index):
                try:controller.logged_call(out,{},events,lock,index,'call-'+str(index),[str(index)])
                except BaseException as error:errors.append(error)
            with patch.object(controller.subprocess,'run',side_effect=command):
                workers=[threading.Thread(target=worker,args=(i,)) for i in range(2)]
                for worker in workers:worker.start()
                for worker in workers:worker.join(timeout=3)
            self.assertFalse(any(worker.is_alive() for worker in workers));self.assertEqual(errors,[])
            controller._write_events(out,events)
            self.assertEqual({row['stage'] for row in events},{'call-0','call-1'})
            self.assertEqual(json.loads((out/'events.json').read_text()),events)
            for i in range(2):self.assertEqual((out/f'{i:03}-call-{i}.stdout').read_text(),str(i))


if __name__=='__main__':unittest.main()
