"""Fixed meminfo files and deterministic Python execution traces; no real proc or services."""
import builtins
import csv
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import prototype_boundary_memory as helper
from probe_boundary_meminfo_timer import START_CODE,END_CODE,MEMINFO,HEADER,fixture_script,reference_function


class BoundaryTimerPrototypeTests(unittest.TestCase):
    def test_fixed_fields_missing_zero_and_before_then_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);mem=root/'meminfo';mem.write_text(MEMINFO);marker=root/'phase';destination=root/'memory.tsv'
            destination.write_text(HEADER);seen=[]
            def wall_time():seen.append(marker.read_text());return 1793000000000000000
            with patch.object(helper.time,'time_ns',side_effect=wall_time):
                helper.sample_memory('before','001',str(destination),str(marker),str(mem),'role','role','segment')
            self.assertEqual(seen,['before\tsegment\t001\n'])
            self.assertEqual(marker.read_text(),'running\tsegment\t001\n')
            with destination.open() as stream:r=list(csv.DictReader(stream,delimiter='\t'))[0]
            self.assertEqual(r['timestamp_ns'],'1793000000000000000')
            self.assertEqual([int(r[k]) for k in list(r)[6:]],[4096,1024,64,512,32,128,111,222,0])

    def test_after_and_complete_preserve_row_phase_and_waiting_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);mem=root/'meminfo';mem.write_text(MEMINFO);destination=root/'memory.tsv';destination.write_text(HEADER);marker=root/'phase'
            for phase,step,expected in [('after','001','after'),('complete','999','waiting')]:
                seen=[]
                def wall_time():seen.append(marker.read_text());return 1793000000000000000
                with patch.object(helper.time,'time_ns',side_effect=wall_time):
                    helper.sample_memory(phase,step,str(destination),str(marker),str(mem),'role','role','segment')
                self.assertEqual(seen,[f'{expected}\tsegment\t{step}\n'])
                self.assertEqual(marker.read_text(),f'{expected}\tsegment\t{step}\n')
            with destination.open() as stream:self.assertEqual([r['phase'] for r in csv.DictReader(stream,delimiter='\t')],['after','complete'])

    def test_meminfo_error_is_not_reported_as_zero_or_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);destination=root/'memory.tsv'
            with self.assertRaises(FileNotFoundError):helper.sample_memory('before','001',str(destination),str(root/'phase'),str(root/'missing'),'role','role','segment')
            self.assertFalse(destination.exists());self.assertEqual((root/'phase').read_text(),'before\tsegment\t001\n')

    def execute_timer(self,code,phase,fail_sample=False):
        trace=[];printed=[]
        def mono():trace.append('clock');return 123
        def sample(*args):
            trace.append('sample-'+args[0]);self.assertEqual(args[0],phase)
            if fail_sample:raise OSError('fixture read failed')
        fake_sys=SimpleNamespace(path=[],argv=['-c','/helper',phase,'001','/output','/phase','/meminfo','role','role','segment'])
        modules=dict(time=SimpleNamespace(monotonic_ns=mono),sys=fake_sys,
            prototype_boundary_memory=SimpleNamespace(sample_memory=sample))
        def importer(name,*args,**kwargs):trace.append('import-'+name);return modules[name]
        namespace={'__builtins__':dict(vars(builtins),__import__=importer),'print':lambda value:printed.append(value)}
        if fail_sample:
            with self.assertRaises(OSError):exec(code,namespace)
        else:exec(code,namespace)
        return trace,printed

    def test_start_samples_before_clock_and_prints_original_scalar(self):
        trace,printed=self.execute_timer(START_CODE,'before')
        self.assertEqual(trace,['import-time','import-sys','import-prototype_boundary_memory','sample-before','clock'])
        self.assertEqual(printed,[123])

    def test_end_captures_clock_before_any_helper_import_or_sampling(self):
        trace,printed=self.execute_timer(END_CODE,'after')
        self.assertEqual(trace,['import-time','clock','import-sys','import-prototype_boundary_memory','sample-after'])
        self.assertEqual(printed,[123])

    def test_end_sampling_error_does_not_print_a_successful_timer_result(self):
        trace,printed=self.execute_timer(END_CODE,'after',fail_sample=True)
        self.assertEqual(trace[1],'clock');self.assertEqual(printed,[])

    def test_fixture_commands_have_literal_ids_and_no_added_work_inside_timer(self):
        for merged in (False,True):
            text=fixture_script('sample_memory() { :; }',merged)
            self.assertIn('command=\'printf "out-001\\n"',text)
            self.assertIn('command=\'printf "out-002\\n"',text)
            self.assertNotIn('capture_phase running',text)
            self.assertLess(text.index('  status=$?'),text.index('  end_ns='))
        with self.assertRaises(AssertionError):reference_function('sample_memory() {\n  :\n}\n')


if __name__=='__main__':unittest.main()
