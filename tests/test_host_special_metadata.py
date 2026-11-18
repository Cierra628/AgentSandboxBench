"""Offline only: synthetic proc, temporary files, mocked GET_DEV_INFO; no service/device access."""
import json
import ast
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from host_mapped_backing import mapped_backing_inventory
from host_special_metadata import mapping_line, attach_smaps, descriptor_metadata, ublkc_metadata, build_ublk_query


class SpecialTests(unittest.TestCase):
    def fixture(self,root,lines):
        proc=root/'proc';p=proc/'10';p.mkdir(parents=True);(p/'root').symlink_to('/')
        (p/'fd').mkdir();(p/'stat').write_text('10 (fixture) '+' '.join(['S']+['0']*18+['123']))
        (p/'maps').write_text('\n'.join(lines)+'\n')
        (p/'smaps').write_text('\n'.join(line+'\nRss: 4 kB\nPss: 2 kB\nAnonymous: 0 kB\nPrivate_Hugetlb: 0 kB\nVmFlags: rd sh' for line in lines)+'\n')
        return proc,p

    def test_singleton_inode_keeps_each_mapping_name_offset_and_smaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proc,p=self.fixture(root,[
                '1000-2000 rw-s 00000000 00:10 69 anon_inode:kvm-vcpu:0',
                '3000-4000 rw-s 00001000 00:10 69 anon_inode:kvm-vcpu:1'])
            with patch('host_mapped_backing.pids',return_value=[10]):value=mapped_backing_inventory(root,[],proc=proc)
            self.assertEqual(len(value['unresolved']),1)
            regions=value['unresolved'][0]['regions'];self.assertEqual(len(regions),2)
            self.assertEqual([r['offset'] for r in regions],[0,4096])
            self.assertEqual([r['mapping_path'] for r in regions],['anon_inode:kvm-vcpu:0','anon_inode:kvm-vcpu:1'])
            self.assertEqual(regions[0]['smaps_bytes']['Pss'],2048)
            self.assertNotIn('Pss_File',regions[0]['smaps_bytes'])
            self.assertEqual(value['special_metadata_status'],'OBSERVED');self.assertEqual(value['coverage'],'INCOMPLETE')

    def test_smaps_race_missing_fields_and_pid_reuse_stay_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);line='1000-2000 rw-s 0 00:10 1 anon_inode:[io_uring]';proc,p=self.fixture(root,[line])
            r=mapping_line(line);r['smaps_status']='NOT_OBSERVED'
            (p/'smaps').write_text((p/'smaps').read_text().replace('rw-s','r--s'))
            attach_smaps(p,[r]);self.assertEqual(r['smaps_status'],'MAPPING_CHANGED')
            (p/'smaps').write_text(line+'\nVmFlags: rd\n');r=mapping_line(line)
            attach_smaps(p,[r]);self.assertEqual(r['smaps_status'],'INCOMPLETE_FIELDS')
            with patch('host_mapped_backing.pids',return_value=[10]),patch('host_mapped_backing.attach_smaps',side_effect=PermissionError('denied')):
                v=mapped_backing_inventory(root,[],proc=proc)
            self.assertEqual(v['special_metadata_status'],'INCOMPLETE');self.assertTrue(v['special_metadata_errors'])
            def reused(proc,regions):
                (p/'stat').write_text((p/'stat').read_text().replace('123','124'))
            with patch('host_mapped_backing.pids',return_value=[10]),patch('host_mapped_backing.attach_smaps',side_effect=reused):
                v=mapped_backing_inventory(root,[],proc=proc)
            self.assertEqual(v['status'],'INCOMPLETE');self.assertEqual(v['unresolved'][0]['regions'][0]['smaps_status'],'PID_CHANGED')

    def test_deleted_descriptor_type_and_size_are_not_residency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);f=root/'backing';f.write_bytes(b'fixture');s=f.stat()
            line=f'1000-2000 rw-p 0 {os.major(s.st_dev):x}:{os.minor(s.st_dev):x} {s.st_ino} /memfd:iov_deque (deleted)'
            proc,p=self.fixture(root,[line]);(p/'fd/7').symlink_to(f)
            def fs(fd,fields):fields._obj[0]=0x01021994;return 0
            with patch('host_mapped_backing.pids',return_value=[10]),patch('host_special_metadata.ctypes.CDLL',return_value=SimpleNamespace(fstatfs=fs)),patch('host_mapped_backing.mincore_file') as query:
                v=mapped_backing_inventory(root,[],proc=proc)
            d=v['unresolved'][0]['deleted_descriptors'][0]
            self.assertEqual(d['backing_type'],'TMPFS');self.assertEqual(d['size'],7)
            self.assertNotIn('resident_bytes',d);query.assert_not_called()
            self.assertEqual(v['coverage'],'INCOMPLETE');self.assertEqual(v['special_metadata_status'],'OBSERVED')
            with patch('host_mapped_backing.pids',return_value=[10]),patch('host_special_metadata.ctypes.CDLL',return_value=SimpleNamespace(fstatfs=fs)):
                (p/'fd/7').unlink();v=mapped_backing_inventory(root,[],proc=proc)
            self.assertEqual(v['special_metadata_status'],'INCOMPLETE')
            with self.assertRaises(ValueError):descriptor_metadata(f,(s.st_dev,s.st_ino+1))

    def queue_fixture(self):
        identity=dict(device=6,inode=42,rdev=os.makedev(240,7))
        # 400 descriptors => 3 mapped pages, but 4 allocated pages per queue.
        info=dict(**identity,dev_id=7,nr_hw_queues=2,queue_depth=400,state=1,ublksrv_pid=10,
                  descriptor_bytes=24,max_queue_depth=4096,command_buffer_offset=0,page_size=4096)
        regions=[dict(start=0x1000,end=0x4000,offset=0,permissions='r--s',smaps_status='OBSERVED',vm_flags=['rd','pf']),
                 dict(start=0x5000,end=0x8000,offset=98304,permissions='r--s',smaps_status='OBSERVED',vm_flags=['rd','pf'])]
        entry=dict(**identity,mapping_path='/dev/ublkc7',owners=[10],regions=regions)
        st=SimpleNamespace(st_dev=6,st_ino=42,st_rdev=identity['rdev'],st_mode=stat.S_IFCHR)
        return info,entry,st

    def test_command_allocation_rounds_to_power_of_two_and_deduplicates_queue_maps(self):
        info,entry,st=self.queue_fixture();entry['regions'].append(dict(entry['regions'][0]))
        with patch.object(Path,'stat',return_value=st),patch('host_special_metadata.subprocess.run',return_value=SimpleNamespace(stdout=json.dumps(info))) as query:
            value=ublkc_metadata(Path('/synthetic/char'),entry,Path('/synthetic/query'))
        self.assertEqual(value['queue_mapping_bytes'],12288);self.assertEqual(value['per_queue_allocation_bytes'],16384)
        self.assertEqual(value['allocation_bytes'],32768);self.assertEqual(value['mapped_queue_ids'],[0,1])
        self.assertEqual(value['status'],'SOURCE_CALCULATED');self.assertNotIn('resident_bytes',value)
        self.assertEqual(query.call_args.kwargs['timeout'],4)

    def test_device_owner_and_mapping_mismatch_cannot_produce_capacity(self):
        for change in ('owner','inode','depth','offset','length','private','no-pfn','gone-smaps'):
            info,entry,st=self.queue_fixture()
            if change=='owner':info['ublksrv_pid']=99
            elif change=='inode':info['inode']=43
            elif change=='depth':info['queue_depth']=0
            elif change=='offset':entry['regions'][0]['offset']=4096
            elif change=='length':entry['regions'][0]['end']+=4096
            elif change=='private':entry['regions'][0]['permissions']='r--p'
            elif change=='no-pfn':entry['regions'][0]['vm_flags']=[]
            else:entry['regions'][0]['smaps_status']='NOT_OBSERVED'
            with self.subTest(change=change),patch.object(Path,'stat',return_value=st),patch('host_special_metadata.subprocess.run',return_value=SimpleNamespace(stdout=json.dumps(info))):
                with self.assertRaises(ValueError):ublkc_metadata(Path('/synthetic/char'),entry,Path('/synthetic/query'))

    def test_pfn_zero_pss_and_query_failure_are_not_zero_allocation_or_complete_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proc,p=self.fixture(root,['1000-2000 r--s 0 00:06 42 /dev/ublkc7'])
            (p/'smaps').write_text((p/'smaps').read_text().replace('Pss: 2 kB','Pss: 0 kB').replace('rd sh','rd sh pf io'))
            info,entry,st=self.queue_fixture()
            original=Path.stat
            def fake(path,*args,**kwargs):
                return st if str(path).endswith('/root/dev/ublkc7') else original(path,*args,**kwargs)
            with patch('host_mapped_backing.pids',return_value=[10]),patch.object(Path,'stat',fake),patch('host_mapped_backing.ublkc_metadata',side_effect=subprocess.TimeoutExpired('query',4)):
                v=mapped_backing_inventory(root,[],proc=proc,ublk_query=Path('/synthetic/query'))
            r=v['special_objects'][0]['regions'][0]
            self.assertEqual(r['smaps_bytes']['Pss'],0);self.assertEqual(r['pss_interpretation'],'SKIPPED_PFNMAP_NOT_RESIDENCY')
            self.assertIsNone(v['ublkc_command_allocation_bytes']);self.assertEqual(v['coverage'],'INCOMPLETE')
            self.assertEqual(v['special_metadata_status'],'INCOMPLETE');self.assertEqual(v['special_objects'][0]['command_buffer']['status'],'UNKNOWN')

    def test_helper_build_and_abi_without_any_ring_or_device(self):
        source=(ROOT/'scripts/host_ublk_info.c').read_text()
        self.assertIn('open(argv[1], O_PATH | O_CLOEXEC)',source)
        self.assertEqual(source.count('sqe->cmd_op = UBLK_U_CMD_GET_DEV_INFO;'),1)
        self.assertNotIn('UBLK_U_CMD_START_DEV',source);self.assertNotIn('UBLK_U_CMD_STOP_DEV',source)
        with tempfile.TemporaryDirectory() as tmp:
            binary=build_ublk_query(Path(tmp))
            result=subprocess.run([str(binary),'--abi'],check=True,text=True,capture_output=True,timeout=5)
            self.assertEqual(json.loads(result.stdout),dict(descriptor_bytes=24,control_bytes=32))

    def test_explicit_detail_callback_passes_query_and_preserves_partial_metadata(self):
        source=(ROOT/'scripts/run_host_file_restore.py').read_text()
        fn=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.FunctionDef) and n.name=='capture_compact')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);compact={};snapshot=dict(status='OBSERVED',start_monotonic_ns=2,end_monotonic_ns=3,processes=[])
            env=dict(group=root,regular_roots=[root],out=root,guests=[],compact=compact,os=os,Path=Path,
                     bound_pss=lambda s,g:dict(snapshot=s),counters=lambda g:{},write=lambda p,v:None)
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'<detail-callback>','exec'),env)
            with patch.dict(os.environ,{'ASB_HOST_UBLK_INFO_QUERY':'/prebuilt/query'}),\
                 patch('host_file_monitor.residency_check',return_value=dict(status='OBSERVED',files=[])),\
                 patch('host_file_monitor.pss_snapshot',return_value=snapshot),\
                 patch('host_mapped_backing.mapped_backing_inventory',return_value=dict(status='OBSERVED',special_metadata_status='INCOMPLETE')) as query:
                env['capture_compact'](dict(before=dict(snapshot=dict(start_monotonic_ns=1))))
            self.assertEqual(query.call_args.kwargs,dict(ublk_query=Path('/prebuilt/query'),query_deleted_cache=True))
            self.assertEqual(compact['mapped_backing']['special_metadata_status'],'INCOMPLETE')
            # The old observation status does not imply the new metadata is complete.
            self.assertEqual(compact['status'],'OBSERVED')

    def test_metadata_helper_build_is_only_required_for_explicit_detail(self):
        source=(ROOT/'scripts/run_host_independent_pair.py').read_text()
        branch=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.If) and
                    'ASB_HOST_MEMORY_DETAIL' in ast.unparse(n.test))
        code=compile(ast.Module(body=[branch],type_ignores=[]),'<detail-build>','exec')
        for enabled in ('off','on'):
            with patch.dict(os.environ,{'ASB_HOST_MEMORY_DETAIL':enabled}),\
                 patch('host_special_metadata.build_ublk_query',return_value=Path('/query')) as build:
                result={};exec(code,dict(os=os,out=Path('/output'),result=result,time=__import__('time')))
                self.assertEqual(build.call_count,int(enabled=='on'))
                self.assertEqual('metadata_query_build' in result,enabled=='on')


if __name__=='__main__':unittest.main()
