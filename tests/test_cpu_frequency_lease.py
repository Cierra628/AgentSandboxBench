import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import cpu_frequency_lease as lease


class LeaseTests(unittest.TestCase):
    def fixture(self,root):
        for cpu in lease.CPUS:
            p=root/f'cpu{cpu}'/'cpufreq';p.mkdir(parents=True)
            for k,v in dict(scaling_min_freq='800000',scaling_max_freq='2801000',scaling_governor='schedutil',scaling_available_frequencies='800000 2000000 2801000').items():(p/k).write_text(v+'\n')
    def check_restored(self,root):
        for cpu in lease.CPUS:
            self.assertEqual(lease.read_policy(root/f'cpu{cpu}'/'cpufreq'),dict(scaling_min_freq='800000',scaling_max_freq='2801000',scaling_governor='schedutil'))
    def test_apply_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            obj=lease.PolicyLease(root/'lease','B',cpu_root=root)
            self.assertEqual((root/'cpu2/cpufreq/scaling_min_freq').read_text().strip(),'2000000')
            obj.close();self.check_restored(root)
    def test_partial_write_failure_restores(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root);original=lease.write;calls=0
            def failure(path,value):
                nonlocal calls
                calls+=1
                if calls==2:raise OSError('injected partial failure')
                return original(path,value)
            with patch.object(lease,'write',failure):
                with self.assertRaises(OSError):lease.PolicyLease(root/'lease','B',cpu_root=root)
            self.check_restored(root)
    def test_failed_min_still_restores_max_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            directory=root/'lease';directory.mkdir()
            policies={str(c):dict(path=str(root/f'cpu{c}/cpufreq'),settings=lease.read_policy(root/f'cpu{c}/cpufreq')) for c in lease.CPUS}
            lease.save(directory/'backup.json',dict(policies=policies))
            for c in lease.CPUS:
                (root/f'cpu{c}/cpufreq/scaling_max_freq').write_text('2000000')
            original=lease.write
            def failure(path,value):
                if path==root/'cpu2/cpufreq/scaling_min_freq':raise AssertionError('injected readback mismatch')
                original(path,value)
            with patch.object(lease,'write',failure):
                self.assertEqual(lease.restore(directory,'test')['status'],'FAIL')
            self.check_restored(root)
            self.assertEqual(lease.restore(directory,'retry')['status'],'PASS')
            self.assertEqual(len(list(directory.glob('restore-failure-*.json'))),1)

    def test_write_waits_for_readback(self):
        from unittest.mock import Mock
        path=Mock();path.read_text.side_effect=['2000000','800000']
        with patch.object(lease.time,'sleep'):
            lease.write(path,'800000')
        path.write_text.assert_called_once_with('800000\n')

    def test_watcher_retries_failed_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            directory=root/'lease';directory.mkdir()
            policies={str(c):dict(path=str(root/f'cpu{c}/cpufreq'),settings=lease.read_policy(root/f'cpu{c}/cpufreq')) for c in lease.CPUS}
            lease.save(directory/'backup.json',dict(policies=policies,owner_pid=os.getpid(),owner_start_ticks=lease.identity(os.getpid()),deadline=time.monotonic()+30))
            lease.save(directory/'restored.json',dict(status='FAIL'))
            (root/'cpu2/cpufreq/scaling_max_freq').write_text('2000000')
            lease.watch(directory)
            self.assertEqual(json.loads((directory/'watch-recovery.json').read_text())['status'],'PASS')
            self.check_restored(root)

    def test_abrupt_owner_exit_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            code='from pathlib import Path; import os,sys; sys.path.insert(0,sys.argv[1]); from cpu_frequency_lease import PolicyLease; PolicyLease(Path(sys.argv[2])/"lease","B",cpu_root=Path(sys.argv[2])); os._exit(0)'
            subprocess.run([sys.executable,'-c',code,str(Path(lease.__file__).parent),tmp],check=True,timeout=10)
            deadline=time.monotonic()+5
            while not (root/'lease/watch-recovery.json').exists() and time.monotonic()<deadline:time.sleep(.05)
            self.assertEqual(json.loads((root/'lease/watch-recovery.json').read_text())['status'],'PASS')
            self.check_restored(root)


if __name__=='__main__':unittest.main()
