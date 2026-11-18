"""Local temporary files / mocked proc only; no services, workloads or devices."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import host_schedule_files as m
from host_file_monitor import mincore_file


class ScheduleFileTests(unittest.TestCase):
    def fixture(self, base):
        env=base/'env';env.mkdir()
        for name in ('lower','snapshot','data','index','log'):(env/name).write_bytes(b'x'*4096)
        (env/'alias').hardlink_to(env/'lower')
        rows=[]
        for name in ('lower','snapshot'):
            s=(env/name).stat();rows.append(dict(path=str(env/name),device=s.st_dev,inode=s.st_ino,status='IDENTIFIED'))
        guest=dict(pid=99999999,image_config=str(env/'image.json'),lowers=rows,upper=dict(data='data',index='index'))
        initial=mincore_file(env/'lower')
        return env,dict(guests={'a':guest,'b':dict(guest)}),[initial]

    def observe(self, env, roots, initial):
        with patch.object(m,'pids',return_value=[]), patch.object(m,'permission_evidence',return_value={'test_only':True}):
            return m.observe_schedule_files(env, roots, initial, Path('/not-real-group'))

    def test_initial_snapshot_upper_and_all_aliases_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            env,roots,initial=self.fixture(Path(tmp))
            with patch.object(m,'mincore_file',wraps=mincore_file) as query:
                value=self.observe(env,roots,initial)
            self.assertEqual(value['status'],'OBSERVED')
            self.assertEqual(value['unique_objects'],5)
            self.assertEqual(value['reused_initial_objects'],1)
            self.assertEqual(query.call_count,4)
            snapshot=next(r for r in value['files'] if 'active_snapshot_rootfs_lower' in r['categories'])
            self.assertEqual(snapshot['users'],['a','b'])
            self.assertIn('owned_snapshot',snapshot['categories'])
            lower=next(r for r in value['files'] if r.get('query_reused'))
            self.assertGreaterEqual(len(lower['aliases']),2)
            self.assertEqual(value['stable_regular_resident_bytes'],sum(r['resident_bytes'] for r in value['files']))

    def test_symlink_external_file_is_included_but_directory_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);env,roots,initial=self.fixture(base)
            external=base/'external';external.write_bytes(b'e'*4096)
            (env/'external-link').symlink_to(external);(env/'directory-link').symlink_to(base,target_is_directory=True)
            value=self.observe(env,roots,initial)
            self.assertEqual(value['status'],'PARTIAL')
            self.assertEqual(value['unique_objects'],6)
            self.assertTrue(any('owned_symlink_file' in r['categories'] for r in value['files']))
            self.assertTrue(any(r['source']=='owned_tree_symlink_directory' for r in value['unresolved']))

    def test_walk_failure_and_missing_reference_stay_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            env,roots,initial=self.fixture(Path(tmp));roots['guests']['a']['upper']['data']='missing'
            real_walk=os.walk
            def walk(path,**kw):
                kw['onerror'](PermissionError(13,'denied','missing-dir'))
                return real_walk(path,**kw)
            with patch.object(m.os,'walk',side_effect=walk):value=self.observe(env,roots,initial)
            self.assertEqual(value['status'],'PARTIAL')
            self.assertTrue(any(r['source']=='owned_tree_walk' for r in value['unresolved']))
            self.assertTrue(any(r['source']=='rootfs_upper_data' and r['resident_bytes'] is None for r in value['unresolved']))

    def test_changed_query_is_not_counted_as_stable_or_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            env,roots,initial=self.fixture(Path(tmp))
            def query(path,**kw):
                value=mincore_file(path,**kw)
                if path.endswith('/data'):value['stable']=False
                return value
            with patch.object(m,'mincore_file',side_effect=query):value=self.observe(env,roots,initial)
            changed=[r for r in value['files'] if r['status']=='CHANGED_DURING_QUERY']
            self.assertEqual(len(changed),1);self.assertGreater(changed[0]['resident_bytes'],0)
            self.assertEqual(value['status'],'PARTIAL')
            self.assertEqual(value['stable_regular_resident_bytes'],sum(r['resident_bytes'] for r in value['files'] if r['status']=='OBSERVED'))

    def test_pinned_unlinked_file_and_old_nofollow_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'file';file.write_bytes(b'x'*4096)
            link=Path(tmp)/'link';link.symlink_to(file)
            with self.assertRaises(OSError):mincore_file(link)
            with file.open('rb') as stream:
                original=file.stat();file.unlink()
                result=mincore_file(file,file_descriptor=stream.fileno(),live=True)
                self.assertEqual((result['device'],result['inode']),(original.st_dev,original.st_ino))
                self.assertTrue(result['stable']);self.assertGreater(result['resident_bytes'],0)
                self.assertEqual(os.fstat(stream.fileno()).st_ino,original.st_ino)

    def test_shmem_is_separate_and_denial_not_all_resident(self):
        with tempfile.TemporaryDirectory() as tmp:
            env,roots,initial=self.fixture(Path(tmp))
            with patch.object(m,'filesystem_magic',return_value=0x01021994):value=self.observe(env,roots,initial)
            separate=[r for r in value['files'] if r['status']=='SEPARATE_SHMEM_OR_HUGETLB']
            self.assertEqual(len(separate),4);self.assertTrue(all(r['resident_bytes'] is None for r in separate))
            self.assertEqual(value['stable_regular_resident_bytes'],initial[0]['resident_bytes'])
            with patch.object(m.os,'geteuid',return_value=12345):
                with self.assertRaises(PermissionError):m.permission_evidence()

    def test_open_unlinked_and_mapped_only_external_files_are_not_missed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);env=base/'env';env.mkdir();proc=base/'proc';root=proc/'101'
            (root/'fd').mkdir(parents=True);(root/'map_files').mkdir()
            (root/'stat').write_text('101 (fake) '+' '.join(['S']+['0']*18+['123']))
            (root/'cgroup').write_text('0::/test\n')
            external=base/'external';external.write_bytes(b'x'*4096);s=external.stat()
            (root/'maps').write_text(f'1000-2000 r--p 00000000 {os.major(s.st_dev):x}:{os.minor(s.st_dev):x} {s.st_ino} /external\n')
            (root/'map_files/1000-2000').symlink_to(external)
            unlinked=base/'unlinked';unlinked.write_bytes(b'u'*4096)
            with unlinked.open('rb') as stream:
                ino=os.fstat(stream.fileno()).st_ino;unlinked.unlink()
                (root/'fd/4').symlink_to('/proc/self/fd/'+str(stream.fileno()))
                with patch.object(m,'pids',return_value=[101]), patch.object(m,'permission_evidence',return_value={'test_only':True}):
                    value=m.observe_schedule_files(env,dict(guests={}),[],Path('/sys/fs/cgroup/test'),proc_root=proc)
            self.assertEqual(value['status'],'OBSERVED')
            self.assertEqual(value['unique_objects'],2)
            self.assertEqual({r['inode'] for r in value['files']},{s.st_ino,ino})
            self.assertTrue(any(r['nlink']==0 and r['resident_bytes']>0 for r in value['files']))
            self.assertEqual(sum(c['stable_resident_bytes'] for c in value['primary_categories'].values()),value['stable_regular_resident_bytes'])

    def test_process_set_change_is_partial_not_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            env,roots,initial=self.fixture(Path(tmp))
            with patch.object(m,'pids',side_effect=[[],[101]]), patch.object(m,'permission_evidence',return_value={'test_only':True}):
                value=m.observe_schedule_files(env,roots,initial,Path('/not-real-group'))
            self.assertEqual(value['status'],'PARTIAL')
            self.assertTrue(any(r['source']=='cgroup_members' and r['resident_bytes'] is None for r in value['unresolved']))

    def test_nonregular_symlink_is_excluded_and_snapshot_alias_controls_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            env,roots,initial=self.fixture(Path(tmp));(env/'device-alias').symlink_to('/dev/null')
            value=self.observe(env,roots,initial)
            self.assertEqual(value['status'],'OBSERVED')
            self.assertTrue(any(r['reason']=='REFERENCED_NONREGULAR_OBJECT' for r in value['excluded_entries']))
        row=dict(categories=['owned_snapshot-store'],aliases=[dict(path='/env/snapshot-store/commit'),
                   dict(path='/env/firecracker-work/managed-snapshots/id/mem_overlaybd/overlaybd.commit')])
        self.assertEqual(m.primary_category(row),'snapshot_mem_overlaybd_file')
        row['aliases']=[dict(path='/env/snapshot-local-cache/runtime/id/memory/image.json')]
        self.assertEqual(m.primary_category(row),'snapshot_memory_config')


if __name__=='__main__':unittest.main()
