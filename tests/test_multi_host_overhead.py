"""Metadata vectors, temporary files and our own children only; no VM or devices."""
import ctypes
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock,patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from host_file_monitor import count_resident,mincore_file
from run_prettier6604_grpo import parallel_guest_cleanup
from sandbox_runtime_monitor import RuntimeMonitor,MEMFIELDS


class MultiHostOverheadTests(unittest.TestCase):
    def test_count_uses_only_low_bit_for_all_byte_values(self):
        for data in (b'',bytes(range(256)),b'\xff'*65536,b'\xfe'*65536,b'\x80\x81\xff\x00'*16384):
            vector=(ctypes.c_ubyte*len(data)).from_buffer_copy(data)
            self.assertEqual(count_resident(vector),sum(bool(v&1) for v in vector))

    def test_mincore_count_and_optional_indices_still_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'sparse';path.touch();path.write_bytes(b'')
            with path.open('r+b') as stream:stream.truncate(3*4096)
            libc=MagicMock();libc.mmap.return_value=123;libc.munmap.return_value=0
            def query(address,size,vector):
                vector[:]=[0x80,0x81,0xff];return 0
            libc.mincore.side_effect=query
            with patch('host_file_monitor.ctypes.CDLL',return_value=libc):
                result=mincore_file(path,include_resident_indices=True)
            self.assertEqual(result['resident_pages'],2);self.assertEqual(result['resident_bytes'],8192)
            self.assertEqual(result['resident_indices'],[1,2]);self.assertTrue(result['stable'])

    def test_cleanup_overlaps_but_each_delete_follows_its_stop(self):
        barrier=threading.Barrier(3);done=set();lock=threading.Lock()
        def stop(sid):
            barrier.wait(timeout=3)
            with lock:done.add(sid)
        def delete(sid):
            with lock:self.assertIn(sid,done)
        result=parallel_guest_cleanup(['a','b','c'],stop,delete)
        self.assertEqual([r['sandbox_id'] for r in result],['a','b','c'])
        self.assertTrue(all(r['stopped'] and r['deleted'] and not r['errors'] for r in result))
        self.assertEqual(parallel_guest_cleanup([],stop,delete),[])

    def test_one_cleanup_failure_does_not_skip_other_guests_or_deletion(self):
        deleted=[]
        def stop(sid):
            if sid=='bad':raise ValueError('owned stop failed')
        result=parallel_guest_cleanup(['bad','good'],stop,deleted.append)
        self.assertEqual(set(deleted),{'bad','good'})
        self.assertFalse(result[0]['stopped']);self.assertTrue(result[0]['deleted']);self.assertTrue(result[0]['errors'])
        self.assertTrue(result[1]['stopped']);self.assertFalse(result[1]['errors'])
        def failed_delete(sid):
            if sid=='bad':raise ValueError('owned delete failed')
        result=parallel_guest_cleanup(['bad','good'],lambda sid:None,failed_delete)
        self.assertTrue(result[0]['stopped']);self.assertFalse(result[0]['deleted']);self.assertTrue(result[0]['errors'])
        self.assertTrue(result[1]['deleted']);self.assertFalse(result[1]['errors'])

    def test_three_real_collector_children_are_reaped_before_parallel_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'raw';raw.mkdir();proc=root/'proc';proc.mkdir()
            values={k:0 for k in MEMFIELDS};values.update(MemTotal=4096,MemAvailable=1024,Cached=512,Shmem=128)
            (proc/'meminfo').write_text(''.join(f'{k}: {v} kB\n' for k,v in values.items()))
            def cli(label,*argv,timeout=15):
                # Actual sandboxes have separate /workspace namespaces.
                def guest_path(value):return str(value).replace(str(root/'state'),str(root/'guests'/argv[1]/'state'))
                if argv[0]=='upload':return ''
                if argv[0]=='download':shutil.copytree(guest_path(argv[2]),Path(argv[3])/Path(argv[2]).name);return ''
                command=[guest_path(v) for v in argv[2:]]
                if 'collect' in command:command+=['--proc-root',str(proc)]
                return subprocess.check_output(command,text=True,timeout=timeout).strip()
            with patch('sandbox_runtime_monitor.GUEST_ROOT',str(root/'state')),patch('sandbox_runtime_monitor.SCRIPT',str(ROOT/'scripts/sandbox_runtime_monitor.py')):
                monitor=RuntimeMonitor(raw,cli,light=True)
                try:
                    for sid in ('a','b','c'):monitor.start(sid,'branch')
                    identities={sid:monitor.active[sid]['identity'] for sid in monitor.active}
                    def delete(sid):self.assertFalse(Path('/proc',str(identities[sid]['pid'])).exists())
                    closed=parallel_guest_cleanup(list(identities),monitor.stop,delete)
                    self.assertTrue(all(r['stopped'] and r['deleted'] and not r['errors'] for r in closed))
                    self.assertFalse(monitor.active);self.assertFalse(monitor.workers)
                    manifest=json.loads((raw/'runtime-monitor.json').read_text());self.assertEqual(manifest['status'],'PASS')
                    for session in manifest['sessions']:
                        self.assertTrue(session['collector_reaped'])
                        with (raw/session['raw_directory']/'guest-memory.tsv').open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
                        self.assertEqual(rows[-1]['phase'],'cleanup');self.assertTrue(all(r['sandbox_id']==session['sandbox_id'] for r in rows))
                finally:
                    for sid in list(monitor.active):monitor.stop(sid)


if __name__=='__main__':unittest.main()
