"""Temporary tiny copies and mocked observations only; no services or devices."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import host_full_rootfs_control as control
import host_private_files as files
import run_host_full_rootfs as run
from isolated_storage import GIB
from validate_isolated_agentenv import prettier_capacity


class FullRootfsTests(unittest.TestCase):
    def layers(self):
        return [dict(relative=f'layer-{i}/base',source_digest=f'sha256:{i}',size=4) for i in range(19)]

    def prepare(self,mount,mode):
        for row in self.layers():
            path=mount/'env/image-cache/commits'/row['relative'];path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'test')
        def call(label,command,timeout):
            self.assertIn('--reflink=never',command);shutil.copy2(command[-2],command[-1])
        return control.prepare(mount,mode,call)

    def test_real_manifest_and_old_mode_are_unchanged(self):
        rows=control.manifest_layers()
        self.assertEqual(len(rows),19);self.assertEqual(sum(r['size'] for r in rows),5565854720)
        self.assertIsNone(control.validate_mode(SimpleNamespace()))
        self.assertEqual(control.validate_mode(SimpleNamespace(host_full_rootfs_mode='private',schedule='grpo')),'private')
        self.assertEqual(control.validate_mode(SimpleNamespace(host_full_rootfs_mode='private',schedule='bpo')),'private')
        for extra in (dict(schedule='tvcache'),dict(host_shared_files=True),dict(role='early_1'),dict(host_scaling_mode='cold')):
            with self.assertRaises(ValueError):control.validate_mode(SimpleNamespace(host_full_rootfs_mode='shared',**extra))

    def test_complete_comparison_scope_and_values_do_not_use_short_pair_label(self):
        shared=dict(P0=dict(resident_bytes=10),P1=dict(resident_bytes=12))
        private=dict(P0=dict(resident_bytes=60),P1=dict(resident_bytes=62))
        value=run.full_comparison(shared,private)
        self.assertEqual(value['endpoint_difference_bytes'],50)
        self.assertIn('seven guests per group',value['scope'])
        self.assertIn('two-service/two-guest',files.compare(shared,private)['scope'])
        with self.assertRaises(ValueError):run.full_comparison(dict(shared,schedule='grpo'),dict(private,schedule='bpo'))

    def test_copy_preparation_reuses_slot_zero_and_makes_only_six_extra_sets(self):
        for mode,count,sets in (('shared',19,1),('private',133,7)):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp,patch.object(control,'manifest_layers',side_effect=self.layers):
                mount=Path(tmp);value=self.prepare(mount,mode);rows=control.prepared_objects(value)
                self.assertEqual(len(rows),count);self.assertEqual(len(value['copies']),sets)
                self.assertEqual(len({(r['identity']['device'],r['identity']['inode']) for r in rows}),count)
                self.assertTrue(all(len(r['users'])==(7 if mode=='shared' else 1) for r in rows))
                self.assertTrue(all('image-cache/commits' in r['path'] for r in value['copies'][0]))
                self.assertEqual(len(list((mount/'env/rootfs-copy-control').glob('copies/*/layer-*/base'))),count-19)
                manifest=files.read(mount/'env/rootfs-copy-control/manifest.json')
                self.assertEqual(len(manifest['copies']),sets)
                self.assertTrue(all(r['file'].startswith('/workspace/env/') for s in manifest['copies'] for r in s))
                self.assertGreaterEqual(value['end_monotonic_ns'],value['start_monotonic_ns'])
                self.assertGreaterEqual(value['cpu_ns'],0)

    def observation_fixture(self,mount,mode):
        self.prepare(mount,mode)
        raw=mount/'controller/prettier6604';raw.mkdir(parents=True)
        guests={f'role-{i}':f'sid-{i}' for i in range(7)}
        binding=SimpleNamespace(cgroup=Path('/mock/cgroup'),resolve=lambda sid:dict(pid=100+int(sid.split('-')[1])))
        observer=control.FullRootfsControl(mode,raw,Path('/mock/group'),binding)
        observer.before()
        objects=[];roots_guests={};receipts=mount/'env/rootfs-copy-control/receipts';receipts.mkdir()
        for slot,rows in enumerate(observer.preparation['copies']):
            for row in rows:
                objects.append(dict(**row['identity'],users=list(guests) if mode=='shared' else [f'role-{slot}'],status='NOT_REQUESTED_SINGLE_USER'))
        for index,role in enumerate(guests):
            slot=0 if mode=='shared' else index
            rows=observer.preparation['copies'][slot]
            roots_guests[role]=dict(lowers=[dict(**r['identity'],reference=dict(digest=r['digest'])) for r in rows])
            files.write(receipts/f'guest-{100+index}.json',dict(mode=mode,slot=slot,lowers=[dict(r['identity']) for r in rows]))
        return observer,guests,dict(status='IDENTIFIED',objects=objects,guests=roots_guests)

    def test_both_endpoints_query_all_live_copies_and_record_costs(self):
        for mode,count in (('shared',19),('private',133)):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp,patch.object(control,'manifest_layers',side_effect=self.layers),\
                 patch.object(files,'counters',return_value={'file':100}),\
                 patch.object(files,'mincore_file',return_value=dict(stable=True,resident_bytes=4)) as query:
                observer,guests,roots=self.observation_fixture(Path(tmp),mode)
                paths_end=control.time.monotonic_ns()
                with patch.object(control,'rootfs_inventory',return_value=roots),\
                     patch('host_prefix_compare.drives_for_vm',return_value=[]),\
                     patch('host_rollout_shared.opened_regular_files',return_value={}),\
                     patch.object(control,'validate_rootfs_inventory') as post,\
                     patch.object(control.os,'readlink',side_effect=lambda p:'/runtime/guest-'+p.split('/')[2]):
                    result=observer.after(guests,paths_end)
                self.assertEqual(result['status'],'OBSERVED');self.assertEqual(query.call_count,2*count);post.assert_called_once_with(roots)
                self.assertEqual(result['pss'],'NOT_REQUESTED');self.assertEqual(result['P1']['resident_bytes'],count*4)
                self.assertLessEqual(result['P0']['end_monotonic_ns'],paths_end)
                self.assertLessEqual(paths_end,result['P1']['start_monotonic_ns'])
                for stage in ('P0','P1'):
                    self.assertEqual(len(result[stage]['objects']),count)
                    self.assertEqual(result[stage]['elapsed_ns'],result[stage]['end_monotonic_ns']-result[stage]['start_monotonic_ns'])
                    self.assertTrue(all('query_cpu_ns' in r for r in result[stage]['objects']))
                self.assertTrue(all(r['status']=='SELECTED' for r in roots['objects']))
                self.assertTrue(all(not r['users'][0].startswith('planned-') for r in result['P1']['objects']))
                for stage in ('P0_metadata','P1_metadata','P1_postcheck'):
                    self.assertGreaterEqual(result[stage]['end_monotonic_ns'],result[stage]['start_monotonic_ns'])
                    self.assertGreaterEqual(result[stage]['cpu_ns'],0)
                self.assertEqual(files.read(observer.out/'host-full-rootfs.json')['status'],'OBSERVED')

    def test_changed_prepared_file_is_rejected_without_cache_query(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(control,'manifest_layers',side_effect=self.layers):
            value=self.prepare(Path(tmp),'shared');value['copies'][0][0]['identity']['inode']+=1
            with self.assertRaisesRegex(ValueError,'prepared file changed'):control.prepared_objects(value)

    def test_bad_live_inventory_receipt_or_postcheck_remains_a_measurement_failure(self):
        for failure in ('missing','order','receipt','slots','postcheck'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as tmp,patch.object(control,'manifest_layers',side_effect=self.layers),\
                 patch.object(files,'counters',return_value={}),patch.object(files,'mincore_file',return_value=dict(stable=True,resident_bytes=4)):
                observer,guests,roots=self.observation_fixture(Path(tmp),'private')
                if failure=='missing':roots['objects'].pop()
                if failure=='order':roots['guests']['role-0']['lowers'].reverse()
                if failure in ('receipt','slots'):
                    path=observer.control/'receipts/guest-100.json';value=files.read(path)
                    if failure=='receipt':value['lowers'][0]['inode']+=1
                    else:value['slot']=1
                    files.write(path,value)
                with patch.object(control,'rootfs_inventory',return_value=roots),\
                     patch('host_prefix_compare.drives_for_vm',return_value=[]),\
                     patch('host_rollout_shared.opened_regular_files',return_value={}),\
                     patch.object(control,'validate_rootfs_inventory',side_effect=ValueError('changed') if failure=='postcheck' else None),\
                     patch.object(control.os,'readlink',side_effect=lambda p:'/runtime/guest-'+p.split('/')[2]):
                    with self.assertRaises(ValueError):observer.after(guests,control.time.monotonic_ns())
                self.assertEqual(files.read(observer.out/'host-full-rootfs.json')['status'],'FAIL')

    def test_complete_grpo_budget_and_original_capacity_contract(self):
        self.assertTrue(run.require_budget(74*GIB+1)['ready'])
        with self.assertRaises(RuntimeError):run.require_budget(74*GIB)
        args=SimpleNamespace(host_full_rootfs_mode='shared')
        self.assertEqual(prettier_capacity(args,True,None,'grpo',False,True),48*GIB)
        self.assertEqual(prettier_capacity(args,True,None,'bpo',False,True),64*GIB)
        for export,role,schedule,checkpoint,seed in ((False,None,'grpo',False,True),(True,None,'tvcache',False,True),(True,None,'grpo',False,False)):
            with self.assertRaises(ValueError):prettier_capacity(args,export,role,schedule,checkpoint,seed)
        self.assertEqual(prettier_capacity(SimpleNamespace(prettier6604_grpo_capacity_gib=16),True,None,'grpo',False,True),16*GIB)
        self.assertEqual(prettier_capacity(SimpleNamespace(),True,None,'bpo',False,True),32*GIB)

    def test_budget_rejection_precedes_all_services(self):
        for action in (lambda:run.pair(Path('/unused'),Path('/unused')),lambda:run.group(Path('/unused'),'shared',Path('/unused'))):
            with patch.object(run,'available',return_value=30*GIB),patch.object(run.subprocess,'run') as start,patch.object(run.subprocess,'check_output') as inspect:
                with self.assertRaises(RuntimeError):action()
                start.assert_not_called();inspect.assert_not_called()

    def test_new_command_keeps_full_grpo_and_requires_explicit_daemon(self):
        cfg={}
        with patch.object(run,'commands',return_value=([],['bash','entry','--prettier6604-readonly-replay','--light-monitor','--host-file-monitor'])):
            command=run.member_command(cfg,Path('/seed'),'private',Path('/experiment/daemon'))
        self.assertNotIn('--prettier6604-readonly-replay',command);self.assertNotIn('--host-prefix-count',command)
        self.assertIn('--host-file-monitor',command);self.assertEqual(command.count('--light-monitor'),1)
        self.assertEqual(command[command.index('--prettier6604-schedule')+1],'grpo')
        self.assertEqual(command[-4:],['--host-full-rootfs-mode','private','--host-full-rootfs-daemon','/experiment/daemon'])
        checked=subprocess.run([sys.executable,'-B',ROOT/'scripts/run_host_file_workflow.py','/unused','--full-rootfs-group','shared'],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(checked.returncode,0);self.assertIn('requires copy-daemon',checked.stderr)

    def test_pair_uses_two_separate_groups_and_stops_after_first_failure(self):
        from run_light_monitor_controls import SOURCE_ID
        for schedule,fail in (('grpo',False),('grpo',True),('bpo',False)):
            with self.subTest(schedule=schedule,fail=fail),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);executed=[]
                def execute(command,**kwargs):
                    if command[0]=='bash':
                        self.assertEqual('--copy-schedule' in command,schedule=='bpo')
                        mode=command[command.index('--full-rootfs-group')+1];executed.append(mode)
                        out=root/'.artifacts/host-full-rootfs'/mode;out.mkdir(parents=True)
                        value=10 if mode=='shared' else 60
                        files.write(out/'result.json',dict(status='FAIL' if fail else 'PASS',mode=mode,observation=dict(P0=dict(resident_bytes=value),P1=dict(resident_bytes=value+1))))
                        kwargs['stdout'].write('status='+('FAIL' if fail else 'PASS')+' result_dir='+str(out)+'\n')
                        return SimpleNamespace(returncode=int(fail))
                    return SimpleNamespace(returncode=0)
                opener=MagicMock();opener.open.return_value.__enter__.return_value.status=200
                with patch.object(run,'ROOT',root),patch.object(run,'available',return_value=100*GIB),\
                     patch.object(run,'daemon_file',return_value=Path('/experiment/daemon')),\
                     patch.object(run.subprocess,'run',side_effect=execute),\
                     patch.object(run.subprocess,'check_output',side_effect=[json.dumps([dict(Id=SOURCE_ID,State=dict(Running=False))]),json.dumps(dict(Running=False))]),\
                     patch.object(run.urllib.request,'build_opener',return_value=opener):
                    code=run.pair(Path('/unused'),'unused',schedule)
                self.assertEqual(code,int(fail));self.assertEqual(executed,['shared'] if fail else ['shared','private'])
                state=files.read(next((root/'.artifacts/host-full-rootfs-pairs').glob('*/result.json')))
                self.assertTrue(state['source_returned_to_stopped'])
                if not fail:
                    self.assertEqual(state['actual_actions'],378 if schedule=='grpo' else 168)
                    self.assertEqual(state['comparison']['endpoint_difference_bytes'],50)
                    self.assertEqual([v['mode'] for v in state['groups']],['shared','private'])

    def test_new_report_keeps_original_full_schedule_audit(self):
        from report_task_overview import write_report
        sample=dict(timestamp_ns=10,MemTotal_kib=100,MemAvailable_kib=50,Cached_kib=20,Shmem_kib=0)
        group=dict(role='backbone',chunks=[[sample]],sessions=[])
        for name,actions in [('grpo',189),('bpo',84)]:
            with self.subTest(schedule=name),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw=root/'raw';raw.mkdir();out=root/'report';out.mkdir()
                value=dict(status='OBSERVED',schedule=name,profile=control.PROFILE,mode='private',P0=dict(resident_bytes=10),P1=dict(resident_bytes=12))
                if name=='bpo':value['generated_layers']=dict(P1=dict(resident_bytes=8))
                files.write(raw/'result.json',dict(status='PASS',roles=['backbone'],paths=dict(backbone=dict(official_task_status='FAIL')),
                    rollout_start_monotonic_ns=1,first_tool_start_monotonic_ns=2,rollout_end_monotonic_ns=20,
                    official_task_directory='/unused',host_full_rootfs_control=value))
                schedule=dict(status='PASS',schedule=name,roles=[],actions=actions,segments=[],logical_action_count=189,checkpoint_count=0 if name=='grpo' else 2,checkpoint_restore={},cleanup={},timing=dict(checkpoint_call_ns=0,restore_request_per_role_ns={},stages=[]))
                with patch('report_task_overview.samples',return_value=({'sid':group},'fixture')),\
                     patch('report_task_overview.tool_rows',return_value=([dict(tool_index=1,duration_ns=1)],1)),\
                     patch('report_prettier6604_grpo.audit',return_value=(schedule,None,None)) as full,\
                     patch('host_prefix_compare.audit_prefix') as prefix:
                    result=write_report(raw,out)
                full.assert_called_once();prefix.assert_not_called()
                self.assertEqual(result['host_full_rootfs_control']['P1_resident_bytes'],12)
                self.assertEqual(result['schedule_audit']['actions'],actions)
                self.assertEqual(result['physical_page_cache'],'NOT_MEASURED')
                if name=='bpo':self.assertEqual(result['host_full_rootfs_control']['generated_layers']['resident_bytes'],8)


if __name__=='__main__':unittest.main()
