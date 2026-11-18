"""Synthetic events and tiny saved files only; no services, workload or devices."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from analyze_grpo_path_costs import analyze_run
from analyze_grpo_local_no_proxy import controller_phases,clipped_union
import test_grpo_path_costs as fixtures


def fixture():
    roles=('backbone','early_1','early_2','early_3','late_1','late_2','late_3');events=[]
    def add(name,start,duration):events.append(dict(index=len(events),stage=f'{len(events):04}-'+name,start_monotonic_ns=start,duration_ns=duration,exit_code=0))
    add('service-state',0,1)
    for i,r in enumerate(roles):add('cold-'+r,10+i,10)
    for i in range(70):add('controller-call',40+i,1)
    for i,r in enumerate(roles):
        for j,prefix in enumerate(('source-diff-','oracle-','upload-official-','official-')):add(prefix+r,200+4*i+j,10 if j==3 else 1)
    add('list-after',330,1)
    task=dict(roles=roles,controller_elapsed_ns=350,rollout_start_monotonic_ns=5,rollout_end_monotonic_ns=250,
        light_cleanup_timing=dict(start_monotonic_ns=300,end_monotonic_ns=320))
    return task,events


class LocalBreakdownTests(unittest.TestCase):
    def test_controller_phases_partition_total_and_preserve_unobserved_ends(self):
        task,events=fixture();r=controller_phases(task,events)
        self.assertEqual(sum(r['detail_ns'].values()),350)
        self.assertEqual(sum(r['groups_ns'].values()),350)
        self.assertEqual(r['detail_ns']['outside_recorded_events'],19)
        self.assertEqual(r['groups_ns']['cold_span'],16)
        self.assertEqual(r['groups_ns']['actions_and_downloads'],70)
        self.assertEqual(r['groups_ns']['cleanup'],20)

    def test_official_upload_is_counted_and_each_role_chain_is_complete(self):
        task,events=fixture();r=controller_phases(task,events)
        for p in r['final_checks']:
            self.assertEqual(p['upload_official_ns'],1)
            self.assertEqual(p['path_window_ns'],sum(p[k] for k in ('source_diff_ns','oracle_ns','upload_official_ns','official_ns','call_gap_ns')))
        self.assertEqual(r['last_final_check_role'],'late_3')
        events= [e for e in events if not e['stage'].endswith('upload-official-backbone')]
        with self.assertRaises(AssertionError):controller_phases(task,events)

    def test_impossible_order_or_negative_recording_remainder_is_rejected(self):
        task,events=fixture();task['controller_elapsed_ns']=300
        with self.assertRaises(AssertionError):controller_phases(task,events)
        task,events=fixture();task['light_cleanup_timing']['end_monotonic_ns']=400
        with self.assertRaises(AssertionError):controller_phases(task,events)

    def test_clipped_union_does_not_sum_overlapping_management_calls(self):
        events=[dict(start_monotonic_ns=0,duration_ns=10),dict(start_monotonic_ns=5,duration_ns=20)]
        self.assertEqual(clipped_union(events,8,15),7)
        self.assertEqual(clipped_union(events,0,8)+clipped_union(events,8,25),25)
        self.assertEqual(clipped_union(events,25,40),0)

    def test_new_schedule_requires_explicit_opt_in_and_old_default_stays(self):
        with tempfile.TemporaryDirectory() as tmp,patch('analyze_grpo_path_costs.ROOT',Path(tmp)):
            raw=fixtures.PathCostTests().fixture(Path(tmp)/'raw')
            self.assertEqual(analyze_run(raw,'off')['window_ns'],38)
            p=raw/'result.json';task=json.loads(p.read_text());task['grpo_setup_schedule']='per-guest-setup';p.write_text(json.dumps(task))
            with self.assertRaises(AssertionError):analyze_run(raw,'off')
            self.assertEqual(analyze_run(raw,'off',setup_schedule='per-guest-setup')['window_ns'],38)


if __name__=='__main__':unittest.main()
