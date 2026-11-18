"""Mocked dual short path; never start real guests, devices or services."""
import asyncio
import ast
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from host_file_matched_task import observe_matched
from run_host_rootfs_short import short_command,workflow,inspect_result,check_source,task_action_receipt,SOURCE_ID,IMAGE
from platform_tasks import configuration
from isolated_storage import rootfs_network_pools
from validate_isolated_agentenv import source_vm_identities_unchanged


class RootfsShortTests(unittest.TestCase):
    def test_short_success_does_not_claim_unrequested_pss(self):
        tree=ast.parse((ROOT/'scripts/run_prettier6604_grpo.py').read_text())
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='success and (not light_monitor)')
        for short,light,expected in ((True,False,'NOT_REQUESTED'),(False,False,'MEASURED_PER_OWNED_VMM'),(False,True,'NOT_MEASURED')):
            ns=dict(success=True,rootfs_short=short,light_monitor=light,result=dict(VMM_PSS='NOT_MEASURED'))
            exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual PSS completion label>','exec'),ns)
            self.assertEqual(ns['result']['VMM_PSS'],expected)

    def test_proxy_bypass_follows_selected_networks_and_old_defaults_stay(self):
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='prettier6604 or python_inputs'
            and any(isinstance(c,ast.Constant) and c.value=='HTTP_PROXY' for c in ast.walk(n)))
        for enabled,pair in ((True,['10.213.0.0/16','10.214.0.0/16']),
                             (True,['10.215.0.0/16','10.216.0.0/16']),(False,[])):
            ns=dict(prettier6604=True,python_inputs=False,rootfs_short=enabled,argv=[],receipt=dict(rootfs_network_pools=pair))
            exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual container proxy environment>','exec'),ns)
            env=dict(s.split('=',1) for s in ns['argv'] if s!='-e')
            expected='localhost,127.0.0.1,::1,'+','.join(pair if enabled else ['10.211.0.0/16','10.212.0.0/16'])
            self.assertEqual(env['NO_PROXY'],expected);self.assertEqual(env['no_proxy'],expected)
            self.assertEqual(env['HTTP_PROXY'],'http://127.0.0.1:17891')

    def test_failed_restore_without_action_count_does_not_mask_original_error(self):
        self.assertEqual(task_action_receipt(dict(status='FAIL',error='WaitReady timeout')),
            dict(actual_actions=None,actual_actions_status='NOT_REPORTED'))
        self.assertEqual(task_action_receipt(dict(actual_action_count=20)),dict(actual_actions=20,actual_actions_status='REPORTED'))

    def test_pool_selection_excludes_host_routes_and_overlapping_ranges(self):
        routes=[dict(dst='default'),dict(dst='::1/128'),dict(dst='10.211.0.1'),dict(dst='10.212.0.2/31')]
        self.assertEqual(rootfs_network_pools(json.dumps(routes)),['10.213.0.0/16','10.214.0.0/16'])
        routes.append(dict(dst='10.213.0.9'))
        self.assertEqual(rootfs_network_pools(json.dumps(routes)),['10.215.0.0/16','10.216.0.0/16'])
        with self.assertRaisesRegex(ValueError,'no unused'):rootfs_network_pools('[{"dst":"10.0.0.0/8"}]')

    def test_external_vm_changes_do_not_hide_changes_to_source_vms(self):
        source='0::/owned-source';foreign=dict(pid=3,cgroup='0::/other')
        self.assertTrue(source_vm_identities_unchanged([], [foreign],source,allow_empty=True))
        self.assertFalse(source_vm_identities_unchanged([], [foreign],source))  # Old caller still requires an initial source VM.
        owned=dict(pid=1,cgroup=source)
        self.assertFalse(source_vm_identities_unchanged([], [owned],source,allow_empty=True))
        self.assertFalse(source_vm_identities_unchanged([owned],[],source,allow_empty=True))
        self.assertTrue(source_vm_identities_unchanged([owned],[owned,foreign],source,allow_empty=True))

    def test_unavailable_networks_stop_before_source_or_volume_creation(self):
        cfg=dict(task_id='prettier__prettier-6604',rollout='single',host_file_monitor=True,checkpoint_after_step=None)
        with tempfile.TemporaryDirectory() as tmp,patch('run_host_rootfs_short.ROOT',Path(tmp)),\
             patch('run_host_rootfs_short.configuration',return_value=cfg),\
             patch('run_host_rootfs_short.measurement_group',return_value=(Path(tmp),'owned')),\
             patch('run_host_rootfs_short.available',return_value=51*2**30),\
             patch('run_host_rootfs_short.resumed_capture',side_effect=AssertionError('import forbidden')),\
             patch('run_host_rootfs_short.subprocess.run') as external,\
             patch('run_host_rootfs_short.subprocess.check_output',return_value='[{"dst":"10.0.0.0/8"}]') as routes:
            self.assertEqual(workflow(Path('/config'),Path('/prefix')),1)
            external.assert_not_called();self.assertEqual(routes.call_args.args[0],['ip','-j','route','show','table','all'])
            result=json.loads(next(Path(tmp).glob('.artifacts/host-rootfs-short/*/result.json')).read_text())
            self.assertEqual(result['stage'],'network-precheck');self.assertEqual(result['actual_actions'],0)

    def test_source_container_id_and_image_id_are_distinct(self):
        check_source(dict(Id=SOURCE_ID,Image=IMAGE))
        for value in (dict(Id=SOURCE_ID,Image='sha256:'+SOURCE_ID),dict(Id='other-container',Image=IMAGE)):
            with self.assertRaises(ValueError):check_source(value)

    def test_command_imports_only_dual_and_new_flag_reaches_both_argument_resets(self):
        cfg=configuration(ROOT/'configs/platform-prettier6604-host-file.json')
        command=short_command(cfg,Path('/metadata/cache'),Path('/metadata/prefix'))
        self.assertIn('--host-rootfs-short',command)
        self.assertEqual(command[command.index('--host-independent-phase')+1],'dual')
        self.assertEqual(command[command.index('--host-checkpoint-source')+1],'/metadata/prefix/prettier6604')
        self.assertNotIn('--light-monitor',command)
        code=(ROOT/'scripts/validate_isolated_agentenv.py').read_text()
        for var in ('check_args','task_args'):self.assertIn("if rootfs_short:"+var+"+=['--host-rootfs-short']",code)
        self.assertIn("if host_matched and host_sampling=='on' and not rootfs_short:",code)
        self.assertIn('if full_shared or rootfs_short:',code)

    def test_insufficient_disk_stops_before_source_start_preflight_or_preparation(self):
        cfg=dict(task_id='prettier__prettier-6604',rollout='single',host_file_monitor=True,checkpoint_after_step=None)
        with tempfile.TemporaryDirectory() as tmp,patch('run_host_rootfs_short.ROOT',Path(tmp)),\
             patch('run_host_rootfs_short.configuration',return_value=cfg),\
             patch('run_host_rootfs_short.measurement_group',return_value=(Path(tmp),'owned')),\
             patch('run_host_rootfs_short.available',return_value=37*2**30),\
             patch('run_host_rootfs_short.preflight',side_effect=AssertionError('preflight forbidden')),\
             patch('run_host_rootfs_short.resumed_capture',side_effect=AssertionError('import forbidden')),\
             patch('run_host_rootfs_short.subprocess.run') as external,\
             patch('run_host_rootfs_short.subprocess.check_output') as inspect:
            self.assertEqual(workflow(Path('/config'),Path('/prefix')),1)
            external.assert_not_called();inspect.assert_not_called()
            result=json.loads(next(Path(tmp).glob('.artifacts/host-rootfs-short/*/result.json')).read_text())
            self.assertEqual(result['stage'],'disk-budget');self.assertEqual(result['actual_actions'],0)

    def test_saved_result_keeps_task_outcome_and_incomplete_measurement_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage=Path(tmp);raw=storage/'prettier6604';raw.mkdir()
            def save(path,value):path.write_text(json.dumps(value))
            roots=dict(status='IDENTIFIED',start_monotonic_ns=21,end_monotonic_ns=22,cpu_ns=1)
            query=dict(start_monotonic_ns=23,end_monotonic_ns=30,rootfs_inventory=roots,
                objects=[dict(object_key=['regular',1,10],category='shared_rootfs_layer',users=['N2-a','N2-b'],
                    residency=dict(stable=True,resident_bytes=4096,start_monotonic_ns=24,end_monotonic_ns=29,cpu_ns=3))])
            phase=dict(sandboxes=2,guests=[dict(role=r) for r in ('N2-a','N2-b')],functional_checks='PASS',
                start_monotonic_ns=10,end_monotonic_ns=20,device_released_ns=40,shared_files=dict(status='OBSERVED',pss='NOT_REQUESTED',query=query))
            save(raw/'host-matched.json',dict(status='PASS',groups=[phase]))
            paths={r:dict(action_count=10,source_patch_check='PASS',independent_check='PASS',guest_action_sum_ns=5,
                official_task_status='FAIL',official_passed=0,official_failed=2) for r in ('N2-a','N2-b')}
            save(raw/'result.json',dict(status='PASS',actual_action_count=20,reused_action_count=11,paths=paths))
            save(raw/'runtime-monitor.json',dict(status='PASS',profile='light',host_vmm_requested=False,
                sessions=[dict(status='PASS',collector_reaped=True,receipt=dict(interval_seconds=.1))]*2))
            save(storage/'result.json',dict(status='PASS',cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed',
                'successful_fixture_removed','original_service_unchanged','original_source_firecracker_identities_unchanged')},
                host_shared_preparation_sync=dict(end_monotonic_ns=1),host_file_creation=dict(verified_before_guest_creation_ns=2)))
            value=inspect_result(storage);self.assertEqual(value['status'],'PASS');self.assertEqual(value['shared_rootfs_resident_bytes'],4096)
            self.assertEqual(value['official_results']['N2-a']['official_task_status'],'FAIL')
            roots['status']='INCOMPLETE';query['objects']=[]
            save(raw/'host-matched.json',dict(status='PASS',groups=[phase]))
            value=inspect_result(storage);self.assertEqual(value['status'],'INCOMPLETE_MEASUREMENT')
            self.assertEqual(value['task_validation'],'PASS');self.assertIsNone(value['shared_rootfs_resident_bytes'])


class RootfsShortSequenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_suffixes_query_before_checks_no_pss_or_broad_scan(self):
        await self.sequence(False)

    async def test_query_failure_still_checks_and_deletes_both_guests(self):
        await self.sequence(True)

    async def sequence(self,query_error):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);events=[];vm={}
            class Controller:
                def __init__(self):self.live={}
                async def start(self,role,**kw):
                    self.live[role]=role;vm[role]=dict(pid=len(vm)+1)
                    events.append(('start',role));(out/('restored-'+role+'.json')).write_text('{}');return role
                async def run_path(self,sid,role,path):events.append(('task',role))
            class Shared:
                def __init__(self,*a):self.restored={};self.templates={}
                def query(self,guests,end):
                    self_roles=set(guests)
                    if self_roles!={'N2-a','N2-b'}:raise AssertionError(self_roles)
                    events.append(('query',2))
                    if query_error:raise RuntimeError('test query failure')
                    return dict(status='OBSERVED')
            controller=Controller();binding=SimpleNamespace(resolve=lambda sid:vm[sid])
            runtime=SimpleNamespace(mark=lambda *a:None,stop=lambda sid,*a:events.append(('stop',sid)))
            def verify(role):events.append(('verify',role));return dict(action_count=10)
            async def final(role,sid):events.append(('check',role))
            def cli(label,action,sid,**kw):events.append(('delete',sid))
            args=SimpleNamespace(container='mock',host_independent_phase='dual',host_rootfs_short=True)
            with patch.dict('os.environ',{'ASB_HOST_FILE_SAMPLING':'on','ASB_HOST_MEMORY_DETAIL':'off'}),\
                 patch('host_file_monitor.measurement_group',return_value=(out,'owned')),\
                 patch('host_file_monitor.counters',return_value=dict(file=1)),\
                 patch('host_file_monitor.pids',return_value=[]),\
                 patch('host_sandbox_monitor.Binding',return_value=binding),\
                 patch('host_rollout_shared.SharedRollout',Shared),\
                 patch('host_file_matched_task.ram_backend',return_value=dict(rdev=0)),\
                 patch('host_file_monitor.pss_snapshot',side_effect=AssertionError('PSS forbidden')),\
                 patch('host_file_monitor.residency_check',side_effect=AssertionError('broad scan forbidden')),\
                 patch('host_file_matched_task.live_residency',side_effect=AssertionError('old full scan forbidden')),\
                 patch('host_file_matched_task.asyncio.sleep') as sleep:
                job=observe_matched(controller,'template','parent',runtime,args,out,cli,
                    {'N2-a':['suffix'],'N2-b':['suffix']},verify,final)
                if query_error:
                    with self.assertRaisesRegex(RuntimeError,'test query failure'):await job
                else:
                    result=await job;self.assertEqual(result['status'],'PASS');self.assertEqual(result['suffix_actions'],20)
                sleep.assert_not_called()
            self.assertFalse(controller.live)
            self.assertEqual([e for e in events if e[0]=='task'],[('task','N2-a'),('task','N2-b')])
            for role in ('N2-a','N2-b'):
                self.assertLess(events.index(('task',role)),events.index(('query',2)))
                self.assertLess(events.index(('query',2)),events.index(('check',role)))
                self.assertLess(events.index(('check',role)),events.index(('delete',role)))


if __name__=='__main__':unittest.main()
