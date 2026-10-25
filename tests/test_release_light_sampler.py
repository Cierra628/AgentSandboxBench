"""Portable light collector test using fake meminfo and only our child process."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from sandbox_runtime_monitor import MEMFIELDS


class ReleaseLightSamplerTests(unittest.TestCase):
    def test_meminfo_only_samples_and_owned_process_is_reaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proc=root/'proc';proc.mkdir();out=root/'session';phase=root/'phase'
            values={k:0 for k in MEMFIELDS};values.update(MemTotal=4096,MemAvailable=1024,Cached=512,Shmem=128)
            (proc/'meminfo').write_text(''.join(f'{k}: {v} kB\n' for k,v in values.items()))
            child=subprocess.Popen([sys.executable,'-B',ROOT/'scripts/sandbox_runtime_monitor.py','collect','--directory',out,'--phase-file',phase,
                '--sandbox-id','fixture','--role','backbone','--session-id','test','--proc-root',proc,'--light','--max-seconds','5'])
            try:
                deadline=time.monotonic()+3
                while not (out/'ready').exists():
                    self.assertIsNone(child.poll());self.assertLess(time.monotonic(),deadline);time.sleep(.01)
                time.sleep(.25)
                result=subprocess.run([sys.executable,'-B',ROOT/'scripts/sandbox_runtime_monitor.py','stop','--directory',out],capture_output=True,text=True,timeout=3)
                self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(child.wait(timeout=3),0)
                with (out/'guest-memory.tsv').open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
                self.assertGreaterEqual(len(rows),3);self.assertTrue(all(r['Cached_kib']=='512' and r['Shmem_kib']=='128' for r in rows))
                status=json.loads((out/'collector-status.json').read_text());self.assertEqual(status['profile'],'light');self.assertEqual(status['samples'],len(rows))
                for name in ('guest-vmstat.tsv','guest-cpu.tsv','guest-diskstats.tsv'):self.assertFalse((out/name).exists())
                self.assertFalse(Path('/proc',str(child.pid)).exists())
            finally:
                if child.poll() is None:child.terminate();child.wait(timeout=3)


if __name__=='__main__':unittest.main()
