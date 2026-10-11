"""Synthetic counters and owned local processes only; no Docker, VM or device changes."""
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from guest_performance import cpu,disks,CPUFIELDS
from host_sandbox_monitor import Binding,HostMonitor,identity,read
from report_sandbox_performance import rates,guest_cpu,check_ids


class PerformanceTests(unittest.TestCase):
    def test_transient_find_control_reaps_owned_children(self):
        from guest_find_diagnostic import transient_control
        def captured(*args):
            time.sleep(.003)
            return dict(error_processes_after_find=[])
        with patch('guest_find_diagnostic.capture',side_effect=captured):
            value=transient_control('command',Path('/unused'))
        self.assertTrue(value['owned_children_reaped'])
        self.assertGreater(value['owned_child_count'],0)
        self.assertEqual([len(value[k]) for k in ('quiet_before','active','quiet_after')],[3,8,3])

    def test_transient_find_failure_still_waits_for_owned_children(self):
        from guest_find_diagnostic import transient_control
        children=[];calls=0;launch=subprocess.Popen
        def owned(*args,**kwargs):
            self.assertEqual(args[0],['/bin/sleep','0.005'])
            child=launch(*args,**kwargs);children.append(child);return child
        def fail(*args):
            nonlocal calls
            calls+=1;time.sleep(.003)
            if calls==5:raise RuntimeError('simulated diagnostic failure')
            return dict(error_processes_after_find=[])
        with patch('guest_find_diagnostic.capture',side_effect=fail),patch('guest_find_diagnostic.subprocess.Popen',side_effect=owned):
            with self.assertRaisesRegex(RuntimeError,'simulated diagnostic failure'):transient_control('command',Path('/unused'))
        self.assertTrue(children)
        self.assertTrue(all(child.poll() is not None for child in children))

    def test_guest_cpu_guest_ticks_are_not_added_twice(self):
        a=cpu('cpu 10 0 0 10 0 0 0 0 10 0\ncpu0 1\ncpu1 1\n')
        b=cpu('cpu 20 0 0 20 0 0 0 0 20 0\ncpu0 1\ncpu1 1\n')
        data=[dict(monotonic_ns=str(i*100_000_000),timestamp_ns=str(i*100_000_000),cpu_count='2',
            **{k+'_ticks':str(v) for k,v in zip(CPUFIELDS,r)}) for i,r in enumerate((a,b))]
        points,invalid=guest_cpu(data)
        self.assertEqual(invalid,0);self.assertEqual(points[0]['values'],[50,0])

    def test_disk_sector_conversion_and_partition_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            sysroot=Path(temp);(sysroot/'class/block/vdb').mkdir(parents=True)
            (sysroot/'class/block/vdb1').mkdir();(sysroot/'class/block/vdb1/partition').write_text('1')
            text='252 0 vdb 1 0 2048 0 1 0 2048 0 0 0 0\n252 1 vdb1 1 0 1024 0 1 0 1024 0 0 0 0\n'
            data=disks(text,sysroot);self.assertEqual([r[3] for r in data],['0','1'])
            rows=[dict(monotonic_ns=str(i*100_000_000),timestamp_ns='0',sectors_read=str(i*2048)) for i in range(2)]
            points,invalid=rates(rows,['sectors_read'],512/2**20)
            self.assertEqual(points[0]['values'],[10]);self.assertEqual(invalid,0)

    def test_counter_reset_and_clock_gap_are_missing_rates(self):
        data=[dict(monotonic_ns=str(t),timestamp_ns='0',n=str(n)) for t,n in [(0,9),(100_000_000,1),(2_000_000_000,3)]]
        points,invalid=rates(data,['n']);self.assertEqual(invalid,2)
        self.assertTrue(all(r['values'] is None for r in points))

    def test_restored_and_branch_labels_must_match_the_session(self):
        session=dict(sandbox_id='child',role='branch',session_id='new')
        data=[dict(sandbox_id='parent',role='branch',session_id='new',monotonic_ns='1')]
        with self.assertRaises(AssertionError):check_ids(data,session)

    def test_host_read_and_sampler_only_observe_owned_test_process(self):
        pid=os.getpid()
        class Owned:
            def resolve(self,sid):return dict(identity(pid),binding_source='local test process')
            def owned(self,value):return value==pid
        with tempfile.TemporaryDirectory() as temp:
            monitor=HostMonitor(Owned(),'VM-test','backbone','session',Path(temp)/'host','test')
            try:time.sleep(.22)
            finally:receipt=monitor.stop()
            self.assertEqual(receipt['status'],'PASS');self.assertGreaterEqual(receipt['samples'],2)
            self.assertEqual(receipt['identity']['pid'],pid)
            self.assertGreaterEqual(receipt['pss_samples'],1)
            value=read(pid,pss=True);self.assertGreater(value['pss_kib'],0)

    def test_changed_ownership_stops_host_sampling_and_cannot_pass(self):
        pid=os.getpid()
        class Owned:
            active=True
            def resolve(self,sid):return identity(pid)
            def owned(self,value):return self.active and value==pid
        binding=Owned()
        with tempfile.TemporaryDirectory() as temp:
            monitor=HostMonitor(binding,'VM-test','branch','session',Path(temp)/'host','test')
            binding.active=False;time.sleep(.15)
            with self.assertRaises(AssertionError):monitor.stop()
            self.assertFalse(monitor.thread.is_alive());self.assertEqual(monitor.receipt['status'],'FAIL')


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.proc=self.root/'proc';self.cg=self.root/'cgroup'
        (self.proc/'sys/kernel/random').mkdir(parents=True);(self.proc/'sys/kernel/random/boot_id').write_text('boot')
        (self.proc/'10').mkdir();(self.proc/'10/cgroup').write_text('0::/owned\n');(self.cg/'owned').mkdir(parents=True)
        self.raw=self.root/'run/mount/controller/raw'
        info=[dict(State=dict(Running=True,Pid=10),Config=dict(Labels={'agentsandboxbench.run_id':'run','agentenv.experiment':'isolated-storage-check'}))]
        with patch('host_sandbox_monitor.subprocess.check_output',return_value=json.dumps(info)):
            self.binding=Binding('owned-container',self.raw,self.proc,self.cg)
        self.add(11,'/owned');self.add(12,'/other')
        (self.cg/'owned/cgroup.procs').write_text('11\n12\n')

    def tearDown(self):self.temp.cleanup()

    def add(self,pid,cgroup,start='100'):
        p=self.proc/str(pid);p.mkdir(exist_ok=True)
        (p/'cgroup').write_text('0::'+cgroup+'\n');(p/'comm').write_text('firecracker\n')
        (p/'cmdline').write_bytes(b'firecracker\0--api-sock\0/owned/api.sock\0')
        fields=['0']*24;fields[0]='S';fields[19]=start;(p/'stat').write_text(str(pid)+' (firecracker) '+' '.join(fields))

    def test_only_owned_cgroup_and_matching_mmds_can_bind(self):
        with patch('host_sandbox_monitor.sandbox_id',return_value='sandbox') as query:
            value=self.binding.resolve('sandbox')
        self.assertEqual(value['pid'],11);self.assertEqual(query.call_count,1)
        with patch('host_sandbox_monitor.sandbox_id',return_value='different'):
            with self.assertRaises(AssertionError):self.binding.resolve('sandbox')

    def test_ambiguous_identity_and_pid_reuse_are_rejected(self):
        self.add(13,'/owned');(self.cg/'owned/cgroup.procs').write_text('11\n13\n')
        with patch('host_sandbox_monitor.sandbox_id',return_value='same'):
            with self.assertRaises(AssertionError):self.binding.resolve('same')
        (self.cg/'owned/cgroup.procs').write_text('11\n')
        def reused(path):self.add(11,'/owned',start='changed');return 'same'
        with patch('host_sandbox_monitor.sandbox_id',side_effect=reused):
            with self.assertRaises(AssertionError):self.binding.resolve('same')


