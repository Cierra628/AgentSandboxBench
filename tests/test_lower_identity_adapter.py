"""Actual receipt shape, synthetic IDs; all live queries mocked."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from snapshot_lower_owner import lower_vm_identity
from live_task_cache import LiveTaskCache

class AdapterTests(unittest.TestCase):
    def setUp(self):
        # TaskPhysicalCache's vmm_binding intentionally has no sandbox_id.
        self.receipt=dict(status='PASS',vmm_binding=dict(pid=42,start_ticks='123',boot_id='boot',
            api_socket='/unused/socket',cgroup='/owned.slice',binding_source='MMDS'),
            actual_cgroup='/owned.slice/vmm-40')
        self.ram=dict(pid=42,start_time_ticks=123,sandbox_id='sid',cgroup='/owned.slice/vmm-40')
    def test_actual_shape_joins_without_mutation(self):
        original=copy.deepcopy(self.receipt)
        value=lower_vm_identity(self.receipt,self.ram,'sid')
        self.assertEqual(value['sandbox_id'],'sid')
        self.assertEqual(value['actual_cgroup'],self.ram['cgroup'])
        self.assertEqual(self.receipt,original)
    def test_conflicts_are_rejected(self):
        for field,value in [('pid',99),('start_time_ticks',999),('cgroup','/other'),('sandbox_id','other')]:
            with self.subTest(field=field),self.assertRaises(ValueError):
                lower_vm_identity(self.receipt,dict(self.ram,**{field:value}),'sid')
        receipt=copy.deepcopy(self.receipt);receipt['vmm_binding']['sandbox_id']='other'
        with self.assertRaises(ValueError):lower_vm_identity(receipt,self.ram,'sid')
        with self.assertRaises(ValueError):lower_vm_identity(dict(self.receipt,status='FAIL'),self.ram,'sid')
    def test_real_caller_passes_joined_identity_to_lower(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'vmm-child-binding.json').write_text(json.dumps(self.receipt))
            obj=LiveTaskCache.__new__(LiveTaskCache)
            obj.session='restored';obj.batch_lookup=False;obj.proof={'slots':[]}
            obj.directory=root;obj.out=root;obj.lower_cache=True;obj.provider_cache=False
            obj.capture=SimpleNamespace(child_scope=True,binding=object())
            obj.vm=self.ram;obj.sid='sid'
            backend=SimpleNamespace(regions=[{'path':'/dev/ublkb3'}])
            with patch('snapshot_cache_residency.SnapshotCache',return_value=backend) as query,patch('snapshot_layer_metadata.owned_memory_layer',return_value=SimpleNamespace(path=root/'layer')) as lower:
                obj._start_snapshot_queries(root/'fake-proc')
                self.assertEqual(lower.call_args.kwargs['vm']['sandbox_id'],'sid')
                self.assertEqual(lower.call_args.kwargs['device_path'],'/dev/ublkb3')
                self.assertIs(lower.call_args.kwargs['binding'],obj.capture.binding)
                self.assertEqual(query.call_count,2)

    def test_provider_caller_passes_same_explicit_owner(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'vmm-child-binding.json').write_text(json.dumps(self.receipt))
            obj=LiveTaskCache.__new__(LiveTaskCache)
            obj.session='restored';obj.batch_lookup=False;obj.proof={'slots':[]}
            obj.directory=root;obj.out=root;obj.lower_cache=True;obj.provider_cache=True
            obj.capture=SimpleNamespace(child_scope=True,binding=object());obj.vm=self.ram;obj.sid='sid'
            backend=SimpleNamespace(regions=[{'path':'/dev/ublkb3'}])
            with patch('snapshot_cache_residency.SnapshotCache',return_value=backend),patch('snapshot_layer_metadata.owned_memory_layer',return_value=SimpleNamespace(path=root/'layer')) as lower,patch('provider_slot_cache.ProviderCache') as provider:
                obj._start_snapshot_queries(root/'fake-proc')
                self.assertEqual(provider.call_args.kwargs,lower.call_args.kwargs)

if __name__=='__main__':unittest.main()
