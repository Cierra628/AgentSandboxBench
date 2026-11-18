"""Offline checkpoint import: no service, guest, or device calls."""
import csv
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_prettier6604_grpo import reuse_capacity_checkpoint
import validate_isolated_agentenv as validator
from report_provider_pair_timing import metrics


class CheckpointReuseTests(unittest.TestCase):
    def fixture(self,root):
        source=root/'first';out=root/'second';source.mkdir();out.mkdir()
        (source/'rollouts/backbone').mkdir(parents=True)
        (source/'rollouts/backbone/evidence').write_text('original prefix, not rerun')
        saved=dict(status='PASS',template='template',parent='parent',step=11,boundary={},process={})
        (source/'checkpoint-11.json').write_text(json.dumps(saved))
        (source/'capacity-checkpoint.json').write_text(json.dumps(dict(container='owned',config_home=str(root/'config'),service_url='url')))
        (source/'result.json').write_text(json.dumps(dict(status='PASS',cache_target_mib=64,
            actual_action_count=31,reused_action_count=0,
            cleanup=dict(snapshots_retained_for_reuse=['template'],service_sandbox_list_empty=True))))
        header='rollout\tsegment\tsandbox_id\tmanifest_sha256\n'
        (source/'segments.tsv').write_text(header+'backbone\tread_repo\tparent\ta\nbackbone\tread_typescript\tparent\tb\nearly_1\tpatch\tchild\tc\n')
        (out/'segments.tsv').write_text(header)
        return source,out,saved

    def test_imports_only_prefix_and_keeps_same_checkpoint_identity(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);source,out,saved=self.fixture(root)
            result=reuse_capacity_checkpoint(source,out,'owned',root/'config','url')
            self.assertEqual(result,saved)
            self.assertEqual((out/'checkpoint-11.json').read_bytes(),(source/'checkpoint-11.json').read_bytes())
            with (out/'segments.tsv').open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
            self.assertEqual([r['segment'] for r in rows],['read_repo','read_typescript'])
            self.assertEqual((out/'rollouts/backbone/evidence').read_text(),'original prefix, not rerun')
            self.assertFalse((out/'rollouts/early_1').exists())

    def test_other_service_cannot_import_retained_checkpoint(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);source,out,_=self.fixture(root)
            with self.assertRaises(AssertionError):reuse_capacity_checkpoint(source,out,'original-service',root/'config','url')
            self.assertFalse((out/'rollouts').exists())

    def test_new_damon_mode_requires_matching_source_and_copies_original_state(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);source,out,_=self.fixture(root)
            with self.assertRaises(AssertionError):reuse_capacity_checkpoint(source,out,'owned',root/'config','url',damon_before_checkpoint=True)
            p=source/'result.json';state=json.loads(p.read_text());state['damon_before_checkpoint']=True;p.write_text(json.dumps(state))
            record=dict(status='PASS',original_enabled='Y',disabled=dict(enabled='N',kdamond_pid=-1))
            (source/'damon-before-checkpoint.json').write_text(json.dumps(record))
            with self.assertRaises(AssertionError):reuse_capacity_checkpoint(source,out,'owned',root/'config','url')
            reuse_capacity_checkpoint(source,out,'owned',root/'config','url',damon_before_checkpoint=True)
            self.assertEqual(json.loads((out/'damon-before-checkpoint.json').read_text()),record)

    def test_explicit_recovery_binds_old_checkpoint_to_new_owned_service(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);source,out,saved=self.fixture(root)
            old=json.loads((source/'capacity-checkpoint.json').read_text())
            recovery=root/'recovery.json'
            recovery.write_text(json.dumps(dict(status='PASS',previous_binding=old,template=saved['template'],
                new_binding=dict(container='recovered',config_home=str(root/'config'),service_url='new-url'))))
            self.assertEqual(reuse_capacity_checkpoint(source,out,'recovered',root/'config','new-url',recovery),saved)
            self.assertTrue((out/'capacity-recovery.json').exists())

    def test_sequence_rejects_missing_sampled_64_target_before_allocation(self):
        for target in (None,128):
            with patch.object(validator.os,'geteuid',return_value=0),patch.object(validator.os,'posix_fallocate') as allocate:
                with self.assertRaisesRegex(ValueError,'capacity sequence requires'):
                    validator.main(SimpleNamespace(prettier6604_capacity_sequence=True,prettier6604_cache_target_mib=target,
                                                  prettier6604_snapshot_cache_pair=True))
                allocate.assert_not_called()

    def test_reused_timing_has_no_cold_start_or_snapshot_execution(self):
        from test_provider_pair_timing import PairTimingTests
        with tempfile.TemporaryDirectory() as name:
            source=PairTimingTests().fixture(Path(name)/'run','on',100)
            p=source/'result.json';state=json.loads(p.read_text())
            state.update(action_count=20,actual_action_count=20,reused_action_count=11,verified_action_count=31);p.write_text(json.dumps(state))
            p=source/'events.json';events=json.loads(p.read_text())
            p.write_text(json.dumps(events[2:]))
            result=metrics(source,'on')['metrics']
            self.assertEqual(result['cold_start_cli_ns'],0);self.assertEqual(result['checkpoint_cli_ns'],0)
            self.assertEqual(result['suffix_tool_sum_ns'],2000)
            self.assertEqual(result['task_tool_sum_ns'],2000);self.assertEqual(result['prefix_tool_sum_ns'],0)
            p.write_text(json.dumps(events))
            with self.assertRaises(AssertionError):metrics(source,'on')


if __name__=='__main__':unittest.main()
