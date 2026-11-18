"""Original promotion logic retains VM memory and only shares the immutable base."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_exp2_trenvx import promoter,copy_image,Bridge
from platform_tasks import ROOT,configuration,commands,preflight


class PromotionTests(unittest.TestCase):
    def test_delete_closes_connection_before_kill_and_still_kills_on_close_error(self):
        import asyncio
        from unittest.mock import patch
        from types import SimpleNamespace
        for fail in (False,True):
            calls=[]
            async def close():
                calls.append('close')
                if fail:raise RuntimeError('close failed')
            async def kill(sid,target_addr):calls.append('kill')
            async def sleep(seconds):calls.append('wait')
            with tempfile.TemporaryDirectory() as tmp,patch('run_exp2_trenvx.asyncio.sleep',sleep):
                bridge=Bridge.__new__(Bridge);bridge.out=Path(tmp)
                bridge.live={'vm':SimpleNamespace(close=close)};bridge.Sandbox=SimpleNamespace(kill=kill)
                if fail:
                    with self.assertRaisesRegex(RuntimeError,'close failed'):asyncio.run(bridge.operate(['delete','vm'],5))
                    self.assertEqual(calls,['close','kill'])
                else:
                    asyncio.run(bridge.operate(['delete','vm'],5));self.assertEqual(calls,['close','kill','wait'])
                    self.assertEqual(bridge.live,{})
                timings=json.loads((bridge.out/'delete-timings.json').read_text())
                self.assertTrue(timings[0]['close_first'])
    def test_resource_list_uses_service_response_and_does_not_export_metadata(self):
        import asyncio
        from types import SimpleNamespace
        calls=[]
        class SDK:
            @staticmethod
            async def list(target_addr):
                calls.append(target_addr);return [SimpleNamespace(sandbox_id='real',metadata={'token':'secret'})]
        bridge=Bridge.__new__(Bridge);bridge.Sandbox=SDK;bridge.live={}
        response=asyncio.run(bridge.operate(['list','--output','json'],5))
        self.assertEqual(json.loads(response),[{'sandboxID':'real'}]);self.assertEqual(calls,['127.0.0.1'])
        self.assertNotIn('secret',response)
    def test_original_promoter_keeps_memory_and_latest_writable_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);current='base';old=data/'templates'/current;image=old/'image';image.mkdir(parents=True)
            (image/'rootfs.ext4').write_bytes(b'immutable base')
            (old/'template.toml').write_text('template_id = "base"\nvcpu = 2\nmem_mb = 4096\n')
            instance=old/'instances/guest';instance.mkdir(parents=True);(instance/'writable-rootfs.ext4').write_bytes(b'changed writable disk')
            snap=data/'snapshot';snap.mkdir();(snap/'memory-ranges').write_bytes(b'original VM memory')
            for name in ['config.json','state.json']:(snap/name).write_text(json.dumps({'path':str(old/'run/writable-rootfs.ext4')}))
            promoter()(data,current,'next','guest',snap)
            new=data/'templates/next/image'
            self.assertEqual((new/'rootfs.ext4').stat().st_ino,(image/'rootfs.ext4').stat().st_ino)
            self.assertEqual((new/'memory-ranges').read_bytes(),b'original VM memory')
            self.assertEqual((new/'writable-rootfs.ext4').read_bytes(),b'changed writable disk')
            self.assertNotEqual((new/'writable-rootfs.ext4').stat().st_ino,(instance/'writable-rootfs.ext4').stat().st_ino)
            self.assertIn(str(data/'templates/next/run'),(new/'state.json').read_text())
            self.assertNotIn(str(old/'run'),(new/'state.json').read_text())

    def test_writable_disk_copy_cannot_change_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'writable-rootfs.ext4';source.write_bytes(b'original')
            target=root/'copy';copy_image(source,target);target.write_bytes(b'changed')
            self.assertEqual(source.read_bytes(),b'original')

    def test_unified_entry_uses_exp2_trenvx_instead_of_file_fork(self):
        cfg=configuration(ROOT/'configs/platform-exp2-trenvx.json');check,live=commands(cfg,Path('/unused'))
        self.assertIn('run_exp2_trenvx.py',check[2]);self.assertEqual(live[live.index('--mode')+1],'exp2')
        self.assertNotIn('--immutable-cache-seed',live)

    def test_preflight_rejects_space_below_the_existing_boundary_before_launch(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        cfg=configuration(ROOT/'configs/platform-exp2-trenvx.json')
        with patch('platform_tasks.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='VALIDATED')),patch('isolated_storage.available',return_value=59*2**30),patch('platform_tasks.os.geteuid',return_value=0),patch('platform_tasks.read_json_command') as query:
            with self.assertRaisesRegex(RuntimeError,'60 GiB'):preflight(cfg)
            query.assert_not_called()


if __name__=='__main__':unittest.main()
