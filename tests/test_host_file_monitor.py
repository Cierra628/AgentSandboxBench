import json
import csv
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
import subprocess
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from host_file_monitor import HostFileMonitor, backing_roots, counters, linear_reference, measurement_group, mincore_file, pss_snapshot, residency_check
from platform_tasks import configuration, commands


class HostFileTests(unittest.TestCase):
    def group(self, root, file=4096):
        group=root/'group';group.mkdir()
        (group/'memory.current').write_text(str(file+8192))
        (group/'memory.stat').write_text(f'file {file}\nanon 8192\nshmem 0\n')
        (group/'cgroup.procs').write_text('')
        return group

    def test_counter_missing_values_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            group=self.group(Path(tmp));value=counters(group)
            self.assertEqual(value['file'],4096);self.assertIsNone(value['kernel'])
            (group/'memory.stat').write_text('anon 8192\nshmem 0\n')
            with self.assertRaises(ValueError):counters(group)

    def test_real_mincore_no_content_read_and_page_rounding(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'backing'
            fd=os.open(path,os.O_CREAT|os.O_RDWR,0o600)
            try:
                os.ftruncate(fd,4097);os.pwrite(fd,b'x'*4096,0);os.fsync(fd)
            finally:os.close(fd)
            first=mincore_file(path);second=mincore_file(path)
            self.assertTrue(first['stable']);self.assertEqual(first['pages'],2)
            self.assertGreaterEqual(first['resident_pages'],1)
            self.assertEqual(first['resident_pages'],second['resident_pages'])
            detailed=mincore_file(path,include_resident_indices=True)
            self.assertNotIn('resident_indices',first)
            self.assertEqual(len(detailed['resident_indices']),detailed['resident_pages'])
            self.assertIn(0,detailed['resident_indices'])
            link=Path(tmp)/'link';link.symlink_to(path)
            with self.assertRaises(OSError):mincore_file(link)

    def test_residency_alias_dedup_and_accounting_not_proven_by_closeness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);group=self.group(root,0);backings=root/'images';backings.mkdir()
            (backings/'one').write_bytes(b'x'*4096);os.link(backings/'one',backings/'alias')
            value=residency_check(group,[backings,backings],root/'check.json')
            self.assertEqual(len(value['files']),1)
            self.assertEqual(value['resident_bytes'],4096)
            self.assertEqual(value['minimum_unaccounted_or_timing_bytes'],4096)
            self.assertEqual(value['accounting'],'SHORTFALL_REQUIRES_RESOLUTION')
            (group/'memory.stat').write_text('file 4096\nanon 8192\nshmem 0\n')
            value=residency_check(group,[backings],root/'check.json')
            self.assertIn('NOT_PROOF',value['accounting'])
            self.assertEqual(value['file_residency_lower_bound_bytes'],4096)
            image=root/'volume.ext4';image.write_bytes(b'y'*4096)
            value=residency_check(group,[backings,image,image],root/'with-loop.json')
            self.assertEqual(len(value['files']),2)
            self.assertEqual(value['resident_bytes'],8192)

    def test_live_inventory_records_mtime_changes_but_rejects_identity_or_size_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);group=self.group(root);path=root/'live-file';path.write_bytes(b'x'*4096)
            first=path.stat();changed=SimpleNamespace(**{k:getattr(first,k) for k in
                ('st_mode','st_dev','st_ino','st_rdev','st_size','st_mtime_ns')})
            changed.st_mtime_ns+=1
            with patch('host_file_monitor.os.fstat',side_effect=[first,changed]):
                row=mincore_file(path,live=True)
            self.assertFalse(row['stable']);self.assertTrue(row['metadata_changed'])
            self.assertTrue(row['identity_stable']);self.assertTrue(row['size_stable'])
            with patch('host_file_monitor.mincore_file',return_value=row):
                old=residency_check(group,[path],root/'old.json')
                self.assertEqual(old['status'],'INCOMPLETE')
                live=residency_check(group,[path,path],root/'live.json',live=True)
                self.assertEqual(live['status'],'OBSERVED');self.assertEqual(len(live['files']),1)
                self.assertEqual(live['metadata_changed_files'],[str(path)])
                self.assertLessEqual(row['start_monotonic_ns'],row['end_monotonic_ns'])
                for field in ('identity_stable','size_stable'):
                    row[field]=False
                    self.assertEqual(residency_check(group,[path],root/'bad.json',live=True)['status'],'INCOMPLETE')
                    row[field]=True

    def test_backing_roots_preserve_old_prefix_and_add_confirmed_paths(self):
        source,mount,image,seed=map(Path,('/input/env','/owned/mount','/owned/volume.ext4','/seed'))
        old=[source/'image-cache/commits',mount/'env/image-cache',image]
        extra=[source/'deps',mount/'env/deps',mount/'env/logs',
               mount/'env/persisted-sandboxes',mount/'env/template-builds']
        self.assertEqual(backing_roots(source,mount,image),old+extra)
        self.assertEqual(backing_roots(source,mount,image,cache_seed=seed),old+[seed/'memory/volume.ext4']+extra)
        self.assertEqual(backing_roots(source,mount,image,cache_seed=seed,restore=True),
                         old+[seed/'memory/volume.ext4',mount/'env/snapshot-store',mount/'env/snapshot-local-cache']+extra)

    def test_expanded_inventory_measures_new_files_and_deduplicates_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);group=self.group(root,64*4096)
            source=root/'input/env';mount=root/'owned/mount';image=root/'owned/volume.ext4';seed=root/'seed'
            roots=backing_roots(source,mount,image,cache_seed=seed,restore=True)
            for path in roots:
                if path.suffix=='.ext4':path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'i'*4096)
                else:path.mkdir(parents=True,exist_ok=True)
            for index,path in enumerate(roots):
                if path.is_dir():(path/'resident').write_bytes(bytes([index])*4096)
            os.link(source/'deps/resident',mount/'env/deps/input-alias')
            (mount/'env/logs/external-link').symlink_to(source/'deps/resident')
            value=residency_check(group,roots,root/'expanded.json')
            self.assertEqual(value['status'],'OBSERVED')
            self.assertEqual(value['roots'],list(map(str,roots)))
            self.assertEqual(len(value['files']),11)
            self.assertEqual(len({(f['device'],f['inode']) for f in value['files']}),11)
            self.assertEqual(value['resident_bytes'],11*4096)
            for path in roots[6:]:
                self.assertTrue(any(Path(f['path']).is_relative_to(path) for f in value['files']))
            missing=mount/'env/template-builds'
            (missing/'resident').unlink();missing.rmdir()
            with self.assertRaises(FileNotFoundError):residency_check(group,roots,root/'missing.json')
            self.assertFalse((root/'missing.json').exists())

    def test_process_exit_is_partial_not_fake_zero_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);group=self.group(root);(group/'cgroup.procs').write_text('10\n11\n')
            proc=root/'proc';pid=proc/'10';pid.mkdir(parents=True)
            (pid/'stat').write_text('10 (worker) '+' '.join(['S']+['0']*18+['99']+['0']*30))
            (pid/'comm').write_text('worker\n');(pid/'cgroup').write_text('0::/group\n')
            (pid/'smaps_rollup').write_text('Pss: 100 kB\nPss_File: 30 kB\nPss_Anon: 70 kB\nPss_Shmem: 0 kB\n')
            value=pss_snapshot(group,proc)
            self.assertEqual(value['status'],'PARTIAL');self.assertEqual(value['pss'],102400)
            self.assertEqual(value['disappeared_pids'],[11])

    def test_monitor_stop_and_sampler_failure_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);group=self.group(root)
            monitor=HostFileMonitor(root/'host',group);time.sleep(.12)
            self.assertEqual(monitor.stop()['status'],'PASS')
            self.assertGreaterEqual(monitor.count,2);self.assertFalse(monitor.thread.is_alive())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);group=self.group(root)
            monitor=HostFileMonitor(root/'host',group);(group/'memory.stat').unlink();time.sleep(.2)
            with self.assertRaises(RuntimeError):monitor.stop()
            self.assertEqual(json.loads((root/'host/result.json').read_text())['status'],'FAIL')

    def test_new_config_opt_in_and_dispatch(self):
        cfg=configuration(ROOT/'configs/platform-prettier6604-host-file.json')
        check,live=commands(cfg,Path('/not-created'))
        self.assertIn('--host-file-monitor',live);self.assertNotIn('--host-file-monitor',check)
        self.assertTrue(cfg['light_monitor'])
        old=configuration(ROOT/'configs/platform-prettier6604.json')
        self.assertNotIn('--host-file-monitor',commands(old,Path('/not-created'))[1])
        with self.assertRaises(ValueError):commands(cfg,Path('/not-created'),tool_capture=True)
        with patch.dict(os.environ,{'ASB_HOST_FILE_SLICE':''}):
            with self.assertRaises(ValueError):measurement_group()
        for changes in ({'host_file_monitor':1},{'checkpoint_after_step':24}):
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'config.json';path.write_text(json.dumps(dict(cfg,**changes)))
                with self.assertRaises(ValueError):configuration(path)

    def test_python_host_configs_only_opt_in_host_sampling(self):
        for profile,policy in (('sphinx7590','--sphinx-pipeline-diagnostic'),('xarray6992','--diagnostic-continuation')):
            base=ROOT/'configs'/('platform-'+profile+'.json');path=ROOT/'configs'/('platform-'+profile+'-host-file.json')
            raw=json.loads(path.read_text());self.assertTrue(raw.pop('host_file_monitor'))
            self.assertEqual(raw,json.loads(base.read_text()))
            cfg=configuration(path);check,live=commands(cfg,Path('/not-created'))
            self.assertTrue(cfg['light_monitor']);self.assertIn('--host-file-monitor',live)
            self.assertIn('--light-monitor',live);self.assertIn(policy,check)
            self.assertNotIn('--host-file-monitor',commands(configuration(base),Path('/not-created'))[1])

    def test_linear_reference_is_estimate_and_negative_values_preserved(self):
        value=linear_reference(100,140,2)
        self.assertEqual(value['reduction_vs_linear_reference'],.3)
        self.assertIn('not a measured',value['meaning'])
        self.assertLess(linear_reference(100,250,2)['reduction_vs_linear_reference'],0)
        for args in ((0,100,2),(100,-1,2),(100,100,True),(float('nan'),1,2)):
            with self.assertRaises(ValueError):linear_reference(*args)

    def test_old_creation_record_report_keeps_baseline_unknown(self):
        from report_host_file import report
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'host';source.mkdir()
            (source/'result.json').write_text(json.dumps(dict(status='PASS',samples=2,cgroup='/owned')))
            rows=[dict(monotonic_ns=t,elapsed_ns=t-100,read_duration_ns=1,current=200,file=f,anon=50,shmem=20,kernel=10,file_mapped=0,file_dirty=0,file_writeback=0) for t,f in [(100,80),(200,90)]]
            with (source/'memory.tsv').open('w') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(rows[0]),delimiter='\t');writer.writeheader();writer.writerows(rows)
            (source/'pss.jsonl').write_text(json.dumps(dict(status='OBSERVED',start_monotonic_ns=100,pss=50,pss_anon=40,pss_file=10,pss_shmem=0))+'\n')
            creation=dict(cgroup='/owned',preexisting_firecrackers=0,firecracker_pool=False,startup_prewarm=False,block_pool=False,verified_before_guest_creation_ns=150)
            (root/'result.json').write_text(json.dumps(dict(status='PASS',host_file_creation=creation)))
            check=dict(status='OBSERVED',resident_bytes=70,cgroup_before={'file':80},cgroup_after={'file':90},minimum_unaccounted_or_timing_bytes=0,file_residency_lower_bound_bytes=90,accounting='NO_SHORTFALL_DETECTED_NOT_PROOF_OF_COMPLETE_ACCOUNTING',scope='test files',cpu_ns=1,start_monotonic_ns=190,end_monotonic_ns=200)
            (root/'residency.json').write_text(json.dumps(check))
            summary=report(source,root/'residency.json',root/'report')
            self.assertEqual(summary['primary_metric'],'memory.current')
            self.assertEqual(summary['current']['baseline_status'],'NOT_MEASURED')
            self.assertIsNone(summary['current']['after_service_ready_peak_increase_bytes'])
            self.assertEqual(summary['current']['observed_peak_sample']['current'],200)
            self.assertTrue((root/'report/REPORT.md').is_file())
            self.assertIsNone(summary['file_before_guest_bytes'])
            self.assertEqual(summary['creation_baseline']['preceding_sample']['file'],80)
            self.assertEqual(summary['creation_baseline']['following_sample']['file'],90)
            self.assertEqual(summary['accounting_completeness'],'NOT_PROVEN_BY_AGGREGATES')
            current_only=report(source,None,root/'current-only',creation_path=root/'result.json')
            self.assertEqual(current_only['current'],summary['current'])
            self.assertEqual(current_only['mincore']['status'],'NOT_REQUESTED')
            self.assertIsNone(current_only['mincore']['resident_bytes'])
            self.assertFalse((root/'current-only/backing-residency.json').exists())
            check['status']='INCOMPLETE';(root/'residency.json').write_text(json.dumps(check))
            with self.assertRaisesRegex(ValueError,'inventory incomplete'):
                report(source,root/'residency.json',root/'rejected')
            partial=report(source,root/'residency.json',root/'partial',allow_incomplete_inventory=True)
            self.assertEqual(partial['status'],'OBSERVED_COUNTERS_INCOMPLETE_INVENTORY')
            self.assertEqual(partial['mincore']['status'],'INCOMPLETE')

    def test_current_statistics_preserve_baseline_peak_composition_and_signed_change(self):
        from report_host_file import current_statistics
        rows=[dict(monotonic_ns=t,current=value,anon=value//2,file=20,kernel=None)
              for t,value in [(100,300),(200,90),(500,80)]]
        creation=dict(verified_before_guest_creation_ns=150,before_guest_counters={'current':100})
        value=current_statistics(rows,creation)
        self.assertEqual(value['initial_bytes'],300)
        self.assertEqual(value['final_bytes'],80)
        self.assertEqual(value['observed_peak_bytes'],300)
        self.assertEqual(value['after_service_ready_observed_peak_bytes'],90)
        self.assertEqual(value['after_service_ready_peak_increase_bytes'],-10)
        self.assertEqual(value['final_increase_from_before_guest_bytes'],-20)
        self.assertEqual(value['time_weighted_mean_bytes'],142.5)
        self.assertEqual(value['observed_peak_sample'],rows[0])
        self.assertIsNone(value['observed_peak_sample']['kernel'])
        creation['before_guest_counters']={'current':0}
        self.assertEqual(current_statistics(rows,creation)['after_service_ready_peak_increase_bytes'],90)
        creation['before_guest_counters']=None
        self.assertEqual(current_statistics(rows,creation)['baseline_status'],'NOT_MEASURED')

    def test_extrapolation_refuses_observed_accounting_shortfall(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=root/'a.json';b=root/'b.json';out=root/'out.json'
            a.write_text(json.dumps(dict(status='PASS',file_peak_bytes=100,mincore={'accounting':'SHORTFALL_REQUIRES_RESOLUTION'})))
            b.write_text(a.read_text())
            result=subprocess.run([sys.executable,str(ROOT/'scripts/report_host_file.py'),str(a),str(b),'--sandboxes','2','--output',str(out)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0);self.assertFalse(out.exists())

    def test_fresh_parent_and_precleanup_check_are_in_actual_entry(self):
        code=(ROOT/'scripts/validate_isolated_agentenv.py').read_text()
        self.assertLess(code.index("argv+=['--cgroup-parent',host_slice]"),code.index("call('start-server'"))
        self.assertLess(code.index("receipt['host_file_creation']"),code.index("stage='guest-cache-metadata-probe'"))
        self.assertLess(code.index('sampler_stop.set();sampler.join'),code.index("receipt['host_file_residency']=residency_check"))
        self.assertLess(code.index("call('stop-own-server'"),code.index("receipt['host_file_residency']=residency_check"))
        self.assertLess(code.index("receipt['host_file_residency']=residency_check"),code.index("call('unmount-own-volume'"))
        self.assertIn('roots=backing_roots(source,mount,image,cache_seed=cache_seed if seed_value else None,restore=host_restore)',code)
        self.assertIn("roots,out/'host-file-residency.json'",code)
        workflow=(ROOT/'scripts/platform_tasks.py').read_text()
        self.assertLess(workflow.index("host_monitor=HostFileMonitor"),workflow.index("if cfg['backend']!='trenvx':call(prepare"))


if __name__=='__main__':unittest.main()
