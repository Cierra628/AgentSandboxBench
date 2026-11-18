"""Mocked pair lifecycle and saved-number statistics; no Docker calls or real caches."""
import json
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import run_host_full_rootfs as run
from host_private_files import read,write
from isolated_storage import GIB


class PairOrderTests(unittest.TestCase):
    def exercise(self,order=None,fail=False):
        from run_light_monitor_controls import SOURCE_ID
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);executed=[];lifecycles=[]
            def execute(command,**kwargs):
                if command[0]=='bash':
                    self.assertIn('--rootfs-cold-read',command)
                    mode=command[command.index('--full-rootfs-group')+1];executed.append(mode)
                    out=root/'.artifacts/host-full-rootfs'/mode;out.mkdir(parents=True)
                    value=10 if mode=='shared' else 100
                    write(out/'result.json',dict(status='FAIL' if fail else 'PASS',mode=mode,
                        observation=dict(P0=dict(resident_bytes=0),P1=dict(resident_bytes=value))))
                    kwargs['stdout'].write('status='+('FAIL' if fail else 'PASS')+' result_dir='+str(out)+'\n')
                    return SimpleNamespace(returncode=int(fail))
                lifecycles.append(command[:2]);return SimpleNamespace(returncode=0)
            opener=MagicMock();opener.open.return_value.__enter__.return_value.status=200
            with patch.object(run,'ROOT',root),patch.object(run,'available',return_value=100*GIB),\
                 patch.object(run,'daemon_file',return_value=Path('/mock/daemon')),\
                 patch.object(run.subprocess,'run',side_effect=execute),\
                 patch.object(run.subprocess,'check_output',side_effect=[json.dumps([dict(Id=SOURCE_ID,State=dict(Running=False))]),json.dumps(dict(Running=False))]),\
                 patch.object(run.urllib.request,'build_opener',return_value=opener):
                code=run.pair(Path('/unused'),Path('/unused'),cold_read=True,**({} if order is None else dict(order=order)))
            result=read(next((root/'.artifacts/host-full-rootfs-pairs').glob('*/result.json')))
            self.assertEqual(code,int(fail));self.assertTrue(result['source_returned_to_stopped'])
            self.assertEqual(lifecycles,[['docker','start'],['docker','stop']])
            return result,executed

    def test_old_default_and_explicit_shared_first_keep_original_order(self):
        for order in (None,'shared-first'):
            with self.subTest(order=order):
                result,executed=self.exercise(order)
                self.assertEqual(executed,['shared','private']);self.assertEqual(result['order'],'shared-first')
                self.assertEqual(result['actual_actions'],378);self.assertEqual(result['comparison']['endpoint_difference_bytes'],90)

    def test_reverse_order_keeps_shared_private_comparison_sign_and_actual_order(self):
        result,executed=self.exercise('private-first')
        self.assertEqual(executed,['private','shared']);self.assertEqual([g['mode'] for g in result['groups']],executed)
        self.assertEqual(result['order'],'private-first');self.assertEqual(result['actual_actions'],378)
        self.assertEqual(result['comparison']['endpoint_difference_bytes'],90)
        self.assertEqual(result['comparison']['endpoint_reduction_fraction'],.9)

    def test_reverse_first_failure_stops_pair_and_restores_source(self):
        result,executed=self.exercise('private-first',fail=True)
        self.assertEqual(executed,['private']);self.assertEqual(result['status'],'FAIL')
        self.assertNotIn('comparison',result)

    def test_bad_order_is_rejected_before_budget_source_or_config(self):
        with patch.object(run,'available') as budget,patch.object(run.subprocess,'check_output') as inspect:
            with self.assertRaisesRegex(ValueError,'invalid.*order'):run.pair(Path('/unused'),Path('/unused'),order='wrong')
            budget.assert_not_called();inspect.assert_not_called()
        p=subprocess.run([sys.executable,'-B',ROOT/'scripts/run_host_full_rootfs.py','group','/unused',
            '--mode','private','--copy-daemon','/unused','--order','private-first'],capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,2);self.assertIn('order requires pair',p.stderr)


class RepeatStatisticsTests(unittest.TestCase):
    def pairs(self):
        from report_host_grpo_cold_repeats import STATUS
        values=[]
        for i,(shared,private) in enumerate([(20,40),(10,40),(10,100)]):
            groups=[dict(mode=mode,objects=count,users_per_object=users,points=[dict(resident_bytes=0)],
                endpoint_resident_bytes=size,guest_samples=1) for mode,count,users,size in
                [('shared',19,7,shared),('private',133,1,private)]]
            values.append(dict(status=STATUS,pair='pair-'+str(i),actual_actions=378,source_returned_to_stopped=True,
                execution_order=['private','shared'] if i==1 else ['shared','private'],groups=groups,
                endpoint_difference_bytes=private-shared,endpoint_reduction_fraction=(private-shared)/private))
        return values

    def test_mean_is_per_pair_arithmetic_not_pooled_and_retains_first_pair(self):
        from report_host_grpo_cold_repeats import summarize
        value=summarize(self.pairs())
        self.assertAlmostEqual(value['statistics']['reduction_fraction']['mean'],(.5+.75+.9)/3)
        self.assertNotAlmostEqual(value['statistics']['reduction_fraction']['mean'],140/180)
        self.assertEqual(value['statistics']['difference_bytes']['mean'],140/3)
        self.assertEqual(value['statistics']['reduction_fraction']['minimum'],.5)
        self.assertEqual(value['statistics']['reduction_fraction']['maximum'],.9)
        self.assertEqual(value['new_actions'],756);self.assertEqual(value['actual_actions'],1134)
        self.assertEqual([r['reused'] for r in value['pair_rows']],[True,False,False])

    def test_duplicate_incomplete_wrong_order_and_nonzero_initial_do_not_enter_mean(self):
        from report_host_grpo_cold_repeats import summarize
        for error in ('duplicate','incomplete','order','initial','target','ratio'):
            with self.subTest(error=error):
                pairs=copy.deepcopy(self.pairs())
                if error=='duplicate':pairs[2]['pair']=pairs[0]['pair']
                if error=='incomplete':pairs[2]['status']='FAIL'
                if error=='order':pairs[1]['execution_order']=['shared','private']
                if error=='initial':pairs[2]['groups'][0]['points'][0]['resident_bytes']=1
                if error=='target':pairs[2]['groups'][1]['objects']=19
                if error=='ratio':pairs[2]['endpoint_reduction_fraction']=0
                with self.assertRaises(ValueError):summarize(pairs)


if __name__=='__main__':unittest.main()
