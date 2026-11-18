"""Fake RAM vectors and temporary files only; never reads live /proc or devices."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch

import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import ram_page_evidence as evidence
from kernel_cache_metadata import PRESENT,SWAPPED
from test_kernel_cache_metadata import dump
from fast_cache_metadata import decode_array,join_arrays
from live_task_cache import LiveTaskCache
import run_live_child_calibration as entry


class RamPageEvidenceTests(unittest.TestCase):
    def test_inside_entry_forwards_opt_in_without_starting_a_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);artifacts=root/'.artifacts';artifacts.mkdir();kernel=artifacts/'kernel';kernel.write_bytes(b'fake')
            out=artifacts/'output';argv=['entry','--inside','--unit','fake','--output',str(out),
                '--kernel',str(kernel),'--guests','2','--dynamic-file','--ram-page-evidence']
            with patch.object(entry,'ROOT',root),patch.object(sys,'argv',argv),patch.object(entry,'inside',return_value=0) as inside:
                self.assertEqual(entry.main(),0)
            inside.assert_called_once_with(out,'fake',2,kernel=kernel,dynamic_file=True,ram_page_evidence=True,host_backing_evidence=False)

    def meta(self):
        return evidence.manifest('a','boot',dict(sandbox_id='a',pid=101,start_time_ticks=3,cgroup='/owned/vmm-a'),
            [dict(guest=0,host=0x10000,size=4*4096)])

    def triple(self,meta,entries):
        return [(dict(guest=s['guest'],host=s['host'],size=s['size']),np.array(entries[s['row_offset']:s['row_offset']+s['pages']],dtype='<u8')) for s in meta['slots']]

    def test_pack_noncontiguous_guest_slots_and_project_exact_original_rows(self):
        meta=evidence.manifest('a','boot',dict(sandbox_id='a'),[
            dict(guest=1<<32,host=0x20000,size=4096),dict(guest=0,host=0x10000,size=8192)])
        triples=[self.triple(meta,[PRESENT|10,PRESENT|11,PRESENT|12]) for _ in range(3)]
        rows=evidence.pack(meta,*triples)
        projected=evidence.project(meta,rows,np.array([0,1<<20],dtype='<u8'))
        self.assertEqual(projected.tolist(),[[0,*([PRESENT|10]*3)],[1<<20,*([PRESENT|12]*3)]])
        with self.assertRaisesRegex(ValueError,'outside attested RAM'):evidence.project(meta,rows,np.array([4],dtype='<u8'))

    def test_physical_union_aliases_mapping_changes_swap_and_nonresident(self):
        rows=np.array([[PRESENT|100]*3,[PRESENT|100,PRESENT|100|(1<<56),PRESENT|100],
                       [PRESENT|102,PRESENT|103,PRESENT|103],[0]*3,[SWAPPED|9]*3],dtype='<u8')
        summary,_=evidence.physical_sets(rows)
        self.assertEqual(summary['stable_host_physical_union_bytes'],4096)
        self.assertEqual(summary['stable_resident_guest_pages'],2)
        self.assertEqual(summary['changed_guest_pages'],1)
        self.assertEqual(summary['stable_swapped_guest_pages'],1)
        self.assertEqual(summary['stable_nonresident_guest_pages'],1)
        with self.assertRaisesRegex(ValueError,'PFNs hidden'):evidence.physical_sets(np.array([[PRESENT]*3],dtype='<u8'))

    def test_slot_change_or_overlap_does_not_fall_back(self):
        meta=self.meta();values=self.triple(meta,[0]*4);wrong=[(dict(values[0][0],host=0x20000),values[0][1])]
        with self.assertRaisesRegex(ValueError,'slot identity'):evidence.pack(meta,values,wrong,values)
        with self.assertRaisesRegex(ValueError,'overlapping'):
            evidence.manifest('a','boot',dict(sandbox_id='a'),[dict(guest=0,host=0x10000,size=8192),dict(guest=4096,host=0x20000,size=4096)])

    def test_existing_budget_fails_before_writing_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix=Path(tmp)/'pair-0000';meta=self.meta();values=self.triple(meta,[PRESENT|100]*4)
            with self.assertRaisesRegex(ValueError,'budget'):evidence.save(prefix,meta,values,values,values,remaining_bytes=96)
            self.assertFalse(Path(str(prefix)+'-ram.bin').exists())
            evidence.save(prefix,meta,values,values,values,remaining_bytes=97)
            with self.assertRaises(FileExistsError):evidence.save(prefix,meta,values,values,values,remaining_bytes=97)

    def fixture(self,live):
        meta=self.meta();write=lambda p,v:p.write_text(json.dumps(v))
        write(live/'marker.json',dict(sandbox_id='a',boot_id='boot'))
        write(live/'result.json',dict(sandbox_id='a',guest_boot_id='boot',host_identity=meta['host_identity'],samples=2))
        write(live/'ram-page-manifest.json',meta)
        entries=[PRESENT|100,PRESENT|101,PRESENT|101,SWAPPED|9]
        values=self.triple(meta,entries);full=evidence.pack(meta,values,values,values);samples=[]
        # The newly tagged cache page is stable in host RAM, but its guest
        # lifetime is not confirmed across the pair. Do not call it non-cache.
        before=dump([(0,20)]);after=dump([(0,20),(2,22)])
        selected=evidence.project(meta,full,np.array([0],dtype='<u8'))
        for point in range(2):
            prefix=live/f'pair-{point:04}';selected.tofile(Path(str(prefix)+'-host.bin'))
            Path(str(prefix)+'-before.bin').write_bytes(before);Path(str(prefix)+'-after.bin').write_bytes(after)
            receipt=evidence.save(prefix,meta,values,values,values,remaining_bytes=1000)
            samples.append(dict(point=point,timestamp_ns=point,ram_page_evidence=receipt,
                **join_arrays(decode_array(before),decode_array(after),selected)))
        (live/'samples.jsonl').write_text(''.join(json.dumps(s)+'\n' for s in samples))
        return meta

    def test_report_separates_ram_cache_subset_and_not_confirmed_as_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            live=Path(tmp);self.fixture(live);value=evidence.analyze(live)
            self.assertEqual(value['status'],'OBSERVED')
            for point in value['points']:
                self.assertEqual(point['stable_host_physical_union_bytes'],8192)
                self.assertEqual(point['confirmed_guest_cache_host_union_bytes'],4096)
                self.assertEqual(point['ram_not_confirmed_as_cache_host_bytes'],4096)
                self.assertEqual(point['guest_cache_unconfirmed_pages'],1)
            self.assertEqual(value['cache_page_key'],['sandbox_id','guest_boot_id','guest_pfn','lifetime_tag'])

    def test_missing_old_evidence_is_unavailable_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            live=Path(tmp);self.fixture(live);(live/'ram-page-manifest.json').unlink()
            value=evidence.analyze(live)
            self.assertEqual(value['status'],'UNAVAILABLE');self.assertIsNone(value['stable_ram_physical_union_bytes'])

    def test_wrong_identity_incomplete_and_conflicting_raw_are_errors(self):
        for mode in ('identity','missing','conflict'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                live=Path(tmp);self.fixture(live)
                if mode=='identity':
                    path=live/'ram-page-manifest.json';v=json.loads(path.read_text());v['host_identity']['pid']=999;path.write_text(json.dumps(v))
                elif mode=='missing':(live/'pair-0000-ram.bin').unlink()
                else:
                    path=live/'pair-0000-ram.bin';rows=np.fromfile(path,dtype='<u8');rows[0]=PRESENT|999;rows.tofile(path)
                with self.assertRaises((ValueError,FileNotFoundError)):evidence.analyze(live)

    def test_collector_opt_in_reuses_exactly_three_bulk_reads_and_counts_bytes(self):
        for enabled in (False,True):
            with self.subTest(enabled=enabled),tempfile.TemporaryDirectory() as tmp:
                c=LiveTaskCache.__new__(LiveTaskCache);meta=self.meta()
                c.directory=Path(tmp);c.ram_page_evidence=enabled;c.ram_manifest=meta;c.ram_evidence_bytes=0
                c.samples=[];c.raw_bytes=0;c.error=None;c.provider=c.backend=c.lower_query=None;c.interval=1
                c.batch_lookup=False;c.stop_event=threading.Event();c.stop_event.wait=lambda seconds:c.stop_event.set()
                metadata=dump([(0,20)])
                c._snapshot=Mock(return_value=(metadata,dict(publication=1,guest_start_ns=1,guest_end_ns=2)))
                c._bulk=Mock(return_value=self.triple(meta,[PRESENT|100]*4))
                c._run()
                self.assertIsNone(c.error);self.assertEqual(c._bulk.call_count,3)
                self.assertEqual(len(c.samples),1)
                self.assertEqual((c.directory/'pair-0000-ram.bin').exists(),enabled)
                self.assertEqual('ram_page_evidence' in c.samples[0],enabled)
                self.assertEqual(c.ram_evidence_bytes,96 if enabled else 0)
                if enabled:self.assertIn('ram_evidence_save',c.samples[0]['host_stage_costs'])

    def test_failed_ram_analysis_never_writes_collector_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=LiveTaskCache.__new__(LiveTaskCache);c.directory=Path(tmp);c.stop_event=Mock();c.thread=None
            c.provider=c.backend=c.lower_query=None;c.file_cache_path=None;c.error=None;c.capture=Mock()
            c.ram_page_evidence=True;c.ram_evidence_bytes=96;c.samples=[dict(timestamp_ns=1),dict(timestamp_ns=2)]
            c.raw_bytes=100;c.interval=1;c.sid='a';c.vm=self.meta()['host_identity'];c.thread_total_cpu_ns=0
            c.bootstrap=dict(boot_id='boot',kernel='fake',locked_anonymous_bytes=4096)
            c.provider_cache=c.lower_cache=False
            with patch.object(evidence,'analyze',side_effect=ValueError('RAM evidence differs')):
                with self.assertRaisesRegex(ValueError,'RAM evidence differs'):c.close()
            self.assertFalse((c.directory/'result.json').exists());c.capture.close.assert_called_once()


if __name__=='__main__':unittest.main()
