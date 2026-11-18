"""Only fake proc trees, simulated pagemap entries and mocked workflow calls."""
import copy
import inspect
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import vmm_page_evidence as e
import ram_page_evidence as ram
import run_live_child_calibration as entry
from live_task_cache import LiveTaskCache
from kernel_cache_metadata import PRESENT, SWAPPED
from test_kernel_cache_metadata import dump


class VmmPageEvidenceTests(unittest.TestCase):
    def fixture(self,root):
        proc=root/'proc';p=proc/'101';p.mkdir(parents=True)
        values=['0']*20;values[19]='3';(p/'stat').write_text('101 (fake) '+' '.join(values))
        (p/'cgroup').write_text('0::/owned.slice/vmm-a\n');(p/'exe').symlink_to('/fake/vmm')
        boot=proc/'sys/kernel/random/boot_id';boot.parent.mkdir(parents=True);boot.write_text('host-boot\n')
        vm=dict(sandbox_id='sid',pid=101,start_time_ticks=3,cgroup='/owned.slice/vmm-a',executable='/fake/vmm')
        meta=ram.manifest('sid','guest-boot',vm,[dict(guest=0,host=0x10000,size=4*4096)])
        maps='\n'.join(['10000-14000 rw-p 0 00:00 0',
            '20000-22000 rw-p 0 00:00 0 [heap]',
            '23000-24000 rw-s 0 00:01 7 /memfd:dup (deleted)',
            '30000-31000 rw-s 0 00:10 69 anon_inode:unknown'])+'\n'
        (p/'maps').write_text(maps);(p/'smaps').write_text(self.smaps(maps))
        with (p/'pagemap').open('wb') as f:f.truncate(0x40000//4096*8)
        self.put(p,0x20000,[PRESENT|100,PRESENT|200]);self.put(p,0x23000,[PRESENT|200])
        triples=[(dict(guest=0,host=0x10000,size=4*4096),np.array([PRESENT|100,PRESENT|101,PRESENT|100,0],dtype='<u8'))]
        return proc,p,meta,triples

    def smaps(self,maps):
        return ''.join(line+'\nPss: 4 kB\nRss: 4 kB\nVmFlags: rd wr '+('pf' if line.startswith('30000-') else 'mr mw ac')+'\n'
                       for line in maps.splitlines())

    def put(self,p,address,entries):
        with (p/'pagemap').open('r+b') as f:
            f.seek(address//4096*8);f.write(np.array(entries,dtype='<u8').tobytes())

    def collect(self,root,*,mutate=None,remaining=100000):
        proc,p,meta,triples=self.fixture(root);live=root/'live';live.mkdir()
        collector=e.VmmPageEvidence(meta,'host-boot',proc_root=proc);collector.begin(remaining_bytes=remaining)
        with (p/'pagemap').open('rb') as pm:
            for column in range(3):
                if mutate:mutate(p,column)
                collector.read(column,pm)
        prefix=live/'pair-0000'
        r=ram.save(prefix,meta,triples,triples,triples,remaining_bytes=100000)
        receipt=collector.save(prefix,triples,triples,triples,remaining_bytes=remaining)
        return live,meta,r,receipt,json.loads((live/'pair-0000-vmm.json').read_text())

    def test_full_maps_includes_inode_zero_and_special_mappings(self):
        with tempfile.TemporaryDirectory() as tmp:
            live,meta,r,receipt,body=self.collect(Path(tmp));s=body['summary']
            self.assertEqual(len(e.regions(body['maps_before'])),4)
            self.assertEqual(len(s['nonram_segments']),3)
            self.assertEqual(s['unknown_nonram_virtual_pages'],1)
            self.assertEqual(s['coverage'],'PARTIAL')
            self.assertEqual((live/receipt['nonram_path']).stat().st_size,3*3*8)
            self.assertIsNone(s['complete_vmm_host_memory_bytes'])

    def test_physical_union_deduplicates_ram_nonram_and_two_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,_,_,_,body=self.collect(Path(tmp));s=body['summary']
            self.assertEqual(s['ram_stable_host_union_bytes'],8192)
            self.assertEqual(s['ordinary_nonram_stable_host_union_bytes'],8192)
            self.assertEqual(s['ram_nonram_physical_overlap_bytes'],4096)
            self.assertEqual(s['ordinary_vmm_stable_host_union_bytes'],12288)

    def test_changed_hidden_swapped_and_nonresident_are_distinct(self):
        cases=[([PRESENT|100,PRESENT|201,PRESENT|100],'changed_pagemap_virtual_pages'),
               ([PRESENT]*3,'hidden_pfn_virtual_pages'),([SWAPPED|9]*3,'stable_swapped_virtual_pages'),
               ([0]*3,'stable_nonresident_virtual_pages')]
        for entries,field in cases:
            with self.subTest(field=field),tempfile.TemporaryDirectory() as tmp:
                def mutate(p,c):self.put(p,0x20000,[entries[c],PRESENT|200])
                *_,body=self.collect(Path(tmp),mutate=mutate)
                self.assertEqual(body['summary']['nonram_segments'][0][field],1)
                self.assertEqual(body['summary']['ordinary_vmm_stable_host_union_bytes'],12288)

    def test_mapping_change_and_new_end_mapping_remain_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            def mutate(p,column):
                if column==2:
                    maps=(p/'maps').read_text().replace('[heap]','[changed]')+'40000-41000 rw-p 0 00:00 0\n'
                    (p/'maps').write_text(maps);(p/'smaps').write_text(self.smaps(maps))
            *_,body=self.collect(Path(tmp),mutate=mutate);s=body['summary']
            self.assertEqual(s['nonram_segments'][0]['status'],'MAPPING_CHANGED')
            self.assertEqual(s['added_or_changed_end_mapping_virtual_bytes'],3*4096)
            self.assertEqual(s['unknown_nonram_virtual_pages'],3)

    def test_partial_vma_ram_partition_and_missing_ram_fail(self):
        before=e.metadata('1000-5000 rw-p 0 00:00 0\n',self.smaps('1000-5000 rw-p 0 00:00 0\n'))
        segments,count=e.layout(before,[dict(host=0x2000,size=8192)])
        self.assertEqual([s['kind'] for s in segments],['NON_RAM','RAM','NON_RAM'])
        self.assertEqual(count,2)
        with self.assertRaisesRegex(ValueError,'cover registered RAM'):e.layout(before,[dict(host=0x9000,size=4096)])

    def test_budget_and_existing_paths_rejected_before_new_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proc,p,meta,triples=self.fixture(root)
            c=e.VmmPageEvidence(meta,'host-boot',proc_root=proc)
            with patch.object(e.np,'empty') as alloc:
                with self.assertRaisesRegex(ValueError,'budget'):c.begin(remaining_bytes=1)
                alloc.assert_not_called()
            c.begin(remaining_bytes=100000)
            with (p/'pagemap').open('rb') as pm:
                for i in range(3):c.read(i,pm)
            prefix=root/'pair-0000'
            with self.assertRaisesRegex(ValueError,'budget'):c.save(prefix,triples,triples,triples,remaining_bytes=1)
            self.assertFalse((root/'pair-0000-vmm-nonram.bin').exists())
            c.save(prefix,triples,triples,triples,remaining_bytes=100000)
            with self.assertRaises(FileExistsError):c.save(prefix,triples,triples,triples,remaining_bytes=100000)

    def test_pid_cgroup_boot_or_executable_change_fails(self):
        for mode in ('pid','cgroup','boot','exe'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                proc,p,meta,_=self.fixture(Path(tmp));c=e.VmmPageEvidence(meta,'host-boot',proc_root=proc)
                if mode=='pid':(p/'stat').write_text((p/'stat').read_text()[:-1]+'4')
                elif mode=='cgroup':(p/'cgroup').write_text('0::/other\n')
                elif mode=='boot':(proc/'sys/kernel/random/boot_id').write_text('other')
                else:(p/'exe').unlink();(p/'exe').symlink_to('/other')
                with self.assertRaisesRegex(ValueError,'identity changed'):c.begin(remaining_bytes=100000)

    def test_ram_mapping_change_retains_failure_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proc,p,meta,triples=self.fixture(root);c=e.VmmPageEvidence(meta,'host-boot',proc_root=proc)
            c.begin(remaining_bytes=100000)
            with (p/'pagemap').open('rb') as pm:
                for i in range(3):c.read(i,pm)
            (p/'maps').write_text((p/'maps').read_text().replace('10000-14000 rw-p','10000-14000 r--p'))
            with self.assertRaisesRegex(ValueError,'retained'):c.save(root/'pair-0000',triples,triples,triples,remaining_bytes=100000)
            body=json.loads((root/'pair-0000-vmm.json').read_text())
            self.assertEqual(body['summary']['status'],'FAIL')

    def test_end_identity_change_retains_error_and_never_returns_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proc,p,meta,triples=self.fixture(root);c=e.VmmPageEvidence(meta,'host-boot',proc_root=proc)
            c.begin(remaining_bytes=100000)
            with (p/'pagemap').open('rb') as pm:
                for i in range(3):c.read(i,pm)
            (p/'cgroup').write_text('0::/other\n')
            with self.assertRaisesRegex(ValueError,'retained'):c.save(root/'pair-0000',triples,triples,triples,remaining_bytes=100000)
            body=json.loads((root/'pair-0000-vmm.json').read_text())
            self.assertEqual(body['summary']['status'],'FAIL')
            self.assertIn('identity changed',body['summary']['error'])

    def test_missing_or_special_smaps_does_not_become_zero_occupancy(self):
        for flags in ('pf','io','mm','ht'):
            r=e.metadata('1000-2000 rw-p 0 00:00 0\n','1000-2000 rw-p 0 00:00 0\nPss: 0 kB\nVmFlags: '+flags+'\n')[0]
            self.assertFalse(e.supported(r))
        r=e.metadata('1000-2000 rw-p 0 00:00 0\n','')[0];self.assertFalse(e.supported(r))
        with self.assertRaises(ValueError):e.regions('not a maps row\n')

    def test_short_reads_and_wrong_column_order_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc,p,meta,_=self.fixture(Path(tmp));c=e.VmmPageEvidence(meta,'host-boot',proc_root=proc)
            c.begin(remaining_bytes=100000)
            with (p/'pagemap').open('rb') as pm:
                with self.assertRaisesRegex(ValueError,'first/middle/last'):c.read(1,pm)
                with patch.object(e.os,'pread',return_value=b''):
                    with self.assertRaisesRegex(ValueError,'short'):c.read(0,pm)

    def offline_fixture(self,root):
        live,meta,r,receipt,body=self.collect(root)
        (live/'ram-page-manifest.json').write_text(json.dumps(meta))
        (live/'vmm-child-binding.json').write_text(json.dumps(dict(vmm_binding=dict(boot_id='host-boot'))))
        collector=dict(host_identity=meta['host_identity'],sandbox_id='sid',guest_boot_id='guest-boot',samples=1)
        (live/'result.json').write_text(json.dumps(collector))
        (live/'samples.jsonl').write_text(json.dumps(dict(point=0,vmm_page_evidence=receipt,ram_page_evidence=r))+'\n')
        return live

    def test_offline_replay_and_old_evidence_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            live=self.offline_fixture(Path(tmp));v=e.analyze(live)
            self.assertEqual(v['status'],'OBSERVED_EVIDENCE');self.assertEqual(v['coverage'],'PARTIAL')
            (live/'samples.jsonl').write_text('{}\n');v=e.analyze(live)
            self.assertEqual(v['status'],'UNAVAILABLE');self.assertIsNone(v['complete_vmm_host_memory_bytes'])

    def test_offline_conflict_truncation_or_wrong_timestamps_fail(self):
        for mode in ('raw','truncated','trailing_ram','trailing_nonram','summary','boot','timestamp','collector'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                live=self.offline_fixture(Path(tmp));p=live/'pair-0000-vmm.json';v=json.loads(p.read_text())
                if mode=='raw':
                    a=np.fromfile(live/'pair-0000-vmm-nonram.bin',dtype='<u8');a[0]=PRESENT|999;a.tofile(live/'pair-0000-vmm-nonram.bin')
                elif mode=='truncated':(live/'pair-0000-ram.bin').write_bytes(b'')
                elif mode.startswith('trailing_'):
                    target=live/('pair-0000-ram.bin' if mode=='trailing_ram' else 'pair-0000-vmm-nonram.bin')
                    with target.open('ab') as f:f.write(b'x')
                elif mode=='summary':v['summary']['ordinary_vmm_stable_host_union_bytes']=0
                elif mode=='boot':v['host_boot_id']='other'
                elif mode=='timestamp':v['reads'][0]['column']=1
                else:
                    c=json.loads((live/'result.json').read_text());c['sandbox_id']='other';(live/'result.json').write_text(json.dumps(c))
                if mode in ('summary','boot','timestamp'):p.write_text(json.dumps(v))
                with self.assertRaises(ValueError):e.analyze(live)

    def test_default_off_and_new_usage_requires_explicit_ram(self):
        self.assertFalse(inspect.signature(LiveTaskCache).parameters['vmm_page_evidence'].default)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'new'
            with self.assertRaisesRegex(ValueError,'explicit RAM'):LiveTaskCache(out,'fake',Mock(),vmm_page_evidence=True)
            self.assertFalse(out.exists())

    def test_inside_flag_forwarding_without_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=root/'.artifacts';a.mkdir();kernel=a/'kernel';kernel.write_bytes(b'fake')
            argv=['entry','--inside','--unit','fake','--output',str(a/'out'),'--kernel',str(kernel),
                  '--guests','2','--ram-page-evidence','--vmm-page-evidence']
            with patch.object(entry,'ROOT',root),patch.object(sys,'argv',argv),patch.object(entry,'inside',return_value=0) as inside:
                self.assertEqual(entry.main(),0)
            self.assertTrue(inside.call_args.kwargs['vmm_page_evidence'])

    def test_collector_integration_adds_no_ram_reads_and_propagates_costs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proc,p,meta,triples=self.fixture(root);live=root/'live';live.mkdir()
            c=LiveTaskCache.__new__(LiveTaskCache);c.directory=live;c.ram_page_evidence=True;c.ram_manifest=meta
            c.ram_evidence_bytes=c.vmm_evidence_bytes=c.raw_bytes=0;c.samples=[];c.error=None
            c.vmm_evidence=e.VmmPageEvidence(meta,'host-boot',proc_root=proc)
            c.provider=c.backend=c.lower_query=None;c.batch_lookup=False;c.interval=1
            c.stop_event=threading.Event();c.stop_event.wait=lambda seconds:c.stop_event.set()
            c._snapshot=Mock(return_value=(dump([(0,20)]),dict(publication=1,guest_start_ns=1,guest_end_ns=2)))
            c._bulk=Mock(return_value=triples)
            with (p/'pagemap').open('rb') as c.pm:c._run()
            self.assertIsNone(c.error);self.assertEqual(c._bulk.call_count,3)
            point=c.samples[0];self.assertIn('vmm_evidence_save',point['host_stage_costs'])
            self.assertEqual(c.vmm_evidence_bytes,point['vmm_page_evidence']['raw_bytes'])
            self.assertEqual(c.raw_bytes,sum(f.stat().st_size for f in live.iterdir() if f.suffix!='.jsonl'))

    def test_failed_vmm_replay_cannot_write_collector_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=LiveTaskCache.__new__(LiveTaskCache);c.directory=Path(tmp);c.stop_event=Mock();c.thread=None
            c.provider=c.backend=c.lower_query=None;c.file_cache_path=None;c.error=None;c.capture=Mock()
            c.ram_page_evidence=False;c.vmm_page_evidence=True;c.vmm_evidence_bytes=100
            c.samples=[dict(timestamp_ns=1),dict(timestamp_ns=2)];c.raw_bytes=100;c.interval=1
            c.sid='sid';c.vm={};c.thread_total_cpu_ns=0;c.provider_cache=c.lower_cache=False
            c.bootstrap=dict(boot_id='boot',kernel='fake',locked_anonymous_bytes=4096)
            with patch.object(e,'analyze',side_effect=ValueError('bad VMM evidence')):
                with self.assertRaisesRegex(ValueError,'bad VMM evidence'):c.close()
            self.assertFalse((c.directory/'result.json').exists());c.capture.close.assert_called_once()


if __name__=='__main__':unittest.main()
