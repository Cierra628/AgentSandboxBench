import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from guest_memory_collector import FIELDS,VMFIELDS,counters,phase_step
from persistent_guest_sampling import instrument
from report_memory_metrics import report


class CollectorTests(unittest.TestCase):
    def test_incomplete_phase_marker_is_unknown_not_a_false_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'marker'
            for text in ('','running\t0','running\t027','unfinished\t024'):
                p.write_text(text);self.assertEqual(phase_step(p),('unknown','NA'))
            p.write_text('running\t024\n');self.assertEqual(list(phase_step(p)),['running','024'])

    def test_missing_counter_is_not_silently_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'meminfo';p.write_text('MemTotal: 10 kB\n')
            with self.assertRaises(ValueError):counters(p,FIELDS,True)

    def test_owned_process_stops_and_flushes_final_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);proc=p/'proc';proc.mkdir()
            (proc/'meminfo').write_text(''.join(f'{key}: 10 kB\n' for key in FIELDS))
            (proc/'vmstat').write_text(''.join(f'{key} 5\n' for key in VMFIELDS))
            (p/'.memory-phase').write_text('preparation\t000\n')
            command=[sys.executable,'-B',str(Path(__file__).resolve().parents[1]/'scripts/guest_memory_collector.py'),str(p),'.1','--proc-root',str(proc)]
            child=subprocess.Popen(command)
            try:
                deadline=time.monotonic()+3
                while not (p/'.memory-ready').exists():
                    self.assertIsNone(child.poll());self.assertLess(time.monotonic(),deadline);time.sleep(.01)
                (p/'.memory-phase').write_text('complete\t999\n')
                child.terminate();self.assertEqual(child.wait(timeout=3),0)
                value=json.loads((p/'collector-status.json').read_text())
                self.assertEqual(value['status'],'PASS');self.assertGreaterEqual(value['samples'],2)
                self.assertIn('\tcomplete\t999\t',(p/'guest-memory.tsv').read_text())
                (p/'.memory-ready').unlink()
                child=subprocess.Popen(command)
                deadline=time.monotonic()+3
                while not (p/'.memory-ready').exists():
                    self.assertIsNone(child.poll());self.assertLess(time.monotonic(),deadline);time.sleep(.01)
                child.terminate();self.assertEqual(child.wait(timeout=3),0)
                continued=json.loads((p/'collector-status.json').read_text())
                self.assertEqual(continued['samples_before'],value['samples'])
                self.assertEqual(continued['samples_in_file'],value['samples']+continued['samples'])
                self.assertEqual(len(list((p/'collector-segments').glob('*.json'))),2)
            finally:
                if child.poll() is None:child.kill();child.wait(timeout=3)

    def test_variant_preserves_action_invocation_and_removes_per_step_process(self):
        root=Path(__file__).resolve().parents[1]
        text=(root/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke/systems/agentenv/guest-runner.sh').read_text()
        variant=instrument(text)
        invocation='(cd /testbed && timeout --signal=TERM --kill-after=5s "${timeout_seconds}s" bash "$action")'
        self.assertIn(invocation,variant)
        self.assertNotIn('monitor_step "$step" &',variant)
        self.assertEqual(variant.count('python3 "${ASB_GUEST_MEMORY_COLLECTOR'),1)

    def test_report_validates_complete_segment_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);replay=root/'replay';replay.mkdir();segments=replay/'collector-segments';segments.mkdir()
            header=['timestamp_ns','phase','step',*[key+'_kib' for key in FIELDS]]
            values=[1000,800,850,10,50,5,1,60,10,5,2,3,1]
            (replay/'guest-memory.tsv').write_text('\t'.join(header)+'\n'+
                ''.join('\t'.join(map(str,[stamp,'running',step,*values]))+'\n'
                        for stamp,step in [(1,'013'),(2,'014')]))
            first=dict(status='PASS',samples=1,samples_before=0,samples_in_file=1)
            last=dict(status='PASS',samples=1,samples_before=1,samples_in_file=2)
            (segments/'first.json').write_text(json.dumps(first))
            (segments/'last.json').write_text(json.dumps(last))
            (replay/'collector-status.json').write_text(json.dumps(last))
            with patch('report_memory_metrics.audit_run',return_value=dict(raw_dir=str(root),guest_memory=dict(sampling_mode='periodic'))):
                result=report({})
                self.assertEqual(len(result['guest_collector_segments']),2)
                first['samples_in_file']=2
                (segments/'first.json').write_text(json.dumps(first))
                with self.assertRaises(AssertionError):report({})
