import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from trenvx_file_diagnostic import checkpoint_file, guest_observation, snapshot


class FileDiagnosticTests(unittest.TestCase):
    def layer(self, root):
        path=root/'data/templates/checkpoint/image/checkpoint-0001.ext4'
        path.parent.mkdir(parents=True)
        path.touch(); path.chmod(0o444)
        return path

    def test_reject_writable_multiple_and_invalid_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); layer=self.layer(root)
            self.assertEqual(checkpoint_file(root,'checkpoint'),layer)
            layer.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError,'read-only'):
                checkpoint_file(root,'checkpoint')
            layer.chmod(0o444)
            layer.with_name('checkpoint-0002.ext4').touch()
            with self.assertRaisesRegex(RuntimeError,'exactly one'):
                checkpoint_file(root,'checkpoint')
            for value in ('../checkpoint','/checkpoint','','..'):
                with self.assertRaises(ValueError): checkpoint_file(root,value)

    def test_guest_units_and_missing_data(self):
        raw='MemTotal: 100 kB\nMemAvailable: 80 kB\nCached: 20 kB\nAnonPages: 5 kB\nSlab: 3 kB\n4096\n'
        self.assertEqual(guest_observation(raw)['meminfo_bytes']['Cached'],20480)
        for bad in (raw.replace('4096','65536'), 'MemTotal: 100 kB\n4096\n'):
            with self.assertRaises(RuntimeError): guest_observation(bad)

    def test_failed_measurement_persists_evidence_and_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); self.layer(root)
            identity=dict(pid=12,start_time_ticks=4,cgroup='/owned/sandbox')
            failed=dict(status='FAIL',first_failure={'location':'pagemap','error':'hidden'})
            with patch('trenvx_file_diagnostic.vmm_snapshot',return_value=identity), \
                 patch('trenvx_file_diagnostic.collect',return_value=(failed,[])):
                with self.assertRaisesRegex(RuntimeError,'measurement failed'):
                    snapshot(root,'restored','sandbox','checkpoint','owned',Path('/vmm'),{})
            saved=json.loads((root/'file-physical/restored/result.json').read_text())
            self.assertEqual(saved['status'],'FAIL')
            self.assertIsNone(saved['upper_file_content']['bytes'])
            self.assertTrue((root/'file-physical/restored/manifest.json').is_file())

    def test_explicit_base_and_checkpoint_paths_use_different_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);layer=self.layer(root)
            base=layer.with_name('rootfs.ext4');base.touch()
            identity=dict(pid=12,start_time_ticks=4,cgroup='/owned/sandbox')
            files=[dict(path='/testbed/changed.js',origin='checkpoint'),dict(path='/testbed/unchanged.js',origin='base')]
            with patch('trenvx_file_diagnostic.vmm_snapshot',return_value=identity), \
                 patch('trenvx_file_diagnostic.collect',return_value=(dict(status='PASS',first_failure=None),[])) as collect:
                snapshot(root,'named','sandbox','checkpoint','owned',Path('/vmm'),{},files)
            mapped=collect.call_args.args[0]['vms'][0]['files']
            self.assertEqual(mapped[0]['backing'],str(layer));self.assertEqual(mapped[0]['guest_path'],'/delta/testbed/changed.js')
            self.assertEqual(mapped[1]['backing'],str(base));self.assertEqual(mapped[1]['guest_path'],'/testbed/unchanged.js')
            self.assertEqual(base.stat().st_mode&0o222,0)


if __name__=='__main__':
    unittest.main()
