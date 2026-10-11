import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from trenvx_diagnostic_metrics import DiagnosticMetrics, guest_meminfo, vmm_snapshot


class DiagnosticTests(unittest.TestCase):
    def test_guest_meminfo_units_and_missing_fields(self):
        raw='MemTotal: 100 kB\nMemAvailable: 80 kB\nCached: 20 kB\nAnonPages: 5 kB\nSlab: 3 kB\n4096\n'
        self.assertEqual(guest_meminfo(raw)['meminfo_bytes']['Cached'],20480)
        for bad in (raw.replace('4096','65536'),raw.replace('Slab: 3 kB\n','')):
            with self.assertRaises(RuntimeError):guest_meminfo(bad)

    def test_guest_meminfo_keeps_composition_fields(self):
        raw='MemTotal: 100 kB\nMemFree: 20 kB\nMemAvailable: 40 kB\nBuffers: 5 kB\nCached: 20 kB\nShmem: 2 kB\nAnonPages: 5 kB\nSlab: 3 kB\nSReclaimable: 1 kB\n4096\n'
        values=guest_meminfo(raw)['meminfo_bytes']
        self.assertEqual(values['MemFree'],20*1024)
        self.assertEqual(values['Shmem'],2*1024)
        self.assertEqual(values['SReclaimable'],1024)

    def fixture(self, root, membership='0::/owned/sandbox\n'):
        proc = root/'proc/12'
        proc.mkdir(parents=True)
        exe = root/'cloud-hypervisor'
        exe.touch()
        (proc/'exe').symlink_to(exe)
        (proc/'cmdline').write_bytes(b'cloud-hypervisor\0--api-socket\0/tmp/vmm-sandbox.socket\0')
        (proc/'stat').write_text('12 (process with spaces) '+' '.join(['S']+['0']*18+['1234'])+'\n')
        (proc/'cgroup').write_text(membership)
        (proc/'smaps_rollup').write_text('000-fff rw-p [rollup]\nRss: 4096 kB\nPss: 2048 kB\n')
        group=root/'cgroup/owned/sandbox'
        group.mkdir(parents=True)
        (group/'cgroup.procs').write_text('12\n')
        return exe, proc

    def test_exact_process_membership_and_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);exe,proc=self.fixture(root)
            row=vmm_snapshot('sandbox','owned',exe,proc_root=root/'proc',cgroup_root=root/'cgroup')
            self.assertEqual(row['memory_bytes']['Pss'],2097152)
            self.assertEqual(row['start_time_ticks'],1234)
            self.assertTrue(row['cgroup_membership_verified'])

    def test_wrong_group_rejected_before_reading_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);exe,proc=self.fixture(root,'0::/other/sandbox\n')
            (proc/'smaps_rollup').unlink()
            with self.assertRaisesRegex(RuntimeError,'cgroup mismatch'):
                vmm_snapshot('sandbox','owned',exe,proc_root=root/'proc',cgroup_root=root/'cgroup')

    def test_missing_measurement_is_not_zero_or_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);exe,proc=self.fixture(root)
            (proc/'smaps_rollup').write_text('Rss: 4096 kB\n')
            with self.assertRaisesRegex(RuntimeError,'missing Pss'):
                vmm_snapshot('sandbox','owned',exe,proc_root=root/'proc',cgroup_root=root/'cgroup')

    def test_absent_vmm_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);exe,proc=self.fixture(root)
            with self.assertRaisesRegex(RuntimeError,'found 0'):
                vmm_snapshot('absent','owned',exe,proc_root=root/'proc',cgroup_root=root/'cgroup')

    def test_timing_failure_and_disabled_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with DiagnosticMetrics(root,False).phase('disabled'):
                pass
            self.assertEqual(list(root.iterdir()),[])
            with self.assertRaises(ValueError):
                with DiagnosticMetrics(root,True).phase('create'):
                    raise ValueError('fixture failure')
            row=json.loads((root/'diagnostic-events.jsonl').read_text())
            self.assertEqual(row['status'],'FAIL')
            self.assertGreater(row['elapsed_ns'],0)

    def test_phase_only_does_not_enable_memory_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            metrics=DiagnosticMetrics(root,False,record_phases=True)
            with metrics.phase('restore'):
                metrics.snapshot('should-not-scan','missing','other',Path('/missing'))
            row=json.loads((root/'diagnostic-events.jsonl').read_text())
            self.assertEqual(row['phase'],'restore')
            self.assertEqual(row['status'],'PASS')
            self.assertGreater(row['elapsed_ns'],0)
            self.assertEqual([p.name for p in root.iterdir()],['diagnostic-events.jsonl'])
