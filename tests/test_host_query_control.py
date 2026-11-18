"""Tiny files, mocked mincore/services and saved metadata; no real devices or VMs."""
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import host_full_rootfs_control as control
import host_private_files as files
import run_host_full_rootfs as run
import run_host_file_workflow as workflow
import run_host_query_control as pair
import test_host_full_rootfs_control as fixtures


class HostQueryControlTests(unittest.TestCase):
    def observation(self,mount,residency='off',failure=None):
        helper=fixtures.FullRootfsTests()
        observer,guests,roots=helper.observation_fixture(mount,'shared',residency=residency)
        if failure=='missing':roots['objects'].pop()
        if failure=='identity':roots['guests']['role-0']['lowers'][0]['inode']+=1
        if failure=='file':Path(observer.objects[0]['path']).unlink()
        if failure=='receipt':
            p=observer.control/'receipts/guest-100.json';v=files.read(p);v['slot']=1;files.write(p,v)
        with patch.object(control,'rootfs_inventory',return_value=roots),\
             patch('host_prefix_compare.drives_for_vm',return_value=[]),\
             patch('host_rollout_shared.opened_regular_files',return_value={}),\
             patch.object(control,'validate_rootfs_inventory',side_effect=ValueError('changed') if failure=='postcheck' else None),\
             patch.object(control.os,'readlink',side_effect=lambda p:'/runtime/guest-'+p.split('/')[2]):
            return observer.after(guests,control.time.monotonic_ns())

    def test_only_mincore_is_switched_both_endpoints_and_identity_checks_remain(self):
        for residency in ('on','off'):
            with self.subTest(residency=residency),tempfile.TemporaryDirectory() as tmp,\
                 patch.object(control,'manifest_layers',side_effect=fixtures.FullRootfsTests().layers),\
                 patch.object(files,'counters',return_value={'file':100}) as counters,\
                 patch.object(files,'mincore_file',return_value=dict(stable=True,resident_bytes=4)) as query:
                value=self.observation(Path(tmp),residency)
                control.validate_observation(value,residency)
                self.assertEqual(query.call_count,38 if residency=='on' else 0);self.assertEqual(counters.call_count,4)
                for stage in ('P0','P1'):
                    rows=value[stage]['objects']
                    self.assertEqual(len(rows),19);self.assertEqual(len({(r['identity']['device'],r['identity']['inode']) for r in rows}),19)
                    self.assertTrue(all(len(r['users'])==7 for r in rows))
                    self.assertGreater(value[stage]['elapsed_ns'],0)
                    self.assertEqual(value[stage]['resident_bytes'],76 if residency=='on' else None)
                    if residency=='off':self.assertTrue(all(r['residency']==dict(status='NOT_MEASURED',resident_bytes=None) for r in rows))
                self.assertEqual(value['pss'],'NOT_REQUESTED')

    def test_off_still_rejects_inventory_identity_receipt_missing_file_and_postcheck(self):
        for failure in ('missing','identity','receipt','file','postcheck'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as tmp,\
                 patch.object(control,'manifest_layers',side_effect=fixtures.FullRootfsTests().layers),\
                 patch.object(files,'counters',return_value={}),patch.object(files,'mincore_file') as query:
                with self.assertRaises((ValueError,FileNotFoundError)):self.observation(Path(tmp),failure=failure)
                query.assert_not_called()
                self.assertEqual(files.read(Path(tmp)/'controller/prettier6604/host-full-rootfs.json')['status'],'FAIL')

    def test_off_state_is_accepted_only_explicitly_after_complete_identity_checks(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(control,'manifest_layers',side_effect=fixtures.FullRootfsTests().layers),\
             patch.object(files,'counters',return_value={}),patch.object(files,'mincore_file'):
            value=self.observation(Path(tmp))
        for failure in ('implicit','failed','checks','postcheck','fake-zero'):
            with self.subTest(failure=failure):
                bad=copy.deepcopy(value)
                if failure=='failed':bad['status']='FAIL'
                if failure=='checks':bad['identity_checks']='FAIL'
                if failure=='postcheck':bad.pop('P1_postcheck')
                if failure=='fake-zero':bad['P1']['resident_bytes']=0
                with self.assertRaises(AssertionError):control.validate_observation(bad,'on' if failure=='implicit' else 'off')

    def test_mode_validation_preserves_defaults_and_restricts_new_off(self):
        self.assertIsNone(control.validate_mode(SimpleNamespace()))
        for mode,schedule in [('shared','grpo'),('private','grpo'),('shared','bpo'),('private','bpo')]:
            self.assertEqual(control.validate_mode(SimpleNamespace(host_full_rootfs_mode=mode,schedule=schedule)),mode)
        for mode,schedule in [(None,'grpo'),('private','grpo'),('shared','bpo')]:
            with self.assertRaises(ValueError):control.validate_mode(SimpleNamespace(host_full_rootfs_mode=mode,schedule=schedule,host_full_rootfs_residency='off'))

    def test_argument_chain_and_unchanged_host_periodic_switch(self):
        with patch.object(run,'commands',return_value=([],['bash','entry','--host-file-monitor','--prettier6604-readonly-replay'])):
            old=run.member_command({},Path('/seed'),'shared',Path('/daemon'))
            new=run.member_command({},Path('/seed'),'shared',Path('/daemon'),residency='off')
            self.assertEqual(new,old+['--host-full-rootfs-residency','off'])
        argv=['entry','/unused','--full-rootfs-group','shared','--copy-daemon','/unused','--rootfs-residency','off']
        with patch.object(sys,'argv',argv),patch('platform_tasks.configuration',return_value=dict(host_file_monitor=True)),\
             patch('platform_tasks.preflight'),patch.object(workflow.subprocess,'check_output',return_value='systemd'),\
             patch.object(workflow.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as execute:
            self.assertEqual(workflow.main(),0)
        command=execute.call_args_list[0].args[0]
        self.assertIn('--setenv=ASB_HOST_FILE_SAMPLING=on',command)
        self.assertEqual(command[-2:],['--rootfs-residency','off'])
        code=(ROOT/'scripts/validate_isolated_agentenv.py').read_text()
        for variable in ('check_args','task_args'):
            begin=code.index(variable+"=['--frozen-export'")
            end=code.index('subprocess.run(' if variable=='check_args' else 'call(stage,[sys.executable',begin)
            self.assertIn("if full_rootfs_residency=='off':"+variable+"+=['--host-full-rootfs-residency','off']",code[begin:end])

    def test_bad_cli_combinations_fail_before_configuration_or_services(self):
        for extra in ([],['--full-rootfs-group','private','--copy-daemon','/unused'],['--full-rootfs-group','shared','--copy-daemon','/unused','--copy-schedule','bpo']):
            p=subprocess.run([sys.executable,'-B',ROOT/'scripts/run_host_file_workflow.py','/unused','--rootfs-residency','off',*extra],capture_output=True,text=True,timeout=10)
            self.assertEqual(p.returncode,2);self.assertIn('residency off requires explicit shared complete GRPO',p.stderr)

    def test_off_report_keeps_full_task_audit_and_null_residency(self):
        from report_task_overview import write_report
        with tempfile.TemporaryDirectory() as tmp,patch.object(control,'manifest_layers',side_effect=fixtures.FullRootfsTests().layers),\
             patch.object(files,'counters',return_value={}),patch.object(files,'mincore_file'):
            root=Path(tmp);value=self.observation(root)
            raw=root/'raw';raw.mkdir();out=root/'report';out.mkdir()
            result=dict(status='PASS',roles=['backbone'],paths=dict(backbone=dict(official_task_status='FAIL')),
                rollout_start_monotonic_ns=1,first_tool_start_monotonic_ns=2,rollout_end_monotonic_ns=20,
                official_task_directory='/unused',host_full_rootfs_control=value,host_full_rootfs_residency='off')
            files.write(raw/'result.json',result)
            sample=dict(timestamp_ns=10,MemTotal_kib=100,MemAvailable_kib=50,Cached_kib=20,Shmem_kib=0)
            group=dict(role='backbone',chunks=[[sample]],sessions=[])
            audit=dict(status='PASS',schedule='grpo',roles=[],actions=189,segments=[],logical_action_count=189,checkpoint_count=0,
                checkpoint_restore={},cleanup={},timing=dict(checkpoint_call_ns=0,restore_request_per_role_ns={},stages=[]))
            with patch('report_task_overview.samples',return_value=({'sid':group},'fixture')),\
                 patch('report_task_overview.tool_rows',return_value=([dict(tool_index=1,duration_ns=1)],1)),\
                 patch('report_prettier6604_grpo.audit',return_value=(audit,None,None)) as full:
                report=write_report(raw,out)
            full.assert_called_once();self.assertEqual(report['schedule_audit']['actions'],189)
            self.assertIsNone(report['host_full_rootfs_control']['P0_resident_bytes'])
            self.assertIsNone(report['host_full_rootfs_control']['P1_resident_bytes'])
            self.assertEqual(report['host_full_rootfs_control']['residency_status'],'NOT_MEASURED')

    def test_pair_summary_keeps_signed_timings_and_excludes_private_patches(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(pair,'ROOT',Path(tmp)):
            root=Path(tmp);runs=[]
            for mode,ns in [('off',100),('on',99)]:
                runs.append(dict(residency=mode,paths={'r':['s']},patches={'r':'private patch'},official={'r':{'failed':2}},targets=[['layer',4]],
                    sessions=[dict(samples=3)],timings_ns=dict(controller=ns),**{k:str(root/k) for k in ('group','storage','raw','report')}))
            files.write(root/'runs.json',runs);value=pair.summarize(root)
            self.assertEqual(value['guest_samples'],6);self.assertAlmostEqual(value['comparison']['controller']['change_percent'],-1)
            self.assertNotIn('patches',value['runs'][0]);self.assertEqual(value['runs'][0]['raw'],'raw')
            runs[1]['patches']['r']='changed';files.write(root/'runs.json',runs)
            with self.assertRaises(AssertionError):pair.summarize(root)


if __name__=='__main__':unittest.main()
