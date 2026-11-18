"""Offline only: local metadata fixtures and mocked controller/proc observations."""
import asyncio
import copy
import csv
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from host_file_matched_task import INSTANCES,matched_paths,source_role,bound_pss,observe_matched,report_matched,report_matched_off
from run_host_file_restore import restore_command
from run_prettier6604_grpo import verify_path


def snapshot(guests,t=0):
    return dict(status='OBSERVED',start_monotonic_ns=t,end_monotonic_ns=t+1,
        processes=[dict(**g['binding'],comm='firecracker',pss=4096) for g in guests])


class MatchedTests(unittest.TestCase):
    def test_three_independent_instances_keep_same_original_suffix(self):
        paths={'backbone':['read_repo','read_typescript','finish'],
               'early_1':['read_repo','read_typescript','inspect','fix']}
        before=copy.deepcopy(paths);selected=matched_paths(paths)
        self.assertEqual(paths,before)
        self.assertEqual(list(selected),['backbone',*INSTANCES])
        self.assertEqual([source_role(r) for r in INSTANCES],['early_1']*3)
        selected[INSTANCES[0]].append('extra')
        self.assertEqual(selected[INSTANCES[1]],['inspect','fix'])
        self.assertEqual(source_role('early_2'),'early_2')

    def test_new_command_opt_in_and_volume_requirements(self):
        base=['bash','runner','--host-file-monitor','--prettier6604-readonly-replay','--light-monitor']
        self.assertIn('--host-restore-short',restore_command(base))
        new=restore_command(base,matched=True)
        self.assertIn('--host-matched-task',new);self.assertNotIn('--host-restore-short',new)
        self.assertIn('--host-file-monitor',new);self.assertNotIn('--light-monitor',new)
        from validate_isolated_agentenv import prettier_capacity,GIB
        args=SimpleNamespace(host_matched_task=True)
        self.assertEqual(prettier_capacity(args,True,None,'bpo',False,True),24*GIB)
        with self.assertRaises(ValueError):prettier_capacity(args,True,None,'grpo',False,True)

    def test_endpoint_requires_exact_nonzero_vmm_identities(self):
        guests=[dict(binding=dict(pid=i,start_ticks=str(i))) for i in (1,2)]
        good=snapshot(guests)
        self.assertEqual(bound_pss(good,guests)['vmm_pss_bytes'],8192)
        for bad in ('missing','reused','zero','extra'):
            value=copy.deepcopy(good)
            if bad=='missing':value['processes'].pop()
            elif bad=='reused':value['processes'][0]['start_ticks']='reused'
            elif bad=='zero':value['processes'][0]['pss']=0
            else:value['processes'].append(dict(pid=99,start_ticks='99',comm='firecracker',pss=4096))
            with self.assertRaises(ValueError):bound_pss(value,guests)

    def test_alias_verification_uses_original_exits_not_alias_sampling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workload=root/'workload';raw=root/'raw';role='N1-a';seg='inspect'
            dest=raw/'rollouts'/role/seg;manifest=workload/'segments'/seg
            dest.mkdir(parents=True);manifest.mkdir(parents=True)
            data='step\tsha256\tcommand\n001\tfixture\ttrue\n'
            (manifest/'actions.tsv').write_text(data);(dest/'actions.tsv').write_text(data)
            (dest/'patch.diff').write_text('patch')
            (dest/'guest-memory.tsv').write_text('MemTotal_kib\tMemFree_kib\n100\t80\n')
            sample=[dict(role='early_1',segment=seg,actions=[dict(exit_code=127)])]
            def steps(code):
                (dest/'steps.tsv').write_text(f'step\texit_code\tbranch\tround\tduration_ns\n001\t{code}\t{role}\t{seg}\t10\n')
            steps(127)
            good=verify_path(raw,role,[seg],workload,sample,{seg:seg},sampled_role='early_1')
            self.assertEqual(good['action_count'],1)
            steps(0)
            with self.assertRaises(AssertionError):verify_path(raw,role,[seg],workload,sample,{seg:seg},sampled_role='early_1')
            (dest/'steps.tsv').write_text('step\texit_code\tbranch\tround\tduration_ns\n')
            with self.assertRaises(AssertionError):verify_path(raw,role,[seg],workload,sample,{seg:seg},sampled_role='early_1')

    def test_actual_plan_count_is_41_not_two_different_suffixes(self):
        workload=ROOT/'.artifacts/prettier6604/grpo-input-audit-20261008T120626Z/workload'
        paths=matched_paths(json.loads((workload/'rollouts.json').read_text()))
        count=0
        for role,path in paths.items():
            for seg in path:
                with (workload/'segments'/seg/'actions.tsv').open() as stream:count+=len(list(csv.DictReader(stream,delimiter='\t')))
        self.assertEqual(count,41)


    def test_report_signed_deltas_without_periodic_pss_or_file_savings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'host';raw=root/'raw';source.mkdir();raw.mkdir()
            def save(path,value):path.write_text(json.dumps(value))
            phases=[];paths={};sessions=[];curves={}
            for n,roles,t,resident in ((1,INSTANCES[:1],100,4096),(2,INSTANCES[1:],200,12288)):
                guests=[]
                for i,role in enumerate(roles):
                    guests.append(dict(role=role,sandbox_id=role,binding=dict(pid=n*10+i,start_ticks='1'),
                        task_start_ns=t,task_end_ns=t+15))
                    paths[role]=dict(action_count=10,source_patch_check='PASS',independent_check='PASS',
                        guest_action_sum_ns=12,official_task_status='FAIL',official_passed=0,official_failed=2)
                    sessions.append(dict(sandbox_id=role,status='PASS'))
                    curves[role]=dict(chunks=[[dict(monotonic_ns=str(k),Cached_kib='10') for k in (1,2)]])
                device=dict(kind='memory-device',stable=True,mapping_length=4*2**30,resident_bytes=resident)
                phases.append(dict(status='PASS',functional_checks='PASS',sandboxes=n,guests=guests,
                    start_monotonic_ns=t,end_monotonic_ns=t+20,baseline=dict(file=100,anon=5,current=110),
                    endpoint_pss=bound_pss(snapshot(guests,t+21),guests),
                    residency=dict(status='OBSERVED',files=[device])))
            save(raw/'result.json',dict(status='PASS',action_count=41,paths=paths,
                checks=dict(final_source_properties_and_complete_official_outcomes='PASS')))
            save(raw/'host-matched.json',dict(status='PASS',groups=phases))
            save(raw/'runtime-monitor.json',dict(status='PASS',sessions=sessions))
            save(root/'host-file-residency.json',dict(status='OBSERVED',accounting='SHORTFALL_REQUIRES_RESOLUTION'))
            (source/'memory.tsv').write_text('monotonic_ns\tfile\tanon\tcurrent\n101\t90\t10\t110\n110\t95\t12\t115\n201\t92\t20\t120\n210\t97\t22\t125\n')
            (source/'pss.jsonl').write_text('')
            with patch('report_sandbox_runtime.samples',return_value=(curves,'test')):
                value=report_matched(source,raw,root/'report')
            self.assertEqual(value['groups'][0]['file_delta_range_bytes'],[-10,-5])
            self.assertEqual(value['groups'][0]['complete_task_pss_count'],0)
            self.assertLess(value['device_linear_reference']['reduction_vs_linear_reference'],0)
            self.assertEqual(value['whole_host_physical_savings'],'NOT_MEASURED')
            self.assertEqual(value['accounting_validation'],'SHORTFALL_REQUIRES_RESOLUTION')
            self.assertTrue((root/'report/matched.png').exists())
            self.assertIsNone(value['groups'][0]['memory_device_pss'])
            self.assertIsNone(value['groups'][0]['ordinary_residency'])
            from run_host_file_restore import paired_memory_pss
            for phase in phases:
                device=phase['residency']['files'][0];t=phase['end_monotonic_ns']
                device.update(device=6,inode=1,rdev=2,start_monotonic_ns=t+4,end_monotonic_ns=t+5)
                before=snapshot(phase['guests'],t+2);after=snapshot(phase['guests'],t+6)
                for snap in (before,after):
                    for process in snap['processes']:process.update(pss_anon=1024,pss_file=2048,pss_shmem=1024)
                phase['residency']['memory_device_pss']=paired_memory_pss(before,device,after,phase['guests'])
                phase['device_released_ns']=t+30
                phase['ordinary_residency']=dict(status='OBSERVED',observation_mode='LIVE',errors=[],
                    start_monotonic_ns=t+8,end_monotonic_ns=t+9,files=[dict(device=9,inode=1)])
            save(raw/'host-matched.json',dict(status='PASS',groups=phases,ordinary_residency_requested=True))
            with patch('report_sandbox_runtime.samples',return_value=(curves,'test')):
                paired=report_matched(source,raw,root/'paired-report')
            self.assertEqual(paired['groups'][1]['memory_device_pss']['after']['vmm_pss_anon_bytes'],2048)
            self.assertEqual(paired['whole_host_physical_savings'],'NOT_MEASURED')
            self.assertEqual(paired['groups'][0]['ordinary_residency']['status'],'OBSERVED')


