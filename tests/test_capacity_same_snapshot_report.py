"""Saved metadata only; report must distinguish 51 actions from reused evidence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import report_cache_capacity_same_snapshot as report


class SameSnapshotReportTests(unittest.TestCase):
    def fixture(self,root):
        storage=root/'storage';wrapper=root/'wrapper';wrapper.mkdir()
        for index,name in enumerate(('prettier6604','prettier6604-128')):
            source=storage/name;source.mkdir(parents=True)
            def save(name,value):
                p=source/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value)+'\n')
            save('checkpoint-11.json',dict(template='one',parent='parent'))
            save('result.json',dict(actual_action_count=31 if index==0 else 20,reused_action_count=0 if index==0 else 11,
                 cleanup={('snapshots_retained_for_reuse' if index==0 else 'snapshots_removed'):['one']}))
            save('events.json',[dict(stage='0000-'+s) for s in (['cold-backbone','snapshot-call'] if index==0 else [])])
            for role,side in [('early_1','child-a'),('early_2','child-b')]:
                save('restored-'+role+'.json',dict(template='one',parent='parent',sandbox_id=str(index)+role,
                     status='PASS',process=dict(status='PASS'),boundary=dict(status='PASS')))
                save('capacity-control/'+role+'-result.json',dict(io_start_ns=1,io_end_ns=5))
                save('live-cache/'+side+'/samples.jsonl',dict(guest_publication_before=dict(guest_start_ns=2),guest_end_ns=4,
                     host_four_segment_current_union_bytes=4096,host_four_segment_unconfirmed_guest_capacity_bytes=0,
                     provider_segment_unattributed_resident_bytes=8192))
        def group(target):
            return dict(common=dict(action_identity=[],manifests=[],final_patches={},configuration={},
                 metrics={k:target for k in ('early_1_tool_sum_ns','early_2_tool_sum_ns','suffix_tool_sum_ns','branch_replay_elapsed_ns')}),
                 roles={r:dict(virtio_read_bytes=target,initial_cache_bytes=4096) for r in ('early_1','early_2')})
        return storage,wrapper,[group(64),group(128)]

    def test_counts_prefix_once_and_compares_only_suffix_timing(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);storage,wrapper,groups=self.fixture(root)
            with patch.object(report,'check_run',side_effect=groups):result=report.report(storage,wrapper,root/'out')
            self.assertEqual(result['actual_actions'],51);self.assertEqual(result['checkpoint_count'],1)
            self.assertEqual(result['comparison']['branch_replay_elapsed_ns']['delta_percent'],-50)
            self.assertNotIn('controller_elapsed_ns',result['comparison'])
            self.assertEqual(result['runs']['64']['roles']['early_1']['control_physical']['unknown_provider_range_bytes'],[8192,8192])

    def test_different_actual_checkpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);storage,wrapper,_=self.fixture(root)
            (storage/'prettier6604-128/checkpoint-11.json').write_text(json.dumps(dict(template='different',parent='parent')))
            with self.assertRaises(AssertionError):report.report(storage,wrapper,root/'out')

    def test_new_mode_carries_parent_state_and_earliest_restored_measurement(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);storage,wrapper,groups=self.fixture(root)
            for i,folder in enumerate(('prettier6604','prettier6604-128')):
                source=storage/folder;p=source/'result.json';state=json.loads(p.read_text())
                state['damon_before_checkpoint']=True;p.write_text(json.dumps(state))
                (source/'damon-before-checkpoint.json').write_text('{"status":"PASS","original_enabled":"Y"}')
                for role in ('early_1','early_2'):
                    groups[i]['roles'][role]['damon_before_checkpoint']=dict(earliest_restored_cache_bytes=4096+i*4096)
            with patch.object(report,'check_run',side_effect=groups):result=report.report(storage,wrapper,root/'out')
            self.assertTrue(result['damon_before_checkpoint'])
            self.assertEqual(result['comparison']['early_2_earliest_restored_cache_bytes']['delta'],-4096)
            self.assertIn('inherit disabled DAMON',result['limitations'][-1])

    def test_damon_modes_cannot_be_mixed_between_conditions(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);storage,wrapper,_=self.fixture(root)
            p=storage/'prettier6604/result.json';state=json.loads(p.read_text());state['damon_before_checkpoint']=True;p.write_text(json.dumps(state))
            with self.assertRaises(AssertionError):report.report(storage,wrapper,root/'out')


if __name__=='__main__':unittest.main()
