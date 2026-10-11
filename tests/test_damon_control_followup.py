import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from guest_damon_control import COMPARISON, REVERSE_COMPARISON
from report_damon_control_followup import summarize


class FollowupAuditTests(unittest.TestCase):
    def fixture(self, root):
        directories, reports = [], {}
        for group, order in enumerate((COMPARISON, REVERSE_COMPARISON), 1):
            directory = root / str(group); directory.mkdir(); directories.append(directory)
            rows, observations = [], []
            for position, mode in enumerate(order, 1):
                raw = directory / str(position); raw.mkdir()
                (raw / 'sandbox-id.txt').write_text(f'{group}-{position}')
                (raw / 'guest-mounts.txt').write_text('/dev/vdb / ext4 rw 0 0\n')
                rows.append(dict(raw_dir=str(raw), metadata={k: 'fixed' for k in
                            ('workload_image', 'sandbox_cpu', 'sandbox_memory_mib', 'action_manifest_sha256',
                             'aenv_version', 'host_kernel', 'agentenv_container_image', 'agentenv_container_id',
                             'overlaybd_io_engine', 'overlaybd_direct_io')}))
                state = dict(start=dict(meminfo_kib={'Cached': 200}),
                             before=dict(meminfo_kib={'MemAvailable': 1024, 'MemFree': 512, 'MemTotal': 2048, 'Buffers': 10, 'Cached': 200}),
                             before_command=dict(counter_deltas={'pgsteal_file': 0}),
                             during_command=dict(counter_deltas={'pgsteal_file': 0, 'workingset_refault_file': 0}))
                observations.append(dict(position=position, damon_control_mode=mode,
                                    git_add_trace2=dict(directory=dict(seconds=.03, counts={})),
                                    steps_ms={'025': 70}, task_window_seconds=4, controller_exec_seconds=4,
                                    guest_action_sum_seconds=3,
                                    command_clock=dict(monotonic_raw_ns=60000000, user_ns=30000000, system_ns=20000000),
                                    guest_diskstats=dict(vdb=dict(reads_completed=0)), guest_cache_state=state,
                                    host_wait_bound=dict(summed_runnable_wait_upper_bound_ns=1000000),
                                    host_kvm_faults=dict(threads=[dict(successful_async_setup=0)]),
                                    damon_control={}, observed_min_free_gib=40))
            (directory / 'result.json').write_text(json.dumps(dict(status='PASS', runs=rows, damon_control_modes=order)))
            (directory / 'offline-report.json').write_text('{}')
            reports[directory] = dict(status='PASS', observations=observations)
        return directories, reports

    def test_rejects_reused_instance_across_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); directories,reports=self.fixture(root)
            with patch('report_damon_control_followup.ROOT',root), patch('report_damon_control_followup.report',side_effect=lambda p:reports[p]):
                self.assertEqual(summarize(directories)['full_replays'],8)
                (directories[1]/'1/sandbox-id.txt').write_text('1-1')
                with self.assertRaises(AssertionError): summarize(directories)

    def test_rejects_mixed_image_and_wrong_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); directories,reports=self.fixture(root)
            path=directories[1]/'result.json'; original=json.loads(path.read_text())
            with patch('report_damon_control_followup.ROOT',root), patch('report_damon_control_followup.report',side_effect=lambda p:reports[p]):
                bad=json.loads(json.dumps(original));bad['runs'][0]['metadata']['workload_image']='different';path.write_text(json.dumps(bad))
                with self.assertRaises(AssertionError): summarize(directories)
                original['damon_control_modes']=COMPARISON;path.write_text(json.dumps(original))
                with self.assertRaises(AssertionError): summarize(directories)


if __name__ == '__main__': unittest.main()
