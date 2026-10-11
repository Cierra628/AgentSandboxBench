import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from guest_layer_file_state import absent_shadow


class ShadowTests(unittest.TestCase):
    def test_upper_leaf_and_parent_symlink_block_attribution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'testbed').mkdir();path=Path('/testbed/file.js')
            absent_shadow(root,path)
            (root/'testbed/file.js').write_text('copied')
            with self.assertRaises(RuntimeError):absent_shadow(root,path)
            absent_shadow(root,path,allow_file=True)
            (root/'testbed/file.js').unlink();(root/'testbed').rmdir()
            (root/'testbed').symlink_to(root,target_is_directory=True)
            with self.assertRaises(RuntimeError):absent_shadow(root,path)

    def test_opaque_directory_is_not_treated_as_absent_shadow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'testbed').mkdir()
            os.setxattr(root/'testbed','user.overlay.opaque',b'y')
            with self.assertRaisesRegex(RuntimeError,'unsupported overlay attribute'):
                absent_shadow(root,Path('/testbed/file.js'))

    def test_impure_directory_is_accepted_but_does_not_hide_upper_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'testbed').mkdir();path=Path('/testbed/file.js')
            os.setxattr(root,'user.overlay.impure',b'y')
            absent_shadow(root,path)
            (root/'testbed/file.js').write_text('upper copy')
            with self.assertRaises(RuntimeError):absent_shadow(root,path)

    def test_redirect_metacopy_and_unknown_impure_values_remain_rejected(self):
        for name,value in [('user.overlay.redirect',b'/other'),('user.overlay.metacopy',b'y'),('user.overlay.impure',b'unknown')]:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);os.setxattr(root,name,value)
                with self.assertRaises(RuntimeError):absent_shadow(root,Path('/testbed/file.js'))
