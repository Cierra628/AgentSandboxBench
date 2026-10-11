from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from owned_thread_wait import WaitTrace, parse_kvm_faults, combined_wait_script


class WaitTraceTests(unittest.TestCase):
    def test_timeout_wrapper_forwards_stop_and_keeps_child_success(self):
        # Uses our own temporary shell, without BPF or live service access.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            binary=root/'bpftrace'
            binary.write_text('#!/bin/bash\ntrap \'printf "WAIT 11 100 1\\nWAIT 22 200 2\\n"; exit 0\' INT\nprintf "READY\\n"\nwhile :; do sleep .05; done\n')
            binary.chmod(0o700)
            original=subprocess.Popen
            def popen(argv,**kwargs):
                argv=[str(binary) if x=='bpftrace' else x for x in argv]
                return original(argv,**kwargs)
            with patch('owned_thread_wait.subprocess.Popen',side_effect=popen):
                trace=WaitTrace([11,22],root/'trace',max_seconds=5)
                result=trace.stop()
            self.assertEqual(result['status'],'PASS')
            self.assertEqual([x['runnable_wait_ns'] for x in result['threads']],[100,200])

    def test_combined_probe_has_single_begin_end_before_ready(self):
        scripts=Path(__file__).resolve().parents[1]/'scripts'
        combined=combined_wait_script((scripts/'trace_owned_vcpu_wait.bt').read_text(),(scripts/'trace_owned_vcpu_faults.bt').read_text())
        self.assertEqual(combined.count('BEGIN {'),1)
        self.assertEqual(combined.count('END {'),1)
        self.assertLess(combined.index('@tries[$1]=0'),combined.index('printf("READY'))
        self.assertIn('kretprobe:kvm_setup_async_pf',combined)

    def test_kvm_counts_do_not_promote_attempts_to_async_success(self):
        result=parse_kvm_faults('KVM 11 20 3 3 1 0\nKVM 22 4 0 0 0 0\n',[11,22])
        self.assertEqual(result['threads'][0]['try_async_get_page'],20)
        self.assertEqual(result['threads'][0]['successful_async_setup'],1)
        self.assertIn('zero does not exclude synchronous faults',result['limits'])

    def test_kvm_missing_duplicate_and_inconsistent_records_rejected(self):
        for output in ('KVM 11 0 0 0 0 0\n',
                       'KVM 11 0 0 0 0 0\nKVM 11 0 0 0 0 0\n',
                       'KVM 11 4 1 1 2 0\nKVM 22 0 0 0 0 0\n',
                       'KVM 11 4 2 0 0 0\nKVM 22 0 0 0 0 0\n'):
            with self.subTest(output=output),self.assertRaises(AssertionError):
                parse_kvm_faults(output,[11,22])

    def test_missing_thread_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace=WaitTrace.__new__(WaitTrace)
            trace.path=Path(tmp)/'trace';trace.begin=0
            trace.log=trace.path.with_suffix('.stderr').open('w')
            trace.process=Mock(returncode=0)
            trace.process.communicate.return_value=('WAIT 11 100 1\n','')
            trace.tids=[11,22]
            with self.assertRaises(AssertionError):trace.stop()


if __name__=='__main__':unittest.main()