class SamplingOffReportTests(unittest.TestCase):
    def test_timing_only_report_does_not_invent_memory_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw=Path(tmp);groups=[];paths={'backbone':dict(action_count=11,guest_action_sum_ns=7)}
            for n,roles in ((1,INSTANCES[:1]),(2,INSTANCES[1:])):
                guests=[]
                for role in roles:
                    guests.append(dict(role=role,task_start_ns=10,task_end_ns=20))
                    paths[role]=dict(action_count=10,guest_action_sum_ns=11,source_patch_check='PASS',independent_check='PASS',official_passed=0,official_failed=2)
                groups.append(dict(status='PASS',functional_checks='PASS',baseline=None,host_measurements='NOT_REQUESTED',sandboxes=n,guests=guests,
                    memory_backends=[dict(device=6,inode=1,rdev=2)]*n,start_monotonic_ns=10,end_monotonic_ns=21))
            task=dict(status='PASS',action_count=41,logical_action_count=63,checks=dict(original='PASS'),controller_elapsed_ns=100,paths=paths)
            trial=dict(status='PASS',host_sampling='off',host_measurements='NOT_REQUESTED',groups=groups)
            runtime=dict(status='PASS',host_vmm_requested=False,sessions=[dict(status='PASS')],profile='light',interval_seconds=.1)
            for name,value in (('result',task),('host-matched',trial),('runtime-monitor',runtime)):(raw/(name+'.json')).write_text(json.dumps(value))
            result=report_matched_off(raw,raw/'report')
            self.assertEqual(result['actual_tool_ns'],40);self.assertEqual(result['host_measurements'],'NOT_REQUESTED')
            self.assertNotIn('memory_device_bytes',result['groups'][0])
            groups[0]['residency']={};(raw/'host-matched.json').write_text(json.dumps(trial))
            with self.assertRaises(AssertionError):report_matched_off(raw,raw/'bad')


class MatchedSequenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_phases_check_before_delete_and_have_no_idle_sleep(self):
        await self.sequence('on')

    async def test_off_keeps_same_tasks_but_never_calls_measurement_functions(self):
        await self.sequence('off')

    async def sequence(self,sampling):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);events=[];guests={};runtime=SimpleNamespace()
            runtime.mark=lambda sid,label:events.append(('mark',sid))
            runtime.stop=lambda sid,label:events.append(('stop',sid))
            class Controller:
                live={}
                async def start(self,role,**kwargs):
                    sid=role;self.live[sid]=sid
                    guests[sid]=dict(role=role,sandbox_id=sid,binding=dict(pid=len(guests)+1,start_ticks='1'))
                    (out/('restored-'+role+'.json')).write_text(json.dumps(dict(status='PASS')))
                    events.append(('start',sid));return sid
                async def run_path(self,sid,role,path):events.append(('task',sid))
            controller=Controller()
            binding=SimpleNamespace(resolve=lambda sid:guests[sid]['binding'])
            def live(group,binding,phase,template,out,*,capture_memory_pss=False):
                self.assertTrue(capture_memory_pss)
                events.append(('residency',len(phase)))
                return dict(status='OBSERVED',backends=[dict(rdev=0)])
            roots=out/'roots.json';roots.write_text(json.dumps([str(out/'backing')]))
            def inventory(group,paths,destination,*,live=False):
                self.assertTrue(live);self.assertEqual(paths,[out/'backing'])
                events.append(('inventory',len(controller.live)))
                return dict(status='OBSERVED')
            def verify(role):events.append(('verify',role));return dict(action_count=10)
            async def check(role,sid):events.append(('check',sid))
            def cli(*args,**kwargs):events.append(('delete',args[2]))
            with patch.dict('os.environ',{'ASB_HOST_FILE_SAMPLING':sampling}),\
                 patch('host_file_monitor.measurement_group',return_value=(out,'owned')),\
                 patch('host_file_monitor.counters',return_value=dict(file=100,anon=1,current=101)) as counter,\
                 patch('host_file_monitor.pids',return_value=[]),\
                 patch('host_file_monitor.pss_snapshot',side_effect=lambda group:snapshot([g for g in guests.values() if g['sandbox_id'] in controller.live])) as pss,\
                 patch('host_sandbox_monitor.Binding',return_value=binding),\
                 patch('host_file_matched_task.live_residency',side_effect=live) as resident,\
                 patch('host_file_matched_task.ram_backend',return_value=dict(device=6,inode=1,rdev=0)),\
                 patch('host_file_monitor.residency_check',side_effect=inventory) as regular,\
                 patch('host_file_matched_task.asyncio.sleep') as sleep:
                value=await observe_matched(controller,'template','parent',runtime,SimpleNamespace(container='mock',host_file_roots=roots if sampling=='on' else None),out,cli,
                    {r:['inspect','fix'] for r in INSTANCES},verify,check)
            self.assertEqual(value['status'],'PASS');self.assertFalse(controller.live);sleep.assert_not_called()
            self.assertEqual([e for e in events if e[0]=='task'],[('task',r) for r in INSTANCES])
            for role in INSTANCES:
                self.assertLess(events.index(('verify',role)),events.index(('check',role)))
                self.assertLess(events.index(('check',role)),events.index(('delete',role)))
            self.assertLess(events.index(('delete','N1-a')),events.index(('start','N2-a')))
            if sampling=='on':
                self.assertTrue(value['ordinary_residency_requested'])
                for n,role in ((1,'N1-a'),(2,'N2-a')):
                    self.assertLess(events.index(('residency',n)),events.index(('inventory',n)))
                    self.assertLess(events.index(('inventory',n)),events.index(('check',role)))
                    self.assertLess(events.index(('inventory',n)),events.index(('delete',role)))
            else:
                for mock in (counter,pss,resident,regular):mock.assert_not_called()
                self.assertEqual(value['host_measurements'],'NOT_REQUESTED')
                self.assertEqual([g['baseline'] for g in value['groups']],[None,None])
                self.assertTrue(all('residency' not in g for g in value['groups']))



if __name__=='__main__':unittest.main()
