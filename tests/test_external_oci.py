"""OCI metadata and private wrapper tests; no registry, VM or service."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from external_oci import validate,install
from external_workload import MATPLOTLIB


def blob(store,value):
    raw=value if isinstance(value,bytes) else json.dumps(value).encode()
    digest='sha256:'+hashlib.sha256(raw).hexdigest();(store/digest[7:]).write_bytes(raw)
    return dict(digest=digest,size=len(raw))


class ExternalOciTests(unittest.TestCase):
    def fixture(self,root,indexed):
        d=root/'input';store=d/'oci/blobs/sha256';store.mkdir(parents=True)
        conf=blob(store,dict(os='linux',architecture='amd64'));layer=blob(store,b'fixture layer')
        manifest=blob(store,dict(schemaVersion=2,config=conf,layers=[layer]))
        requested=blob(store,dict(schemaVersion=2,manifests=[dict(manifest,platform=dict(os='linux',architecture='amd64'))])) if indexed else manifest
        image='docker.io/fixture/task@'+requested['digest'];cfg=dict(task_id=MATPLOTLIB,image=image)
        (d/'requested-manifest.json').write_bytes((store/requested['digest'][7:]).read_bytes())
        (d/'request.json').write_text(json.dumps(dict(image=image,metadata_exit_code=0)))
        (d/'oci/oci-layout').write_text(json.dumps({'imageLayoutVersion':'1.0.0'}))
        (d/'oci/index.json').write_text(json.dumps(dict(schemaVersion=2,manifests=[manifest])))
        return d,cfg,layer

    def test_fixed_manifest_index_identity_and_changed_blob(self):
        for indexed in (False,True):
            with self.subTest(indexed=indexed),tempfile.TemporaryDirectory() as tmp:
                d,cfg,layer=self.fixture(Path(tmp),indexed)
                v,layout,m=validate(d,cfg);self.assertEqual(v['layer_count'],1)
                validate(d,cfg)
                (layout/'blobs/sha256'/layer['digest'][7:]).write_bytes(b'corrupt layer')
                with self.assertRaises(ValueError):validate(d,cfg)

    def test_private_transport_reuses_real_binary_and_never_falls_back_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);d,cfg,_=self.fixture(root,True);mount=root/'mount'
            deps=mount/'env/deps/regctl/v0.11.5';deps.mkdir(parents=True)
            (deps/'regctl').write_text('original wrapper');(deps/'regctl.real').write_bytes(b'\x7fELFfixture')
            value=install(mount,d,cfg);self.assertFalse(value['remote_fallback'])
            self.assertEqual((deps/'regctl.before-external-oci').read_text(),'original wrapper')
            script=(deps/'regctl').read_text().replace('/workspace/env',str(mount/'env'))
            path=root/'wrapper';path.write_text(script);path.chmod(0o755)
            p=subprocess.run([path,'manifest','get','--format','raw-body',cfg['image']],capture_output=True,timeout=5)
            self.assertEqual(p.returncode,0);self.assertEqual(p.stdout,(d/'requested-manifest.json').read_bytes())
            p=subprocess.run([path,'manifest','get','unknown'],capture_output=True,timeout=5)
            self.assertEqual(p.returncode,78);self.assertIn(b'remote fallback disabled',p.stderr)
            with self.assertRaises(ValueError):install(mount,d,cfg)

    def test_reject_other_task_and_wrong_requested_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            d,cfg,_=self.fixture(Path(tmp),False)
            with self.assertRaises(ValueError):validate(d,dict(cfg,task_id='unapproved-task'))
            (d/'requested-manifest.json').write_text('{}')
            with self.assertRaises(ValueError):validate(d,cfg)


if __name__=='__main__':unittest.main()
