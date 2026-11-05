"""Local metadata and shell argument checks; no service, data workload or network."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch,MagicMock

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import c4_density_input as c
import host_density as d


class C4DensityTests(unittest.TestCase):
    def test_fixed_c4_request_is_seven_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);daemon=root/'daemon';daemon.write_bytes(b'\x7fELF');request=root/'request.json'
            value=dict(profile='host-density-v1',task='c4',count=7,mode='shared',daemon=str(daemon))
            request.write_text(json.dumps(value));self.assertEqual(d.request(request)['count'],7)
            for n in (1,2,4,10):
                request.write_text(json.dumps(dict(value,count=n)))
                with self.assertRaises(ValueError):d.request(request)

    def test_local_wrapper_has_no_remote_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);mount=root/'mount';entry=mount/'env/deps/regctl/v0.11.5/regctl';entry.parent.mkdir(parents=True)
            entry.write_bytes(b'\x7fELFfixture');layout=root/'oci';store=layout/'blobs/sha256';store.mkdir(parents=True)
            (store/c.DIGEST[7:]).write_text('{}');(store/('a'*64)).write_text('{}')
            with patch.object(c,'validate',return_value=({},layout,dict(config=dict(digest='sha256:'+'a'*64)))),patch.object(c,'dependency_version',return_value='v0.11.5'):
                c.install(mount,root)
            subprocess.run(['sh','-n',entry],check=True)
            for argv in (['manifest','get','--format','raw-body','docker.io/unapproved:latest'],['image','copy','--platform','local',c.IMAGE,'/outside-owned-staging']):
                p=subprocess.run(['sh',entry,*argv],capture_output=True,text=True)
                self.assertEqual(p.returncode,78)
            self.assertNotIn('exec "$@"',entry.read_text())

    def test_converted_manifest_requires_nine_distinct_local_lowers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cache=root/'env/image-cache';commits=cache/'commits';commits.mkdir(parents=True);(cache/'configs').mkdir()
            lowers=[]
            for i in range(9):
                (commits/str(i)).write_bytes(b'x');lowers.append(dict(file='/workspace/env/image-cache/commits/'+str(i),digest='sha256:'+str(i),size=1))
            path=cache/'configs'/('sha256-'+c.DIGEST[7:]+'-image.json');path.write_text(json.dumps(dict(lowers=lowers)))
            self.assertEqual(len(c.converted_layers(root)),9)
            lowers[-1]=lowers[0];path.write_text(json.dumps(dict(lowers=lowers)))
            with self.assertRaises(ValueError):c.converted_layers(root)

    def test_native_conversion_request_has_bounded_http_and_no_guest(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);(home/'aenv').mkdir();(home/'aenv/credentials').write_text('url="http://127.0.0.1:34567"\napi_key="fixture"\n')
            response=MagicMock();response.status=201;response.read.return_value=json.dumps(dict(volumeID='vol_'+'a'*32,name='density-c4-input')).encode();response.__enter__.return_value=response
            opener=MagicMock();opener.open.return_value=response
            with patch('urllib.request.build_opener',return_value=opener):
                result=c.prepare_volume(home,'http://127.0.0.1:34567')
            request=opener.open.call_args.args[0]
            self.assertEqual(request.full_url,'http://127.0.0.1:34567/volumes')
            self.assertEqual(opener.open.call_args.kwargs['timeout'],600)
            self.assertEqual(json.loads(request.data),dict(name='density-c4-input',mode='ro',sizeMB=65536,image=c.IMAGE))
            self.assertEqual(result['volume_id'],'vol_'+'a'*32)
            with self.assertRaises(ValueError):c.prepare_volume(home,'https://remote.invalid')

    def test_nine_layer_independent_copies_always_use_new_inodes(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);commits=root/'env/image-cache/commits';commits.mkdir(parents=True);layers=[]
            for i in range(9):
                (commits/str(i)).write_bytes(b'x');layers.append(dict(relative=str(i),source_digest=str(i),size=1))
            def call(label,argv,timeout):
                if argv[0]=='cp':shutil.copy2(argv[-2],argv[-1]);return ''
                return 'ext4'
            result=d.prepare(root,dict(task='c4',count=7,mode='independent'),call,layers=layers)
            self.assertEqual(len(result['objects']),63)
            original={(p.stat().st_dev,p.stat().st_ino) for p in commits.iterdir()}
            self.assertFalse(original & {(o['identity']['device'],o['identity']['inode']) for o in result['objects']})
            manifest=json.loads((root/'env/rootfs-copy-control/manifest.json').read_text());self.assertEqual(manifest['initial_lower_count'],9)


if __name__=='__main__':unittest.main()