class FrozenContinuationTests(unittest.TestCase):
    def test_find_error_process_identity_and_disappeared_paths(self):
        from guest_find_diagnostic import error_processes
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'17').mkdir()
            (root/'17/stat').write_text('17 (owned test) Z 1 '+'0 '*17+'12345 0')
            result=error_processes('find: /proc/17/task/17/net invalid; /proc/17/net invalid; /proc/99/net disappeared',root)
            self.assertEqual(len(result),2)
            self.assertEqual(result[0],dict(pid=17,comm='owned test',state='Z',parent_pid=1,start_ticks='12345'))
            self.assertEqual(result[1]['status'],'DISAPPEARED_AFTER_FIND')

    def test_find_diagnostic_preserves_hidden_error_and_both_pipeline_exits(self):
        from guest_find_diagnostic import capture
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            command='find ./missing -name "docutils" -maxdepth 6 -type d 2>/dev/null | head'
            result=capture(command,Path(temp))
            self.assertEqual(result['original_exit_code'],1)
            self.assertEqual(result['pipeline_exit_codes'],[1,0])
            self.assertEqual(result['original_stderr'],'')
            self.assertIn('./missing',result['find_stderr'])
            self.assertEqual(result['original_stdout'],result['diagnostic_stdout'])
            self.assertEqual(result['command_sha256'],hashlib.sha256(command.encode()).hexdigest())
            self.assertEqual(list(Path(temp).iterdir()),[])

    def test_both_branches_preserve_original_steps_outputs_and_expected_failures(self):
        from run_runtime_monitor_smoke import check_continuation
        from sphinx7590_inputs import sha
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);reference=root/'reference';raw=root/'raw'
            expected=[0,0,1,1,0,1,1]
            cfg=dict(expected_exit_codes={'trace_cpp_parser':expected},input_sha256={})
            for base,role in [(reference,'backbone'),(raw,'backbone'),(raw,'branch')]:
                p=base/'rollouts'/role/'trace_cpp_parser';p.mkdir(parents=True)
                (p/'actions.tsv').write_text('step\tsha256\tcommand\noriginal immutable actions\n')
                (p/'patch.diff').write_text('unchanged patch')
                for kind in ('stdout','stderr'):
                    (p/kind).mkdir()
                    for i in range(1,8):(p/kind/f'{i:03}.log').write_text(f'{kind} {i}')
                (p/'steps.tsv').write_text('step\tbranch\tround\texit_code\tduration_ns\n'+''.join(
                    f'{i:03}\t{role}\ttrace_cpp_parser\t{code}\t100\n' for i,code in enumerate(expected,1)))
            cfg['input_sha256']['segments/trace_cpp_parser/actions.tsv']=sha(reference/'rollouts/backbone/trace_cpp_parser/actions.tsv')
            for role in ('backbone','branch'):self.assertEqual(check_continuation(raw,reference,cfg,role)['action_count'],7)
            p=raw/'rollouts/branch/trace_cpp_parser'
            original=(p/'steps.tsv').read_text()
            (p/'steps.tsv').write_text(original.replace('005\tbranch\ttrace_cpp_parser\t0', '005\tbranch\ttrace_cpp_parser\t1'))
            with self.assertRaisesRegex(AssertionError,'branch frozen continuation exits'):check_continuation(raw,reference,cfg,'branch')
            (p/'steps.tsv').write_text(original)
            (p/'stderr/003.log').write_text('different failure output')
            with self.assertRaises(AssertionError):check_continuation(raw,reference,cfg,'branch')


if __name__=='__main__':unittest.main()
