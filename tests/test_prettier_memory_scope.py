"""Saved counter fixtures only; no live systemd, service, VM or cgroup changes."""
import ast
import json
import os
import sys
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from experiment_memory import normalize_groups
from vmm_child_cgroup_check import accounting_acceptance,accounting_controller_state

class AcceptanceTests(unittest.TestCase):
    def test_io_controller_is_required_only_when_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,value in {'cgroup.controllers':'memory pids','cgroup.subtree_control':'',
                'cgroup.type':'domain','cgroup.procs':'123'}.items():(root/name).write_text(value)
            self.assertIsNone(accounting_controller_state(root)['io.stat'])
            with self.assertRaises(AssertionError):accounting_controller_state(root,require_io=True)
            (root/'cgroup.controllers').write_text('io memory pids')
            (root/'io.stat').write_text('8:0 rbytes=4096 wbytes=8192')
            self.assertIn('wbytes=8192',accounting_controller_state(root,require_io=True)['io.stat'])
    def rows(self,delta=0):
        parent=Path('/sys/fs/cgroup/asbvmmtest.slice')
        fields=dict(current=100,file=10,anon=80,kernel=10)
        groups={str(parent):dict(fields,file=10+delta),str(parent/'control.service'):fields}
        return [dict(monotonic_ns=i*100000000,experiment_memory=normalize_groups(parent,groups)) for i in range(20)]

    def test_closed_passes(self):
        rows=self.rows();metrics={'experiment_memory_p1':rows[-1]}
        self.assertEqual(accounting_acceptance(metrics,rows)['status'],'PASS')

    def test_long_offset_and_endpoint_fail(self):
        rows=self.rows(2**30);metrics={'experiment_memory_p1':rows[-1]}
        result=accounting_acceptance(metrics,rows)
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(result['endpoint']['status'],'MISMATCH')
        self.assertEqual(result['periodic']['status'],'FAIL')

    def test_gate_before_success_and_cleanup(self):
        source=Path('scripts/validate_isolated_agentenv.py').read_text()
        gate=source.index("stage='experiment-memory-closure-acceptance'")
        self.assertLess(gate,source.index('success=True',gate))
        self.assertIn("os.environ.get('ASB_VMM_ACCOUNTING_ONLY')=='on'",source)

    def test_gate_reads_mounted_metrics_before_cleanup_copy(self):
        # Execute the real gate only: no import of the live validator, service or VM.
        source=Path('scripts/validate_isolated_agentenv.py').read_text()
        gate=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.If)
            and isinstance(n.test,ast.BoolOp) and ast.get_source_segment(source,n.test).startswith('vmm_check and'))
        code=compile(ast.Module(body=[gate],type_ignores=[]),'<acceptance gate>','exec')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);mount=root/'mount';out=root/'out';out.mkdir()
            metrics=mount/'controller/prettier6604/schedule-smoke-metrics.json'
            metrics.parent.mkdir(parents=True)
            rows=self.rows();series=root/'memory.jsonl'
            series.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            scope=dict(vmm_check=True,os=os,Path=Path,json=json,mount=mount,out=out,receipt={})
            with patch.dict(os.environ,ASB_VMM_ACCOUNTING_ONLY='on',ASB_EXPERIMENT_MEMORY_SERIES=str(series)):
                metrics.write_text(json.dumps({'experiment_memory_p1':rows[-1]}))
                exec(code,scope)
                self.assertEqual(scope['receipt']['experiment_memory_acceptance']['status'],'PASS')
                self.assertFalse((out/'prettier6604').exists())
                metrics.write_text(json.dumps({'experiment_memory_p1':self.rows(2**30)[-1]}))
                with self.assertRaisesRegex(AssertionError,'retain volume'):exec(code,scope)
                self.assertEqual(scope['receipt']['experiment_memory_acceptance']['status'],'FAIL')

if __name__=='__main__':unittest.main()
