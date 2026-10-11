from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from trenvx_kernel_cache import SnapshotGuestKernel,verify_layout
from trenvx_whole_cache import file_identity


class SnapshotKernelTests(unittest.TestCase):
    def setup_reader(self,path):
        r=SnapshotGuestKernel.__new__(SnapshotGuestKernel);r.slots=[]
        r.backings=[dict(index=0,path=str(path),identity=file_identity(path),regions=[dict(start=4096,end=8192,offset=0,permissions='rw-p')])]
        return r

    def test_cold_backing_is_never_faulted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'memory-ranges';path.write_bytes(b'A'*4096);r=self.setup_reader(path)
            with patch('trenvx_kernel_cache.ram_address',return_value=4096),patch('trenvx_kernel_cache.resident',return_value=b'\x00'),patch('trenvx_kernel_cache.read_entry',side_effect=AssertionError('cold page touched')):
                self.assertIsNone(r.backing_page(0,required=False))
                with self.assertRaisesRegex(AssertionError,'refusing'):r.backing_page(0)

    def test_resident_file_backing_has_stable_physical_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'memory-ranges';path.write_bytes(b'A'*4096);r=self.setup_reader(path)
            entry=(1<<63)|(1<<61)|77
            with patch('trenvx_kernel_cache.ram_address',return_value=4096),patch('trenvx_kernel_cache.resident',return_value=b'\x01'),patch('trenvx_kernel_cache.read_entry',return_value=entry):
                data,receipt=r.backing_page(0)
                self.assertEqual(data,b'A'*4096);self.assertEqual(receipt['entry'],entry)

    def test_shared_snapshot_mapping_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'memory-ranges';path.write_bytes(b'A'*4096);r=self.setup_reader(path)
            r.backings[0]['regions'][0]['permissions']='rw-s'
            with patch('trenvx_kernel_cache.ram_address',return_value=4096):
                with self.assertRaises(AssertionError):r.backing_page(0)

    def test_current_vmm_configuration_uses_trenvx_get_api(self):
        from kernel_file_cache import classify
        p=Mock();p.paused=True;p.state.return_value='Paused'
        p.api.return_value={'config':{'payload':{'kernel':'/wrong/kernel'}}}
        with tempfile.TemporaryDirectory() as tmp,patch('kernel_file_cache.identity',return_value=Path('/unused')):
            with self.assertRaisesRegex(AssertionError,'actual TrEnv-X kernel'):classify(Path(tmp),{}, {},p,backend='trenvx')
        p.api.assert_called_once_with('GET','/api/v1/vm.info')

    def test_owned_template_kernel_alias_is_accepted_but_hash_still_checked(self):
        from kernel_file_cache import classify
        p=Mock();p.paused=True;p.state.return_value='Paused'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'fixture-files';directory.mkdir()
            target=root/'data/templates/asb-task-base/run/vmlinux'
            proc=root/'proc';asset=proc/'root'/str(target).lstrip('/');asset.parent.mkdir(parents=True);asset.write_bytes(b'wrong kernel')
            p.api.return_value={'config':{'payload':{'kernel':str(target)}}}
            with patch('kernel_file_cache.identity',return_value=proc):
                with self.assertRaisesRegex(AssertionError,'unsupported exact kernel ELF'):classify(directory,{}, {},p,backend='trenvx')

    def test_calibration_labels_do_not_change_with_download_filenames(self):
        import asyncio
        from unittest.mock import AsyncMock
        from trenvx_whole_cache import run
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'capture';source.mkdir()
            (source/'vmm-identity.json').write_text(json.dumps({'sandbox_id':'fixture-vm'}))
            (source/'ram-attestation.json').write_text(json.dumps({'iomem':'fixture-ram'}))
            sb=Mock();sb.id='fixture-vm';tool=AsyncMock()
            async def download(path,**kwargs):return b'{}' if path.endswith('.json') else b'fixture-ram'
            sb.download_file=AsyncMock(side_effect=download)
            with patch('trenvx_whole_cache.scan_host',side_effect=RuntimeError('stop before host access')):
                with self.assertRaisesRegex(RuntimeError,'stop before host access'):
                    asyncio.run(run(sb,root,tool,Mock(directory=source),name='calibration'))
            labels=[call.args[1] for call in tool.await_args_list]
            self.assertTrue(all(name.startswith('calibration-') for name in labels),labels)
            self.assertEqual(labels[-1],'calibration-whole-cache-release')


if __name__=='__main__':unittest.main()
