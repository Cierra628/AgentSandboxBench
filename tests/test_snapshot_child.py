"""Sampler startup cancellation without attaching BPF or starting a VM."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import collect_snapshot_child


class SnapshotChildTests(unittest.TestCase):
    def test_stop_before_sandbox_assignment_closes_capture(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);directory=root/'live-cache/child-a';directory.mkdir(parents=True)
            (directory/'stop').touch();live=Mock()
            args=['collect_snapshot_child','--output',str(root),'--container','unused',
                  '--config-home',str(root/'config'),'--side','child-a']
            with patch.object(sys,'argv',args),patch.object(collect_snapshot_child,'LiveTaskCache',return_value=live), \
                 patch.object(collect_snapshot_child.signal,'signal'),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(collect_snapshot_child.main(),1)
            live.start.assert_not_called();live.close.assert_called_once()
            self.assertIn('stopped before sandbox assignment',(directory/'helper-failure.txt').read_text())


if __name__=='__main__':unittest.main()
