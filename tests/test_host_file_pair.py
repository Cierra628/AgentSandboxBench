"""Offline coverage tests; no service, guest or device access."""
import ast
import copy
import json
import subprocess
import tempfile
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from run_host_file_pair import GUEST_CHECK, paired_pss


class HostPairTests(unittest.TestCase):
    def test_marker_check_does_not_require_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'owned-marker-directory/marker'
            for mode in ('write','read'):
                raw=subprocess.check_output([sys.executable,'-c',GUEST_CHECK,mode,'guest-a',str(marker)],text=True)
                value=json.loads(raw);self.assertEqual(value['marker'],'guest-a')
                self.assertIn('Cached',value)
            failed=subprocess.run([sys.executable,'-c',GUEST_CHECK,'read','guest-b',str(marker)],capture_output=True)
            self.assertNotEqual(failed.returncode,0)

    def inputs(self):
        bindings=[dict(pid=10,start_ticks='100'),dict(pid=20,start_ticks='200')]
        def row(t):
            return dict(status='OBSERVED',start_monotonic_ns=t,end_monotonic_ns=t+1,
                        processes=[dict(pid=10,start_ticks='100',comm='firecracker',pss=4096),
                                   dict(pid=20,start_ticks='200',comm='firecracker',pss=8192),
                                   dict(pid=30,start_ticks='300',comm='server',pss=1024)])
        return bindings,[row(10),row(20)]

    def test_exact_identities_and_one_count_per_sample(self):
        bindings,rows=self.inputs();value=paired_pss(rows,bindings,9,22)
        self.assertEqual(len(value),2);self.assertEqual(value[0]['vmm_pss_bytes'],12288)
        self.assertEqual(len(value[0]['vmm_pss']),2)

    def test_missing_reused_and_extra_vm_not_accepted(self):
        for change in ('missing','reused','extra','zero','duplicate'):
            bindings,rows=self.inputs()
            if change=='missing':rows[0]['processes'].pop(0)
            elif change=='reused':rows[0]['processes'][0]['start_ticks']='101'
            elif change=='extra':rows[0]['processes'].append(dict(pid=40,start_ticks='400',comm='firecracker',pss=100))
            elif change=='zero':rows[0]['processes'][0]['pss']=0
            else:rows[0]['processes'].append(dict(rows[0]['processes'][0]))
            with self.assertRaises(ValueError):paired_pss(rows,bindings,9,22)

    def test_partial_outside_and_too_few_samples_do_not_pass(self):
        bindings,rows=self.inputs()
        for kind in ('partial','outside','late-end'):
            candidate=copy.deepcopy(rows)
            if kind=='partial':candidate[0]['status']='PARTIAL'
            elif kind=='outside':candidate[0]['start_monotonic_ns']=8
            else:candidate[0]['end_monotonic_ns']=23
            with self.assertRaises(ValueError):paired_pss(candidate,bindings,9,22)
        with self.assertRaises(ValueError):paired_pss([rows[0],rows[0]],bindings,9,22)

    def test_sampling_off_rejects_unrelated_modes_before_any_runtime_call(self):
        for mode in ([],['--restore-pair'],['--trace-dual-task']):
            result=subprocess.run([sys.executable,str(ROOT/'scripts/run_host_file_workflow.py'),
                'unused-config.json','--host-sampling','off',*mode],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,2)
            self.assertIn('host-sampling off requires matched-task without tracing',result.stderr)

    def test_actual_entry_selects_smoke_without_full_task(self):
        code=(ROOT/'scripts/validate_isolated_agentenv.py').read_text();tree=ast.parse(code)
        smoke=next(node for node in ast.walk(tree) if isinstance(node,ast.If) and isinstance(node.test,ast.Name)
                   and node.test.id=='host_pair_smoke' and any(isinstance(child,ast.Constant) and child.value=='host-file-pair-smoke' for body in node.body for child in ast.walk(body)))
        block='\n'.join(ast.unparse(node) for node in smoke.body)
        self.assertIn('run_host_file_pair.py',block);self.assertNotIn('run_prettier6604.py',block)
        self.assertIn("shutil.copytree(pair_output, out / 'host-file-pair')",ast.unparse(tree))
        wrapper=(ROOT/'scripts/run_host_file_workflow.py').read_text()
        self.assertIn("if args.short_pair or args.restore_pair or args.matched_task:",wrapper)
        self.assertIn("'scripts/run_host_file_pair.py'),'run'",wrapper)


if __name__=='__main__':unittest.main()
