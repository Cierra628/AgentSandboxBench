"""One mocked collector iteration; no services, devices, or payload reads."""
import copy
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from live_task_cache import LiveTaskCache
import test_provider_slot_cache as fixtures


class ProviderPairOrderTests(unittest.TestCase):
    def run_once(self, mode, *, batch=False, observe=None):
        before,after,host,direct,lower,first,last=fixtures.ProviderTests().data()
        events=[]
        def event(name,value):
            events.append(name)
            return value
        c=LiveTaskCache.__new__(LiveTaskCache)
        c.ram_page_evidence=False
        c.batch_lookup=batch
        c.stop_event=threading.Event();c.samples=[];c.error=None;c.raw_bytes=0;c.interval=0
        snapshots=iter([before,after]);bulk=iter(['vmm-first','vmm-middle','vmm-last'])
        c._snapshot=lambda:(event('guest',next(snapshots)),{})
        values=[(dict(guest=0,host=0,size=3*4096),np.array([0,host[0][1],host[1][1]],dtype='<u8'))]
        c._bulk=lambda:event(next(bulk),values)
        c.provider=None;c.backend=None;c.lower_query=None
        if mode!='no-backend':
            queries=iter([np.array([r[2] for r in direct],dtype='<u8'),np.array([r[3] for r in direct],dtype='<u8')])
            c.backend=SimpleNamespace(ram_regions=lambda:event('maps',[]),
                offsets=lambda pfns:event('offsets',np.array([r[1] for r in direct],dtype='<u8')),
                query=lambda offsets:event('backend',next(queries)))
            lower_queries=iter([np.array([r[2] for r in lower],dtype='<u8'),np.array([r[3] for r in lower],dtype='<u8')])
            c.lower_layer=SimpleNamespace(offsets=lambda offsets:event('lower-offsets',offsets))
            c.lower_query=SimpleNamespace(query=lambda offsets:event('lower',next(lower_queries)))
        if mode in ('legacy','segmented'):
            records=[]
            for i,frame in enumerate((first,last)):
                value=copy.deepcopy(frame)
                value.update(host_start_ns=100+i*100,host_end_ns=120+i*100,
                    cpu_ns=10,elapsed_ns=20,metadata_bytes_read=128)
                records.append(value)
            records=iter(records)
            c.provider=SimpleNamespace(segmented=mode=='segmented',
                snapshot=lambda:event('provider',next(records)))
        # Stop after the first successfully persisted point.
        c.stop_event.wait=lambda timeout:c.stop_event.set()
        with tempfile.TemporaryDirectory() as name:
            c.directory=Path(name)
            with patch('provider_slot_cache.account_observed_provider',return_value=({},set())), \
                 patch('live_task_cache._batch_memory',side_effect=observe or (lambda q:dict(observed=True))) as memory:
                c._run()
            if batch:
                memory.assert_called_once_with([c.backend,c.lower_query])
                self.assertEqual(c.samples[0]['bpf_batch_memory'],dict(observed=True))
            else:memory.assert_not_called()
            self.assertIsNone(c.error,(c.directory/'failure.txt').read_text() if c.error else '')
            self.assertEqual(len(c.samples),1)
            self.last_sample=c.samples[0];self.last_thread_cpu=c.thread_total_cpu_ns
            point=c.samples[0];stages=point['host_stage_costs']
            expected=['guest_before','decode_before','vmm_first','vmm_middle','guest_after',
                      'decode_after','vmm_last','host_rows','vmm_account']
            if c.backend:
                expected+=['maps_before','backend_offsets_before','backend_query_first','lower_offsets',
                           'lower_query_first','backend_query_last','lower_query_last','maps_after',
                           'backend_offsets_after','backend_rows','backend_account','lower_rows','lower_account']
            if c.provider:expected+=['provider_first','provider_last','provider_account']
            self.assertEqual(set(stages),set(expected))
            cursor=point['host_sample_start_ns']
            for stage in stages.values():
                self.assertGreaterEqual(stage['host_start_ns'],cursor)
                self.assertEqual(stage['elapsed_ns'],stage['host_end_ns']-stage['host_start_ns'])
                self.assertGreaterEqual(stage['cpu_ns'],0)
                cursor=stage['host_end_ns']
            self.assertLessEqual(cursor,point['host_sample_start_ns']+point['host_elapsed_ns'])
            self.assertLessEqual(sum(s['cpu_ns'] for s in stages.values()),point['host_thread_cpu_ns'])
            if c.provider:
                self.assertEqual(c.samples[0]['provider_pair_elapsed_ns'],120)
                self.assertEqual(c.samples[0]['provider_pair_gap_ns'],80)
                self.assertTrue((c.directory/'pair-0000-provider.json').exists())
        return events

    def test_point_timer_excludes_output_writes(self):
        clocks={'cpu':100,'wall':1000}
        write=Path.write_bytes
        def delayed_write(path,data):
            clocks['cpu']+=10**9;clocks['wall']+=2*10**9
            return write(path,data)
        with patch('live_task_cache.time.thread_time_ns',side_effect=lambda:clocks['cpu']), \
             patch('live_task_cache.time.monotonic_ns',side_effect=lambda:clocks['wall']), \
             patch.object(Path,'write_bytes',delayed_write):
            self.run_once('segmented')
        self.assertEqual(self.last_sample['host_thread_cpu_ns'],0)
        self.assertEqual(self.last_sample['host_elapsed_ns'],0)
        self.assertGreater(self.last_thread_cpu,0)

    def test_batch_memory_observation_is_after_point_timer_and_keeps_query_order(self):
        clocks={'cpu':100,'wall':1000}
        def observe(queries):
            clocks['cpu']+=10**9;clocks['wall']+=2*10**9
            return dict(observed=True)
        with patch('live_task_cache.time.monotonic_ns',side_effect=lambda:clocks['wall']), \
             patch('live_task_cache.time.thread_time_ns',side_effect=lambda:clocks['cpu']):
            events=self.run_once('segmented',batch=True,observe=observe)
        self.assertEqual(self.last_sample['host_thread_cpu_ns'],0)
        self.assertEqual(self.last_sample['host_elapsed_ns'],0)
        self.assertGreater(self.last_thread_cpu,0)
        self.assertEqual(events,['guest','vmm-first','maps','offsets','backend','lower-offsets',
            'lower','provider','vmm-middle','provider','guest','backend','lower','vmm-last','maps','offsets'])

    def test_segmented_queries_immediately_surround_middle_vmm(self):
        self.assertEqual(self.run_once('segmented'),['guest','vmm-first','maps','offsets','backend',
            'lower-offsets','lower','provider','vmm-middle','provider','guest','backend','lower',
            'vmm-last','maps','offsets'])

    def test_legacy_provider_keeps_original_order(self):
        self.assertEqual(self.run_once('legacy'),['guest','vmm-first','provider','maps','offsets',
            'backend','lower-offsets','lower','vmm-middle','guest','backend','lower','vmm-last',
            'provider','maps','offsets'])

    def test_no_provider_keeps_original_order(self):
        self.assertEqual(self.run_once('no-provider'),['guest','vmm-first','maps','offsets','backend',
            'lower-offsets','lower','vmm-middle','guest','backend','lower','vmm-last','maps','offsets'])

    def test_no_backend_keeps_original_order(self):
        self.assertEqual(self.run_once('no-backend'),['guest','vmm-first','vmm-middle','guest','vmm-last'])


if __name__=='__main__':unittest.main()
