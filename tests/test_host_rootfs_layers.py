"""Temporary metadata and mocked residency only; no real service, proc, or device."""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from host_rootfs_layers import rootfs_inventory,validate_rootfs_inventory
from host_rollout_shared import SharedRollout,select_objects,report


class RootfsLayersTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.proc=self.base/'proc';self.container=self.base/'container'
        self.container.mkdir();self.env=self.container/'workspace/env';self.env.mkdir(parents=True)
        self.bindings={};self.inventory={};self.configs={};self.fake_blocks={};self.opened={}
        for n,role in enumerate(('backbone','early_1','late_1'),101):
            process=self.proc/str(n);(process/'cwd/overlaybd').mkdir(parents=True)
            (process/'root').symlink_to(self.container,target_is_directory=True)
            self.bindings[role]=dict(pid=n,role=role)
            root=process/'cwd/user-rootfs';root.touch();self.fake_blocks[str(root)]=(6,n,1000+n)
            self.inventory[role]=[
                dict(drive_id='user_rootfs',path=str(root),block_device=True,device=6,inode=n,rdev=1000+n,is_read_only=False),
                dict(drive_id='rootfs',path='/metadata/readonly',block_device=True,device=6,inode=1,rdev=1,is_read_only=True)]
            self.configs[role]=process/'cwd/overlaybd/image.json'
        self.restored={'early_1':'step-11','late_1':'step-24'}

    def file(self,name):
        path=self.env/name;path.parent.mkdir(parents=True,exist_ok=True);path.touch()
        with path.open('r+b') as stream:stream.truncate(4096)
        info=path.stat();self.opened[(info.st_dev,info.st_ino)]=[dict(pid=50,fd='9')]
        return path

    def config(self,role,lowers):
        self.configs[role].write_text(json.dumps(dict(lowers=lowers,upper=dict(data='./upper.data',index='./upper.index'))))

    def collect(self):
        # Fail any attempted layer read: only configuration JSON may be read.
        original=Path.read_text
        def metadata_only(path,*a,**kw):
            self.assertEqual(path.name,'image.json')
            return original(path,*a,**kw)
        with patch.object(Path,'read_text',metadata_only):
            return rootfs_inventory(self.bindings,self.inventory,self.opened,self.restored,proc_root=self.proc)

    def block_stat(self):
        original=Path.stat
        def fake(path,*a,**kw):
            if str(path) in self.fake_blocks:
                dev,ino,rdev=self.fake_blocks[str(path)]
                return SimpleNamespace(st_mode=stat.S_IFBLK,st_dev=dev,st_ino=ino,st_rdev=rdev)
            return original(path,*a,**kw)
        return patch.object(Path,'stat',fake)

    def test_shared_file_hardlink_order_and_actual_subset_users(self):
        path=self.file('base.commit');alias=self.env/'alias.commit';os.link(path,alias)
        self.config('backbone',[dict(file='/workspace/env/base.commit'),dict(file='/workspace/env/alias.commit')])
        self.config('early_1',[dict(file='/workspace/env/alias.commit')])
        self.file('private.commit');self.config('late_1',[dict(file='/workspace/env/private.commit')])
        value=self.collect();self.assertEqual(value['status'],'IDENTIFIED');self.assertEqual(len(value['objects']),2)
        shared=next(o for o in value['objects'] if o['status']=='SELECTED')
        self.assertEqual(shared['users'],['backbone','early_1']);self.assertEqual(shared['templates'],['step-11'])
        self.assertEqual([r['index'] for r in value['guests']['backbone']['lowers']],[0,1])
        selected,_=select_objects(self.inventory,{},[],{},value['objects'])
        self.assertEqual(len([o for o in selected if o['category']=='shared_rootfs_layer']),1)
        self.assertTrue(all(o['resident_bytes'] is None for o in value['objects']))
        self.assertEqual(value['guests']['backbone']['upper']['status'],'NOT_REQUESTED')
        with self.block_stat():validate_rootfs_inventory(value)

    def test_same_digest_copies_not_shared_and_single_user_not_queried(self):
        for role in self.bindings:
            self.file(role+'.commit');self.config(role,[dict(file='/workspace/env/'+role+'.commit',digest='same-digest')])
        value=self.collect()
        self.assertEqual(len(value['objects']),3)
        self.assertTrue(all(o['status']=='NOT_REQUESTED_SINGLE_USER' for o in value['objects']))
        selected,_=select_objects(self.inventory,{},[],{},value['objects'])
        self.assertEqual([o['category'] for o in selected],['shared_readonly_backing'])

    def test_directory_commit_then_sealed_and_relative_path(self):
        self.file('dir/overlaybd.commit');self.file('dir/overlaybd.sealed')
        self.file('old/overlaybd.sealed');self.file('explicit.commit')
        self.config('backbone',[dict(dir='/workspace/env/dir')])
        self.config('early_1',[dict(dir='/workspace/env/old')])
        # A relative path is relative to the runtime config, not the host CWD.
        local=self.configs['late_1'].parent/'relative.commit';local.touch()
        info=local.stat();self.opened[(info.st_dev,info.st_ino)]=[dict(pid=50,fd='10')]
        self.config('late_1',[dict(file='./relative.commit',dir='/workspace/env/dir')])
        rows=self.collect()['guests']
        self.assertTrue(rows['backbone']['lowers'][0]['path'].endswith('/dir/overlaybd.commit'))
        self.assertTrue(rows['early_1']['lowers'][0]['path'].endswith('/old/overlaybd.sealed'))
        self.assertTrue(rows['late_1']['lowers'][0]['path'].endswith('/relative.commit'))

    def test_missing_dir_remote_unopened_and_invalid_config_stay_unknown(self):
        self.file('unopened.commit');self.opened={}
        self.config('backbone',[dict(dir='/workspace/env/missing'),dict(digest='remote-only')])
        self.config('early_1',[dict(file='/workspace/env/unopened.commit')])
        self.configs['late_1'].write_text('broken JSON')
        value=self.collect();self.assertEqual(value['status'],'INCOMPLETE');self.assertEqual(value['objects'],[])
        for guest in value['guests'].values():
            self.assertIsNone(guest['resident_bytes'])
            for row in guest['lowers']:self.assertEqual(row['status'],'UNKNOWN');self.assertIsNone(row['resident_bytes'])
        self.configs['late_1'].unlink()
        self.assertEqual(self.collect()['guests']['late_1']['status'],'UNKNOWN')

    def test_config_or_lower_replacement_rejected_after_query(self):
        path=self.file('base.commit')
        for role in self.bindings:self.config(role,[dict(file='/workspace/env/base.commit')])
        value=self.collect();self.config('backbone',[dict(file='/workspace/env/changed.commit')])
        with self.block_stat(),self.assertRaisesRegex(ValueError,'config changed'):validate_rootfs_inventory(value)
        self.assertEqual(value['status'],'INCOMPLETE')
        self.config('backbone',[dict(file='/workspace/env/base.commit')]);value=self.collect()
        path.rename(path.with_suffix('.old'));path.touch()
        with self.block_stat(),self.assertRaisesRegex(ValueError,'lower changed'):validate_rootfs_inventory(value)

    def test_config_changes_while_reading_leave_no_confirmed_references(self):
        self.file('base.commit')
        for role in self.bindings:self.config(role,[dict(file='/workspace/env/base.commit')])
        original=Path.read_text
        def changed(path,*a,**kw):
            text=original(path,*a,**kw)
            if path==self.configs['backbone']:path.write_text(text+'\n')
            return text
        with patch.object(Path,'read_text',changed):
            value=rootfs_inventory(self.bindings,self.inventory,self.opened,self.restored,proc_root=self.proc)
        self.assertEqual(value['guests']['backbone']['status'],'UNKNOWN')
        self.assertEqual(value['objects'][0]['users'],['early_1','late_1'])

    def test_reserved_slot_never_substitutes_for_missing_system_drive(self):
        self.file('base.commit')
        for role in self.bindings:self.config(role,[dict(file='/workspace/env/base.commit')])
        self.inventory['backbone'][0].update(drive_id='agentenv_volume_slot_0',block_device=False)
        value=self.collect();self.assertEqual(value['guests']['backbone']['status'],'UNKNOWN')
        self.assertEqual(value['objects'][0]['users'],['early_1','late_1'])

    def test_final_query_once_costs_and_no_metadata_in_earlier_phases(self):
        path=self.file('shared.commit')
        for role in self.bindings:self.config(role,[dict(file='/workspace/env/shared.commit')])
        out=self.base/'out';out.mkdir();guests={r:r for r in self.bindings}
        binding=SimpleNamespace(cgroup=Path('/metadata/owned'),resolve=lambda sid:self.bindings[sid])
        collector=SharedRollout(Path('/metadata/group'),binding,out,'grpo')
        calls=[]
        def regular(target):
            info=target.stat();calls.append((info.st_dev,info.st_ino))
            return dict(device=info.st_dev,inode=info.st_ino,rdev=0,block_device=False,stable=True,
                mapping_length=info.st_size,resident_bytes=4096)
        def devices(*a,**kw):
            self.assertFalse(kw['capture_memory_pss'])
            now=time.monotonic_ns()
            return [dict(device=6,inode=1,rdev=1,block_device=True,stable=True,mapping_length=64*2**30,
                resident_bytes=4096,start_monotonic_ns=now,end_monotonic_ns=now)],None
        real=rootfs_inventory
        with self.block_stat(),patch('host_rollout_shared.counters',return_value={'file':0}),\
             patch('host_rollout_shared.drives_for_vm',side_effect=lambda vm:self.inventory[vm['role']]),\
             patch('host_rollout_shared.opened_regular_files',return_value=self.opened),\
             patch('host_rollout_shared.rootfs_inventory',side_effect=lambda *a:real(*a,proc_root=self.proc)) as capture,\
             patch('host_rollout_shared.device_residency',side_effect=devices),\
             patch('host_rollout_shared.mincore_file',side_effect=regular),\
             patch('host_file_monitor.pss_snapshot',side_effect=AssertionError('PSS forbidden')):
            start=time.monotonic_ns();collector.phase('roots-ready',guests);capture.assert_not_called()
            end=time.monotonic_ns();value=collector.query(guests,end);capture.assert_called_once()
        self.assertEqual(len(calls),1);self.assertEqual(value['status'],'OBSERVED')
        query=value['query'];meta=query['rootfs_inventory']
        self.assertEqual(meta['status'],'IDENTIFIED')
        self.assertLessEqual(meta['end_monotonic_ns'],query['start_monotonic_ns'])
        self.assertEqual(query['categories']['shared_rootfs_layer'],4096)
        self.assertEqual(next(o['users'] for o in query['objects'] if o['category']=='shared_rootfs_layer'),list(self.bindings))
        task=dict(status='PASS',rollout_start_monotonic_ns=start,rollout_end_monotonic_ns=time.monotonic_ns(),
            actual_action_count=189,paths={r:dict(official_task_status='FAIL',official_passed=0,official_failed=2) for r in self.bindings})
        (out/'result.json').write_text(json.dumps(task));summary=report(out,out/'report')
        self.assertEqual(summary['rootfs_coverage_status'],'IDENTIFIED')
        self.assertGreater(summary['rootfs_metadata_elapsed_ns'],0)
        self.assertGreater(summary['rootfs_query_elapsed_ns'],0)
        self.assertGreater(summary['rootfs_query_thread_cpu_ns'],0)
        self.assertLessEqual(summary['rootfs_metadata_elapsed_ns'],summary['metadata_elapsed_ns'])
        self.assertLessEqual(summary['rootfs_query_elapsed_ns'],summary['query_elapsed_ns'])

    def test_new_helper_is_in_all_executed_source_receipts(self):
        for name in ('validate_isolated_agentenv.py','platform_tasks.py','run_prettier6604_grpo.py','run_light_rollout_pair.py'):
            self.assertIn("'host_rollout_shared.py','host_rootfs_layers.py'",(ROOT/'scripts'/name).read_text())


if __name__=='__main__':unittest.main()
