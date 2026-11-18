"""Endpoint acquisition with synthetic ownership and files; no real proc or VM."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import external_task_metrics as metrics


class ExternalMetricsTests(unittest.TestCase):
    def test_endpoint_ownership_identity_cost_and_failure_records(self):
        for fault in (None,'outside','inventory','pss-permission','pid','missing-pss','uid','mincore','changed-file','changed-binding'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp);group=out/'group';pid=999999999
                vm=dict(pid=pid,start_ticks='5',boot_id='fixture',api_socket='fixture')
                roots=dict(status='INCOMPLETE' if fault=='inventory' else 'IDENTIFIED',objects=[dict(path='fixture',device=1,inode=2,size=4096,mtime_ns=1)])
                stopped=[];resolved=[];intervals=[]
                class Binding:
                    def __init__(self,*a):self.cgroup=out/'elsewhere' if fault=='outside' else group/'service'
                    def resolve(self,sid):
                        resolved.append(sid)
                        return dict(vm,start_ticks='6') if fault=='changed-binding' and len(resolved)>2 else dict(vm)
                class Monitor:
                    def __init__(self,*a,**kw):
                        self_interval=kw['pss_interval_seconds'];intervals.append(self_interval)
                        directory=a[4];directory.mkdir(parents=True)
                        (directory/'host-vmm.tsv').write_text('monotonic_ns\tpss_kib\n100\t4\n200\t\n')
                    def stop(self):stopped.append(True);return dict(status='PASS',pss_samples=1,interval_seconds=.1,pss_interval_seconds=intervals[-1])
                def read(*a,**kw):
                    self.assertTrue(kw['pss'])
                    if fault=='pss-permission':raise PermissionError('fixture proc denied')
                    return dict(start_ticks='6' if fault=='pid' else '5',pss_kib=None if fault=='missing-pss' else 4)
                def query(*a):
                    if fault=='mincore':raise PermissionError('fixture mincore denied')
                    return dict(device=1,inode=9 if fault=='changed-file' else 2,size=4096,mtime_ns=1,resident_bytes=4096,stable=True)
                original_stat=Path.stat
                def stat(path,*a,**kw):
                    if str(path)==f'/proc/{pid}':return SimpleNamespace(st_uid=1 if fault=='uid' else 0)
                    return original_stat(path,*a,**kw)
                with patch.object(metrics,'measurement_group',return_value=(group,'fixture')),patch.object(metrics,'Binding',Binding),patch.object(metrics,'HostMonitor',Monitor),patch.object(metrics,'rootfs_inventory',return_value=roots),patch.object(metrics,'opened_regular_files',return_value={}),patch.object(metrics,'drives_for_vm',return_value=[]),patch.object(metrics,'read',side_effect=read),patch.object(metrics,'mincore_file',side_effect=query) as queried,patch.object(metrics,'counters',return_value=dict(file=1024,current=8192)),patch.object(metrics,'validate_rootfs_inventory') as validate,patch.object(Path,'stat',stat):
                    if fault in ('outside','inventory'):
                        with self.assertRaises(ValueError):metrics.ExternalMetrics('fixture',out,'sid')
                        queried.assert_not_called();continue
                    sampler=metrics.ExternalMetrics('fixture',out,'sid',pss_interval_seconds=2)
                    self.assertEqual(intervals,[2])
                    try:
                        if fault:
                            with self.assertRaises((ValueError,PermissionError)):sampler.observe()
                            value=json.loads((out/'external-metrics.json').read_text());self.assertEqual(value['status'],'FAIL')
                            self.assertIn('error',value)
                        else:
                            value=sampler.observe();pss=value['pss'];p1=value['p1']
                            self.assertEqual(pss['total_bytes'],4096);self.assertEqual(p1['resident_bytes'],4096)
                            self.assertEqual(p1['net_shortfall_bytes'],3072);self.assertIsNone(p1['outside_charge_bytes'])
                            self.assertEqual(p1['unique_files'],1);queried.assert_called_once();validate.assert_called_once()
                            for row in (pss,p1):
                                self.assertGreaterEqual(row['cpu_ns'],0);self.assertGreaterEqual(row['end_monotonic_ns'],row['start_monotonic_ns'])
                            receipt=sampler.stop()
                            task=dict(functional_validation='PASS',endpoint_metrics=value,host_vmm_collector=receipt,schedule_elapsed_ns=10,actions_download_elapsed_ns=5)
                            metrics.validate_result(task,pss_interval_seconds=2)
                            with self.assertRaises(AssertionError):metrics.validate_result(task,pss_interval_seconds=1)
                            bad=copy.deepcopy(task);bad['endpoint_metrics']['p1']['files']*=2;bad['endpoint_metrics']['p1']['unique_files']=2
                            with self.assertRaises(AssertionError):metrics.validate_result(bad)
                            bad=copy.deepcopy(task);bad['host_vmm_collector']['pss_samples']=0
                            with self.assertRaises(AssertionError):metrics.validate_result(bad)
                            self.assertEqual(receipt['maximum_sampling_gap_ns'],100)
                    finally:
                        sampler.stop();sampler.stop();self.assertEqual(stopped,[True])


if __name__=='__main__':unittest.main()
