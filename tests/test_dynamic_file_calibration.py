"""Synthetic file/host evidence only; never starts a guest, service or probe."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import report_dynamic_file_calibration as report
from test_dynamic_file_cache import file_dump,guest_dump
import test_dynamic_file_cache as fixtures
from file_cache_metadata import decode
from report_dynamic_file_cache import analyze
from run_live_child_calibration import release_empty_slice


class DynamicCalibrationTests(unittest.TestCase):
    def fixture(self,root):
        live=root/'live-cache';live.mkdir()
        write=lambda p,v:p.write_text(json.dumps(v)+'\n')
        write(live/'marker.json',dict(sandbox_id='a',boot_id='boot'))
        (root/'sandbox-id.txt').write_text('a\n')
        endpoints={'written':[(0,1,2),(1,2,4)],'evicted':[], 'reread':[(0,9,6),(1,10,8)]}
        run={'phases':[]};observations=[];samples=[]
        for i,(phase,rows) in enumerate(endpoints.items()):
            raw=file_dump(rows)
            (root/(phase+'-file.bin')).write_bytes(raw)
            write(root/(phase+'.json'),{'file_cache':{'header':decode(raw)[0],'identity':{'st_ino':10}}})
            run['phases'].append(dict(name=phase,start_ns=i*100,end_ns=i*100+90))
            # Subsequent cache need not retain the initial PFNs or all 8192 bytes.
            observations.append(dict(point=i,current_file_cache_after_bytes=4096 if rows else 0,
                                     confirmed_host_union_bytes=4096 if rows else 0))
            samples.append(dict(point=i,timestamp_ns=i*100+1,host_elapsed_ns=1))
        write(live/'dynamic-file-cache.json',dict(sandbox_id='a',boot_id='boot',observations=observations))
        (live/'samples.jsonl').write_text(''.join(json.dumps(s)+'\n' for s in samples))
        return run

    def test_new_lifetimes_and_smaller_subsequent_cache_pass(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(report,'SIZE',8192):
            root=Path(tmp);run=self.fixture(root);value=report.check(root,run)
            self.assertEqual(value['status'],'PASS',value['errors'])
            self.assertEqual(value['new_lifetime_offsets_after_reread'],2)

    def test_no_physical_confirmation_is_not_hidden_by_successful_workload(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(report,'SIZE',8192):
            root=Path(tmp);run=self.fixture(root);p=root/'live-cache/dynamic-file-cache.json'
            v=json.loads(p.read_text());v['observations'][-1]['confirmed_host_union_bytes']=0;p.write_text(json.dumps(v))
            value=report.check(root,run)
            self.assertEqual(value['status'],'FAIL')
            self.assertIn('reread: no positive',value['errors'][0])

    def test_missing_eviction_evidence_fails(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(report,'SIZE',8192):
            root=Path(tmp);run=self.fixture(root)
            raw=file_dump([(0,1,2)]);(root/'evicted-file.bin').write_bytes(raw)
            (root/'evicted.json').write_text(json.dumps({'file_cache':{'header':decode(raw)[0],'identity':{'st_ino':10}}}))
            value=report.check(root,run)
            self.assertEqual(value['status'],'FAIL')
            self.assertTrue(any('evicted:' in e for e in value['errors']))

    def test_reusing_initial_tags_cannot_prove_repopulation(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(report,'SIZE',8192):
            root=Path(tmp);run=self.fixture(root)
            (root/'reread-file.bin').write_bytes((root/'written-file.bin').read_bytes())
            (root/'reread.json').write_text((root/'written.json').read_text())
            value=report.check(root,run)
            self.assertEqual(value['status'],'FAIL')
            self.assertEqual(value['new_lifetime_offsets_after_reread'],0)

    def test_wrong_sandbox_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(report,'SIZE',8192):
            root=Path(tmp);run=self.fixture(root);(root/'sandbox-id.txt').write_text('b\n')
            with self.assertRaisesRegex(ValueError,'identity mismatch'):report.check(root,run)

    def test_file_unknown_can_exceed_later_whole_guest_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            live,side=fixtures.DynamicFileCacheTests().fixture(Path(tmp))
            destination=live/'file-query-download/file-query';destination.parent.mkdir()
            side.rename(destination);side=destination
            # The file query sees the page; the later whole-guest snapshot sees removal.
            (live/'pair-0000-after.bin').write_bytes(guest_dump([]))
            sample=json.loads((live/'samples.jsonl').read_text());sample['unconfirmed_after_pages']=0
            rows=analyze(live,side)['observations']
            detail=report.unconfirmed_details(live,rows,{0:sample})[0]
            self.assertEqual(detail['unconfirmed_after_pages'],1)
            self.assertEqual(detail['reasons']['whole_guest_after_changed'],1)
            self.assertEqual(detail['whole_guest_unconfirmed_after_pages'],0)

    def test_owned_slice_cleanup_never_stops_occupied_group(self):
        with tempfile.TemporaryDirectory() as tmp,patch('run_live_child_calibration.subprocess.run') as command:
            root=Path(tmp)/'asbvmm-test.slice';root.mkdir();(root/'cgroup.procs').write_text('123\n')
            with self.assertRaisesRegex(RuntimeError,'still has processes'):release_empty_slice(root)
            command.assert_not_called()

    def test_owned_empty_slice_is_stopped_only_after_membership_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'asbvmm-test.slice';root.mkdir();(root/'cgroup.procs').write_text('')
            def command(argv,**kwargs):
                self.assertEqual(argv,['systemctl','stop','asbvmm-test.slice'])
                (root/'cgroup.procs').unlink();root.rmdir()
            with patch('run_live_child_calibration.subprocess.run',side_effect=command):
                self.assertEqual(release_empty_slice(root)['status'],'PASS')


if __name__=='__main__':unittest.main()
