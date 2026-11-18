"""Mocked full-rollout integration; no services, payloads or real proc/device queries."""
import ast
import asyncio
import copy
import csv
import json
from pathlib import Path
import stat
import re
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from host_rollout_shared import SharedRollout,select_objects,memory_layers,report
from platform_tasks import configuration,commands

ROLES=['backbone',*[f'early_{n}' for n in range(1,4)],*[f'late_{n}' for n in range(1,4)]]
SOURCE=ast.parse((ROOT/'scripts/run_prettier6604_grpo.py').read_text())


def metadata():
    def block(n,readonly=True):
        return dict(path='/metadata/'+str(n),device=6,inode=100+n,rdev=n,block_device=True,is_read_only=readonly,drive_id='drive'+str(n))
    inventory={r:[block(1),block(10+i,False)] for i,r in enumerate(ROLES)}
    # A second device-node alias for the same readonly cache is not a second object.
    inventory['early_1'].append(dict(block(1),inode=999,drive_id='alias'))
    restored={r:'early' if r.startswith('early') else 'late' for r in ROLES[1:]}
    backends={r:dict(block(2 if t=='early' else 3),regions=[dict(offset=0,start=0,end=4096)]) for r,t in restored.items()}
    layer=dict(path='/metadata/shared.raw',device=7,inode=40,rdev=0,block_device=False,size=4096,origin='CREATED_THIS_ROLLOUT')
    layers=[dict(layer,template=t) for t in ('early','early','late')]
    layers.append(dict(layer,path='/metadata/late.raw',inode=41,template='late'))
    return inventory,backends,layers,restored


