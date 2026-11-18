"""Synthetic files and clocks only; no real proc, BPF, guest or service."""
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from provider_slot_cache import ProviderCache,ROW,SEGMENT,MAGIC,PRESENT

class TimingTests(unittest.TestCase):
    def exercise(self,extended):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proc=root/'proc';proc.mkdir()
            (proc/'stat').write_text('7 (fake) '+' '.join(['0']*19+['123']))
            data=root/'slot';header=ROW.pack(MAGIC,2,4096,4096,0,4096,3,1)
            data.write_bytes(header+b''.join(SEGMENT.pack(2,i*512) for i in range(8)))
            pm=root/'pagemap';pm.write_bytes(struct.pack('<2Q',0,PRESENT|50))
            obj=ProviderCache.__new__(ProviderCache);obj.owner=None;obj.proc=proc;obj.start='123';obj.directory=root
            obj.pm=pm.open('rb');obj.slots=[(data,data.open('rb'),dict(count=8) if extended else None)]
            clock={'wall':1000,'cpu':500};events=[];pread=os.pread
            def read(fd,size,offset):
                name='pagemap' if fd==obj.pm.fileno() else 'metadata'
                events.append(name)
                clock['wall']+=100 if name=='pagemap' else 20
                clock['cpu']+=30 if name=='pagemap' else 10
                return pread(fd,size,offset)
            try:
                with patch('provider_slot_cache.os.pread',side_effect=read),patch('provider_slot_cache.time.monotonic_ns',side_effect=lambda:clock['wall']),patch('provider_slot_cache.time.thread_time_ns',side_effect=lambda:clock['cpu']):
                    result=obj.snapshot()
                self.assertEqual(events,['metadata','pagemap','metadata'])
                self.assertEqual(result['rows'][0]['before'],ROW.unpack(header))
                self.assertEqual(result['rows'][0]['pagemap'],[PRESENT|50])
                self.assertEqual(result['stage_timing_version'],1)
                costs=result['stage_costs']
                self.assertEqual(list(costs),['identity_before','metadata_before_read','header_decode','pagemap_read','metadata_after_read','record_build','identity_after'])
                self.assertTrue(all(v['calls']==1 for v in costs.values()))
                self.assertEqual(costs['pagemap_read'],dict(calls=1,elapsed_ns=100,cpu_ns=30))
                self.assertEqual(result['elapsed_ns'],140);self.assertEqual(result['cpu_ns'],50)
                for key in ('elapsed_ns','cpu_ns'):self.assertEqual(sum(v[key] for v in costs.values()),result[key])
                self.assertEqual(result['metadata_bytes_read'],384 if extended else 128)
                obj.close()
                saved=json.loads((root/'final.json').read_text())
                self.assertIn('stage_costs',saved)
                self.assertEqual(sum(v['elapsed_ns'] for v in saved['stage_costs'].values()),saved['elapsed_ns'])
            finally:
                obj.pm.close()
                for _,f,_ in obj.slots:f.close()
    def test_segmented(self):self.exercise(True)
    def test_legacy(self):self.exercise(False)

if __name__=='__main__':unittest.main()
