"""Offline tests only: temporary regular files, mocked block metadata, saved data."""
import ast
import copy
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from host_file_monitor import mincore_file
from run_host_file_restore import phase_pss,restore_command,report_restore,device_residency,paired_memory_pss


class RestoreTests(unittest.TestCase):
    def test_explicit_command_keeps_host_and_selects_24g_restore(self):
        command=restore_command(['bash','runner','--host-file-monitor','--prettier6604-readonly-replay','--light-monitor'])
        self.assertIn('--host-file-monitor',command);self.assertIn('--host-restore-short',command)
        self.assertNotIn('--prettier6604-readonly-replay',command);self.assertNotIn('--light-monitor',command)
        from validate_isolated_agentenv import prettier_capacity,GIB
        args=SimpleNamespace(host_restore_short=True)
        self.assertEqual(prettier_capacity(args,True,None,'bpo',False,True),24*GIB)
        with self.assertRaises(ValueError):prettier_capacity(args,True,None,'grpo',False,True)

    def test_block_st_size_zero_uses_verified_ioctl_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fake-device';path.write_bytes(b'x'*4096)
            s=path.stat()
            info=SimpleNamespace(st_mode=stat.S_IFBLK,st_dev=s.st_dev,st_ino=s.st_ino,st_rdev=100,
                                 st_size=0,st_mtime_ns=s.st_mtime_ns)
            def ioctl(fd,request,capacity,mutate):
                self.assertEqual(request,0x80081272);capacity[0]=4096
            with patch('host_file_monitor.os.fstat',return_value=info),patch('host_file_monitor.fcntl.ioctl',side_effect=ioctl):
                value=mincore_file(path,block_identity=(s.st_dev,s.st_ino,100),block_length=4096)
                self.assertEqual(value['size'],0);self.assertEqual(value['mapping_length'],4096)
                self.assertEqual(value['pages'],1);self.assertEqual(value['resident_bytes'],4096)
                self.assertTrue(value['block_device']);self.assertTrue(value['stable'])
                changed=SimpleNamespace(**{**vars(info),'st_mtime_ns':info.st_mtime_ns+1})
                with patch('host_file_monitor.os.fstat',side_effect=[info,changed]):
                    value=mincore_file(path,block_identity=(s.st_dev,s.st_ino,100),block_length=4096)
                self.assertTrue(value['metadata_changed']);self.assertTrue(value['stable'])
                with self.assertRaises(ValueError):mincore_file(path)
                with self.assertRaises(ValueError):mincore_file(path,block_identity=(s.st_dev,s.st_ino,101),block_length=4096)
                with self.assertRaises(ValueError):mincore_file(path,block_identity=(s.st_dev,s.st_ino,100),block_length=8192)

    def test_target_device_is_first_and_pss_brackets_only_that_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths=[Path(tmp)/name for name in ('disk','ram')]
            for path in paths:path.write_bytes(b'fixture')
            keys=[(p.stat().st_dev,p.stat().st_ino,p.stat().st_rdev) for p in paths]
            devices={i:dict(identity=key,path=path,target=path.name,owner_pid=1)
                     for i,(key,path) in enumerate(zip(keys,paths))}
            guests=[dict(binding=dict(pid=1,start_ticks='1'))];events=[]
            def pss(group):
                events.append('pss');now=time.monotonic_ns()
                return dict(status='OBSERVED',start_monotonic_ns=now,end_monotonic_ns=now,
                    processes=[dict(pid=1,start_ticks='1',comm='firecracker',pss=4096,
                        pss_anon=1024,pss_file=2048,pss_shmem=1024)])
            def mincore(path,*,block_identity,block_length):
                name='ram' if block_identity==keys[1] else 'disk';events.append(name)
                return dict(device=block_identity[0],inode=block_identity[1],rdev=block_identity[2],
                    resident_bytes=4096,stable=True,mapping_length=block_length)
            def ioctl(fd,request,capacity,mutate):capacity[0]=4096
            with patch('host_file_monitor.mincore_file',side_effect=mincore),\
                 patch('host_file_monitor.pss_snapshot',side_effect=pss),\
                 patch('run_host_file_restore.fcntl.ioctl',side_effect=ioctl):
                files,pair=device_residency(devices,keys[1],Path(tmp),guests,capture_memory_pss=True)
                self.assertEqual(events,['pss','ram','pss','disk'])
                events.clear()
                device_residency(devices,keys[1],Path(tmp),guests,capture_memory_pss=True,after_memory_query=lambda pair:events.append('ordinary-and-mapped'))
                self.assertEqual(events,['pss','ram','pss','ordinary-and-mapped','disk'])
                self.assertEqual([f['kind'] for f in files],['memory-device','other-owned-block-device'])
                self.assertEqual(pair['status'],'OBSERVED')
                self.assertEqual(pair['before']['vmm_pss_anon_bytes'],1024)
                events.clear()
                files,pair=device_residency(devices,keys[1],Path(tmp),guests)
                self.assertEqual(events,['ram','disk']);self.assertIsNone(pair)

    def test_memory_pair_keeps_missing_anon_unknown_and_rejects_time_or_pid_change(self):
        guests=[dict(binding=dict(pid=1,start_ticks='1'))]
        def snap(t):
            return dict(status='OBSERVED',start_monotonic_ns=t,end_monotonic_ns=t+1,
                processes=[dict(pid=1,start_ticks='1',comm='firecracker',pss=4096,pss_anon=None)])
        device=dict(device=6,inode=1,rdev=2,resident_bytes=8192,start_monotonic_ns=3,end_monotonic_ns=4)
        pair=paired_memory_pss(snap(1),device,snap(5),guests)
        self.assertEqual(pair['status'],'MISSING_ANON_PSS')
        self.assertIsNone(pair['before']['vmm_pss_anon_bytes'])
        with self.assertRaises(ValueError):paired_memory_pss(snap(1),device,snap(2),guests)
        reused=snap(5);reused['processes'][0]['start_ticks']='2'
        with self.assertRaises(ValueError):paired_memory_pss(snap(1),device,reused,guests)

    def test_phase_pss_accepts_one_and_two_not_pid_reuse(self):
        for n in (1,2):
            guests=[dict(binding=dict(pid=i,start_ticks=str(i))) for i in range(1,n+1)]
            rows=[dict(status='OBSERVED',start_monotonic_ns=t,end_monotonic_ns=t+1,
                       processes=[dict(pid=i,start_ticks=str(i),comm='firecracker',pss=4096) for i in range(1,n+1)]) for t in (10,20)]
            self.assertEqual(len(phase_pss(rows,guests,9,22)),2)
            for change in ('reused','missing','extra','zero','partial','duplicate'):
                values=copy.deepcopy(rows)
                if change=='reused':values[0]['processes'][0]['start_ticks']='999'
                elif change=='missing':values[0]['processes'].pop()
                elif change=='extra':values[0]['processes'].append(dict(pid=99,start_ticks='99',comm='firecracker',pss=4096))
                elif change=='zero':values[0]['processes'][0]['pss']=0
                elif change=='partial':values[0]['status']='PARTIAL'
                else:values[1]=values[0]
                with self.assertRaises(ValueError):phase_pss(values,guests,9,22)

    def test_new_controller_path_has_no_suffix_or_official_pass(self):
        code=(ROOT/'scripts/run_prettier6604_grpo.py').read_text()
        self.assertIn("if host_restore:executed={'backbone':rollouts['backbone'][:2]}",code)
        tree=ast.parse(code)
        # Prefix and scaling modes share the same guard; test its meaning rather
        # than requiring the old one-condition source spelling.
        empty_roles=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and any(
            isinstance(a,ast.Assign) and ast.unparse(a.targets[0])=='final_roles' and isinstance(a.value,ast.List) and not a.value.elts for a in n.body))
        no_official=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and any(
            isinstance(a,ast.Assign) and ast.unparse(a.targets[0])=="result['official_evaluation']" and isinstance(a.value,ast.Constant) and a.value.value=='NOT_RUN_PREFIX_ONLY' for a in n.body))
        for guard in (empty_roles,no_official):
            check=compile(ast.Expression(guard.test),'<prefix-guard>','eval')
            self.assertTrue(eval(check,dict(host_restore=True,prefix_count=None,scaling=None)))
            self.assertFalse(eval(check,dict(host_restore=False,prefix_count=None,scaling=None)))
        helper=(ROOT/'scripts/run_host_file_restore.py').read_text()
        self.assertLess(helper.index("phase['residency']=await"),helper.index("for item in reversed(phase['guests']):await remove"))
        self.assertIn("for count in (1,2):",helper)

    def test_report_with_signed_deltas_and_saved_guest_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'host';raw=root/'raw';source.mkdir();raw.mkdir()
            def write(path,v):path.write_text(json.dumps(v))
            write(raw/'result.json',dict(status='PASS',action_count=11))
            phases=[];pss=[];sessions=[];curves={}
            for n,start in ((1,100),(2,200)):
                guests=[]
                for i in range(n):
                    sid=f'{n}-{i}';binding=dict(pid=n*10+i,start_ticks='1')
                    guests.append(dict(sandbox_id=sid,role='early_'+str(i+1),binding=binding))
                    sessions.append(dict(sandbox_id=sid,status='PASS'))
                    curves[sid]=dict(chunks=[[dict(monotonic_ns=str(t),Cached_kib='100') for t in (1,2)]])
                phases.append(dict(sandboxes=n,baseline={'file':1000,'anon':10,'current':1100},
                                   start_monotonic_ns=start,end_monotonic_ns=start+20,guests=guests,residency={}))
                for t in (start+1,start+10):
                    pss.append(dict(status='OBSERVED',start_monotonic_ns=t,end_monotonic_ns=t+1,
                                    processes=[dict(**g['binding'],comm='firecracker',pss=4096) for g in guests]))
            write(raw/'host-restore.json',dict(status='PASS',groups=phases))
            write(raw/'runtime-monitor.json',dict(status='PASS',sessions=sessions))
            write(root/'host-file-residency.json',dict(status='OBSERVED',resident_bytes=1100,cgroup_before={'file':1000},cgroup_after={'file':1000},
                minimum_unaccounted_or_timing_bytes=100,accounting='SHORTFALL_REQUIRES_RESOLUTION',cpu_ns=1))
            for phase in phases:phase['residency']['minimum_unaccounted_or_timing_bytes']=0
            write(raw/'host-restore.json',dict(status='PASS',groups=phases))
            (source/'memory.tsv').write_text('monotonic_ns\tfile\tanon\tcurrent\n101\t900\t20\t1100\n110\t950\t30\t1200\n201\t910\t20\t1100\n210\t960\t30\t1200\n')
            (source/'pss.jsonl').write_text('\n'.join(map(json.dumps,pss)))
            with patch('report_sandbox_runtime.samples',return_value=(curves,'test')):
                value=report_restore(source,raw,root/'report')
            self.assertEqual(value['groups'][0]['file_delta_range_bytes'],[-100,-50])
            self.assertFalse(value['percentage_output']);self.assertTrue((root/'report/restore.png').exists())
            self.assertEqual(value['functional_validation'],'PASS')
            self.assertEqual(value['accounting_validation'],'SHORTFALL_REQUIRES_RESOLUTION')


if __name__=='__main__':unittest.main()
