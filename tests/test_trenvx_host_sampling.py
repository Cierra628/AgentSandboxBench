import asyncio
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
spec = importlib.util.spec_from_file_location('service_smoke', ROOT/'scripts/smoke_trenvx_service.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class HostSamplerContract(unittest.TestCase):
    def test_run_owned_cgroup_fields_and_stop(self):
        with tempfile.TemporaryDirectory() as name:
            out = Path(name)/'run'
            group = Path(name)/'cgroup'
            out.mkdir()
            group.mkdir()
            (group/'memory.current').write_text('4096\n')
            (group/'memory.stat').write_text('anon 1024\nfile 2048\nkernel 512\n')

            async def sample():
                stop = asyncio.Event()
                task = asyncio.create_task(smoke.sample_host_cgroup(out, group, .01, stop))
                await asyncio.sleep(.035)
                stop.set()
                await task

            asyncio.run(sample())
            with (out/'host/memory-samples.tsv').open(newline='') as stream:
                rows = list(csv.DictReader(stream, delimiter='\t'))
            summary = json.loads((out/'host-sampling.json').read_text())
            self.assertGreaterEqual(len(rows), 2)
            self.assertEqual(summary['sample_count'], len(rows))
            self.assertIn('excludes orchestrator', summary['scope'])
            self.assertEqual(summary['vmm_pss'], 'NOT_MEASURED')
            self.assertEqual(rows[0]['cgroup_current_bytes'], '4096')
            self.assertEqual(rows[0]['cgroup_file_bytes'], '2048')
            self.assertEqual(rows[0]['cgroup_anon_bytes'], '1024')
            self.assertEqual(rows[0]['cgroup_kernel_bytes'], '512')


if __name__ == '__main__':
    unittest.main()
