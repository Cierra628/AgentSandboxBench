"""Offline reference checks; no real VMM, file residency or service operations."""
import ast,copy,json,sys,tempfile,time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from host_scaling_reference import reference,audit_observation
from host_rollout_shared import SharedRollout

class ReferenceTests(unittest.TestCase):
    def points(self):
        def row(category,key,n,resident):return dict(category=category,object_key=key,users=list(map(str,range(n))),mapping_length=8192,resident_bytes=resident)
        first=dict(status='OBSERVED',sandboxes=1,objects=[row('shared_rootfs_layer',['regular',7,1],1,4096),row('snapshot_memory_layer',['regular',7,2],1,8192)])
        last=dict(status='OBSERVED',sandboxes=7,objects=[row('shared_rootfs_layer',['regular',7,1],7,8192),row('snapshot_memory_layer',['regular',7,2],7,8192)])
        return first,last
    def test_same_object_reference_and_public_layer_not_multiplied(self):
        a,b=self.points();rows=reference(a,b)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['reference_bytes'],7*4096)
        self.assertEqual(rows[0]['reduction_bytes'],5*4096);self.assertAlmostEqual(rows[0]['reduction_fraction'],5/7)
        a['objects'][0]['resident_bytes']=0
        self.assertIsNone(reference(a,b)[0]['reduction_fraction'])
        self.assertEqual(reference(a,b)[0]['reduction_bytes'],-8192)
    def test_changed_identity_capacity_or_consumer_count_rejects_reference(self):
        for key,value in (('object_key',['regular',7,999]),('mapping_length',16384),('users',['only-one'])):
            a,b=self.points();b['objects'][0][key]=value
            with self.assertRaises(ValueError):reference(a,b)
        a,b=self.points();a['objects']=[];b['objects']=[]
        with self.assertRaises(ValueError):reference(a,b)
    def test_single_candidate_is_queried_only_with_explicit_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            for enabled in (False,True):
                roots=dict(status='IDENTIFIED',guests={},objects=[dict(status='NOT_REQUESTED_SINGLE_USER',path='/metadata/base',device=7,inode=40,rdev=0,block_device=False,size=4096,users=['one'],origin='ACTIVE_ROOTFS_LOWER')])
                binding=SimpleNamespace(cgroup=Path('/owned'),resolve=lambda sid:dict(pid=1))
                collector=SharedRollout(Path('/owned'),binding,Path(tmp),'cold-reference',single_reference=enabled)
                def devices(*a,**kw):
                    return [dict(block_device=True,device=6,inode=9,rdev=2,stable=True,mapping_length=64*2**30,resident_bytes=4096,resident_pages=1,start_monotonic_ns=time.monotonic_ns(),end_monotonic_ns=time.monotonic_ns())],None
                with patch('host_rollout_shared.counters',return_value={'file':8192}),\
                     patch('host_rollout_shared.drives_for_vm',return_value=[dict(path='/metadata/ro',drive_id='rootfs',is_read_only=True,block_device=True,device=6,inode=9,rdev=2)]),\
                     patch('host_rollout_shared.opened_regular_files',return_value={}),\
                     patch('host_rollout_shared.rootfs_inventory',return_value=roots),\
                     patch('host_rollout_shared.validate_rootfs_inventory'),\
                     patch('host_rollout_shared.device_residency',side_effect=devices),\
                     patch('host_rollout_shared.mincore_file',return_value=dict(block_device=False,device=7,inode=40,rdev=0,stable=True,mapping_length=4096,resident_bytes=4096,resident_pages=1)) as query:
                    result=collector.query({'one':'sid'},time.monotonic_ns())
                self.assertEqual(query.call_count,int(enabled))
                self.assertEqual(sum(o['category']=='shared_rootfs_layer' for o in result['query']['objects']),int(enabled))
                if enabled:self.assertIn('single-guest reference candidates',result['scope'])
    def test_wrapper_checks_scope_before_docker_and_sets_only_new_flag(self):
        import run_host_file_workflow as entry
        config=str(ROOT/'configs/platform-prettier6604-host-file.json')
        with patch.object(sys,'argv',['entry',config,'--rootfs-reference']),patch.object(entry.subprocess,'check_output') as docker:
            with self.assertRaises(SystemExit):entry.main()
            docker.assert_not_called()
        for enabled in (False,True):
            argv=['entry',config,'--scaling','cold']+(['--rootfs-reference'] if enabled else [])
            with patch.object(sys,'argv',argv),patch('platform_tasks.preflight',return_value={}),\
                 patch.object(entry.subprocess,'check_output',return_value='systemd\n'),\
                 patch.object(entry.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(entry.main(),0)
                self.assertEqual('--setenv=ASB_HOST_ROOTFS_REFERENCE=on' in run.call_args_list[0].args[0],enabled)
    def test_explicit_path_bypasses_old_broad_observer(self):
        from host_scaling import observe_files
        with patch.dict('os.environ',ASB_HOST_ROOTFS_REFERENCE='on'),\
             patch('host_scaling_reference.observe_reference',return_value={'status':'OBSERVED'}) as query,\
             patch('host_prefix_compare.observe_prefix',side_effect=AssertionError('old broad query forbidden')):
            self.assertEqual(observe_files(None,None,{},None,None,None),{'status':'OBSERVED'})
            query.assert_called_once()

if __name__=='__main__':unittest.main()
