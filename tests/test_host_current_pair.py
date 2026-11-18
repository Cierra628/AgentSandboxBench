"""Metadata fixtures and mocked controller only: no service, guest, mount or device."""
import asyncio
from contextlib import ExitStack
import copy
import csv
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from host_current_pair import ROLES,enabled,paths,observe,summarize,report
from run_host_scaling import scaling_command
from validate_isolated_agentenv import prettier_capacity
from platform_tasks import configuration


class CurrentPairTests(unittest.IsolatedAsyncioTestCase):
    def test_options_paths_capacity_and_command_are_new_mode_only(self):
        self.assertFalse(enabled(SimpleNamespace()))
        for mode in (None,'restore'):
            with self.assertRaises(ValueError):enabled(SimpleNamespace(host_current_pair=True,host_scaling_mode=mode))
        args=SimpleNamespace(host_current_pair=True,host_scaling_mode='cold')
        self.assertTrue(enabled(args))
        with patch.dict('os.environ',ASB_HOST_ROOTFS_REFERENCE='on'):
            with self.assertRaises(ValueError):enabled(args)
        rollouts={r:['read_repo','read_typescript','suffix'] for r in ['backbone','early_1','early_2','early_3','late_1','late_2','late_3']}
        self.assertEqual(paths(rollouts),{r:['read_repo','read_typescript'] for r in ROLES})
        self.assertEqual(prettier_capacity(args,True,None,'grpo',False,True),8*2**30)
        for mode,capacity in [('cold',16),('restore',24)]:
            self.assertEqual(prettier_capacity(SimpleNamespace(host_scaling_mode=mode),True,None,'grpo',False,True),capacity*2**30)
        with self.assertRaises(ValueError):prettier_capacity(SimpleNamespace(host_current_pair=True,host_scaling_mode='restore'),True,None,'grpo',False,True)
        cfg=configuration(ROOT/'configs/platform-prettier6604-host-file.json')
        old=scaling_command(cfg,Path('/seed'),'cold');new=scaling_command(cfg,Path('/seed'),'cold',current_pair=True)
        self.assertEqual(new,old+['--host-current-pair'])
        self.assertNotIn('--prettier6604-readonly-replay',new)

    def test_outer_routes_new_mode_and_rejects_query_or_sampling_conflicts(self):
        import run_host_file_workflow as entry
        argv=['entry',str(ROOT/'configs/platform-prettier6604-host-file.json'),'--current-pair']
        with patch.object(sys,'argv',argv),patch('platform_tasks.preflight',return_value={}),\
             patch.object(entry.subprocess,'check_output',return_value='systemd\n'),\
             patch.object(entry.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(entry.main(),0)
            command=run.call_args_list[0].args[0]
            self.assertIn('--current-pair',command);self.assertIn(str(ROOT/'scripts/run_host_scaling.py'),command)
        for extra in (['--host-sampling','off'],['--rootfs-reference']):
            with patch.object(sys,'argv',argv+extra),patch.object(entry.subprocess,'check_output') as docker:
                with self.assertRaises(SystemExit):entry.main()
                docker.assert_not_called()

    async def fixture(self,directory,fail_second=False,persistent=False):
        clock=[1_000_000_000];events=[];live={}
        def now():clock[0]+=10_000_000;return clock[0]
        async def wait(seconds):events.append(('wait',seconds));clock[0]+=int(seconds*1e9)
        async def start(role):
            events.append(('start',role))
            if fail_second and role=='early_1':raise RuntimeError('create failed')
            sid='sid-'+role;live[role]=sid;return sid
        async def path(sid,role,plan):events.append(('path',role,list(plan)))
        def verify(role):events.append(('verify',role));return dict(action_count=11)
        async def checkpoint(group,binding,guests,cli,label):
            t=now();bindings={r:dict(pid=20+ROLES.index(r),start_ticks='1') for r in guests}
            count=100+len(guests)*100
            return dict(label=label,group=str(group),group_id=group.stat().st_ino,start_monotonic_ns=t,counter_end_monotonic_ns=t,
                end_monotonic_ns=now(),counters=dict(current=count,anon=count-50,file=40,kernel=10),bindings=bindings,
                guest_meminfo=[dict(role=r,sandbox_id=sid,MemTotal=10000,MemAvailable=9000,Cached=100,Shmem=0) for r,sid in guests.items()])
        with ExitStack() as stack:
            stack.enter_context(patch('host_file_monitor.measurement_group',return_value=(directory,'fake.slice')))
            stack.enter_context(patch('host_sandbox_monitor.Binding'))
            stack.enter_context(patch('host_scaling.checkpoint',side_effect=checkpoint))
            stack.enter_context(patch('host_current_pair.time.monotonic_ns',side_effect=now))
            stack.enter_context(patch('host_current_pair.asyncio.sleep',side_effect=wait))
            query=stack.enter_context(patch('host_scaling.observe_files',side_effect=AssertionError('mincore must not run')))
            args=SimpleNamespace(host_current_pair=True,host_scaling_mode='cold',container='fake')
            if persistent:
                args.host_persistent_seed=directory/'seed'
                imported=dict(profile='host-persistent-input-v1',group=str(directory),group_id=directory.stat().st_ino,
                    import_end_monotonic_ns=1,scratch_removed_before_runtime=True)
                stack.enter_context(patch('host_persistent_input.validate_seed',return_value=(imported,directory)))
                def observe_input(*unused):
                    events.append(('input-query',));return dict(status='OBSERVED',group=str(directory),group_id=directory.stat().st_ino,
                        start_monotonic_ns=now(),end_monotonic_ns=now(),files=[],resident_bytes=0,ownership='NOT_QUERIED')
                stack.enter_context(patch('host_persistent_input.observe_input',side_effect=observe_input))
            await observe(SimpleNamespace(start=start,run_path=path),args,directory,Mock(),{r:['read_repo','read_typescript'] for r in ROLES},verify)
            query.assert_not_called()
        state=json.loads((directory/'host-scaling.json').read_text())
        points=[dict(monotonic_ns=state['baseline']['start_monotonic_ns']-1,current=100)]
        for s in state['stages']:
            a,b=s['window_start_monotonic_ns'],s['window_end_monotonic_ns']
            points.extend(dict(monotonic_ns=t,current=100+s['sandboxes']*100) for t in (a,a+1_000_000_000,b))
        points.append(dict(monotonic_ns=state['stages'][-1]['endpoint']['end_monotonic_ns']+1,current=300))
        return state,points,events

    async def test_sequence_measures_1_then_2_with_same_old_guest_and_no_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            state,points,events=await self.fixture(Path(tmp))
            result=summarize(state,points)
            self.assertEqual(state['sizes'],[1,2]);self.assertEqual(state['pss'],state['mincore'],'NOT_REQUESTED')
            self.assertEqual([v for v in events if v[0]=='wait'],[('wait',5),('wait',5)])
            self.assertEqual([v[1] for v in events if v[0]=='path'],list(ROLES))
            self.assertLess(events.index(('verify','backbone')),events.index(('start','early_1')))
            self.assertEqual([s['baseline_increase_bytes'] for s in result['phases']],[100,200])
            self.assertEqual(result['second_phase_mean_increase_bytes'],100)
            self.assertEqual(result['phases'][1]['old_guest_cached_change_bytes'],{'sid-backbone':0})

    async def test_persistent_query_is_once_after_both_windows_and_not_part_of_their_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            state,points,events=await self.fixture(Path(tmp),persistent=True)
            result=summarize(state,points)
            self.assertEqual(events[-1],('input-query',))
            self.assertEqual(sum(e[0]=='input-query' for e in events),1)
            self.assertEqual([e for e in events if e[0]=='wait'],[('wait',5),('wait',5)])
            self.assertEqual(result['mincore'],'INPUT_FILES_ONCE_AFTER_WINDOWS')
            self.assertEqual(result['input_file_check']['ownership'],'NOT_QUERIED')

    async def test_audit_rejects_replaced_old_guest_short_window_and_missing_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            state,points,_=await self.fixture(Path(tmp))
            changed=copy.deepcopy(state);changed['stages'][1]['endpoint']['bindings']['backbone']['pid']=99
            with self.assertRaises(AssertionError):summarize(changed,points)
            changed=copy.deepcopy(state);changed['stages'][1]['window_end_monotonic_ns']-=2_000_000_000
            with self.assertRaises(AssertionError):summarize(changed,points)
            with self.assertRaises(AssertionError):summarize(state,[points[0],points[-1]])

    async def test_offline_report_keeps_baseline_and_draws_actual_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'raw';host=root/'host';raw.mkdir();host.mkdir()
            state,points,_=await self.fixture(raw)
            task=dict(status='PASS',actual_action_count=22,reused_action_count=0,host_scaling=state,
                host_current_pair=True,host_scaling_mode='cold',paths={r:dict(action_count=11,guest_action_sum_ns=10) for r in ROLES},
                final_roles=[],official_evaluation='NOT_RUN_PREFIX_ONLY',cleanup=dict(service_sandbox_list_empty=True),controller_elapsed_ns=20_000_000_000)
            (raw/'result.json').write_text(json.dumps(task))
            plan={r:['read_repo','read_typescript','suffix'] for r in ['backbone','early_1','early_2','early_3','late_1','late_2','late_3']}
            (raw/'effective-config.json').write_text(json.dumps(dict(paths=plan,executed_paths=paths(plan))))
            (raw/'frozen-input').mkdir();(raw/'frozen-input/agent-sampling.json').write_text('{"segments":{}}')
            (raw/'input-provenance.json').write_text('{"original_segment_names":{}}')
            for p in points:p.update(anon=p['current']-50,file=40,kernel=10)
            with (host/'memory.tsv').open('w') as f:
                writer=csv.DictWriter(f,list(points[0]),delimiter='\t');writer.writeheader();writer.writerows(points)
            with patch('run_prettier6604_grpo.verify_path',return_value=dict(action_count=11,guest_action_sum_ns=10)):
                value=report(raw,host,root/'report')
                # The native guest report must not dispatch this 22-action mode to the old 77-action audit.
                from report_task_overview import write_report
                group=dict(role='backbone',sessions=[],chunks=[[dict(timestamp_ns='1',MemTotal_kib='100',MemAvailable_kib='50',Cached_kib='10',Shmem_kib='0')]])
                target=root/'guest-report';target.mkdir()
                with patch('report_task_overview.samples',return_value=({'sid':group},'fixture')),\
                     patch('report_task_overview.tool_rows',return_value=([dict(tool_index=i+1,duration_ns=20 if i==0 else 0) for i in range(22)],20)),\
                     patch('host_scaling.audit',side_effect=AssertionError('old audit must not run')):
                    guest=write_report(raw,target)
                self.assertEqual(guest['host_current_pair']['actual_actions'],22)
                self.assertEqual(guest['official_task_status'],'NOT_RUN_PREFIX_ONLY')
            self.assertEqual(value['baseline_counters']['current'],100)
            self.assertEqual(value['actual_actions'],22)
            self.assertGreater(value['checkpoint_elapsed_ns'],0)
            self.assertGreater((root/'report/host-current.png').stat().st_size,1000)

    async def test_failure_retains_first_guest_for_existing_cleanup_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError,'create failed'):await self.fixture(Path(tmp),True)
            state=json.loads((Path(tmp)/'host-scaling.json').read_text())
            self.assertEqual(state['status'],'FAIL')
            self.assertEqual([s['status'] for s in state['stages']],['PASS','FAIL'])
            self.assertEqual(state['stages'][0]['endpoint']['bindings']['backbone']['pid'],20)


if __name__=='__main__':unittest.main()
