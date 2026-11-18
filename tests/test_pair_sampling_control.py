"""Offline control-flow checks; fake all guest/service and collector operations."""
import ast
import asyncio
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_prettier6604_grpo as runner
import validate_isolated_agentenv as validator


class PairSamplingControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_on_and_off_keep_task_paths_and_only_on_creates_samplers(self):
        tree=ast.parse(Path(runner.__file__).read_text())
        cls=next(n for n in ast.walk(tree) if isinstance(n,ast.ClassDef) and n.name=='SampledPaths')
        method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='do_bpo')
        cases=[('off',None,None,False),('on',None,None,False),('on','preserve',None,False),('on','drop-clean',None,False),('on',None,128,False),('on',None,64,False),('on',None,128,True)]
        for sampling,policy,target,reuse,early in [(*v,False) for v in cases]+[('on',None,64,False,True),('on',None,128,True,True)]:
            with self.subTest(sampling=sampling,policy=policy,target=target),tempfile.TemporaryDirectory() as name:
                out=Path(name);workers={};result=dict(phases=[],checks={});events=[]
                class Controller:
                    rollouts={'backbone':['read_repo','read_typescript','unused']}
                    def __init__(self):self.live={};self.agentenv_templates=[]
                    async def start(self,role,**kwargs):
                        sid='root' if role=='backbone' else role
                        self.live[sid]=sid;events.append(('start',role));return sid
                    async def run_path(self,sid,role,path):events.append(('path',role,list(path)))
                    async def save_prefix(self,*args):events.append(('save',11));return 'snapshot'
                def start_samplers():
                    events.append(('samplers',))
                    for side in ['child-a','child-b']:
                        workers[side]=object();(out/'live-cache'/side).mkdir(parents=True)
                def cache_control(role,sid,phase,requested):events.append(('cache',role,phase,requested))
                def capacity_start(role,sid):events.append(('capacity-start',role))
                def capacity_stop(role,sid):events.append(('capacity-stop',role))
                parent={}
                def checkpoint_damon(action,role,sid):
                    events.append(('damon',action,role));parent.update(original_enabled='Y')
                def reuse_capacity_checkpoint(*args):
                    if early:(out/'damon-before-checkpoint.json').write_text('{"original_enabled":"Y"}')
                    return dict(template='snapshot',parent='root',step=11,boundary={},process={})
                namespace=dict(asyncio=asyncio,cache_pair=True,cache_sampling=sampling,guest_cache_policy=policy,cache_control=cache_control,
                    reuse_checkpoint=out/'preceding' if reuse else None,
                    recovery_checkpoint=None,
                    damon_before_checkpoint=early,parent_damon=parent,checkpoint_damon=checkpoint_damon,json=runner.json,
                    reuse_capacity_checkpoint=Mock(side_effect=reuse_capacity_checkpoint),
                    args=SimpleNamespace(container='owned',config_home=out,service_url='owned'),
                    cache_target=target,capacity_start=capacity_start,capacity_stop=capacity_stop,
                    runtime=SimpleNamespace(stop=Mock()),cli=Mock(),time=time,
                    start_samplers=start_samplers,workers=workers,out=out,
                    wait_sampler=Mock(),result=result,joined=runner.joined,
                    final_roles=['early_1','early_2'],executed={'early_1':['patch','verify'],'early_2':['inspect','verify']})
                exec(compile(ast.Module(body=[method],type_ignores=[]),'<actual do_bpo>','exec'),namespace)
                by_role=await namespace['do_bpo'](Controller())
                self.assertEqual(set(by_role),{'backbone','early_1','early_2'})
                self.assertEqual([e for e in events if e[0]=='path'],([] if reuse else [
                    ('path','backbone',['read_repo','read_typescript'])])+[
                    ('path','early_1',['patch','verify']),('path','early_2',['inspect','verify'])])
                self.assertGreaterEqual(result['restore_and_cache_ready_ns'],0)
                self.assertGreaterEqual(result['branch_replay_elapsed_ns'],0)
                self.assertEqual(result['branch_sides'],{'child-a':'early_1','child-b':'early_2'})
                self.assertEqual((out/'live-cache').exists(),sampling=='on')
                self.assertEqual(namespace['wait_sampler'].call_count,2 if sampling=='on' else 0)
                if reuse:
                    namespace['cli'].assert_not_called()
                    self.assertFalse(any(e[0]=='save' for e in events))
                    self.assertFalse(any(e[:2]==('start','backbone') for e in events))
                else:namespace['cli'].assert_called_once_with('delete-parent','delete','root',timeout=60)
                observed=[e for e in events if e[0]=='cache']
                self.assertEqual(len(observed),6 if policy else 0)
                if policy:
                    for role in ['early_1','early_2']:
                        self.assertEqual([e[2:] for e in observed if e[1]==role],
                            [('intervention',policy),('task-start','observe'),('task-end','observe')])
                    first_task=next(i for i,e in enumerate(events) if e[:2]==('path','early_1'))
                    self.assertTrue(all(i<first_task for i,e in enumerate(events) if e[0]=='cache' and e[2]!='task-end'))
                    self.assertEqual(result['checks']['guest_cache_intervention_and_endpoints'],'PASS')
                if target:
                    first=next(i for i,e in enumerate(events) if e[:2]==('path','early_1'))
                    last=max(i for i,e in enumerate(events) if e[0]=='path')
                    self.assertEqual(sum(e[0]=='capacity-start' for e in events),2)
                    self.assertEqual(sum(e[0]=='capacity-stop' for e in events),2)
                    self.assertTrue(all(i<first for i,e in enumerate(events) if e[0]=='capacity-start'))
                    self.assertTrue(all(i>last for i,e in enumerate(events) if e[0]=='capacity-stop'))
                if early:
                    self.assertEqual(parent['original_enabled'],'Y')
                    if not reuse:
                        prefix=next(i for i,e in enumerate(events) if e[:2]==('path','backbone'))
                        stop=events.index(('damon','prepare','backbone'))
                        save=events.index(('save',11))
                        self.assertLess(prefix,stop);self.assertLess(stop,save)
                    self.assertEqual(result['checks']['checkpoint_damon_inherited_and_parent_original_restored'],'PASS')

    async def test_storage_sampling_control_requires_real_pair_before_allocation(self):
        for setting in ['on','off']:
            with self.subTest(setting=setting),patch.object(validator.os,'geteuid',return_value=0), \
                 patch.object(validator.os,'posix_fallocate') as allocate:
                with self.assertRaisesRegex(ValueError,'requires the actual-task pair'):
                    validator.main(SimpleNamespace(prettier6604_snapshot_cache_sampling=setting))
                allocate.assert_not_called()

    async def test_direct_off_control_rejects_other_workflows(self):
        cfg=dict(task_id='t',base_commit='b');task=dict(instance_id='t',base_commit='b',F2P=[],P2P=[])
        args=SimpleNamespace(frozen_export=None,official_task=None,expected_official_audit=None,
                             role=None,schedule='grpo',snapshot_cache_sampling='off')
        with patch.object(runner,'validated_export',return_value=(cfg,None,None,None,None,None)), \
             patch.object(runner,'load',return_value=task),patch.object(runner,'reference',return_value=task):
            with self.assertRaisesRegex(AssertionError,'sampling control requires the real pair'):
                runner.run(args)

if __name__=='__main__':unittest.main()
