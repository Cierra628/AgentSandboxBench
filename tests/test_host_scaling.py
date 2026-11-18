"""Offline only: mocked guest/controller operations and tiny metadata fixtures."""
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from host_scaling import SIZES,options,paths,checkpoint,observe,audit,report,sync_preparation
from host_independent_task import phase_options
from run_host_scaling import scaling_command
from platform_tasks import configuration
from validate_isolated_agentenv import prettier_capacity
from analyze_light_host_growth import split_changes,file_family

ROLES=['backbone','early_1','early_2','early_3','late_1','late_2','late_3']
ROLLOUTS={r:['read_repo','read_typescript','inspect','fix'] for r in ROLES}
MEM='MemTotal: 4096000 kB\nMemAvailable: 4000000 kB\nCached: 1200 kB\nShmem: 4 kB\n'


class OptionTests(unittest.TestCase):
    def test_options_preserve_old_modes_and_reject_mixed_experiments(self):
        self.assertIsNone(options(SimpleNamespace()))
        for mode in ('cold','restore'):
            args=SimpleNamespace(host_scaling_mode=mode)
            if mode=='restore':args.host_checkpoint_bundle=Path('/bundle');args.host_checkpoint_source=Path('/source')
            self.assertEqual(options(args),mode);self.assertIsNone(phase_options(args))
            args.host_matched_task=True
            with self.assertRaises(ValueError):options(args)
        for args in (SimpleNamespace(host_scaling_mode='restore'),SimpleNamespace(host_scaling_mode='cold',host_checkpoint_bundle='/bundle'),
                     SimpleNamespace(host_checkpoint_bundle='/bundle'),SimpleNamespace(host_scaling_mode='unknown')):
            with self.assertRaises(ValueError):(options(args) if getattr(args,'host_scaling_mode',None) else phase_options(args))

    def test_paths_counts_commands_and_capacity(self):
        cfg=configuration(ROOT/'configs/platform-prettier6604-host-file.json')
        for mode,count,capacity in (('cold',77,16),('restore',70,24)):
            plan=paths(ROLLOUTS,mode)
            weights=dict(read_repo=5,read_typescript=6,inspect=4,fix=6)
            self.assertEqual(sum(weights[s] for p in plan.values() for s in p),count)
            command=scaling_command(cfg,Path('/seed'),mode,Path('/bundle'),Path('/source'))
            self.assertIn('--host-file-monitor',command);self.assertEqual(command.count('--light-monitor'),1)
            self.assertNotIn('--prettier6604-readonly-replay',command)
            self.assertEqual(command[command.index('--prettier6604-schedule')+1],'grpo')
            self.assertEqual('--host-checkpoint-bundle' in command,mode=='restore')
            self.assertEqual(prettier_capacity(SimpleNamespace(host_scaling_mode=mode),True,None,'grpo',False,True),capacity*2**30)
        with self.assertRaises(ValueError):prettier_capacity(SimpleNamespace(host_scaling_mode='restore'),True,None,'bpo',False,True)

    def test_sync_is_one_owned_filesystem_before_service_and_failures_propagate(self):
        call=Mock();receipt=sync_preparation(call,Path('/owned'))
        call.assert_called_once_with('sync-scaling-preparation',['sync','-f',Path('/owned')],60)
        self.assertEqual(receipt['status'],'PASS')
        full=Mock();sync_preparation(full,Path('/owned'),timeout=300)
        full.assert_called_once_with('sync-scaling-preparation',['sync','-f',Path('/owned')],300)
        with self.assertRaises(RuntimeError):sync_preparation(Mock(side_effect=RuntimeError('sync failed')),Path('/owned'))
        source=(ROOT/'scripts/validate_isolated_agentenv.py').read_text()
        self.assertLess(source.index("receipt['host_scaling_preparation_sync']="),source.index("call('start-server'"))
        self.assertEqual(source.count('scaling_arguments(scaling,host_bundle,'),2)

    def test_sample_association_does_not_duplicate_concurrent_requests(self):
        points=[dict(monotonic_ns=t,file=f) for t,f in ((0,100),(10,120),(20,110),(30,160))]
        events=[dict(stage='0001-restore-a',start_monotonic_ns=2,duration_ns=12),
                dict(stage='0002-restore-b',start_monotonic_ns=3,duration_ns=10)]
        values=split_changes(points,events)
        self.assertEqual(values['restore_request']['file_change_bytes'],10)
        self.assertEqual(values['other']['file_change_bytes'],50)
        self.assertEqual(sum(v['file_change_bytes'] for v in values.values()),60)
        self.assertEqual(file_family('/owned/volume.ext4',Path('/owned')),'private_loop_image')
        self.assertEqual(file_family('/owned/mount/env/firecracker-work/a/upper',Path('/owned')),'task_writable_backing')

    def test_wrapper_builds_one_slice_and_rejects_missing_restore_before_docker(self):
        import run_host_file_workflow as wrapper
        args=['entry',str(ROOT/'configs/platform-prettier6604-host-file.json'),'--scaling','restore']
        with patch.object(sys,'argv',args),patch.object(wrapper.subprocess,'check_output') as docker:
            with self.assertRaises(SystemExit):wrapper.main()
            docker.assert_not_called()
        for mode in ('cold','restore'):
            command=args[:2]+['--scaling',mode,'--host-sampling','off']+(['--resume-prefix','/saved'] if mode=='restore' else [])
            with patch.object(sys,'argv',command),patch('platform_tasks.preflight',return_value={}),\
                 patch.object(wrapper.subprocess,'check_output',return_value='systemd\n'),\
                 patch.object(wrapper.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(wrapper.main(),0)
                launch=run.call_args_list[0].args[0]
                self.assertEqual(sum(v.startswith('--slice=') for v in launch),1)
                self.assertIn('--setenv=ASB_HOST_FILE_SAMPLING=off',launch)
                self.assertIn(str(ROOT/'scripts/run_host_scaling.py'),launch)
                self.assertEqual(launch[launch.index('--mode')+1],mode)


class SequenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_checkpoints_do_not_request_pss_and_keep_phase_meminfo(self):
        with tempfile.TemporaryDirectory() as tmp:
            group=Path(tmp);vm=dict(pid=42,start_ticks='1');binding=Mock();binding.resolve.return_value=vm
            pss=dict(status='PARTIAL',processes=[dict(comm='firecracker',**vm)],disappeared_pids=[100])
            with patch('host_file_monitor.counters',return_value=dict(current=1,file=1,anon=0)),\
                 patch('host_file_monitor.pss_snapshot',side_effect=AssertionError('PSS must be disabled')):
                row=await checkpoint(group,binding,{'a':'sid'},Mock(return_value=MEM),'test')
            self.assertEqual(row['pss']['status'],'NOT_REQUESTED_FILE_FOCUS')
            self.assertEqual(row['guest_meminfo'][0]['Cached'],1200*1024)
            self.assertEqual(row['group_id'],group.stat().st_ino)

    async def sequence(self,out,mode,fail=None,fail_verify=None):
        events=[];live={};checkpoint_calls=[];created={}
        async def start(role,**kwargs):
            sid='sid-'+role;live[sid]=sid;created[role]=kwargs;events.append(('start',role))
            await asyncio.sleep(0)  # Yield only inside a mocked test controller.
            if fail==role:raise RuntimeError('creation failed')
            return sid
        async def run_path(sid,role,plan):events.append(('path',role,plan))
        def verify_instance(role):
            events.append(('verify',role))
            if role==fail_verify:raise ValueError('original exit or action mismatch')
            return dict(action_count=11 if mode=='cold' else 10)
        controller=SimpleNamespace(start=start,run_path=run_path,live=live)
        async def point(group,binding,guests,cli,label):
            begin=time.monotonic_ns();checkpoint_calls.append((label,list(guests)))
            identities={r:dict(pid=100+ROLES.index(r),start_ticks=str(ROLES.index(r))) for r in guests}
            counters=dict(current=1000+len(guests)*100,file=100,anon=900+len(guests)*100)
            return dict(label=label,group=str(group),group_id=group.stat().st_ino,start_monotonic_ns=begin,
                counter_end_monotonic_ns=begin,counters=counters,bindings=identities,
                guest_meminfo=[dict(role=r,sandbox_id=sid,Cached=1000-len(guests)) for r,sid in guests.items()],
                pss=dict(status='NOT_REQUESTED_FILE_FOCUS'),counters_after=counters,end_monotonic_ns=time.monotonic_ns())
        args=SimpleNamespace(host_scaling_mode=mode,container='fake')
        saved=None
        if mode=='restore':
            args.host_checkpoint_bundle='/bundle';args.host_checkpoint_source='/source'
            saved=dict(template='snapshot',parent='parent')
        with ExitStack() as stack:
            stack.enter_context(patch('host_file_monitor.measurement_group',return_value=(out,'fake.slice')))
            binding=stack.enter_context(patch('host_sandbox_monitor.Binding'))
            binding.return_value.resolve.side_effect=lambda sid:dict(pid=100+ROLES.index(sid.removeprefix('sid-')),start_ticks=str(ROLES.index(sid.removeprefix('sid-'))))
            stack.enter_context(patch('host_scaling.checkpoint',side_effect=point))
            query_sizes=[]
            def query(args,out,guests,group,binding,saved):
                query_sizes.append(len(guests))
                begin=time.monotonic_ns();end=time.monotonic_ns()
                from host_scaling import classify_objects
                item=dict(device=1,inode=2,rdev=7,block_device=True,mapping_length=4096,resident_bytes=4096,
                    stable=True,path='/metadata/only',start_monotonic_ns=begin,end_monotonic_ns=end)
                if mode=='cold':
                    observation=dict(regular=dict(status='OBSERVED',files=[]),shared_readonly_drives=[dict(
                        drive_id='rootfs',users=sorted(guests),residency=item,start_monotonic_ns=begin,end_monotonic_ns=end)])
                else:observation=dict(status='OBSERVED',files=[dict(item,kind='memory-device')])
                inventory={r:[] for r in guests}
                objects=classify_objects(observation,mode,list(guests),inventory)
                return dict(status='OBSERVED',profile='shared-files-v1',sandboxes=len(guests),objects=objects,
                    start_monotonic_ns=begin,end_monotonic_ns=end,elapsed_ns=end-begin,cpu_ns=0,
                    observation=observation,drive_inventory=inventory)
            queries=stack.enter_context(patch('host_scaling.observe_files',side_effect=query))
            await observe(controller,args,out,Mock(),paths(ROLLOUTS,mode),verify_instance,saved=saved)
            self.assertEqual(query_sizes,[1,7])
        return json.loads((out/'host-scaling.json').read_text()),events,checkpoint_calls,created

    async def test_incremental_sequence_keeps_old_guests_and_executes_each_path_once(self):
        for mode in ('cold','restore'):
            with tempfile.TemporaryDirectory() as tmp:
                state,events,points,created=await self.sequence(Path(tmp),mode)
                self.assertEqual(state['status'],'PASS')
                self.assertEqual([s['new_roles'] for s in state['stages']],[ROLES[:1],ROLES[1:2],ROLES[2:4],ROLES[4:]])
                self.assertEqual([len(p[1]) for p in points],[0,0,1,1,2,2,4,4,7])
                self.assertEqual([e[1] for e in events if e[0]=='path'],ROLES)
                self.assertLess(events.index(('verify','backbone')),events.index(('start','early_1')))
                self.assertEqual(len(created),7)
                if mode=='restore':self.assertTrue(all(k==dict(template='snapshot',parent='parent') for k in created.values()))
                else:self.assertTrue(all(k=={} for k in created.values()))
                self.assertEqual(len(state['stages'][-1]['endpoint']['bindings']),7)

    async def test_sampling_off_keeps_fixed_checkpoints_and_final_query(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',ASB_HOST_FILE_SAMPLING='off'):
            state,events,points,_=await self.sequence(Path(tmp),'restore')
            self.assertEqual(state['periodic_host_sampling'],'off')
            self.assertEqual(len(points),9)
            self.assertEqual(len([e for e in events if e[0]=='path']),7)
            self.assertIn('endpoint_residency_query_ns',state)

    async def test_restore_audit_uses_early_1_source_for_all_seven_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw=Path(tmp);state,*_=await self.sequence(raw,'restore')
            value=dict(action_count=10,guest_action_sum_ns=11,final_patch_sha256='existing-contract')
            outcome=dict(value,source_patch_check='PASS',independent_check='PASS',official_passed=0,official_failed=2,official_task_status='FAIL')
            task=dict(status='PASS',host_scaling=state,host_scaling_mode='restore',actual_action_count=70,reused_action_count=0,
                paths={r:outcome for r in ROLES},cleanup=dict(service_sandbox_list_empty=True,snapshots_removed=['snapshot']),
                imported_snapshot='snapshot',checks=dict(restored_files_progress_and_process='PASS'))
            (raw/'frozen-input').mkdir();(raw/'frozen-input/agent-sampling.json').write_text('{"segments":{}}')
            entries=[('result',task),('effective-config',dict(paths=ROLLOUTS,executed_paths=paths(ROLLOUTS,'restore'))),('input-provenance',dict(original_segment_names={}))]
            entries += [('restored-'+r,dict(status='PASS',template='snapshot')) for r in ROLES]
            for name,obj in entries:(raw/(name+'.json')).write_text(json.dumps(obj))
            with patch('run_prettier6604_grpo.verify_path',return_value=value) as verify:
                result=audit(raw)
                self.assertEqual(result['actual_actions'],70)
                self.assertEqual(verify.call_count,7)
                self.assertTrue(all(c.kwargs['sampled_role']=='early_1' for c in verify.call_args_list))
                task['paths']['early_2']=dict(outcome,official_task_status='PASS')
                (raw/'result.json').write_text(json.dumps(task))
                with self.assertRaises(AssertionError):audit(raw)

    async def test_failure_stops_expansion_and_retains_state_for_owner_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError,'creation failed'):
                await self.sequence(Path(tmp),'cold',fail='early_2')
            state=json.loads((Path(tmp)/'host-scaling.json').read_text())
            self.assertEqual(state['status'],'FAIL')
            self.assertEqual([s['sandboxes'] for s in state['stages']],[1,2,4])
            self.assertEqual(state['stages'][-1]['status'],'FAIL')
            self.assertNotIn('endpoint_residency',state)

    async def test_first_path_must_pass_original_action_checks_before_expansion(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'original exit or action mismatch'):
                await self.sequence(Path(tmp),'cold',fail_verify='backbone')
            state=json.loads((Path(tmp)/'host-scaling.json').read_text())
            self.assertEqual([s['sandboxes'] for s in state['stages']],[1])
            self.assertEqual(state['stages'][0]['status'],'FAIL')

    async def test_saved_audit_and_plot_keep_unknown_total_and_reject_group_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'raw';raw.mkdir()
            state,*_=await self.sequence(raw,'cold')
            paths_value=paths(ROLLOUTS,'cold');value=dict(action_count=11,guest_action_sum_ns=10,final_patch_sha256='existing-contract')
            task=dict(status='PASS',host_scaling=state,host_scaling_mode='cold',actual_action_count=77,reused_action_count=0,
                paths={r:value for r in ROLES},cleanup=dict(service_sandbox_list_empty=True),official_evaluation='NOT_RUN_PREFIX_ONLY',final_roles=[])
            (raw/'frozen-input').mkdir();(raw/'frozen-input/agent-sampling.json').write_text('{"segments":{}}')
            for name,obj in [('result',task),('effective-config',dict(paths=ROLLOUTS,executed_paths=paths_value)),('input-provenance',dict(original_segment_names={}))]:
                (raw/(name+'.json')).write_text(json.dumps(obj))
            with patch('run_prettier6604_grpo.verify_path',return_value=value):
                summary=report(raw,root/'plot')
                self.assertEqual(summary['whole_physical_savings'],'NOT_MEASURED')
                self.assertTrue((root/'plot/scaling.png').exists())
                self.assertEqual(summary['stages'][-1]['baseline_delta']['anon'],700)
                self.assertTrue(all(v<0 for v in summary['stages'][-1]['old_guest_cached_changes_bytes'].values()))
                task['host_scaling'].pop('profile')
                (raw/'result.json').write_text(json.dumps(task))
                self.assertNotIn('profile',audit(raw))
                task['host_scaling']['stages'][2]['endpoint']['group_id']=-1
                (raw/'result.json').write_text(json.dumps(task))
                with self.assertRaises(AssertionError):audit(raw)


if __name__=='__main__':unittest.main()
