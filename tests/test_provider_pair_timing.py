"""Saved synthetic metadata only; no services or workload contents."""
import csv,json
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_provider_pair_timing import difference,report

class PairTimingTests(unittest.TestCase):
    def test_difference_keeps_direction_units_and_zero_baseline(self):
        self.assertEqual(difference(100,125),dict(off_ns=100,on_ns=125,delta_ns=25,delta_percent=25))
        self.assertEqual(difference(100,90)['delta_percent'],-10)
        self.assertIsNone(difference(0,10)['delta_percent'])

    def fixture(self,root,mode,multiplier):
        root.mkdir();paths={};trace=[]
        for role,count in [('backbone',11),('early_1',10),('early_2',10)]:
            folder=root/'rollouts'/role/role;folder.mkdir(parents=True)
            with (folder/'steps.tsv').open('w') as f:
                w=csv.DictWriter(f,fieldnames=['branch','round','step','exit_code','duration_ns'],delimiter='\t');w.writeheader()
                for i in range(count):w.writerow(dict(branch=role,round=role,step=str(i+1),exit_code=0,duration_ns=multiplier))
            trace.append(dict(rollout=role,segment=role,manifest_sha256=role))
            paths[role]=dict(action_count=count,guest_action_sum_ns=count*multiplier,final_patch_sha256=role,
                source_patch_check='PASS',independent_check='PASS',official_task_status='FAIL',official_passed=0,official_failed=2)
            if role!='backbone':(root/('restored-'+role+'.json')).write_text(json.dumps(dict(status='PASS',process=dict(status='PASS'),boundary=dict(status='PASS'))))
        with (root/'segments.tsv').open('w') as f:
            w=csv.DictWriter(f,fieldnames=list(trace[0]),delimiter='\t');w.writeheader();w.writerows(trace)
        task=dict(status='PASS',cache_sampling=mode,cache_pair=True,provider_cache=True,batch_lookup=True,action_count=31,
            logical_action_count=42,checks=dict(tasks='PASS'),paths=paths,branch_replay_elapsed_ns=2*multiplier,
            restore_and_cache_ready_ns=3*multiplier,controller_elapsed_ns=10*multiplier,
            phases=[dict(name='real-task-branches',start_ns=1,end_ns=1+5*multiplier)],cleanup=dict(samplers_stopped=mode=='on'),
            cache_sampler_stop_ns=multiplier,physical_page_cache='x' if mode=='on' else 'NOT_MEASURED_SAMPLING_OFF')
        (root/'result.json').write_text(json.dumps(task))
        (root/'effective-config.json').write_text(json.dumps(dict(guest_cpu=2,guest_memory_mib=4096,sample_interval_seconds=.1,executed_paths={},final_roles=['early_1','early_2'])))
        (root/'events.json').write_text(json.dumps([dict(stage=f'{i:04}-'+s,exit_code=0,duration_ns=multiplier)
            for i,s in enumerate(['cold-backbone','snapshot-call','restore-early_1','restore-early_2','official-early_1','official-early_2'])]))
        return root

    def test_report_sums_actual_31_actions_and_checks_same_semantics(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);off=self.fixture(root/'off','off',100);on=self.fixture(root/'on','on',110)
            r=report(off,on,root/'report')
            self.assertEqual(r['comparison']['task_tool_sum_ns']['off_ns'],3100)
            self.assertEqual(r['comparison']['suffix_tool_sum_ns']['on_ns'],2200)
            self.assertEqual(r['comparison']['task_tool_sum_ns']['delta_percent'],10)
            self.assertNotIn('cache_sampler_stop_ns',r['comparison'])
            self.assertEqual(r['runs']['on']['metrics']['cache_sampler_stop_ns'],110)
            broken=json.loads((on/'result.json').read_text());broken['paths']['early_1']['final_patch_sha256']='changed'
            (on/'result.json').write_text(json.dumps(broken))
            with self.assertRaisesRegex(AssertionError,'final_patches'):report(off,on,root/'bad-report')

if __name__=='__main__':unittest.main()
