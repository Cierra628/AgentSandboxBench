"""Saved configuration and mocked lifecycle only; no real services or devices."""
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
import platform_tasks as platform
import run_host_file_workflow as entry
from validate_isolated_agentenv import ordinary_current_only

PROFILES=('prettier6604','sphinx7590','xarray6992')
CLEANUP={k:True for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed',
    'original_firecracker_identities_unchanged','original_service_unchanged')}


class HostCurrentTaskTests(unittest.TestCase):
    def config(self,profile='prettier6604',**changes):
        cfg=json.loads((ROOT/f'configs/platform-{profile}-host-current.json').read_text())
        cfg.update(changes)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json';path.write_text(json.dumps(cfg))
            return platform.configuration(path)

    def test_three_configs_only_add_explicit_flag_and_leave_checks_unchanged(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                old=platform.configuration(ROOT/f'configs/platform-{profile}-host-file.json')
                new=self.config(profile)
                self.assertEqual(new,dict(old,host_current_only=True))
                old_check,old_live=platform.commands(old,Path('/unused-cache'))
                check,live=platform.commands(new,Path('/unused-cache'))
                self.assertEqual(check,old_check)
                self.assertEqual([x for x in live if x!='--host-current-only'],old_live)
                self.assertEqual(live.count('--host-current-only'),1)
                self.assertIn('--light-monitor',live)
                self.assertNotIn('--host-current-only',old_live)

    def test_new_flag_is_boolean_and_only_for_full_light_host_tasks(self):
        for change in ({'host_current_only':1},{'host_file_monitor':False},{'light_monitor':False},
                       {'monitor_mode':'off','light_monitor':True},{'checkpoint_after_step':24},
                       {'damon_observe':True},{'damon_observe':True,'damon_disable':True},
                       {'rollout':'grpo','frozen_export':'/unused'}):
            with self.subTest(change=change),self.assertRaises(ValueError):self.config(**change)
        old=platform.configuration(ROOT/'configs/platform-prettier6604-host-file.json')
        self.assertEqual(platform.commands(old,Path('/unused')),
            platform.commands(self.config(host_current_only=False),Path('/unused')))

    def test_outer_entry_retains_fresh_cgroup_and_rejects_other_modes_before_docker(self):
        cfg=ROOT/'configs/platform-prettier6604-host-current.json'
        with patch.object(sys,'argv',['entry',str(cfg)]),patch.object(entry.subprocess,'check_output',return_value='systemd'),\
                patch('platform_tasks.preflight'),patch.object(entry.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as call:
            self.assertEqual(entry.main(),0)
        command=call.call_args_list[0].args[0]
        self.assertIn('--property=MemoryAccounting=yes',command)
        self.assertIn('--property=IOAccounting=yes',command)
        self.assertIn('--setenv=ASB_HOST_FILE_SAMPLING=on',command)
        self.assertEqual(command[-4:],['bash',str(ROOT/'scripts/use-platform.sh'),'run',str(cfg)])
        for mode in ('--restore-pair','--short-pair','--matched-task'):
            with self.subTest(mode=mode),patch.object(sys,'argv',['entry',str(cfg),mode]),\
                    patch.object(entry.subprocess,'check_output') as docker,patch('platform_tasks.preflight') as preflight:
                with self.assertRaises(SystemExit):entry.main()
                docker.assert_not_called();preflight.assert_not_called()

    def native_args(self,profile):
        shared=dict(immutable_cache_seed=Path('/unused'),light_monitor=True,host_file_monitor=True,host_current_only=True)
        if profile=='prettier6604':return SimpleNamespace(**shared,prettier6604_task=Path('/input'),prettier6604_readonly_replay=True)
        return SimpleNamespace(**shared,python_task_profile=profile,python_backbone_inputs=Path('/input'),
            python_readonly_replay=True,python_persistent_sampling=True)

    def test_native_accepts_three_ordinary_tasks_but_not_analysis_or_off(self):
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        node=next(n for n in main.body if isinstance(n,ast.If) and ast.unparse(n.test).startswith('host_current_only and'))
        code=compile(ast.Module(body=[node],type_ignores=[]),'<actual current-only guard>','exec')
        for profile in PROFILES:
            args=self.native_args(profile)
            self.assertTrue(ordinary_current_only(args))
            ns=dict(args=args,host_current_only=True,python_tool_capture=False,tool_source=None,exp1_light=False,
                    light_monitor=True,ordinary_current_only=ordinary_current_only,current_monitor_off=False)
            ns.update(external_off=False,external_config=None)
            exec(code,ns)
            for field,value in [('host_file_monitor',False),('immutable_cache_seed',None),('damon_observe',True),
                                ('diagnostic_guest_kernel','/kernel'),('python_checkpoint',True),
                                ('prettier6604_export','/export'),('python_tool_capture_demo',True),
                                ('prettier6604_monitor_mode' if profile=='prettier6604' else 'python_monitor_mode','off')]:
                bad=SimpleNamespace(**dict(vars(args),**{field:value}))
                with self.subTest(profile=profile,field=field),self.assertRaises(ValueError):exec(code,dict(ns,args=bad))
            with self.assertRaises(ValueError):exec(code,dict(ns,light_monitor=False))

    def test_native_skips_only_final_inventory_and_retains_storage_audit(self):
        text=(ROOT/'scripts/validate_isolated_agentenv.py').read_text();tree=ast.parse(text)
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and
            ast.unparse(n.test).startswith("host_group and mounted and (host_sampling == 'on')"))
        code=compile(ast.Module(body=[node],type_ignores=[]),'<actual final inventory branch>','exec')
        for sampling in ('on','off'):
            receipt={}
            exec(code,dict(host_group=True,mounted=True,host_sampling=sampling,private_control=False,
                full_rootfs_mode=None,host_current_only=True,receipt=receipt))
            self.assertEqual(receipt['host_file_residency']['status'],'NOT_REQUESTED')
        # These actions remain outside the skipped inventory branch, before success.
        audit=text.index("receipt['checks']['actual_writable_files_inside_volume']='PASS'")
        self.assertLess(audit,text.index("receipt['checks']['fixed_backing_allocation']='PASS'"))
        self.assertLess(audit,text.index("elif host_current_only or current_pair or os.environ.get('ASB_HOST_EXPANDED_FILES')=='on':"))
        self.assertIn("call('readonly-root-check'",text)
        self.assertIn("call('unmount-own-volume'",text)

    def test_platform_report_uses_creation_not_residency_in_new_mode_and_keeps_old_mode(self):
        for current_only in (False,True):
            with self.subTest(current_only=current_only),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);workflow=root/'.artifacts/workflow';child=root/'.artifacts/storage';raw=child/'prettier6604'
                workflow.mkdir(parents=True);raw.mkdir(parents=True)
                cfg=dict(backend='agentenv',task_id='prettier__prettier-6604',rollout='single',host_file_monitor=True,
                         host_current_only=current_only)
                (workflow/'effective-config.json').write_text(json.dumps(cfg))
                (workflow/'result.json').write_text(json.dumps(dict(status='PASS',storage_result_dir=str(child))))
                (child/'result.json').write_text(json.dumps(dict(status='PASS',cleanup=CLEANUP)))
                (raw/'result.json').write_text(json.dumps(dict(status='PASS',official_task_status='FAIL')))
                (raw/'runtime-monitor.json').write_text(json.dumps(dict(profile='light')))
                host=dict(file_peak_bytes=5,scope='owned',primary_metric='memory.current',current_peak_bytes=8,
                    current=dict(before_guest_bytes=1,after_service_ready_peak_increase_bytes=7))
                output=root/'.artifacts/report'
                with patch.object(platform,'ROOT',root),\
                        patch('report_task_overview.write_report',return_value=dict(task_id=cfg['task_id'])),\
                        patch('report_host_file.report',return_value=host) as report:
                    platform.report(workflow,output)
                if current_only:
                    report.assert_called_once_with(workflow/'host-file',None,output/'host-file',creation_path=child/'result.json')
                else:report.assert_called_once_with(workflow/'host-file',child/'host-file-residency.json',output/'host-file')
                saved=json.loads((output/'summary.json').read_text())['host_file']
                self.assertEqual(saved['current_before_guest_bytes'],1);self.assertEqual(saved['current_peak_increase_bytes'],7)
                self.assertEqual(json.loads((output/'result.json').read_text())['official_task_status'],'FAIL')

    def test_worker_orders_collection_before_prepare_preserves_cost_and_handles_failure(self):
        for current_only,failure,off in ((True,False,False),(False,False,False),(True,True,False),(True,False,True),(True,True,True)):
            with self.subTest(current_only=current_only,failure=failure,off=off),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'.artifacts').mkdir();order=[]
                cfg=self.config(host_current_only=current_only,**({'monitor_mode':'off'} if off else {}));cfg['output_root']=str(root/'.artifacts/workflows')
                storage=root/'.artifacts/storage-isolated-service/owned';storage.mkdir(parents=True)
                def command(argv,**kwargs):
                    argv=list(map(str,argv))
                    if any('prepare_prettier6604_cache.py' in x for x in argv):
                        cache=Path(argv[3]);cache.mkdir(parents=True,exist_ok=True)
                        release='--release' in argv;order.append('release' if release else 'prepare')
                        (cache/'resources.json').write_text(json.dumps(dict(released=release)))
                    else:
                        order.append('task')
                        self.assertEqual('--host-current-only' in argv,current_only)
                        self.assertEqual('--light-monitor' in argv,not off)
                        self.assertEqual('--prettier6604-monitor-mode' in argv,off)
                        if failure:raise RuntimeError('mock native task failed')
                        kwargs['stdout'].write('status=PASS result_dir='+str(storage)+'\n')
                def create_monitor(*args,**kwargs):
                    self.assertFalse(off,'OFF must not construct a host sampler')
                    order.append('monitor');self.assertEqual(kwargs['include_pss'],not current_only)
                    return SimpleNamespace(stop=lambda:(order.append('stop') or dict(status='PASS',thread_cpu_ns=17,counter_read_total_ns=9)))
                def group():
                    order.append('group');return root/'group','owned'
                def copy(source,target):
                    target=Path(target);target=target/Path(source).name if target.is_dir() else target
                    target.write_text('mock executed source')
                def report(workflow,output):
                    order.append('report');output.mkdir()
                    (output/'result.json').write_text(json.dumps(dict(status='PASS',cleanup=CLEANUP,official_task_status='FAIL')))
                    return output
                with patch.object(platform,'ROOT',root),patch.object(platform,'preflight',return_value={}),\
                        patch('host_file_monitor.measurement_group',side_effect=group),\
                        patch('host_file_monitor.HostFileMonitor',side_effect=create_monitor),\
                        patch.object(platform.shutil,'copy2',side_effect=copy),\
                        patch.object(platform.hashlib,'sha256',return_value=SimpleNamespace(hexdigest=lambda:'fixture')),\
                        patch.object(platform.subprocess,'check_output',return_value='fixture-commit'),\
                        patch.object(platform.subprocess,'run',side_effect=command),patch.object(platform,'report',side_effect=report):
                    self.assertEqual(platform.run(cfg),1 if failure else 0)
                result_path=next(p for p in (root/'.artifacts/workflows').glob('*/result.json')
                                 if (p.parent/'effective-config.json').is_file())
                result=json.loads(result_path.read_text())
                self.assertEqual(order,['group']+([] if off else ['monitor'])+['prepare','task']+([] if off else ['stop'])+['release']+([] if failure else ['report']))
                if off:
                    self.assertEqual(result['host_file_monitor'],dict(status='NOT_REQUESTED',host_sampling='off'))
                    self.assertFalse((result_path.parent/'host-file').exists())
                else:self.assertEqual(result['host_file_monitor']['thread_cpu_ns'],17)
                self.assertTrue(result['cache_released'])
                self.assertEqual(result['status'],'FAIL' if failure else 'PASS')


if __name__=='__main__':unittest.main()
