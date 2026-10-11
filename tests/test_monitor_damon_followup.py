import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_monitor_damon_followup import audit
from run_exp1_monitor_balanced import schedule


class FollowupTests(unittest.TestCase):
    def test_completed_plan_cannot_start_more_replays(self):
        from continue_monitor_damon_groups import main
        for section in ('monitoring_return_to_original_replay','monitoring_default_policy'):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'configs').mkdir()
                (root/'configs/exp1-damon-control-plan.json').write_text(json.dumps({
                    section:dict(groups=[{}]*5,maximum_balanced_groups=5)}))
                previous_umask=os.umask(0o077)
                try:
                    with patch('continue_monitor_damon_groups.ROOT',root),patch('continue_monitor_damon_groups.audit') as audit_mock,patch('continue_monitor_damon_groups.subprocess.run') as run_mock:
                        with self.assertRaises(AssertionError):main(section)
                        audit_mock.assert_not_called();run_mock.assert_not_called()
                    self.assertFalse((root/'.artifacts').exists())
                finally:os.umask(previous_umask)

    def fixture(self,root,groups):
        directories=[];observed={}
        fixed={k:'fixture' for k in ('workload_image','sandbox_cpu','sandbox_memory_mib',
               'action_manifest_sha256','aenv_version','host_kernel','agentenv_container_image',
               'agentenv_container_id','overlaybd_io_engine','overlaybd_direct_io')}
        for number in range(groups+2):
            directory=root/str(number);directory.mkdir();directories.append(directory)
            balanced=number>=2;seed=20261001+number-2
            order=schedule(seed,1)[0] if balanced else ['off','light','light','off']
            rows=[dict(metadata=fixed,label=label,task_window_ns=1_020_000_000 if label=='light' else 1_000_000_000) for label in order]
            source=dict(monitor_damon_mode='default' if number==1 else 'damon_disabled',
                        balanced=balanced,seed=seed,order=order,runs=rows)
            (directory/'result.json').write_text(json.dumps(source))
            observed[directory]=dict(differences={},observations=[dict(position=n,mode=row['label'],
                task_window_seconds=row['task_window_ns']/1e9,controller_exec_seconds=1,
                guest_action_sum_seconds=1,sample_count=2 if row['label']=='light' else 0,
                steps_ms={'025':10},observed_min_free_gib=40) for n,row in enumerate(rows,1)])
        return directories,observed

    def test_one_group_cannot_accept_but_five_groups_use_declared_rule(self):
        for groups,verdict in ((1,'INCONCLUSIVE'),(5,'WITHIN_5_PERCENT_FOR_THIS_PROTOCOL')):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);directories,observed=self.fixture(root,groups)
                with patch('report_monitor_damon_followup.ROOT',root),patch('report_monitor_damon_followup.report',side_effect=lambda d:observed[d]):
                    value=audit(directories)
                self.assertEqual(value['monitoring_acceptance'],verdict)
                self.assertEqual(value['completed_full_replays'],8+groups*8)

    def test_mixed_environment_or_default_policy_in_formal_groups_is_rejected(self):
        for field in ('image','mode','seed'):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);directories,observed=self.fixture(root,1)
                p=directories[-1]/'result.json';value=json.loads(p.read_text())
                if field=='image':value['runs'][0]['metadata']['workload_image']='different'
                elif field=='mode':value['monitor_damon_mode']='default'
                else:value['seed']+=1
                p.write_text(json.dumps(value))
                with patch('report_monitor_damon_followup.ROOT',root),patch('report_monitor_damon_followup.report',side_effect=lambda d:observed[d]):
                    with self.assertRaises(AssertionError):audit(directories)

    def test_default_groups_are_audited_separately_and_cannot_mix_disabled_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directories,observed=self.fixture(root,5)
            for directory in directories[2:]:
                p=directory/'result.json';value=json.loads(p.read_text());value['monitor_damon_mode']='default';p.write_text(json.dumps(value))
            with patch('report_monitor_damon_followup.ROOT',root),patch('report_monitor_damon_followup.report',side_effect=lambda d:observed[d]):
                value=audit(directories,balanced_mode='default')
                self.assertEqual(value['balanced_mode'],'default')
                self.assertEqual(value['monitoring_acceptance'],'WITHIN_5_PERCENT_FOR_THIS_PROTOCOL')
                p=directories[-1]/'result.json';mixed=json.loads(p.read_text());mixed['monitor_damon_mode']='damon_disabled';p.write_text(json.dumps(mixed))
                with self.assertRaises(AssertionError):audit(directories,balanced_mode='default')


if __name__=='__main__':unittest.main()
