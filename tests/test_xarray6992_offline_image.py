"""Private transport guards only; no network, services or devices."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from xarray6992_offline_image import wrapper,budget
from xarray6992_inputs import IMAGE
from isolated_storage import GIB


class OfflineImageTests(unittest.TestCase):
    def execute(self,args):
        with tempfile.TemporaryDirectory() as name:
            real=Path(name)/'real';real.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n');real.chmod(0o755)
            script=Path(name)/'wrapper';script.write_text(wrapper(real,Path('/readonly/oci')));script.chmod(0o755)
            return subprocess.run([str(script),*args],capture_output=True,text=True,timeout=5)

    def test_only_pinned_copy_uses_local_layout(self):
        result=self.execute(['image','copy','--platform','local',IMAGE,'ocidir:///workspace/env/image-cache/staging/owned/oci:latest'])
        self.assertEqual(result.returncode,0)
        self.assertEqual(result.stdout.splitlines()[4],'ocidir:///readonly/oci:latest')

    def test_other_operations_delegate_unchanged(self):
        for args in (['version'],['manifest','get',IMAGE],['image','copy','--platform','local','another-image','elsewhere']):
            result=self.execute(args);self.assertEqual(result.returncode,0);self.assertEqual(result.stdout.splitlines(),args)

    def test_pinned_copy_cannot_target_other_locations(self):
        self.assertEqual(self.execute(['image','copy','--platform','local',IMAGE,'ocidir:///tmp/unowned:latest']).returncode,2)
        self.assertEqual(self.execute(['image','copy','--platform','local',IMAGE,'ocidir:///workspace/env/image-cache/staging/owned/oci:latest','--fast']).returncode,2)

    def test_tar_budget_keeps_margin(self):
        result=dict(downloaded_bytes=2*GIB,unpacked_regular_rounded_bytes=5*GIB)
        with patch('xarray6992_offline_image.validate',return_value=(result,None)),patch('xarray6992_offline_image.allocated',return_value=6*GIB):
            self.assertEqual(budget(None,None,28*GIB)['estimated_peak_bytes'],int(23.5*GIB))
            with self.assertRaises(AssertionError):budget(None,None,24*GIB)
        result['unpacked_regular_rounded_bytes']=8*GIB
        with patch('xarray6992_offline_image.validate',return_value=(result,None)),patch('xarray6992_offline_image.allocated',return_value=6*GIB):
            with self.assertRaises(AssertionError):budget(None,None,28*GIB)

    def test_actual_size_plan_fits_24_without_lowering_host_reserve(self):
        result=dict(downloaded_bytes=1646232339,unpacked_regular_rounded_bytes=3974459392)
        with patch('xarray6992_offline_image.validate',return_value=(result,None)),patch('xarray6992_offline_image.allocated',return_value=5834698752):
            plan=budget(None,None,24*GIB)
            self.assertGreaterEqual(plan['unallocated_margin_bytes'],2*GIB)


if __name__=='__main__':unittest.main()
