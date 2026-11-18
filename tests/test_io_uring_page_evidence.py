"""Fake proc/sys trees only; no kernel queries, services or real processes."""
import inspect
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import vmm_page_evidence as e
import ram_page_evidence as ram
import run_live_child_calibration as entry
from live_task_cache import LiveTaskCache
from kernel_cache_metadata import PRESENT, SWAPPED
import test_vmm_page_evidence as base
from test_kernel_cache_metadata import dump


class IoUringEvidenceTests(unittest.TestCase):
    def fixture(self,root):
        self.helper=base.VmmPageEvidenceTests()
        proc,p,meta,triples=self.helper.fixture(root)
        extra=('40000-42000 rw-s 0 00:10 69 anon_inode:[io_uring]\n'
               '42000-44000 rw-s 8000000 00:10 69 anon_inode:[io_uring]\n')
        maps=(p/'maps').read_text()+extra
        smaps=self.helper.smaps((p/'maps').read_text())+''.join(
            line+'\nPss: 4 kB\nRss: 8 kB\nVmFlags: rd wr sh mr mw ms de sd mm\n' for line in extra.splitlines())
        (p/'maps').write_text(maps);(p/'smaps').write_text(smaps)
        with (p/'pagemap').open('r+b') as f:f.truncate(0x50000//4096*8)
        self.helper.put(p,0x40000,[PRESENT|100,PRESENT|300])
        self.helper.put(p,0x42000,[PRESENT|300,PRESENT|400])
        (proc/'sys/kernel/osrelease').write_text('6.19.9+\n')
        (proc/'version').write_text('Fake Linux build for offline testing\n')
        sysroot=root/'sys';(sysroot/'kernel').mkdir(parents=True)
        (sysroot/'kernel/notes').write_bytes(struct.pack('<III',4,4,3)+b'GNU\0'+b'\x01\x02\x03\x04')
        return proc,p,meta,triples,sysroot

    def collect(self,root,*,enabled=True,mutate=None):
        proc,p,meta,triples,sysroot=self.fixture(root);live=root/'live';live.mkdir()
        c=e.VmmPageEvidence(meta,'host-boot',proc_root=proc,sys_root=sysroot,io_uring=enabled)
        c.begin(remaining_bytes=100000)
        with (p/'pagemap').open('rb') as pm:
            for i in range(3):
                if mutate:mutate(p,i)
                c.read(i,pm)
        prefix=live/'pair-0000';r=ram.save(prefix,meta,triples,triples,triples,remaining_bytes=100000)
        receipt=c.save(prefix,triples,triples,triples,remaining_bytes=100000)
        body=json.loads((live/'pair-0000-vmm.json').read_text())
        (live/'ram-page-manifest.json').write_text(json.dumps(meta))
        (live/'vmm-child-binding.json').write_text(json.dumps(dict(vmm_binding=dict(boot_id='host-boot'))))
        collector=dict(host_identity=meta['host_identity'],sandbox_id='sid',guest_boot_id='guest-boot',samples=1)
        (live/'result.json').write_text(json.dumps(collector))
        (live/'samples.jsonl').write_text(json.dumps(dict(point=0,ram_page_evidence=r,vmm_page_evidence=receipt))+'\n')
        return live,body

    def test_only_named_shared_known_offsets_and_no_special_flags(self):
        maps='1000-2000 rw-s 8000000 00:10 69 anon_inode:[io_uring]\n'
        smaps=maps+'Pss: 4 kB\nVmFlags: rd wr sh mm\n'
        r=e.metadata(maps,smaps)[0]
        self.assertFalse(e.supported(r));self.assertTrue(e.supported(r,io_uring=True))
        changes=[dict(mapping_path='anon_inode:other'),dict(offset=0x18000000),dict(permissions='rw-p'),
                 dict(inode=0),dict(device=0),dict(device=0x800),dict(smaps_status='NOT_OBSERVED'),
                 dict(vm_flags=['rd','wr','sh'])]
        changes += [dict(vm_flags=r['vm_flags']+[flag]) for flag in ('pf','io','ht')]
        for change in changes:
            with self.subTest(change=change):self.assertFalse(e.supported(dict(r,**change),io_uring=True))

    def test_io_alias_ram_and_other_nonram_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            live,b=self.collect(Path(tmp));s=b['summary'];io=s['io_uring']
            self.assertEqual(b['version'],e.IO_VERSION)
            self.assertEqual((io['listed_segments'],io['queried_segments']),(2,2))
            self.assertEqual(io['stable_host_union_bytes'],3*4096)
            self.assertEqual(io['ram_overlap_bytes'],4096)
            self.assertEqual(io['other_nonram_overlap_bytes'],4096)
            self.assertEqual(s['ordinary_nonram_stable_host_union_bytes'],4*4096)
            self.assertEqual(s['ordinary_vmm_stable_host_union_bytes'],5*4096)
            self.assertIsNone(s['complete_vmm_host_memory_bytes'])
            replay=e.analyze(live)
            self.assertEqual(replay['version'],e.IO_VERSION)
            self.assertEqual(replay['points'][0],dict(point=0,**s))
            self.assertEqual(replay['host_kernel_identity']['gnu_build_id'],'01020304')

    def test_default_does_not_read_kernel_identity_or_query_mixed_maps(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(e,'kernel_identity',side_effect=AssertionError('must not read host identity')):
            live,b=self.collect(Path(tmp),enabled=False)
            self.assertEqual(b['version'],e.VERSION)
            self.assertNotIn('host_kernel_identity',b);self.assertNotIn('io_uring',b['summary'])
            self.assertEqual(b['summary']['unknown_nonram_virtual_pages'],5)
            self.assertEqual((live/'pair-0000-vmm-nonram.bin').stat().st_size,3*24)
            self.assertEqual(e.analyze(live)['version'],e.VERSION)

    def test_changed_hidden_nonresident_and_swapped_are_kept_separate(self):
        cases=[([PRESENT|100,PRESENT|500,PRESENT|100],'changed_pagemap_virtual_pages'),
               ([PRESENT]*3,'hidden_pfn_virtual_pages'),([0]*3,'stable_nonresident_virtual_pages'),
               ([SWAPPED|9]*3,'stable_swapped_virtual_pages')]
        for values,key in cases:
            with self.subTest(key=key),tempfile.TemporaryDirectory() as tmp:
                def mutate(p,i):self.helper.put(p,0x40000,[values[i],PRESENT|300])
                live,b=self.collect(Path(tmp),mutate=mutate)
                segment=b['summary']['nonram_segments'][-2]
                self.assertEqual(segment[key],1)
                self.assertEqual(segment['unknown_virtual_pages'],1 if key in ('changed_pagemap_virtual_pages','hidden_pfn_virtual_pages') else 0)
                self.assertEqual(e.analyze(live)['points'][0]['io_uring']['unknown_virtual_pages'],segment['unknown_virtual_pages'])

    def test_changed_inode_and_disallowed_end_flags_remain_unknown(self):
        for mode in ('inode','flags'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                def mutate(p,i):
                    if i!=2:return
                    if mode=='inode':
                        for name in ('maps','smaps'):
                            path=p/name;path.write_text(path.read_text().replace('40000-42000 rw-s 0 00:10 69','40000-42000 rw-s 0 00:10 70'))
                    else:
                        path=p/'smaps';path.write_text(path.read_text().replace('ms de sd mm','ms de sd mm pf',1))
                live,b=self.collect(Path(tmp),mutate=mutate)
                self.assertEqual(b['summary']['io_uring']['unknown_virtual_pages'],2)
                self.assertIsNone(b['summary']['nonram_segments'][-2]['physical_union_bytes'])
                self.assertEqual(e.analyze(live)['coverage'],'PARTIAL')

    def test_original_strict_budget_includes_new_rows_and_kernel_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proc,p,meta,t,sysroot=self.fixture(root)
            c=e.VmmPageEvidence(meta,'host-boot',proc_root=proc,sys_root=sysroot,io_uring=True)
            with patch.object(e.np,'empty') as alloc:
                with self.assertRaisesRegex(ValueError,'budget'):c.begin(remaining_bytes=1)
                alloc.assert_not_called()
            c.begin(remaining_bytes=100000);self.assertEqual(c.rows.nbytes,7*24)
            with (p/'pagemap').open('rb') as pm:
                for i in range(3):c.read(i,pm)
            with self.assertRaisesRegex(ValueError,'budget'):c.save(root/'no-space',t,t,t,remaining_bytes=1)
            self.assertFalse((root/'no-space-vmm.json').exists())
            self.assertFalse((root/'no-space-vmm-nonram.bin').exists())

    def test_new_policy_and_kernel_identity_must_be_explicit_and_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            live,b=self.collect(Path(tmp))
            for change in (dict(version=e.VERSION),dict(io_uring_policy='unknown'),dict(host_kernel_identity={})):
                bad=dict(b,**change)
                with self.subTest(change=change),self.assertRaises(ValueError):e.io_policy(bad)
            receipt=json.loads((live/'samples.jsonl').read_text());receipt['vmm_page_evidence']['version']=e.VERSION
            (live/'samples.jsonl').write_text(json.dumps(receipt)+'\n')
            with self.assertRaisesRegex(ValueError,'conflict'):e.analyze(live)

    def test_kernel_notes_are_parsed_not_hashed_and_missing_identity_fails(self):
        valid=struct.pack('<III',4,4,3)+b'GNU\0abcd'
        self.assertEqual(e.kernel_build_id(valid),'61626364')
        for raw in (b'',b'x',valid[:-1],valid+valid):
            with self.subTest(raw=raw),self.assertRaises(ValueError):e.kernel_build_id(raw)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proc,p,meta,t,sysroot=self.fixture(root)
            (sysroot/'kernel/notes').unlink()
            with self.assertRaises(FileNotFoundError):e.VmmPageEvidence(meta,'host-boot',proc_root=proc,sys_root=sysroot,io_uring=True)

    def test_new_option_default_off_and_preconditions_do_not_create_output(self):
        self.assertFalse(inspect.signature(LiveTaskCache).parameters['io_uring_page_evidence'].default)
        for kwargs in (dict(io_uring_page_evidence=True),dict(io_uring_page_evidence=True,vmm_page_evidence=True),
                       dict(io_uring_page_evidence=True,vmm_page_evidence=True,ram_page_evidence=True,session='restored')):
            with tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp)/'not-created'
                with self.assertRaises(ValueError):LiveTaskCache(out,'fake',Mock(),**kwargs)
                self.assertFalse(out.exists())

    def test_private_entry_flag_forwarding_uses_no_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=root/'.artifacts';a.mkdir();kernel=a/'kernel';kernel.write_bytes(b'fake')
            argv=['entry','--inside','--unit','fake','--output',str(a/'out'),'--kernel',str(kernel),
                  '--ram-page-evidence','--vmm-page-evidence','--io-uring-page-evidence']
            with patch.object(entry,'ROOT',root),patch.object(sys,'argv',argv),patch.object(entry,'inside',return_value=0) as inside:
                self.assertEqual(entry.main(),0)
            self.assertTrue(inside.call_args.kwargs['io_uring_page_evidence'])

    def test_live_start_constructs_restricted_collector_without_real_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proc,p,meta,triples,sysroot=self.fixture(root)
            (p/'mem').write_bytes(b'fake memory, never read')
            live=root/'live';live.mkdir();cap=root/'capture';cap.mkdir()
            (cap/'kvm-slots.tsv').write_text('READY\n')
            (live/'vmm-child-binding.json').write_text(json.dumps(dict(vmm_binding=dict(boot_id='host-boot'))))
            c=LiveTaskCache.__new__(LiveTaskCache);c.directory=live;c.expected_kernel='fake';c.file_cache_path=None
            c.ram_page_evidence=c.vmm_page_evidence=c.io_uring_page_evidence=True
            c.capture=SimpleNamespace(directory=cap,child_scope=True,ready=Mock(),
                bind=Mock(return_value=(meta['host_identity'],dict(slots=meta['slots']))))
            c.cli=Mock(return_value=json.dumps(dict(kernel='fake',boot_id='guest-boot',iomem='fake',export_pfns=[0])))
            c._start_snapshot_queries=Mock()
            with patch('live_task_cache.identity',return_value=p),patch('live_task_cache.threading.Thread') as thread,\
                 patch.object(e,'VmmPageEvidence') as construct:
                c.start('sid')
                construct.assert_called_once_with(c.ram_manifest,'host-boot',io_uring=True)
                thread.return_value.start.assert_called_once()
            c.pm.close();c.memory.close()

    def test_live_sample_reuses_three_ram_reads_and_records_added_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proc,p,meta,triples,sysroot=self.fixture(root);live=root/'live';live.mkdir()
            c=LiveTaskCache.__new__(LiveTaskCache);c.directory=live;c.ram_page_evidence=True;c.ram_manifest=meta
            c.ram_evidence_bytes=c.vmm_evidence_bytes=c.raw_bytes=0;c.samples=[];c.error=None
            c.vmm_evidence=e.VmmPageEvidence(meta,'host-boot',proc_root=proc,sys_root=sysroot,io_uring=True)
            c.provider=c.backend=c.lower_query=None;c.batch_lookup=False;c.interval=1
            c.stop_event=threading.Event();c.stop_event.wait=lambda seconds:c.stop_event.set()
            c._snapshot=Mock(return_value=(dump([(0,20)]),dict(publication=1,guest_start_ns=1,guest_end_ns=2)))
            c._bulk=Mock(return_value=triples)
            with (p/'pagemap').open('rb') as c.pm:c._run()
            self.assertIsNone(c.error);self.assertEqual(c._bulk.call_count,3)
            point=c.samples[0]
            self.assertEqual(point['vmm_page_evidence']['version'],e.IO_VERSION)
            for stage in ('nonram_first','nonram_middle','nonram_last','vmm_evidence_save'):
                self.assertIn(stage,point['host_stage_costs'])
            self.assertEqual(c.raw_bytes,sum(f.stat().st_size for f in live.iterdir() if f.suffix!='.jsonl'))

    def test_live_close_preserves_policy_and_kernel_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);live,body=self.collect(root);measured=e.analyze(live)
            c=LiveTaskCache.__new__(LiveTaskCache);c.directory=live;c.stop_event=Mock();c.thread=None
            c.provider=c.backend=c.lower_query=None;c.file_cache_path=None;c.error=None;c.capture=Mock()
            c.ram_page_evidence=False;c.vmm_page_evidence=c.io_uring_page_evidence=True;c.vmm_evidence_bytes=100
            c.samples=[dict(timestamp_ns=1),dict(timestamp_ns=2)];c.raw_bytes=100;c.interval=1
            c.sid='sid';c.vm={};c.thread_total_cpu_ns=0;c.provider_cache=c.lower_cache=False
            c.bootstrap=dict(boot_id='boot',kernel='fake',locked_anonymous_bytes=4096)
            with patch.object(e,'analyze',return_value=measured):c.close()
            result=json.loads((live/'result.json').read_text())['vmm_page_evidence']
            self.assertEqual(result['version'],e.IO_VERSION)
            self.assertEqual(result['io_uring_policy'],e.IO_POLICY)
            self.assertEqual(result['host_kernel_identity'],body['host_kernel_identity'])
            self.assertIn('io_uring',result['scope']);c.capture.close.assert_called_once()


if __name__=='__main__':unittest.main()
