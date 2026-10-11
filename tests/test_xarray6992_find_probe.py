"""Offline process evidence parsing; never scans or signals live processes."""
import sys
from pathlib import Path
import tempfile
import subprocess
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from xarray6992_find_probe import process_snapshot,error_processes,pipeline_footer


class FindProbeTests(unittest.TestCase):
    def test_footer_preserves_signal_exit_and_each_pipeline_status(self):
        result=subprocess.run(['bash','-o','pipefail','-c',pipeline_footer('yes | head -1')],
                              capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0)
        self.assertEqual(result.stderr.strip(),'ASB_PIPE_STATUS 141 141 0')

    def test_zombie_with_parentheses_in_command_and_disappeared_pid(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc=Path(temporary)
            (proc/'398').mkdir();(proc/'392').mkdir();(proc/'net').mkdir()
            fields=['Z','1']+['0']*17+['12345']
            (proc/'398/stat').write_text('398 (sleep (probe)) '+' '.join(fields)+'\n')
            snapshot=process_snapshot(proc)
            self.assertEqual(snapshot,{'398':dict(state='Z',ppid=1,start_ticks=12345,command='sleep (probe)')})
            evidence=error_processes(['find: /proc/398/net: Invalid argument',
                                     'find: /proc/392: No such file or directory'],snapshot,{})
            self.assertEqual(evidence['398']['before']['state'],'Z')
            self.assertIsNone(evidence['398']['after'])
            self.assertEqual(evidence['392'],dict(before=None,after=None))


if __name__=='__main__':unittest.main()
