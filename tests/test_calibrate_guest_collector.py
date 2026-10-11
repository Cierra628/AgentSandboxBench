import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import calibrate_guest_collector as calibration
from fixed_runner_git_identity import instrument as fixed


class CollectorCalibrationTests(unittest.TestCase):
    def test_native_work_matches_independent_arithmetic_and_rejects_bad_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'work'
            subprocess.run(['gcc','-O2','-Wall','-Wextra','-Werror',str(ROOT/'scripts/collector_fixed_work.c'),'-o',str(binary)],check=True)
            state=0x123456789abcdef0
            for _ in range(257):
                state=(state*6364136223846793005+1442695040888963407)&((1<<64)-1)
                state^=state>>13
            result=subprocess.run([str(binary),'257'],capture_output=True,text=True,check=True)
            self.assertEqual(result.stdout,f'iterations=257 checksum={state:016x}\n')
            for value in ('0','-1','2000000001','bad'):
                self.assertEqual(subprocess.run([str(binary),value],capture_output=True).returncode,2)

    def test_real_fixture_output_and_corrupted_result_rejected(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(calibration,'ITERATIONS',257):
            base=Path(tmp)
            original=ROOT/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke/systems/agentenv/guest-runner.sh'
            (base/'lifecycle-original.sh').write_text(fixed(original.read_text()))
            for source,name in (('command_clock.c','command-clock'),('collector_fixed_work.c','fixed-work')):
                subprocess.run(['gcc','-O2',str(ROOT/'scripts'/source),'-o',str(base/name)],check=True)
            (base/'calibration-collector.py').write_text((ROOT/'scripts/guest_memory_collector.py').read_text())
            calibration.prepare(base)
            fixture=base/'collector-calibration';output=fixture/'case-00/replay'
            env=dict(os.environ,ACTIONS_DIR=str(fixture/'actions'),OUTPUT_DIR=str(output),GUEST_MEMORY_SAMPLING='0',DROP_GUEST_CACHES='0')
            subprocess.run([str(base/'command-clock'),str(output/'lifecycle-envelope.json'),'bash',str(fixture/'runners/off.sh')],env=env,check=True,capture_output=True,timeout=15)
            row=calibration.inspect_case(output.parent,'off',2_000_000_000)
            self.assertEqual(row['sample_count'],0)
            with self.assertRaises(AssertionError):calibration.inspect_case(output.parent,'off',2_000_000_000,b'wrong output')
            clock=json.loads((output/'fixed-work-clock.json').read_text());clock['exit_code']=7
            (output/'fixed-work-clock.json').write_text(json.dumps(clock))
            with self.assertRaises(AssertionError):calibration.inspect_case(output.parent,'off',2_000_000_000)

    def test_first_failure_stops_and_partial_guest_data_is_downloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);archive=io.BytesIO()
            with tarfile.open(fileobj=archive,mode='w:gz') as f:
                entry=tarfile.TarInfo('partial-output.txt');data=b'failed call retained\n';entry.size=len(data);f.addfile(entry,io.BytesIO(data))
            class Sandbox:
                async def download_file(self,*_,**__):return archive.getvalue()
            calls=[]
            async def tool(sb,label,cmd,**kwargs):
                calls.append(label)
                if label=='collector-case-00-off':raise RuntimeError('injected failure')
            with self.assertRaisesRegex(RuntimeError,'injected failure'):asyncio.run(calibration.run(Sandbox(),base,tool))
            self.assertEqual(calls,['prepare-collector-calibration','collector-case-00-off','pack-collector-calibration'])
            self.assertTrue((base/'collector-calibration/partial-output.txt').exists())
            self.assertEqual(json.loads((base/'collector-calibration-result.json').read_text())['status'],'FAIL')


if __name__=='__main__':unittest.main()
