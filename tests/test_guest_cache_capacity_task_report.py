"""Saved-task capacity comparison uses fake run metadata only."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import report_guest_cache_capacity_task as report


class CapacityTaskReportTests(unittest.TestCase):
    def test_delta_direction_and_original_task_identity_are_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);p=root/'runs.json'
            p.write_text(json.dumps({str(t):dict(storage=str(root/f's{t}'),wrapper=str(root/f'w{t}')) for t in [128,64]}))
            def group(target,value):
                return dict(target_mib=target,common=dict(action_identity=[],manifests=[],final_patches={},configuration={},
                    metrics={k:value for k in ['early_1_tool_sum_ns','early_2_tool_sum_ns','suffix_tool_sum_ns','branch_replay_elapsed_ns','controller_elapsed_ns']}))
            with patch.object(report,'check_run',side_effect=[group(128,100),group(64,120)]):
                result=report.report(p,root/'out')
            self.assertEqual(result['live_runs'],2)
            self.assertEqual(result['comparison']['branch_replay_elapsed_ns']['delta_percent'],20)


if __name__=='__main__':unittest.main()
