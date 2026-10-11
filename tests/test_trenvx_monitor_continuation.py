import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import continue_trenvx_monitor_groups as driver


class MonitorContinuationTests(unittest.TestCase):
    def test_finished_plan_cannot_repeat_groups(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            plan=root/'plan.json'
            directories=[root/str(i) for i in range(5)]
            for directory in directories: directory.mkdir()
            plan.write_text(json.dumps(dict(completed_groups=[str(p) for p in directories],
                                            maximum_groups=5,seed_base=20261001)))
            with patch.object(driver,'ROOT',root),patch.object(driver,'PLAN',plan), \
                    patch.object(driver,'report') as audit,patch.object(driver,'guarded_run') as launch:
                with self.assertRaises(AssertionError): driver.main()
                audit.assert_not_called()
                launch.assert_not_called()
                self.assertFalse((root/'.artifacts').exists())

    def test_unstable_first_group_cannot_expand(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            plan=root/'plan.json'
            directory=root/'one-group'
            directory.mkdir()
            plan.write_text(json.dumps(dict(completed_groups=[str(directory)],maximum_groups=5,seed_base=20261001)))
            with patch.object(driver,'ROOT',root),patch.object(driver,'PLAN',plan), \
                    patch.object(driver,'report',return_value={'analysis':{'control':{'group_percent':[6]}}}), \
                    patch.object(driver,'guarded_run') as launch:
                with self.assertRaisesRegex(AssertionError,'unstable control'): driver.main()
                launch.assert_not_called()
                self.assertFalse((root/'.artifacts').exists())


if __name__=='__main__': unittest.main()