class SharedRolloutTests(unittest.TestCase):
    def test_explicit_configs_flags_and_legacy_defaults(self):
        for schedule in ('grpo','bpo'):
            cfg=configuration(ROOT/f'configs/platform-prettier6604-{schedule}-shared-files.json')
            self.assertTrue(cfg['host_shared_files'])
            check,live=commands(cfg,Path('/not-created'))
            self.assertIn('--host-shared-files',check);self.assertIn('--host-shared-files',live)
            old=configuration(ROOT/f'configs/platform-prettier6604-{schedule}-light-host.json')
            self.assertNotIn('host_shared_files',old);self.assertNotIn('--host-shared-files',commands(old,Path('/not-created'))[1])
            for delta in ({'light_monitor':False},{'host_file_monitor':False},{'damon_observe':True},{'host_prefix_count':1}):
                with tempfile.TemporaryDirectory() as tmp:
                    p=Path(tmp)/'cfg.json';p.write_text(json.dumps(dict(cfg,**delta)))
                    with self.assertRaises(ValueError):configuration(p)
        code=(ROOT/'scripts/validate_isolated_agentenv.py').read_text()
        self.assertEqual(code.count("if full_shared:check_args+=['--host-shared-files']"),1)
        self.assertEqual(code.count("if full_shared:task_args+=['--host-shared-files']"),1)
        self.assertLess(code.index("receipt['host_shared_preparation_sync']="),code.index("call('start-server'"))
        self.assertIn("include_pss=not (cfg.get('host_shared_files') or cfg.get('host_current_only'))",(ROOT/'scripts/platform_tasks.py').read_text())

    def test_two_templates_global_dedup_users_origins_and_skipped_unknown(self):
        inv,back,layers,restored=metadata();objects,excluded=select_objects(inv,back,layers,restored)
        self.assertEqual(len(objects),5);self.assertEqual(len(excluded),7)
        self.assertTrue(all(v['resident_bytes'] is None and v['resident_pages'] is None and v['status']=='NOT_REQUESTED' for v in excluded))
        mem=[v for v in objects if v['category']=='snapshot_memory_device']
        self.assertEqual([sorted(v['users']) for v in mem],[ROLES[1:4],ROLES[4:]])
        common=next(v for v in objects if v['object_key']==['regular',7,40])
        self.assertEqual(sorted(common['users']),ROLES[1:]);self.assertEqual(sorted(common['templates']),['early','late'])
        self.assertEqual(common['origin'],'CREATED_THIS_ROLLOUT')
        self.assertNotIn('backbone',{r for v in mem for r in v['users']})
        # A drive shared by a proper subset must also be selected.
        extra=dict(inv['backbone'][0],rdev=4,inode=104)
        inv['early_1'].append(extra);inv['early_2'].append(extra)
        selected,_=select_objects(inv,back,layers,restored)
        self.assertEqual(next(v['users'] for v in selected if v['object_key']==['block',4]),['early_1','early_2'])
        with self.assertRaises(ValueError):select_objects(inv,back,[*layers,dict(layers[0],size=8192)],restored)

    def fixture(self,out):
        inventory,back,layers,restored=metadata()
        guests={r:r for r in ROLES};bindings={r:dict(pid=100+i,role=r) for i,r in enumerate(ROLES)}
        binding=SimpleNamespace(cgroup=Path('/metadata/group'),resolve=lambda sid:bindings[sid])
        collector=SharedRollout(Path('/metadata/group'),binding,out,'bpo')
        collector.templates={'early':11,'late':24};collector.restored=restored
        return collector,guests,inventory,back,layers

    def test_queries_once_across_both_templates_and_reports_costs(self):
        self.check_query('bpo')

    def test_short_restore_marks_memory_layers_as_imported_not_newly_saved(self):
        self.check_query('short-restore')

    def check_query(self,schedule):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);collector,guests,inv,back,layers=self.fixture(out);queried=[]
            collector.state['schedule']=schedule
            def device_query(devices,first,group,guests,**kw):
                self.assertFalse(kw['capture_memory_pss']);self.assertEqual(list(devices),[2,3,1])
                rows=[]
                for n,row in devices.items():
                    queried.append(('block',n));begin=time.monotonic_ns()
                    rows.append(dict(block_device=True,device=6,inode=row['identity'][1],rdev=n,stable=True,
                        mapping_length=(4 if n in (2,3) else 64)*2**30,resident_bytes=4096,
                        start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns()))
                return rows,None
            def regular_query(path):
                inode=40 if path.name=='shared.raw' else 41;queried.append(('regular',7,inode))
                return dict(block_device=False,device=7,inode=inode,rdev=0,stable=True,mapping_length=4096,resident_bytes=4096)
            with patch('host_rollout_shared.counters',return_value={'file':100000}),\
                 patch('host_rollout_shared.drives_for_vm',side_effect=lambda vm:inv[vm['role']]),\
                 patch('host_rollout_shared.ram_backend',side_effect=lambda vm:back[vm['role']]),\
                 patch('host_rollout_shared.memory_layers',return_value=layers),\
                 patch('host_rollout_shared.device_residency',side_effect=device_query),\
                 patch('host_rollout_shared.mincore_file',side_effect=regular_query),\
                 patch('host_file_monitor.pss_snapshot',side_effect=AssertionError('PSS forbidden')):
                start=time.monotonic_ns();collector.phase('before-guests',{})
                paths_end=time.monotonic_ns();value=collector.query(guests,paths_end)
            self.assertEqual(value['status'],'OBSERVED');self.assertEqual(len(queried),len(set(queried)));self.assertEqual(len(queried),5)
            self.assertEqual(value['query']['categories']['snapshot_memory_layer'],8192)
            expected='IMPORTED_PREFIX' if schedule=='short-restore' else 'CREATED_THIS_ROLLOUT'
            self.assertEqual({o['origin'] for o in value['query']['objects'] if o['category']=='snapshot_memory_layer'},{expected})
            task=dict(status='PASS',rollout_start_monotonic_ns=start,rollout_end_monotonic_ns=time.monotonic_ns(),actual_action_count=84,
                paths={r:dict(official_task_status='FAIL',official_passed=0,official_failed=2) for r in ROLES})
            (out/'result.json').write_text(json.dumps(task))
            summary=report(out,out/'report')
            self.assertTrue(summary['rollout_includes_query']);self.assertGreater(summary['query_elapsed_ns'],0)
            self.assertGreater(summary['metadata_elapsed_ns'],0);self.assertEqual(summary['original_paths_elapsed_ns'],paths_end-start)
            self.assertEqual(summary['official_results']['backbone']['official_failed'],2)
            self.assertNotIn('resident_bytes',summary)  # No invented all-layer physical total.
            self.assertTrue(all(o['residency']['start_monotonic_ns']>=value['query']['start_monotonic_ns'] for o in value['query']['objects']))
            text=(out/'report/REPORT.md').read_text()
            self.assertIn('rootfs下层覆盖见下方识别状态',text)
            self.assertNotIn('rootfs不可变下层文件未完整覆盖',text)
            legacy=copy.deepcopy(value);legacy['query'].pop('rootfs_inventory')
            (out/'host-shared-files.json').write_text(json.dumps(legacy))
            report(out,out/'legacy-report')
            self.assertIn('rootfs不可变下层文件未完整覆盖',(out/'legacy-report/REPORT.md').read_text())

    def test_save_without_restore_is_not_zero_and_parent_never_gets_ram_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            collector,guests,inv,back,layers=self.fixture(Path(tmp));collector.restored={}
            with patch('host_rollout_shared.counters',return_value={'file':0}),\
                 patch('host_rollout_shared.drives_for_vm',return_value=inv['backbone']),\
                 patch('host_rollout_shared.ram_backend',side_effect=AssertionError('cold parent is not restored')):
                row=collector.phase('after-save',{'backbone':'backbone'})
            self.assertEqual(row['backends'],{})
            self.assertTrue(all(v['device_status']=='NOT_CREATED_FOR_RESTORE' and v['config_status']=='NOT_MATERIALIZED' and v['resident_bytes'] is None for v in row['snapshots']))

    def test_grpo_only_queries_actual_readonly_backing_not_nonexistent_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            collector,guests,inv,back,layers=self.fixture(Path(tmp));collector.restored={};collector.templates={}
            def devices(rows,*a,**kw):
                self.assertEqual(list(rows),[1])
                return [dict(block_device=True,device=6,inode=101,rdev=1,stable=True,mapping_length=64*2**30,
                    resident_bytes=4096,start_monotonic_ns=time.monotonic_ns(),end_monotonic_ns=time.monotonic_ns())],None
            with patch('host_rollout_shared.counters',return_value={'file':0}),\
                 patch('host_rollout_shared.drives_for_vm',side_effect=lambda vm:inv[vm['role']]),\
                 patch('host_rollout_shared.ram_backend',side_effect=AssertionError('cold RAM not selected')),\
                 patch('host_rollout_shared.memory_layers',side_effect=AssertionError('no snapshots')),\
                 patch('host_rollout_shared.device_residency',side_effect=devices):
                result=collector.query(guests,time.monotonic_ns())
            self.assertEqual(result['query']['categories'],{'shared_readonly_backing':4096})

    def test_no_pss_report_has_no_empty_pss_panel_and_old_missing_pss_still_fails(self):
        from report_host_file import report as host_report
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'host';source.mkdir()
            receipt=dict(status='PASS',samples=2,cgroup='/owned',pss_interval_seconds=None)
            (source/'result.json').write_text(json.dumps(receipt))
            rows=[dict(monotonic_ns=t,elapsed_ns=t-100,read_duration_ns=1,current=200,file=f,anon=50,shmem=20,kernel=10,file_mapped=0,file_dirty=0,file_writeback=0) for t,f in [(100,80),(200,90)]]
            with (source/'memory.tsv').open('w') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(rows[0]),delimiter='\t');writer.writeheader();writer.writerows(rows)
            creation=dict(cgroup='/owned',preexisting_firecrackers=0,firecracker_pool=False,startup_prewarm=False,block_pool=False,verified_before_guest_creation_ns=150)
            (root/'result.json').write_text(json.dumps(dict(status='PASS',host_file_creation=creation)))
            check=dict(status='OBSERVED',resident_bytes=70,cgroup_before={'file':80},cgroup_after={'file':90},minimum_unaccounted_or_timing_bytes=0,file_residency_lower_bound_bytes=90,accounting='NO_SHORTFALL_DETECTED_NOT_PROOF_OF_COMPLETE_ACCOUNTING',scope='mock files',cpu_ns=1,start_monotonic_ns=190,end_monotonic_ns=200)
            (root/'residency.json').write_text(json.dumps(check))
            import matplotlib.pyplot as plt
            real_subplots=plt.subplots
            with patch.object(plt,'subplots',wraps=real_subplots) as subplot:
                value=host_report(source,root/'residency.json',root/'report')
            self.assertEqual(subplot.call_args_list[0].args[:2],(2,1));self.assertEqual(value['pss_status'],'NOT_REQUESTED')
            self.assertTrue((root/'report/after-service-ready.png').is_file())
            self.assertIsNone(value['pss_observed_peak_bytes']);self.assertFalse((root/'report/pss.jsonl').exists())
            receipt.pop('pss_interval_seconds');(source/'result.json').write_text(json.dumps(receipt))
            with self.assertRaises(FileNotFoundError):host_report(source,root/'residency.json',root/'rejected')

    def test_new_pair_rejects_legacy_reuse_before_any_service_or_file_operation(self):
        from run_light_rollout_pair import main
        with patch.object(sys,'argv',['entry','--shared-files','--reuse-completed-grpo','/old','--output','/not-created']),\
             patch('run_light_rollout_pair.subprocess.check_output') as external:
            with self.assertRaises(SystemExit):main()
            external.assert_not_called()

    def test_pair_delivery_carries_new_observation_and_checks_pss_and_sync(self):
        from test_light_rollout_pair import LightRolloutTests
        from run_light_rollout_pair import inspect_run
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workflow,raw=LightRolloutTests().fixture(root,'bpo')
            def update(path,**kw):
                value=json.loads(path.read_text());value.update(kw);path.write_text(json.dumps(value))
            update(raw/'result.json',host_shared_files={'status':'OBSERVED'})
            update(raw.parent/'result.json',host_shared_preparation_sync=dict(status='PASS',end_monotonic_ns=80),host_file_creation=dict(verified_before_guest_creation_ns=90))
            update(root/'report/host-file/summary.json',pss_status='NOT_REQUESTED')
            p=root/'report/shared-files/summary.json';p.parent.mkdir();p.write_text(json.dumps(dict(status='OBSERVED',rollout_includes_query=True)))
            value=inspect_run(workflow,'bpo',root/'accepted');self.assertEqual(value['shared_files']['status'],'OBSERVED')
            update(root/'report/host-file/summary.json',pss_status='OBSERVED')
            with self.assertRaises(AssertionError):inspect_run(workflow,'bpo',root/'rejected')

    def test_image_metadata_requires_owned_open_layers_without_payload_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            env=Path(tmp)
            for t in ('early','late'):
                p=env/'snapshot-local-cache/runtime'/t/'memory/image.json';p.parent.mkdir(parents=True)
                p.write_text(json.dumps(dict(lowers=[dict(file='/workspace/env/shared.raw',size=4096)],upper={})))
            real_stat=Path.stat
            def fake_stat(path,*a,**kw):
                if str(path) in ('/proc/17/fd/2',str(env/'shared.raw')):
                    return SimpleNamespace(st_mode=stat.S_IFREG,st_dev=7,st_ino=40,st_size=4096)
                return real_stat(path,*a,**kw)
            with patch('host_rollout_shared.pids',return_value=[17]),\
                 patch.object(Path,'iterdir',lambda path:iter([Path('/proc/17/fd/2')]) if str(path)=='/proc/17/fd' else (_ for _ in ()).throw(AssertionError('unexpected inventory'))),\
                 patch.object(Path,'stat',fake_stat):
                rows=memory_layers(env,['early','late'],Path('/metadata/group'))
            self.assertEqual(len(rows),2);self.assertTrue(all(v['origin']=='CREATED_THIS_ROLLOUT' for v in rows))
            self.assertEqual(rows[0]['open_descriptors'],[dict(pid=17,fd='2')])
            self.assertTrue(rows[0]['image_config'].endswith('early/memory/image.json'))
            with patch('host_rollout_shared.pids',return_value=[]),patch.object(Path,'stat',fake_stat):
                with self.assertRaises(ValueError):memory_layers(env,['early'],Path('/metadata/group'))

    def test_added_query_failure_does_not_skip_original_official_checks(self):
        node=next(n for n in ast.walk(SOURCE) if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id=='shared_files' and any(isinstance(v,ast.Try) for v in n.body))
        shared=SimpleNamespace(query=Mock(side_effect=RuntimeError('query failed')),state={},save=Mock())
        ns=dict(shared_files=shared,time=time,result={},by_role={})
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual query integration>','exec'),ns)
        self.assertEqual(ns['result']['host_shared_files']['status'],'FAIL');shared.save.assert_called_once()
        official=next(n for n in ast.walk(SOURCE) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='final_check')
        self.assertLess(node.lineno,official.lineno)
        failed=next(n for n in ast.walk(SOURCE) if isinstance(n,ast.If) and isinstance(n.test,ast.BoolOp) and any(isinstance(v,ast.Compare) and any(isinstance(c,ast.Constant) and c.value=='OBSERVED' for c in v.comparators) for v in n.test.values))
        ns['success']=True
        exec(compile(ast.Module(body=[failed],type_ignores=[]),'<actual completion integration>','exec'),ns)
        self.assertFalse(ns['success']);self.assertEqual(ns['result']['failed_stage'],'host-shared-files')

    def test_actual_schedule_methods_keep_paths_and_only_add_metadata_between_batches(self):
        cls=next(n for n in ast.walk(SOURCE) if isinstance(n,ast.ClassDef) and n.name=='SampledPaths')
        for enabled in (False,True):
            for schedule in ('grpo','bpo'):
                visits=[];observations=[]
                class Parent:
                    async def start(self,role,**kwargs):return role
                    async def run_path(self,sid,role,path):visits.append((role,list(path)))
                    async def save_prefix(self,sid,point,step):return 'early' if step==11 else 'late'
                    def phase(self,*a):pass
                    def record(self,*a):pass
                shared=SimpleNamespace(phase=lambda label,*a,**kw:observations.append(label)) if enabled else None
                async def joined(tasks):return await asyncio.gather(*tasks)
                ns=dict(module=SimpleNamespace(Controller=Parent),shared_files=shared,schedule=schedule,independent_phase=None,reuse_checkpoint=None,
                    damon_before_checkpoint=False,host_restore=False,cache_pair=False,args=SimpleNamespace(role=None),roles=ROLES,
                    executed={r:([2,3] if r.startswith('early') else [4]) for r in ROLES},joined=joined,asyncio=asyncio,time=time,
                    scaling=None,prefix_count=None,physical=None,create_before_setup=False,private_control=False,full_rootfs=None)
                exec(compile(ast.Module(body=[copy.deepcopy(cls)],type_ignores=[]),'<actual sampled controller>','exec'),ns)
                controller=ns['SampledPaths']();controller.rollouts={r:list(range(5)) for r in ROLES}
                # Keep start/run/save host operations mocked; execute the actual schedule methods.
                controller.start=Parent.start.__get__(controller);controller.save_prefix=Parent.save_prefix.__get__(controller)
                result=asyncio.run(getattr(controller,'do_'+schedule)())
                self.assertEqual(list(result),ROLES)
                if schedule=='bpo':self.assertEqual(visits[:3],[('backbone',[0,1]),('backbone',[2,3]),('backbone',[4])])
                self.assertEqual(observations,(['roots-ready'] if schedule=='grpo' else ['root-ready','early-restores-ready','all-restores-ready']) if enabled else [])

    def test_actual_save_keeps_metadata_outside_original_pause(self):
        cls=next(n for n in ast.walk(SOURCE) if isinstance(n,ast.ClassDef) and n.name=='SampledPaths')
        for enabled in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                events=[]
                class Parent:
                    async def capture_agentenv_template(self,*a):events.append('capture');return '01a1626a-d32e-7bf2-8bce-3fe20ead2eed'
                proc=dict(pid='1',start_ticks='2',boot_id='3')
                def guest(label,*a):return '{}' if label.startswith('boundary') else '\n'.join(k+'='+v for k,v in proc.items())
                def phase(label,*a):events.append(label)
                shared=SimpleNamespace(templates={},phase=phase) if enabled else None
                ns=dict(module=SimpleNamespace(Controller=Parent),shared_files=shared,guest=guest,asyncio=asyncio,json=json,re=re,
                    verify_boundary=Mock(),runtime=SimpleNamespace(stop=lambda *a:events.append('stop'),start=lambda *a:events.append('resume')),out=Path(tmp),full_rootfs=None)
                exec(compile(ast.Module(body=[copy.deepcopy(cls)],type_ignores=[]),'<actual sampled controller>','exec'),ns)
                c=ns['SampledPaths']();c.root_process=proc;c.checkpoints={}
                asyncio.run(c.save_prefix('parent','point',11))
                self.assertEqual(events,['before-checkpoint-11','stop','capture','resume','after-checkpoint-11'] if enabled else ['stop','capture','resume'])


if __name__=='__main__':unittest.main()
