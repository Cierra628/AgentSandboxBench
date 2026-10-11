"""Sampler lifecycle regression on local fixtures; no services or devices."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from xarray6992_persistent_sampling import instrument


class PersistentSamplingTests(unittest.TestCase):
    def test_variant_keeps_frozen_execution_and_reaps_one_collector(self):
        original=(ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/workloads/xarray-6992-rl-fork/guest-runner.sh').read_text()
        modified=instrument(original)
        subprocess.run(['bash','-n'],input=modified,text=True,check=True,timeout=5)
        for line in original.splitlines():
            if 'bash -o pipefail -c "$command"' in line or 'actual_hash=' in line:
                self.assertIn(line,modified)
        self.assertNotIn('monitor_step "$step" &',modified)
        self.assertIn('wait "$collector_pid"',modified)

    def test_collector_preserves_columns_and_stops_with_final_sample(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp);proc=out/'proc';proc.mkdir()
            fields='MemTotal MemFree Buffers Cached Shmem AnonPages Active(file) Inactive(file) Dirty'.split()
            (proc/'meminfo').write_text(''.join(f'{key}: {i+1} kB\n' for i,key in enumerate(fields)))
            (proc/'vmstat').write_text('pgfault 1\npgmajfault 2\npgpgin 3\npgpgout 4\n')
            (out/'.memory-phase').write_text('baseline\t000\n')
            child=subprocess.Popen([sys.executable,'-B',str(ROOT/'scripts/xarray6992_persistent_sampling.py'),
                '--directory',str(out),'--interval','.1','--sandbox','test','--branch','backbone','--round','trace_index_state','--proc-root',str(proc)])
            try:
                end=time.monotonic()+3
                while not (out/'.memory-ready').exists():
                    self.assertIsNone(child.poll());self.assertLess(time.monotonic(),end);time.sleep(.01)
                (out/'.memory-phase').write_text('complete\t999\n')
                child.terminate();self.assertEqual(child.wait(timeout=3),0)
                rows=(out/'guest-memory.tsv').read_text().splitlines()
                self.assertGreaterEqual(len(rows),2)
                self.assertEqual(rows[-1].split('\t')[1:],['complete','test','backbone','trace_index_state','999',*map(str,range(1,10))])
                receipt=json.loads((out/'collector-status.json').read_text())
                self.assertEqual(receipt['samples'],len(rows));self.assertEqual(receipt['status'],'PASS')
            finally:
                if child.poll() is None:child.kill();child.wait(timeout=3)


if __name__=='__main__':unittest.main()
