"""Path checks only; no loop devices, mounts, fsck or real filesystem repair."""
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import diagnose_ext4_journal as diagnostic


class DiagnosticPathTests(unittest.TestCase):
    def test_original_experiment_images_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'original.ext4';image.write_bytes(bytes(4096))
            with self.assertRaisesRegex(ValueError,'diagnostic copy'):diagnostic.source_image(image)

    def test_only_bounded_regular_diagnostic_copies_accepted(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(diagnostic,'FIXTURES',Path(tmp)):
            image=Path(tmp)/'copy.ext4';image.write_bytes(bytes(4096))
            self.assertEqual(diagnostic.source_image(image),image)
            image.write_bytes(b'short')
            with self.assertRaises(ValueError):diagnostic.source_image(image)
            with self.assertRaises(ValueError):diagnostic.source_image(Path(tmp))

    def test_symlink_to_original_image_cannot_bypass_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);copies=base/'diagnostic';copies.mkdir()
            original=base/'original.ext4';original.write_bytes(bytes(4096))
            link=copies/'copy.ext4';link.symlink_to(original)
            with patch.object(diagnostic,'FIXTURES',copies):
                with self.assertRaises(ValueError):diagnostic.source_image(link)

    def test_probe_refuses_output_outside_diagnostic_before_device_access(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(diagnostic,'FIXTURES',Path(tmp)),patch('diagnose_ext4_journal.subprocess.check_output') as query:
            image=Path(tmp)/'copy.ext4';image.write_bytes(bytes(4096))
            with self.assertRaisesRegex(ValueError,'new diagnostic directory'):
                diagnostic.probe(image,Path('/tmp/not-this-diagnostic-fixture'))
            query.assert_not_called()


if __name__=='__main__':unittest.main()
