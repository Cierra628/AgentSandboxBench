"""Offline branch identity, progress and joined-failure regression checks."""
import asyncio
import csv
import json
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_prettier6604_grpo import validated_export, verify_path, joined, execution_paths, verify_process, prefix_tree_plan
from prettier6604_inputs import ROOT, sha


class JoinedCreation(unittest.IsolatedAsyncioTestCase):
    async def test_creation_failure_waits_for_other_owned_request(self):
        completed=[]
        async def fails():raise ValueError('first request')
        async def other():
            await asyncio.sleep(.01)
            completed.append('second sandbox registered')
        with self.assertRaisesRegex(RuntimeError,'first request'):
            await joined([fails(),other()])
        self.assertEqual(completed,['second sandbox registered'])


class BranchInputs(unittest.TestCase):
    def setUp(self):
        pointer=ROOT/'.artifacts/prettier6604/latest-grpo-input-audit.txt'
        if not pointer.exists():self.skipTest('requires local hash-verified frozen export')
        self.directory=Path(pointer.read_text().strip())
        self.cfg,self.workload,self.patch,self.samples,self.rollouts,self.names=validated_export(self.directory)

    def test_bpo_runs_only_suffixes_after_saved_prefixes(self):
        from prepare_prettier6604_rollouts import sampled_segment
        full=execution_paths(self.rollouts,'bpo')
        self.assertEqual(sum(map(len,full.values())),17)
        count=lambda paths:sum(len(sampled_segment(self.samples,r,self.names[s])['actions']) for r,path in paths.items() for s in path)
        self.assertEqual(count(full),84)
        self.assertEqual(count(self.rollouts),189)
        smoke=execution_paths(self.rollouts,'bpo','early_1')
        self.assertEqual(list(smoke),['backbone','early_1'])
        self.assertEqual(sum(map(len,smoke.values())),4)
        self.assertEqual(count(smoke),21)
        for role,path in full.items():
            if role!='backbone':self.assertTrue(all(s not in path for s in ('read_repo','read_typescript')))
        with self.assertRaises(AssertionError):execution_paths(self.rollouts,'bpo','late_1')

    def test_shared_cache_subsets_keep_original_paths_and_common_prefix(self):
        from prepare_prettier6604_rollouts import sampled_segment
        for count,actions in ((1,29),(2,50),(7,189)):
            selected=execution_paths(self.rollouts,'grpo',root_count=count)
            self.assertEqual(len(selected),count)
            self.assertTrue(all(path[:2]==['read_repo','read_typescript'] for path in selected.values()))
            self.assertEqual(sum(len(sampled_segment(self.samples,role,self.names[s])['actions']) for role,path in selected.items() for s in path),actions)
            self.assertTrue(all(path==self.rollouts[role] for role,path in selected.items()))
        for schedule,role in (('bpo',None),('grpo','early_1')):
            with self.assertRaises(AssertionError):execution_paths(self.rollouts,schedule,role,2)

    def test_process_must_keep_identity_and_resume_progress(self):
        before=dict(pid='4',start_ticks='10',boot_id='original',count='20')
        after=dict(before,count='21')
        self.assertEqual(verify_process(before,after)['status'],'PASS')
        for key in before:
            wrong=dict(after);wrong[key]=before[key] if key=='count' else 'changed'
            with self.assertRaises(AssertionError):verify_process(before,wrong)

    def test_tvcache_plan_matches_pinned_controller_without_live_resources(self):
        vendor=ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/controller.py'
        spec=importlib.util.spec_from_file_location('test_pinned_tvcache',vendor)
        module=importlib.util.module_from_spec(spec)
        sys.path.insert(0,str(vendor.parent))
        try:spec.loader.exec_module(module)
        finally:sys.path.pop(0)
        module.WORKLOAD=self.workload
        for selected in (self.rollouts,{r:self.rollouts[r] for r in ('backbone','early_1')}):
            module.ROLES=tuple(selected)
            class DryController(module.Controller):
                async def start(self,role):return role
                async def run_path(self,sandbox,role,segments):self.seen.extend([role,s] for s in segments)
                async def fork(self,sandbox,count,point,role):
                    self.points.append(point)
                    return sandbox,[f'{point}-{i}' for i in range(count)]
            with tempfile.TemporaryDirectory() as d:
                from types import SimpleNamespace
                controller=DryController(SimpleNamespace(raw_dir=Path(d)))
                controller.seen=[];controller.points=[]
                asyncio.run(controller.do_tvcache())
                planned=prefix_tree_plan(selected)
                self.assertEqual(controller.seen,planned['segments'])
                self.assertEqual(controller.points,list(planned['forks']))
                self.assertEqual([f['offset'] for f in planned['forks'].values()],[2,4] if len(selected)==7 else [2])
        full=execution_paths(self.rollouts,'tvcache')
        self.assertEqual(full,execution_paths(self.rollouts,'bpo'))
        smoke=execution_paths(self.rollouts,'tvcache','early_1')
        self.assertEqual(smoke['backbone'],self.rollouts['backbone'])
        from prepare_prettier6604_rollouts import sampled_segment
        count=sum(len(sampled_segment(self.samples,r,self.names[s])['actions']) for r,path in smoke.items() for s in path)
        self.assertEqual(count,39)

    def test_tvcache_rejects_strict_prefix_and_unhandled_secondary_groups(self):
        for paths in ({'backbone':['read_repo'],'early_1':['read_repo','early_1']},
                      {'backbone':['read_repo','core_patch'],'early_1':['read_repo','shared','early_1'],
                       'early_2':['read_repo','shared','early_2']}):
            with self.assertRaises(AssertionError):prefix_tree_plan(paths)

    def test_branch_inspection_cannot_be_swapped_even_with_new_hash(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'export';out.mkdir()
            shutil.copytree(self.workload,out/'workload')
            shutil.copy2(self.directory/'provenance.json',out/'provenance.json')
            target=out/'workload/segments/early_1_inspect/actions.tsv'
            target.write_bytes((self.workload/'segments/early_3_inspect/actions.tsv').read_bytes())
            p=out/'provenance.json';provenance=json.loads(p.read_text())
            provenance['exported_sha256']['segments/early_1_inspect/actions.tsv']=sha(target)
            p.write_text(json.dumps(provenance))
            with self.assertRaises(AssertionError):validated_export(out)

    def test_real_sample_failure_is_preserved_and_order_checked(self):
        with tempfile.TemporaryDirectory() as d:
            raw=Path(d);segment='read_repo';role='early_1'
            output=raw/'rollouts'/role/segment;output.mkdir(parents=True)
            shutil.copy2(self.workload/'segments'/segment/'actions.tsv',output/'actions.tsv')
            sample=next(s for s in self.samples if s['role']=='backbone' and s['segment']==segment)
            rows=[dict(branch=role,round=segment,step=f'{i:03}',exit_code=a['exit_code'],duration_ns=100)
                  for i,a in enumerate(sample['actions'],1)]
            def write():
                with (output/'steps.tsv').open('w') as f:
                    writer=csv.DictWriter(f,fieldnames=list(rows[0]),delimiter='\t');writer.writeheader();writer.writerows(rows)
            write()
            (output/'guest-memory.tsv').write_text('MemTotal_kib\tMemFree_kib\n4096\t2048\n')
            (output/'patch.diff').write_text('')
            self.assertEqual(verify_path(raw,role,[segment],self.workload,self.samples,self.names)['action_count'],5)
            (output/'guest-memory.tsv').write_text('MemTotal_kib\tMemFree_kib\n')
            control=verify_path(raw,role,[segment],self.workload,self.samples,self.names,sampling=False)
            self.assertEqual(control['action_count'],5)
            self.assertIsNone(control['guest_observed_peak_bytes'])
            with self.assertRaises(AssertionError):verify_path(raw,role,[segment],self.workload,self.samples,self.names)
            (output/'guest-memory.tsv').write_text('MemTotal_kib\tMemFree_kib\n4096\t2048\n')
            rows[1]['exit_code']=0;write()
            with self.assertRaises(AssertionError):verify_path(raw,role,[segment],self.workload,self.samples,self.names)
            rows[1]['exit_code']=127;rows.reverse();write()
            with self.assertRaises(AssertionError):verify_path(raw,role,[segment],self.workload,self.samples,self.names)


if __name__=='__main__':unittest.main()
