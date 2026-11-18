"""Initial-cache report regression cases use synthetic counters only."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import report_guest_cache_start as report
from guest_diskstats import FIELDS


def point(t,pages,reads):
    counters={k:0 for k in FIELDS};counters['sectors_read']=reads
    return dict(boot_id='guest',start_monotonic_ns=t,end_monotonic_ns=t+1,
        guest_cache_bytes=pages*4096,
        metadata=dict(records=pages,duplicate_adds=0,missing_removes=0,out_of_range=0),
        meminfo_kib=dict(Cached=pages*4,Shmem=4,Dirty=0,Writeback=0),
        vmstat=dict(pgfault=t,pgmajfault=t),disks={'vda':dict(major=254,minor=0,**counters)},
        disk_is_partition={'vda':False})


def control(policy):
    return dict(status='PASS',policy=policy,before_sync=point(0,100,0),after_sync=point(2,100,0),
        after_control=point(4,10 if policy=='drop-clean' else 100,0),sync_ns=1,control_ns=1)


def group(policy):
    start=dict(status='PASS',policy='observe',observation=point(6,11 if policy=='drop-clean' else 100,10))
    end=dict(status='PASS',policy='observe',observation=point(8,80,30))
    observed=report.endpoints(control(policy),start,end);observed['tool_sum_ns']=10 if policy=='preserve' else 20
    return dict(policy=policy,roles={r:copy.deepcopy(observed) for r in report.ROLES},
        common=dict(action_identity=[],manifests=[],final_patches={},configuration={},metrics=dict(branch_replay_elapsed_ns=100)))


class InitialCacheReportTests(unittest.TestCase):
    def test_units_and_initial_cache_contrast(self):
        a,b=group('preserve'),group('drop-clean')
        self.assertEqual(b['roles']['early_1']['virtio_read_bytes'],20*512)
        compared=report.compare(a,b)
        self.assertEqual(compared['roles']['early_1']['tool_sum_ns']['delta_percent'],100)

    def test_rebound_is_recorded_and_contrast_checked_against_matched_run(self):
        start=dict(status='PASS',policy='observe',observation=point(6,100,10))
        end=dict(status='PASS',policy='observe',observation=point(8,100,30))
        value=report.endpoints(control('drop-clean'),start,end)
        self.assertEqual(value['post_control_rebound_bytes'],90*4096)
        a,b=group('preserve'),group('drop-clean');b['roles']['early_1']=dict(value,tool_sum_ns=20)
        with self.assertRaisesRegex(AssertionError,'initial cache contrast missing'):
            report.compare(a,b)

    def test_four_run_report_preserves_order_and_actions(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);runs=root/'runs.json';out=root/'out'
            runs.write_text(json.dumps([dict(policy=p,storage=str(root/f's{i}'),wrapper=str(root/f'w{i}'))
                                       for i,p in enumerate(report.ORDER)]))
            with patch.object(report,'check_run',side_effect=[group(p) for p in report.ORDER]):
                result=report.report(runs,out)
            self.assertEqual(result['live_runs'],4)
            self.assertEqual(len(result['comparisons']),2)
            self.assertEqual(len((out/'runs.csv').read_text().splitlines()),9)
            self.assertEqual(json.loads((out/'summary.json').read_text())['status'],'PASS')


if __name__=='__main__':unittest.main()
