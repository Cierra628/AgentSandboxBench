"""Pure saved-sample arithmetic; no service, task, network or devices."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from collect_host_density import memory_metrics
from report_host_density import fit,savings
import collect_host_density as collection


class DensityCollectionTests(unittest.TestCase):
    def test_full_peak_execution_peak_median_and_components_are_distinct(self):
        data=[dict(monotonic_ns=str(t),current=str(x),file=str(x//2),anon='7',kernel='3',shmem='1') for t,x in ((0,9000),(10,100),(20,1000),(30,200),(40,1))]
        v=memory_metrics(data,10,30)
        self.assertEqual(v['current_peak_bytes'],9000)
        self.assertEqual(v['execution_current_peak_bytes'],1000)
        self.assertEqual(v['execution_current_median_bytes'],200)
        self.assertEqual(v['execution_samples'],3)
        self.assertEqual(v['components_at_execution_current_peak_bytes'],dict(file=500,anon=7,kernel=3,shmem=1))

    def test_fit_and_signed_savings_preserve_all_points(self):
        result=fit((1,2,4,7,10),[10+3*n for n in (1,2,4,7,10)])
        self.assertEqual(result['slope_mib_per_guest'],3)
        self.assertEqual(result['intercept_mib'],10)
        self.assertEqual(result['r_squared'],1)
        self.assertEqual(savings(110,100),dict(difference=-10,fraction=-.1))
        result=fit((1,2,4,7,10),(13,16,22,31,100))
        self.assertEqual(result['points'],5)
        self.assertEqual(result['observed_median_mib'],22)

    def test_empty_window_and_missing_counter_are_not_zero(self):
        with self.assertRaisesRegex(ValueError,'no current samples'):memory_metrics([],0,1)
        with self.assertRaises(KeyError):memory_metrics([dict(monotonic_ns='0',current='1')],0,1)

    def test_c4_new_timing_is_additive_and_p0_flag_does_not_change_measurements(self):
        import matplotlib.figure
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);member=root/'member';storage=root/'storage';storage.mkdir();raw=storage/'density/guest-00'
            (storage/'disk-samples.jsonl').write_text(json.dumps(dict(volume_available_bytes=10,host_available_bytes=20))+'\n')
            counters=dict(current=100,file=110,anon=1,kernel=2,shmem=0)
            queries={p:dict(start_monotonic_ns=1,end_monotonic_ns=2,elapsed_ns=1,cpu_ns=1,resident_bytes=0 if p=='P0' else 10,counters_before=counters,counters_after=counters,objects=[{}]) for p in ('P0','P1')}
            child=dict(role='guest-00',raw='guest-00',result={})
            task=dict(status='PASS',**queries,identity=dict(status='PASS'),copy_independence=dict(status='PASS'),results=[child],guests={'guest-00':dict(start_monotonic_ns=1,end_monotonic_ns=2)},controller_elapsed_ns=100)
            files={member/'result.json':dict(status='PASS',storage=str(storage),request=dict(task='c4',count=1,mode='shared'),collector={}),storage/'result.json':dict(status='PASS',capacity_bytes=100),storage/'density/result.json':task,
                raw/'events.json':[dict(stage=stage,start_monotonic_ns=t,duration_ns=1,exit_code=0) for stage,t in (('replay',10),('download-tools',20))],
                raw/'runtime-monitor.json':dict(sessions=[dict(raw_directory='samples',receipt=dict(samples=2,interval_seconds=.1))])}
            tables={member/'host-file/memory.tsv':[dict(monotonic_ns=t,current=100,file=110,anon=1,kernel=2,shmem=0) for t in (10,21)],
                raw/'tools.tsv':[dict(step=i,duration_ns=1,exit_code=0) for i in range(36)],
                raw/'samples/guest-memory.tsv':[dict(monotonic_ns=t,elapsed_ns=t,MemTotal_kib=100,MemAvailable_kib=10,Cached_kib=4,Shmem_kib=0) for t in (10,21)]}
            with patch.object(collection,'ROOT',root),patch.object(collection,'read',side_effect=files.__getitem__),patch.object(collection,'rows',side_effect=tables.__getitem__),patch.object(collection.shutil,'copy2'),patch.object(matplotlib.figure.Figure,'savefig'):
                old=collection.collect_member(member,root/'old')
                child['result']['call_timing']=dict(version=2,replay=dict(command_duration_ns=7))
                new=collection.collect_member(member,root/'new')
            self.assertEqual(new.pop('external_call_timing'),[dict(guest='guest-00',timing=child['result']['call_timing'])])
            self.assertEqual(old,new)
            self.assertEqual(new['execution_window_monotonic_ns'],[10,21])
            self.assertEqual(new['cold_baseline'],counters)
            self.assertTrue(all(v['file_exceeds_current'] for v in new['p0_counter_flags'].values()))


if __name__=='__main__':unittest.main()
