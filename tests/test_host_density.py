"""Tiny temporary files and mocked metadata only; no service or task execution."""
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import host_density as d
from run_host_density import planned_runs,SEED_PREFIX


class DensityTests(unittest.TestCase):
    def test_seed_name_uses_existing_ownership_contract(self):
        from host_persistent_input import owned
        path=ROOT/'.artifacts/prettier6604'/(SEED_PREFIX+'offline-fixture')
        self.assertEqual(owned(path),path)

    def test_exact_twelve_runs_and_fixed_counts(self):
        plan=planned_runs();self.assertEqual(len(plan),12)
        self.assertEqual({(r['count'],r['mode']) for r in plan[:10]}, {(n,m) for n in (1,2,4,7,10) for m in ('shared','independent')})
        self.assertEqual(plan[10:],[dict(task='c4',count=7,mode=m) for m in ('shared','independent')])

    def test_explicit_timing_pair_selects_only_the_two_original_c4_runs(self):
        self.assertEqual(planned_runs(True),planned_runs()[10:])
        self.assertEqual(len(planned_runs()),12)

    def test_pressure_extension_has_four_curve_points_then_four_repeats(self):
        plan=planned_runs(pressure_extension=True)
        self.assertEqual([(r['condition'],r['count'],r['mode']) for r in plan],
            [('E',4,'shared'),('F',4,'independent'),('G',7,'independent'),('H',7,'shared'),
             ('I',10,'shared'),('J',10,'independent'),('K',10,'independent'),('L',10,'shared')])
        self.assertTrue(all(r['pressure'] for r in plan))
        self.assertEqual(len(planned_runs()),12)
        self.assertEqual([r['condition'] for r in planned_runs(pressure_comparison=True)],list('ABCD'))
        for timing,comparison in ((True,False),(False,True)):
            with self.assertRaises(ValueError):planned_runs(timing,comparison,True)

    def test_pressure_curve_count_requires_explicit_new_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);daemon=root/'daemon';daemon.write_bytes(b'\x7fELF');p=root/'request.json'
            base=dict(profile='host-density-v1',task='prettier6604',count=4,mode='shared',daemon=str(daemon),
                pressure_context=str(d.ROOT/'.artifacts/offline-fixture/pressure'),pressure_enabled=True)
            p.write_text(json.dumps(base))
            with self.assertRaises(ValueError):d.request(p)
            for n in (4,7,10):
                p.write_text(json.dumps(dict(base,count=n,pressure_extension=True)))
                self.assertEqual(d.request(p)['count'],n)
            p.write_text(json.dumps(dict(base,count=2,pressure_extension=True)))
            with self.assertRaises(ValueError):d.request(p)

    def test_request_rejects_wrong_count_mode_and_missing_image_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);daemon=root/'daemon';daemon.write_bytes(b'\x7fELF');p=root/'request.json'
            base=dict(profile='host-density-v1',task='prettier6604',count=10,mode='independent',daemon=str(daemon))
            p.write_text(json.dumps(base));self.assertEqual(d.request(p)['count'],10)
            for change in (dict(count=3),dict(count=True),dict(mode='private'),dict(task='c4')):
                p.write_text(json.dumps(dict(base,**change)))
                with self.assertRaises(ValueError):d.request(p)

    def fixture(self,root):
        layers=[];commits=root/'env/image-cache/commits';commits.mkdir(parents=True)
        for i in range(19):
            p=commits/str(i);p.write_bytes(b'test');layers.append(dict(relative=str(i),source_digest=str(i),size=4))
        def call(label,argv,timeout):
            if argv[0]=='cp':
                self.assertIn('--reflink=never',argv);shutil.copy2(argv[-2],argv[-1]);return ''
            self.assertEqual(argv[0],'findmnt');return 'ext4\n'
        return layers,call

    def test_prepared_counts_and_manifest_do_not_alias(self):
        for n,mode in ((1,'independent'),(2,'independent'),(10,'independent'),(10,'shared')):
            with self.subTest(n=n,mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);layers,call=self.fixture(root)
                with patch.object(d,'manifest_layers',return_value=layers):value=d.prepare(root,dict(count=n,mode=mode),call)
                self.assertEqual(len(value['objects']),19*(n if mode=='independent' else 1))
                manifest=json.loads((root/'env/rootfs-copy-control/manifest.json').read_text())
                self.assertEqual((manifest['schedule'],manifest['density_count'],manifest['initial_lower_count']),('density',n,19))
                self.assertEqual(len({(o['identity']['device'],o['identity']['inode']) for o in value['objects']}),len(value['objects']))

    def test_cold_start_requires_zero_residency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'.artifacts/storage-isolated-service/run/data';p.parent.mkdir(parents=True);p.write_bytes(b'x')
            objects=[dict(path=str(p),identity=d.file_identity(p))]
            with patch.object(d,'ROOT',root),patch.object(d.os,'posix_fadvise') as evict,patch.object(d,'query_objects',return_value=dict(resident_bytes=4096)):
                with self.assertRaisesRegex(RuntimeError,'P0 is not zero'):d.evict(objects,None)
                evict.assert_called_once()

    def test_context_must_bind_output_and_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'controller/density-guest-00';ctx=root/'controller/density/guest-00.json';ctx.parent.mkdir(parents=True)
            value=dict(container='owned',output=str(out),task='prettier6604',sandbox_id='11111111-1111-1111-1111-111111111111')
            ctx.write_text(json.dumps(value));args=SimpleNamespace(output=out,container='owned')
            self.assertEqual(d.context(ctx,args)['sandbox_id'],value['sandbox_id'])
            args.container='other'
            with self.assertRaises(ValueError):d.context(ctx,args)
            self.assertIsNone(d.context(None,args))

    def test_extent_check_rejects_shared_and_overlapping_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);objects=[]
            for i in range(2):
                p=root/str(i);p.write_bytes(b'x');objects.append(dict(path=str(p)))
            def ioctl(fd,request,buffer,mutate):
                self.assertEqual(request,0xC020660B)
                struct.pack_into('=I',buffer,20,1)
                struct.pack_into('=QQQ',buffer,32,0,4096,4096)
                struct.pack_into('=I',buffer,72,1)
            with patch.object(d.fcntl,'ioctl',side_effect=ioctl):
                with self.assertRaisesRegex(ValueError,'overlapping'):d.independent_extents(objects)
            def shared(fd,request,buffer,mutate):
                ioctl(fd,request,buffer,mutate);struct.pack_into('=I',buffer,72,0x2001)
            with patch.object(d.fcntl,'ioctl',side_effect=shared):
                with self.assertRaisesRegex(ValueError,'shared or unresolved'):d.independent_extents(objects[:1])


if __name__=='__main__':unittest.main()
