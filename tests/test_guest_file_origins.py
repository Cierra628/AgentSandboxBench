import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from guest_file_origins import observe, overlay_layout


class OriginTests(unittest.TestCase):
    def fixture(self, root):
        layers = []
        for name in ('upper', 'new', 'old', 'base'):
            directory = root / name; (directory / 'testbed').mkdir(parents=True)
            layers.append(dict(origin=name, root=str(directory)))
        visible = root / 'merged'; (visible / 'testbed').mkdir(parents=True)
        return dict(layers=layers), visible

    def write(self, root, layer, path, value, visible=None):
        candidate = root / layer / 'testbed' / path
        candidate.parent.mkdir(parents=True, exist_ok=True); candidate.write_text(value)
        if visible:
            target = visible / 'testbed' / path
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(candidate, target)
        return candidate

    def test_upper_and_multiple_lower_precedence_even_with_equal_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); layout, merged = self.fixture(root)
            for layer in ('base', 'old', 'new', 'upper'):
                self.write(root, layer, 'equal', 'identical', merged)
            self.write(root, 'base', 'lower', 'base')
            self.write(root, 'old', 'lower', 'old')
            self.write(root, 'new', 'lower', 'new', merged)
            rows = observe(dict(files=['/testbed/equal', '/testbed/lower']), layout, merged, True)
            self.assertEqual(rows['status'], 'PASS')
            self.assertEqual([r['origin'] for r in rows['files']], ['upper', 'new'])
            self.assertTrue(all(r['content_verified'] for r in rows['files']))
            self.assertTrue(all(r['physical_memory']['bytes'] is None for r in rows['files']))

    def test_whiteout_and_opaque_hide_lower_but_present_opaque_child_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); layout, merged = self.fixture(root)
            self.write(root, 'base', 'removed', 'base')
            leaf = self.write(root, 'new', 'removed', '')
            os.setxattr(leaf, 'user.overlay.whiteout', b'')
            self.write(root, 'base', 'opaque/missing', 'hidden')
            directory = root / 'new/testbed/opaque'; directory.mkdir()
            os.setxattr(directory, 'user.overlay.opaque', b'y')
            self.write(root, 'new', 'opaque/present', 'visible', merged)
            result = observe(dict(files=['/testbed/removed', '/testbed/opaque/missing', '/testbed/opaque/present']), layout, merged, True)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual([r['status'] for r in result['files']], ['DELETED', 'DELETED', 'SOURCE_VERIFIED'])

    def test_redirect_metacopy_symlink_and_unexpected_source_are_unknown(self):
        for attribute in ('user.overlay.redirect', 'user.overlay.metacopy'):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); layout, merged = self.fixture(root)
                target = self.write(root, 'upper', 'file', 'content', merged)
                os.setxattr(target, attribute, b'y')
                result = observe(dict(files=['/testbed/file']), layout, merged, True)
                self.assertEqual(result['files'][0]['status'], 'UNKNOWN')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); layout, merged = self.fixture(root)
            self.write(root, 'upper', 'file', 'content', merged)
            result = observe(dict(files=[dict(path='/testbed/file', expected_origin='base')]), layout, merged)
            self.assertEqual(result['files'][0]['status'], 'UNKNOWN')
            (root/'upper/testbed/file').unlink(); (root/'upper/testbed/file').symlink_to(merged/'testbed/file')
            self.assertEqual(observe(dict(files=['/testbed/file']),layout,merged)['status'], 'PARTIAL')

    def test_changed_file_is_unknown_and_does_not_return_zero_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); layout, merged = self.fixture(root)
            self.write(root, 'upper', 'file', 'before', merged)
            original = Path.read_bytes
            def changing(path):
                value = original(path)
                if path == merged/'testbed/file': path.write_text('modified')
                return value
            with patch.object(Path, 'read_bytes', changing):
                result = observe(dict(files=['/testbed/file']), layout, merged, True)
            self.assertEqual(result['files'][0]['status'], 'UNKNOWN')
            self.assertIsNone(result['files'][0]['physical_memory']['bytes'])

    def test_mount_order_pivot_paths_and_submount_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for directory in ['overlay/root', 'overlay/dax-lowers/1/delta', 'overlay/dax-lowers/2/delta']:
                (root/directory).mkdir(parents=True)
            text = '1 0 0:1 / / rw - overlay overlay rw,lowerdir=/overlay/dax-lowers/2/delta:/overlay/dax-lowers/1/delta:/,upperdir=/overlay/root,workdir=/overlay/work\n'
            text += '2 1 0:2 / /testbed/bound rw - tmpfs tmpfs rw\n'
            layout = overlay_layout(text, pivot_prefix=root)
            self.assertEqual([r['origin'] for r in layout['layers']], ['upper', 'checkpoint:2', 'checkpoint:1', 'base'])
            result = observe(dict(files=['/testbed/bound/file']), layout, root)
            self.assertEqual(result['files'][0]['status'], 'UNKNOWN')
            for bad in [text.replace('/overlay/root', '/other'), text.replace(':/,', '::/,')]:
                with self.assertRaises(ValueError): overlay_layout(bad, pivot_prefix=root)

    def test_missing_is_not_claimed_deleted_and_invalid_manifests_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout, merged = self.fixture(Path(tmp))
            result = observe(dict(files=['/testbed/missing']), layout, merged)
            self.assertEqual(result['files'][0]['status'], 'NOT_FOUND')
            for files in [[], ['/testbed/../escape'], ['/other/file'], ['/testbed/x', '/testbed/x']]:
                with self.assertRaises(ValueError): observe(dict(files=files), layout, merged)

    def test_root_uuid_identity_does_not_hide_sources_but_bad_uuid_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); layout, merged = self.fixture(root)
            self.write(root, 'base', 'file', 'base', merged)
            os.setxattr(root/'upper', 'user.overlay.uuid', bytes(range(16)))
            self.assertEqual(observe(dict(files=['/testbed/file']),layout,merged)['status'], 'PASS')
            os.setxattr(root/'upper', 'user.overlay.uuid', b'bad')
            self.assertEqual(observe(dict(files=['/testbed/file']),layout,merged)['status'], 'PARTIAL')


if __name__ == '__main__': unittest.main()
