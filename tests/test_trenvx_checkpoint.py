import asyncio
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('checkpoint',ROOT/'backend/trenvx/quiesced_checkpoint.py')
cp=importlib.util.module_from_spec(spec);spec.loader.exec_module(cp)
class FakeIO:
    def __init__(self, fail=None): self.calls=[];self.fail=fail;self.state='Running'
    def step(self, name):
        self.calls.append(name)
        if name==self.fail: raise TimeoutError(name)
    def task(self,op,token): self.step(op);return {'runtime_path':'/tmp/fake'}
    def download(self,*args): self.step('download')
    def vm(self,op):
        if op=='pause': self.state='Paused' # Simulate a lost response after effect.
        self.step(op)
        if op=='resume': self.state='Running'
        return {'state':self.state}
    def copy(self,*args): self.step('copy')
class CheckpointContract(unittest.TestCase):
    def test_order_and_file_semantics(self):
        io=FakeIO();audit={};cp.capture_upper(io,None,None,None,audit)
        self.assertEqual(io.calls,['freeze','prepare','download','pause','info','copy','resume','info','discard-runtime','thaw'])
        self.assertTrue(audit['complete']);self.assertEqual(audit['restore_semantics'],'file-state-only')
    def test_each_failure_thaws(self):
        for stage in ['freeze','prepare','download','pause','info','copy','resume','discard-runtime','thaw']:
            with self.subTest(stage=stage):
                io=FakeIO(stage);audit={}
                with self.assertRaises(BaseExceptionGroup):cp.capture_upper(io,None,None,None,audit)
                self.assertIn('thaw',io.calls);self.assertFalse(audit['complete'])
                if stage in ('pause','info'):self.assertIn('resume',io.calls)
                if stage=='pause':self.assertEqual(io.state,'Running')
    def test_cleanup_only_inventory_and_continues_on_failure(self):
        class Owned(FakeIO):
            def tasks(self): return [{'id':'owned-1'},{'id':'owned-2'}]
            def task(self,op,token,*,id):
                self.calls.append(id)
                if id=='owned-1':raise TimeoutError('injected cleanup timeout')
        io=Owned();audit={}
        with self.assertRaises(ExceptionGroup):cp.cleanup_tasks(io,audit)
        self.assertEqual(io.calls,['owned-1','owned-2'])
        self.assertFalse(audit['complete']);self.assertTrue(audit['tasks'][1]['cleaned'])
    def test_cancellation_joins_worker_before_return(self):
        import threading
        async def run():
            started=threading.Event();release=threading.Event()
            class Blocking(FakeIO):
                def copy(self,*args):started.set();release.wait(2);super().copy(*args)
            io=Blocking();task=asyncio.create_task(cp.capture_joined(io,None,None,None,{}))
            await asyncio.to_thread(started.wait,2);task.cancel();await asyncio.sleep(.02)
            self.assertFalse(task.done());release.set()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertEqual(io.calls[-1],'thaw')
        asyncio.run(run())
if __name__=='__main__':unittest.main()
