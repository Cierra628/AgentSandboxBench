"""Offline frozen-prefix and sampler-off checks; no VM, device or real service."""
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from task_metrics_prefix import without_sampling,check_prefix,controller_module
from sphinx7590_inputs import ROOT,WORKLOAD


class TaskMetricsTests(unittest.TestCase):
    def test_vendor_controller_loads_its_local_checkpoint_dependency(self):
        module=controller_module(WORKLOAD)
        self.assertEqual(module.WORKLOAD,WORKLOAD)
        self.assertTrue(callable(module.archive_upper))
        self.assertTrue(callable(module.Controller))

    def test_sampling_off_keeps_frozen_command_execution_and_timing(self):
        text=(WORKLOAD/'guest-runner.sh').read_text();changed=without_sampling(text)
        self.assertNotIn('monitor_pid',changed)
        self.assertNotIn('/proc/meminfo',changed)
        self.assertNotIn('persistent-sampling.py',changed)
        begin=text.index('while IFS=')
        for line in text[begin:].splitlines():
            if 'monitor_' not in line:self.assertIn(line,changed)
        subprocess.run(['bash','-n'],input=changed,text=True,check=True,timeout=5)

    def fixture(self,base):
        cfg=json.loads((ROOT/'configs/sphinx7590-backbone.json').read_text())
        raw=base/'raw';reference=base/'reference'
        for parent in (raw,reference):
            d=parent/'rollouts/backbone/read_repo';(d/'stdout').mkdir(parents=True);(d/'stderr').mkdir()
            shutil.copy2(WORKLOAD/'segments/read_repo/actions.tsv',d/'actions.tsv')
            (d/'patch.diff').write_text('same image patch')
            for i in range(1,7):
                for name in ('stdout','stderr'):(d/name/f'{i:03}.log').write_text(name+str(i))
        d=raw/'rollouts/backbone/read_repo'
        with (d/'steps.tsv').open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=['step','exit_code','branch','round','duration_ns'],delimiter='\t');writer.writeheader()
            for i,code in enumerate(cfg['expected_exit_codes']['read_repo'],1):writer.writerow(dict(step=f'{i:03}',exit_code=code,branch='backbone',round='read_repo',duration_ns=10))
        (d/'guest-memory.tsv').write_text('MemTotal_kib\tMemFree_kib\n')
        return raw,reference,cfg

    def test_missing_samples_are_only_valid_for_explicit_sampling_off(self):
        with tempfile.TemporaryDirectory() as directory:
            raw,reference,cfg=self.fixture(Path(directory))
            self.assertEqual(check_prefix(raw,cfg,reference,False)['guest_sample_count'],0)
            with self.assertRaises(FileNotFoundError):check_prefix(raw,cfg,reference,True)
            (raw/'rollouts/backbone/read_repo/stdout/006.log').write_text('changed output')
            with self.assertRaises(AssertionError):check_prefix(raw,cfg,reference,False)

    def test_expected_exploratory_failure_cannot_be_changed_to_success(self):
        with tempfile.TemporaryDirectory() as directory:
            raw,reference,cfg=self.fixture(Path(directory))
            p=raw/'rollouts/backbone/read_repo/steps.tsv'
            text=p.read_text();self.assertIn('001\t141\t',text)
            p.write_text(text.replace('001\t141\t','001\t0\t'))
            with self.assertRaises(AssertionError):check_prefix(raw,cfg,reference,False)


if __name__=='__main__':unittest.main()
