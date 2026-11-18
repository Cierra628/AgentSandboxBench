"""Pure prefix selection, metadata grouping and signed arithmetic; no live service."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import stat

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from platform_tasks import configuration,commands,preflight
from host_prefix_compare import prefix_paths,shared_drives,drives_for_vm
from run_host_prefix_compare import signed_reference


class PrefixTests(unittest.TestCase):
    def test_short_configs_do_not_change_full_rollout(self):
        for n in (1,7):
            cfg=configuration(ROOT/f'configs/platform-prettier6604-prefix-N{n}.json')
            check,live=commands(cfg,Path('/unused'))
            self.assertEqual(check[check.index('--host-prefix-count')+1],str(n))
            self.assertEqual(live[live.index('--host-prefix-count')+1],str(n))
            self.assertEqual(preflight(cfg,offline=True)['status'],'VALIDATED')
        cfg=configuration(ROOT/'configs/platform-prettier6604-grpo-light-host.json')
        self.assertNotIn('--host-prefix-count',commands(cfg,Path('/unused'))[0])
        base=json.loads((ROOT/'configs/platform-prettier6604-prefix-N1.json').read_text())
        for changes in ({'host_prefix_count':True},{'host_prefix_count':2},{'light_monitor':False},{'rollout':'bpo'}):
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/'cfg.json';p.write_text(json.dumps(dict(base,**changes)))
                with self.assertRaises(ValueError):configuration(p)

    def test_only_common_prefix_is_selected(self):
        paths={r:['read_repo','read_typescript','later'] for r in ['backbone',*[f'early_{i}' for i in (1,2,3)],*[f'late_{i}' for i in (1,2,3)]]}
        self.assertEqual(prefix_paths(paths,1),{'backbone':['read_repo','read_typescript']})
        self.assertEqual(len(prefix_paths(paths,7)),7)
        self.assertTrue(all(v==['read_repo','read_typescript'] for v in prefix_paths(paths,7).values()))
        for n in (True,2,0):
            with self.assertRaises(ValueError):prefix_paths(paths,n)

    def test_shared_device_requires_same_identity_and_all_guests(self):
        ro=dict(drive_id='rootfs',device=1,inode=2,rdev=3,is_read_only=True)
        rw=dict(ro,drive_id='scratch',is_read_only=False)
        a={'a':[ro,rw],'b':[dict(ro),dict(rw)]}
        self.assertEqual(len(shared_drives(a)),1)
        a['b'][0]['inode']=4;self.assertEqual(shared_drives(a),[])
        a['b']=[];self.assertEqual(shared_drives(a),[])
        self.assertEqual(shared_drives({}),[])

    def test_signed_reference_keeps_negative_and_unknown_values(self):
        self.assertEqual(signed_reference(10,80)['reduction_bytes'],-10)
        self.assertEqual(signed_reference(10,10)['reduction_bytes'],60)
        self.assertIsNone(signed_reference(0,0)['reduction_fraction'])
        self.assertEqual(signed_reference(None,0)['status'],'NOT_MEASURED')

    def test_prefix_path_does_not_pause_or_collect_full_metrics(self):
        code=(ROOT/'scripts/run_prettier6604_grpo.py').read_text()
        start=code.index('            async def do_grpo(self):')
        part=code[start:code.index('        controller=SampledPaths',start)]
        self.assertIn("observe_prefix,args,out,'before',{}",part)
        self.assertIn("observe_prefix,args,out,'after',dict(zip",part)
        self.assertIn('self.run_path(sid,role,executed[role])',part)
        self.assertNotIn('sleep(',part)
        self.assertIn('light=host_restore or light_monitor',code)

    def test_drive_query_is_read_only_and_does_not_save_full_config(self):
        vm=dict(pid=100,start_ticks='20',boot_id='boot',api_socket='/owned/socket')
        identity={k:vm[k] for k in ('pid','start_ticks','boot_id')}
        drive=dict(drive_id='rootfs',is_read_only=True,is_root_device=True,path_on_host='/dev/ublkb9')
        with patch('host_sandbox_monitor.identity',return_value=identity),patch('host_sandbox_monitor.UnixHTTP') as http,patch('pathlib.Path.stat',return_value=SimpleNamespace(st_mode=stat.S_IFBLK,st_dev=1,st_ino=2,st_rdev=3)):
            http.return_value.getresponse.return_value.status=200
            http.return_value.getresponse.return_value.read.return_value=json.dumps(dict(drives=[drive],unrelated_private_field='not-to-save')).encode()
            rows=drives_for_vm(vm)
            http.return_value.request.assert_called_once_with('GET','/vm/config')
            self.assertEqual(rows[0]['rdev'],3);self.assertNotIn('unrelated_private_field',json.dumps(rows))
            self.assertEqual(rows[0]['path'],'/proc/100/root/dev/ublkb9')
            http.return_value.close.assert_called_once()
        drive['path_on_host']='agentenv_volume_slot_2.img'
        with patch('host_sandbox_monitor.identity',return_value=identity),patch('host_sandbox_monitor.UnixHTTP') as http,patch('pathlib.Path.stat',return_value=SimpleNamespace(st_mode=stat.S_IFBLK,st_dev=1,st_ino=2,st_rdev=3)):
            http.return_value.getresponse.return_value.status=200
            http.return_value.getresponse.return_value.read.return_value=json.dumps(dict(drives=[drive])).encode()
            self.assertEqual(drives_for_vm(vm)[0]['path'],'/proc/100/cwd/agentenv_volume_slot_2.img')


if __name__=='__main__':unittest.main()
