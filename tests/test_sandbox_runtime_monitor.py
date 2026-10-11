"""Local sampler and report regressions; no services, devices or VM mutation."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from sandbox_runtime_monitor import MEMFIELDS, boundary_runner
from report_sandbox_runtime import samples, curve_chunks, write_report


class RuntimeSamplingTests(unittest.TestCase):
    def test_managed_sampler_is_waited_for_and_leaves_no_proc_entry(self):
        import shutil
        from sandbox_runtime_monitor import RuntimeMonitor
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);state=root/'guest-state';(root/'raw').mkdir()
            def cli(label,*argv,timeout=15):
                if argv[0]=='upload':return ''
                if argv[0]=='download':
                    shutil.copytree(argv[2],Path(argv[3])/Path(argv[2]).name);return ''
                self.assertEqual(argv[:2],('exec','owned-test'))
                return subprocess.check_output(argv[2:],text=True,timeout=timeout).strip()
            with patch('sandbox_runtime_monitor.GUEST_ROOT',str(state)),patch('sandbox_runtime_monitor.SCRIPT',str(ROOT/'scripts/sandbox_runtime_monitor.py')):
                monitor=RuntimeMonitor(root/'raw',cli)
                try:
                    monitor.start('owned-test','backbone','idle')
                    pid=monitor.active['owned-test']['identity']['pid']
                    time.sleep(.22);monitor.stop('owned-test')
                    self.assertTrue(monitor.sessions[0]['collector_reaped'])
                    self.assertFalse(Path(f'/proc/{pid}').exists())
                    self.assertFalse(monitor.workers)
                    self.assertEqual(monitor.sessions[0]['status'],'PASS')
                    self.assertEqual(len(samples(root/'raw')[0]),1)
                    manifest=root/'raw/runtime-monitor.json'
                    value=json.loads(manifest.read_text());value['sessions'][0]['collector_reaped']=False
                    manifest.write_text(json.dumps(value))
                    with self.assertRaisesRegex(AssertionError,'managed collector was not reaped'):
                        samples(root/'raw')
                finally:
                    if monitor.active:monitor.stop('owned-test')

    def test_an_unreaped_owned_child_reproduces_find_net_error(self):
        import os
        # This child belongs to this test; it is always waited for below.
        child=subprocess.Popen([sys.executable,'-c','pass'])
        try:
            end=time.monotonic()+3;stat=Path(f'/proc/{child.pid}/stat')
            while stat.read_text().rsplit(')',1)[1].split()[0]!='Z':
                self.assertLess(time.monotonic(),end);time.sleep(.01)
            value=subprocess.run(['find',f'/proc/{child.pid}/net','-maxdepth','1'],capture_output=True,text=True,timeout=3,env=dict(os.environ,LC_ALL='C'))
            self.assertEqual(value.returncode,1)
            self.assertIn('Invalid argument',value.stderr)
        finally:child.wait(timeout=3)

    def test_performance_collection_has_aligned_cpu_and_multiple_device_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);proc=root/'proc';proc.mkdir();sysroot=root/'sys';out=root/'session';phase=root/'phase'
            (proc/'meminfo').write_text(''.join(f'{k}: 4096 kB\n' for k in MEMFIELDS))
            (proc/'vmstat').write_text('pgfault 1\npgmajfault 0\npgpgin 1\npgpgout 1\n')
            (proc/'stat').write_text('cpu 10 0 0 10 0 0 0 0 10 0\ncpu0 1\ncpu1 1\n')
            (proc/'diskstats').write_text('252 0 vda 1 0 1 0 1 0 1 0 0 0 0\n252 1 vdb 1 0 1 0 1 0 1 0 0 0 0\n')
            for name in ('vda','vdb'):(sysroot/'class/block'/name).mkdir(parents=True)
            phase.write_text('idle\t\t\n')
            child=subprocess.Popen([sys.executable,'-B',str(ROOT/'scripts/sandbox_runtime_monitor.py'),'collect',
                '--directory',str(out),'--phase-file',str(phase),'--sandbox-id','VM','--role','branch','--session-id','session',
                '--proc-root',str(proc),'--sys-root',str(sysroot),'--performance'])
            try:
                end=time.monotonic()+3
                while not (out/'ready').exists():
                    self.assertIsNone(child.poll());self.assertLess(time.monotonic(),end);time.sleep(.01)
                time.sleep(.22);child.terminate();self.assertEqual(child.wait(timeout=3),0)
                from report_sandbox_runtime import rows
                memory=rows(out/'guest-memory.tsv');cpu=rows(out/'guest-cpu.tsv');disks=rows(out/'guest-diskstats.tsv')
                self.assertEqual(len(cpu),len(memory));self.assertEqual(len(disks),2*len(cpu))
                self.assertEqual([r['monotonic_ns'] for r in cpu],[r['monotonic_ns'] for r in memory])
                self.assertTrue(all(r['is_partition']=='0' for r in disks))
                self.assertEqual(json.loads((out/'collector-status.json').read_text())['performance_version'],1)
            finally:
                if child.poll() is None:child.terminate();child.wait(timeout=3)

    def test_guest_phase_updates_keep_boundary_rows_and_label_finished_segment_waiting(self):
        original=(ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/workloads/sphinx-7590-rl-fork/guest-runner.sh').read_text()
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);guest=base/'phase-state';guest.mkdir()
            with patch('sandbox_runtime_monitor.GUEST_ROOT',str(guest)):
                variant=boundary_runner(original,guest_phases=True)
            function=variant[variant.index('sample_memory() {'):variant.index('sample_vmstat() {')]
            setup='out='+str(base)+'; sandbox_label=VM; branch=backbone; round=read_repo\n'
            command=setup+function+'\nsample_memory before 001\ncat '+str(guest/'phase')+'\nsample_memory complete 999\ncat '+str(guest/'phase')+'\n'
            result=subprocess.run(['bash','-euc',command],capture_output=True,text=True,check=True,timeout=3)
            self.assertEqual(result.stdout.splitlines(),['before\tread_repo\t001','waiting\tread_repo\t999'])
            rows=[line.split('\t') for line in (base/'guest-memory.tsv').read_text().splitlines()]
            self.assertEqual([r[1] for r in rows],['before','complete'])
            self.assertTrue(all(int(r[6])>0 for r in rows))
            # The historical default remains byte-for-byte reproducible.
            self.assertIn('"$phase" "$round" "$step"',boundary_runner(original))
            self.assertIn('"${phase/complete/waiting}"',boundary_runner(original,guest_phases=True))

    def test_sampler_keeps_sampling_between_phases_and_stops_with_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);proc=base/'proc';proc.mkdir();out=base/'session';phase=base/'phase'
            def mem(cached):
                (proc/'meminfo').write_text(''.join(f'{k}: {cached if k=="Cached" else 4096} kB\n' for k in MEMFIELDS))
            mem(1024);(proc/'vmstat').write_text('pgfault 1\npgmajfault 2\npgpgin 3\npgpgout 4\n')
            phase.write_text('tool\tread_repo\t001\n')
            child=subprocess.Popen([sys.executable,str(ROOT/'scripts/sandbox_runtime_monitor.py'),'collect',
                '--directory',str(out),'--phase-file',str(phase),'--sandbox-id','VM-A','--role','backbone',
                '--session-id','session-A','--proc-root',str(proc)])
            try:
                end=time.monotonic()+3
                while not (out/'ready').exists():
                    self.assertIsNone(child.poll());self.assertLess(time.monotonic(),end);time.sleep(.01)
                phase.write_text('waiting\t\t\n');mem(3072)
                time.sleep(.22)
                completed=subprocess.run([sys.executable,str(ROOT/'scripts/sandbox_runtime_monitor.py'),'stop','--directory',str(out)],capture_output=True,text=True,timeout=3)
                self.assertEqual(completed.returncode,0,completed.stderr)
                self.assertEqual(child.wait(timeout=3),0)
                with (out/'guest-memory.tsv').open() as f:data=list(csv.DictReader(f,delimiter='\t'))
                self.assertTrue(any(r['phase']=='waiting' and r['Cached_kib']=='3072' for r in data))
                self.assertTrue(all(r['sandbox_id']=='VM-A' for r in data))
                self.assertTrue(all(int(b['elapsed_ns'])>int(a['elapsed_ns']) for a,b in zip(data,data[1:])))
                self.assertIn('Slab_kib',data[0]);self.assertEqual(json.loads((out/'collector-status.json').read_text())['samples'],len(data))
            finally:
                if child.poll() is None:child.terminate();child.wait(timeout=3)

    def test_boundary_variant_preserves_commands_for_all_tasks(self):
        from task_metrics_prefix import without_sampling
        for task in ('prettier-6604','xarray-6992','sphinx-7590'):
            original=(ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/workloads'/f'{task}-rl-fork/guest-runner.sh').read_text()
            variant=boundary_runner(original)
            subprocess.run(['bash','-n'],input=variant,text=True,check=True,timeout=3)
            for line in original.splitlines():
                if 'bash -o pipefail -c "$command"' in line or 'actual_hash=' in line or 'start_ns=' in line or 'end_ns=' in line:
                    self.assertIn(line,variant)
            self.assertNotIn('monitor_step "$step" &',variant)
            self.assertIn('sample_memory before "$step"',variant)
            disabled=without_sampling(original)
            subprocess.run(['bash','-n'],input=disabled,text=True,check=True,timeout=3)
            self.assertNotIn('/proc/meminfo',disabled)
            self.assertNotIn('/proc/vmstat',disabled)
            self.assertNotIn('monitor_step "$step" &',disabled)
            self.assertIn('bash -o pipefail -c "$command"',disabled)

    def test_recorded_clock_gaps_are_not_interpolated(self):
        data=[dict(timestamp_ns=str(v)) for v in (10,100_000_010,2_000_000_010,5)]
        self.assertEqual([len(x) for x in curve_chunks(data)],[2,1,1])

    def test_restored_samples_cannot_keep_parent_instance_label(self):
        with tempfile.TemporaryDirectory() as temp:
            raw=Path(temp);directory=raw/'runtime/VM-B/session-B';directory.mkdir(parents=True)
            receipt=dict(status='PASS',samples=2)
            (directory/'collector-status.json').write_text(json.dumps(receipt))
            header='timestamp_ns\tmonotonic_ns\tsandbox_id\trole\tsession_id\n'
            # Parent label in restored child's directory must fail the audit.
            data='1\t1\tVM-A\tbackbone\tsession-B\n2\t2\tVM-A\tbackbone\tsession-B\n'
            for name in ('guest-memory.tsv','guest-vmstat.tsv'):(directory/name).write_text(header+data)
            manifest=dict(status='PASS',sessions=[dict(sandbox_id='VM-B',role='backbone',session_id='session-B',raw_directory='runtime/VM-B/session-B',receipt=receipt)])
            (raw/'runtime-monitor.json').write_text(json.dumps(manifest))
            with self.assertRaises(AssertionError):samples(raw)

    def test_incomplete_monitor_cannot_be_reported_as_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            raw=Path(temp);(raw/'runtime-monitor.json').write_text('{"status":"INCOMPLETE"}')
            with self.assertRaises(AssertionError):samples(raw)

    def test_historical_samples_are_split_by_actual_restored_instance(self):
        with tempfile.TemporaryDirectory() as temp:
            raw=Path(temp)/'raw';raw.mkdir();out=Path(temp)/'report';out.mkdir()
            (raw/'segments.tsv').write_text('rollout\tsegment\tsandbox_id\nbackbone\tfirst\tVM-A\nbackbone\tsecond\tVM-B\n')
            for i,segment in enumerate(('first','second')):
                p=raw/'rollouts/backbone'/segment;p.mkdir(parents=True)
                (p/'guest-memory.tsv').write_text('timestamp_ns\tMemTotal_kib\tMemFree_kib\tCached_kib\tAnonPages_kib\tBuffers_kib\tShmem_kib\n'+f'{i*1000000000}\t4096\t2048\t1024\t256\t128\t32\n')
            grouped,scope=samples(raw)
            self.assertEqual(set(grouped),{'VM-A','VM-B'});self.assertIn('historical',scope)
            result=write_report(raw,out)
            self.assertEqual(len(result['per_sandbox']),2)
            self.assertTrue(all(v['slab_status']=='NOT_MEASURED' for v in result['per_sandbox']))
            self.assertTrue((out/'memory.svg').exists())


if __name__=='__main__':unittest.main()
