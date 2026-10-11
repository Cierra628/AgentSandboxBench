import json
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,AsyncMock
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from trenvx_named_guest_files import probe_manifest,with_mapping_source,run,combined_pages
from named_guest_file_physical import validate_guest
import guest_named_file_probe


class ManifestTests(unittest.TestCase):
    def test_combined_pages_deduplicate_alias_and_report_type_conflict(self):
        present=1<<63
        upper=[dict(owner='/upper.js',entry=present|7)]
        base=[dict(owner='/base.js',entry=present|7,kind='file_content'),
              dict(owner='/package.json',entry=present|8,kind='file_content')]
        measured=combined_pages(upper,base)
        self.assertEqual(measured['attributed_physical_bytes'],8192)
        self.assertEqual(measured['declared_file_count'],3)
        base[0]=dict(owner='/base.js',entry=present|7,kind='anonymous_cow',
            content_verification=dict(status='MATCH',mapped_sha256='a'*64,backing_sha256='a'*64))
        conflict=combined_pages(upper,base)
        self.assertEqual(conflict['status'],'PARTIAL')
        self.assertIsNone(conflict['attributed_physical_bytes'])
        self.assertEqual(conflict['unknown_unique_pages'],1)

    def overlay(self):
        metadata=dict(st_dev=21,st_ino=17,st_size=4096,st_mtime_ns=1,st_ctime_ns=1)
        snapshot=dict(status='PASS',sandbox_id='vm',page_size=4096,files=[dict(path='/testbed/file.js',
            identity=metadata,address=4096,total_pages=1,selected_indices=[],pages=[],
            mapping='1000-2000 rw-p 00000000 fe:00 17 /testbed/file.js')])
        underlying=dict(metadata,st_dev=65024)
        source=dict(status='SOURCE_VERIFIED',origin='upper',path='/testbed/file.js',visible=dict(metadata),
            underlying=underlying,candidate='/rom/overlay/root/testbed/file.js')
        source['proof']=[dict(candidate=source['candidate'],identity=underlying,kind='file')]
        return snapshot,dict(status='PASS',files=[source])

    def test_explicit_upper_proof_handles_device_difference(self):
        before,origins=self.overlay()
        with self.assertRaises(AssertionError):validate_guest(before,'vm')
        joined=with_mapping_source(before,origins);validate_guest(joined,'vm')
        self.assertEqual(joined['files'][0]['identity']['st_dev'],21)
        self.assertEqual(joined['files'][0]['mapping_identity']['st_dev'],65024)
        self.assertNotIn('mapping_identity',before['files'][0])

    def test_wrong_backing_or_visible_identity_still_rejected(self):
        for target,key in [('underlying','st_dev'),('visible','st_ino')]:
            before,origins=self.overlay();source=origins['files'][0]
            source[target][key]=999
            with self.assertRaises(AssertionError):
                validate_guest(with_mapping_source(before,origins),'vm')

    def test_failed_probe_releases_and_preserves_available_guest_data(self):
        snapshot,origins=self.overlay();snapshot['status']='FAIL'
        async def download(path,timeout):
            if path.endswith('/before.json'):return json.dumps(snapshot).encode()
            if path.endswith('/after.json'):return b'{"fixture":"after"}'
            if path.endswith('/result.json'):return b'{"fixture":"result"}'
            if path.endswith('/named-probe.log'):return b'fixture log'
            raise FileNotFoundError(path)
        sandbox=SimpleNamespace(id='vm',filesystem=SimpleNamespace(write_bytes=AsyncMock()),download_file=download)
        tool=AsyncMock(return_value='')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AssertionError):asyncio.run(run(sandbox,Path(tmp),tool,None,origins))
            out=Path(tmp)/'named-guest-files'
            self.assertTrue((out/'error-after.json').exists())
            self.assertTrue((out/'error-guest-result.json').exists())
            self.assertEqual((out/'guest-probe.log').read_text(),'fixture log')
            self.assertEqual(set(json.loads((out/'failure-download.json').read_text())),{'failure.txt'})
            self.assertIn('named-error-release',[call.args[1] for call in tool.await_args_list])

    def test_real_probe_accepts_manifest_header(self):
        origins=dict(status='PASS',files=[dict(path='/testbed/file.js',origin='upper'),
            dict(path='/testbed/base.js',origin='base')])
        with tempfile.TemporaryDirectory() as tmp:
            manifest=Path(tmp)/'manifest.json';out=Path(tmp)/'probe'
            manifest.write_text(json.dumps(probe_manifest(origins,'vm')))
            # Reach file opening in the real probe, with no host /testbed access.
            with patch.object(sys,'argv',['probe',str(manifest),str(out)]),\
                 patch.object(guest_named_file_probe.os,'open',side_effect=FileNotFoundError('fixture')),\
                 self.assertRaisesRegex(FileNotFoundError,'fixture'):
                guest_named_file_probe.main()
            self.assertTrue((out/'failure.txt').exists())

    def test_ambiguous_or_missing_upper_is_rejected(self):
        for rows in [[],[dict(path='/testbed/a',origin='upper'),dict(path='/testbed/b',origin='upper')]]:
            with self.assertRaises(AssertionError):probe_manifest(dict(status='PASS',files=rows),'vm')


if __name__=='__main__':unittest.main()
