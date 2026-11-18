"""Saved/synthetic counters and fake subprocesses only; no live resource access."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from experiment_memory import normalize_groups
from run_live_child_calibration import memory_window_checks


class MeasurementBoundaryTests(unittest.TestCase):
    def row(self,ns,*,deleted=False):
        parent=Path('/fake/experiment.slice')
        fields=lambda file:dict(current=file+4096,file=file,anon=1024,kernel=3072)
        groups={str(parent):fields(4*2**20),str(parent/'control.service'):fields(2*2**20)}
        if not deleted:groups[str(parent/'docker.scope')]=fields(2*2**20)
        return dict(monotonic_ns=ns,duration_ns=1000,groups=groups,experiment_memory=normalize_groups(parent,groups))

    def test_measurement_endpoint_and_teardown_remain_separate_and_full_fail_stays(self):
        rows=[self.row(i*100000000,deleted=i>=3) for i in range(6)]
        endpoint=dict(status='OBSERVED',snapshot=self.row(250000000))
        result=memory_window_checks(rows,endpoint,rows[-1])
        self.assertEqual(result['measurement']['original_indices'],[0,1,2])
        self.assertEqual(result['cleanup']['original_indices'],[3,4,5])
        self.assertEqual(result['measurement']['checks']['periodic']['status'],'PASS')
        self.assertEqual(result['full_collection']['periodic']['status'],'FAIL')
        self.assertEqual(result['cleanup']['checks']['periodic']['status'],'FAIL')
        self.assertEqual(result['full_collection']['points'],6)
        self.assertEqual(result['full_collection']['endpoint_file_closure']['parent_minus_children_bytes'],2*2**20)

    def test_missing_boundary_does_not_choose_a_previous_closed_point(self):
        rows=[self.row(0),self.row(100000000,deleted=True)]
        result=memory_window_checks(rows,dict(status='UNAVAILABLE',reason='query failed'),rows[-1])
        self.assertEqual(result['measurement']['status'],'UNAVAILABLE')
        self.assertEqual(result['cleanup']['status'],'UNAVAILABLE')
        self.assertEqual(result['full_collection']['points'],2)
        self.assertEqual(result['full_collection']['periodic']['status'],'FAIL')

    def test_read_overlapping_endpoint_is_retained_in_full_series_not_assigned_to_either_side(self):
        rows=[self.row(0),self.row(100000000),self.row(200000000,deleted=True)]
        rows[1]['duration_ns']=5000000
        result=memory_window_checks(rows,dict(status='OBSERVED',snapshot=self.row(102000000)),rows[-1])
        self.assertEqual(result['overlapping_endpoint_indices'],[1])
        self.assertEqual(result['full_collection']['points'],3)
        self.assertEqual(result['measurement']['original_indices'],[0])
        self.assertEqual(result['cleanup']['original_indices'],[2])

    def test_command_receipt_saves_absolute_timing_without_changing_duration_or_wait(self):
        # Compile only the nested command helper, not the service workflow.
        source=Path(__file__).resolve().parents[1]/'scripts/validate_isolated_agentenv.py'
        module=ast.parse(source.read_text())
        node=next(n for n in ast.walk(module) if isinstance(n,ast.FunctionDef) and n.name=='call'
            and any(isinstance(a,ast.Attribute) and a.attr=='communicate' for a in ast.walk(n)))
        code=compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),str(source),'exec')
        waits=[]
        class Worker:
            returncode=0
            def communicate(self,timeout):waits.append(timeout);return b'owned output\n',b''
        with tempfile.TemporaryDirectory() as tmp:
            times=iter((100,350));commands=[]
            namespace=dict(time=SimpleNamespace(monotonic_ns=lambda:next(times)),cleaning=True,
                subprocess=SimpleNamespace(Popen=lambda *a,**kw:Worker(),PIPE=subprocess.PIPE,
                    CompletedProcess=subprocess.CompletedProcess),env={},ROOT=Path(tmp),out=Path(tmp),
                commands=commands,json=json)
            exec(code,namespace)
            self.assertEqual(namespace['call']('stop-own-server',['fake-command'],30),'owned output')
            self.assertEqual(waits,[30])
            self.assertEqual(commands,[dict(stage='stop-own-server',exit_code=0,duration_ns=250,
                start_monotonic_ns=100,end_monotonic_ns=350)])
            self.assertEqual(json.loads((Path(tmp)/'commands.json').read_text()),commands)


if __name__=='__main__':unittest.main()
