"""Offline only: synthetic proc metadata and mocked residency, no real processes/devices."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from host_mapped_backing import mapped_backing_inventory


class MappedTests(unittest.TestCase):
    def fixture(self,root,files):
        proc=root/'proc';proc.mkdir()
        for pid in (10,11):
            p=proc/str(pid);p.mkdir();(p/'root').symlink_to('/')
            (p/'stat').write_text(str(pid)+' (fixture) '+' '.join(['S']+['0']*18+['123']))
            (p/'maps').write_text('\n'.join(f'1000-2000 r--p 00000000 {os.major(f.stat().st_dev):x}:{os.minor(f.stat().st_dev):x} {f.stat().st_ino} {f}' for f in files)+'\n')
        return proc

    def test_backings_are_unique_and_known_cache_not_queried_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=root/'known';b=root/'new';a.write_bytes(b'a');b.write_bytes(b'b')
            proc=self.fixture(root,[a,b]);info=b.stat()
            with patch('host_mapped_backing.pids',return_value=[10,11]),patch('host_mapped_backing.mincore_file',return_value=dict(device=info.st_dev,inode=info.st_ino,resident_bytes=4096,identity_stable=True,size_stable=True)) as query:
                value=mapped_backing_inventory(root,[dict(device=a.stat().st_dev,inode=a.stat().st_ino)],proc=proc)
            self.assertEqual(value['status'],'OBSERVED');self.assertEqual(value['additional_global_resident_bytes'],4096)
            self.assertEqual(len(value['additional_files']),1);self.assertEqual(len(value['overlap_objects']),1)
            self.assertEqual(value['additional_files'][0]['owners'],[10,11]);self.assertEqual(query.call_count,1)

    def test_deleted_backing_is_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proc=self.fixture(root,[])
            (proc/'10/maps').write_text('1000-2000 rw-s 0 00:01 999 /memfd:fixture (deleted)\n')
            with patch('host_mapped_backing.pids',return_value=[10]),patch('host_mapped_backing.mincore_file') as query:
                value=mapped_backing_inventory(root,[],proc=proc)
            self.assertEqual(value['coverage'],'INCOMPLETE');self.assertEqual(len(value['unresolved']),1)
            self.assertNotIn('resident_bytes',value['unresolved'][0]);query.assert_not_called()

    def test_changed_inode_and_gone_process_remain_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=root/'new';a.write_bytes(b'a');proc=self.fixture(root,[a])
            (proc/'10/maps').write_text((proc/'10/maps').read_text().replace(str(a.stat().st_ino),str(a.stat().st_ino+1)))
            with patch('host_mapped_backing.pids',return_value=[10,99]),patch('host_mapped_backing.mincore_file') as query:
                value=mapped_backing_inventory(root,[],proc=proc)
            self.assertEqual(value['status'],'INCOMPLETE');self.assertEqual(len(value['errors']),2);query.assert_not_called()

    def test_new_option_is_rejected_before_any_service_access_in_old_mode(self):
        import run_host_file_workflow as workflow
        with patch('sys.argv',['runner','unused.json','--host-memory-detail']),patch.object(workflow.subprocess,'check_output') as call:
            with self.assertRaises(SystemExit) as error:workflow.main()
        self.assertEqual(error.exception.code,2);call.assert_not_called()


if __name__=='__main__':unittest.main()
