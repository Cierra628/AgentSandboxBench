"""Offline OCI metadata, mock transfer and private wrapper checks; no real service/network."""
import contextlib
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import django_oci as oci
import run_host_creation_controls as runner
import validate_isolated_agentenv as validator
from prepare_xarray6992_image import Redirect

FIXTURE=Path(__file__).parent/'fixtures/django-oci'
sha=lambda b:'sha256:'+hashlib.sha256(b).hexdigest()


class DjangoOCITests(unittest.TestCase):
    @contextlib.contextmanager
    def synthetic(self,root):
        cfg=json.dumps(dict(architecture='amd64',os='linux')).encode()
        bodies=[cfg,*[('layer-'+str(i)).encode() for i in range(10)]]
        desc=[dict(digest=sha(b),size=len(b),mediaType='unit') for b in bodies]
        m=json.dumps(dict(schemaVersion=2,mediaType='unit',config=desc[0],layers=desc[1:])).encode();md=sha(m)
        index=json.dumps(dict(schemaVersion=2,manifests=[dict(digest=md,size=len(m),mediaType='unit',platform=dict(architecture='amd64',os='linux'))])).encode()
        with patch.multiple(oci,ROOT=root,INDEX=sha(index),MANIFEST=md,LAYER_BYTES=sum(len(b) for b in bodies[1:])):
            yield index,m,dict(zip((r['digest'] for r in desc),bodies))

    def make_layout(self,root,index,m,bodies):
        out=root/'.artifacts/django-oci/unit';store=out/'oci/blobs/sha256';store.mkdir(parents=True)
        store.joinpath(oci.INDEX[7:]).write_bytes(index);store.joinpath(oci.MANIFEST[7:]).write_bytes(m)
        for digest,body in bodies.items():store.joinpath(digest[7:]).write_bytes(body)
        _,_,local=oci.metadata(index,m);oci.write(out/'oci/index.json',local);oci.write(out/'oci/oci-layout',{'imageLayoutVersion':'1.0.0'})
        oci.write(out/'result.json',dict(status='PASS',image=oci.IMAGE,downloaded_bytes=sum(map(len,bodies.values()))))
        return out

    def test_real_pinned_index_platform_and_ten_layers(self):
        manifest,blobs,index=oci.metadata((FIXTURE/'index.json').read_bytes(),(FIXTURE/'manifest.json').read_bytes())
        self.assertEqual(len(manifest['layers']),10);self.assertEqual(sum(r['size'] for r in blobs),1242121418)
        self.assertEqual(index['manifests'][0]['digest'],oci.MANIFEST)
        self.assertEqual(index['manifests'][0]['annotations'],{'org.opencontainers.image.ref.name':'latest'})
        for changed in ('index','manifest'):
            a=(FIXTURE/'index.json').read_bytes();b=(FIXTURE/'manifest.json').read_bytes()
            with self.assertRaisesRegex(ValueError,'digest mismatch'):oci.metadata(a+b'\n' if changed=='index' else a,b+b'\n' if changed=='manifest' else b)

    def test_platform_relationship_rejected_even_with_a_matching_index_digest(self):
        data=json.loads((FIXTURE/'index.json').read_text());data['manifests'][0]['size']+=1
        encoded=json.dumps(data).encode()
        with patch.object(oci,'INDEX',sha(encoded)):
            with self.assertRaisesRegex(ValueError,'relationship'):oci.metadata(encoded,(FIXTURE/'manifest.json').read_bytes())

    def test_admission_checks_all_blob_contents_and_local_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.synthetic(root) as (index,m,bodies):
                out=self.make_layout(root,index,m,bodies);_,layout=oci.validate(out)
                receipt=json.loads((out/'result.json').read_text());oci.write(out/'result.json',dict(receipt,downloaded_bytes=0))
                with self.assertRaisesRegex(ValueError,'byte count'):oci.validate(out)
                oci.write(out/'result.json',receipt)
                blob=layout/'blobs/sha256'/next(iter(bodies))[7:];before=blob.read_bytes();blob.write_bytes(b'bad')
                with self.assertRaisesRegex(ValueError,'incomplete or corrupt'):oci.validate(out)
                blob.write_bytes(before);oci.write(layout/'index.json',{})
                with self.assertRaisesRegex(ValueError,'index/layout'):oci.validate(out)

    def test_two_stage_disk_budget_does_not_lower_the_58_gib_launch_gate(self):
        self.assertFalse(oci.download_budget(60*2**30)['ready'])
        self.assertTrue(oci.download_budget(60*2**30+1)['ready'])
        self.assertEqual(oci.download_budget(100*2**30)['input_allowance_bytes'],2*2**30)

    def test_verified_blob_reused_and_unfinished_blob_restarted(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(oci,'available',return_value=100*2**30):
            root=Path(tmp);a,b=b'complete',b'restart-me'
            rows=[dict(digest=sha(x),size=len(x)) for x in (a,b)]
            root.joinpath(sha(a)[7:]).write_bytes(a);root.joinpath(sha(b)[7:]+'.part').write_bytes(b'wrong-old-data')
            calls=[];events=[]
            def fetch(row,offset,length):calls.append((row['digest'],offset));return b[offset:offset+length]
            oci.download_blobs(root,rows,fetch,events.append,time.monotonic()+30)
            self.assertEqual(calls,[(sha(b),0)]);self.assertTrue(oci.blob_valid(root/sha(b)[7:],rows[1]))
            self.assertIn('REUSED',[e['status'] for e in events]);self.assertIn('VERIFIED',[e['status'] for e in events])

    def test_digest_mismatch_never_becomes_a_completed_blob(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(oci,'available',return_value=100*2**30):
            row=dict(digest=sha(b'good'),size=4)
            with self.assertRaisesRegex(ValueError,'digest differs'):oci.download_blobs(Path(tmp),[row],lambda *a:b'evil',lambda e:None,time.monotonic()+10)
            self.assertFalse((Path(tmp)/row['digest'][7:]).exists());self.assertTrue((Path(tmp)/(row['digest'][7:]+'.part')).exists())

    def test_range_retry_is_limited_to_three_attempts(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(oci,'available',return_value=100*2**30):
            events=[];calls=[]
            def fetch(*args):calls.append(args);raise TimeoutError('unit transfer failure')
            with self.assertRaises(TimeoutError):oci.download_blobs(Path(tmp),[dict(digest=sha(b'a'),size=1)],fetch,events.append,time.monotonic()+30)
            self.assertEqual(len(calls),3);self.assertEqual([e['attempt'] for e in events if e['status']=='FAIL'],[1,2,3])

    def test_deadline_stops_before_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TimeoutError):oci.download_blobs(Path(tmp),[dict(digest=sha(b'a'),size=1)],lambda *a:self.fail('no fetch'),lambda e:None,time.monotonic()-1)

    def test_mock_preparation_failure_retains_input_and_resume_can_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.synthetic(root) as (index,m,bodies):
                index_file=root/'index';index_file.write_bytes(index);manifest_file=root/'manifest';manifest_file.write_bytes(m)
                args=types.SimpleNamespace(index=index_file,manifest=manifest_file,output=root/'.artifacts/django-oci/unit',resume=False,download_timeout_seconds=180)
                class Client:
                    def __init__(self,deadline):pass
                    def fetch(self,row,offset,length):return bodies[row['digest']][offset:offset+length]
                with patch.object(oci,'available',return_value=100*2**30),patch.object(oci,'DockerHub',Client),contextlib.redirect_stdout(io.StringIO()):
                    with patch.object(oci,'download_blobs',side_effect=TimeoutError):self.assertEqual(oci.prepare(args),1)
                    self.assertEqual(json.loads((args.output/'result.json').read_text())['status'],'FAIL')
                    args.resume=True;self.assertEqual(oci.prepare(args),0)
                self.assertTrue(list(args.output.glob('previous-*.json')));oci.validate(args.output)

    def test_range_client_uses_public_docker_auth_and_rejects_ignored_ranges(self):
        class Response(io.BytesIO):
            def __init__(self,body,status=200,headers=None):super().__init__(body);self.status=status;self.headers=headers or {}
        client=oci.DockerHub(time.monotonic()+30);calls=[]
        def open_(req,**kw):
            calls.append(req)
            if isinstance(req,str):return Response(b'{"token":"unit-only-token"}')
            self.assertEqual(req.get_header('Authorization'),'Bearer unit-only-token');return Response(b'ab',206,{'Content-Range':'bytes 1-2/4'})
        client.opener=types.SimpleNamespace(open=open_)
        self.assertEqual(client.fetch(dict(digest=sha(b'abcd'),size=4),1,2),b'ab')
        self.assertTrue(calls[0].startswith('https://auth.docker.io/token?'))
        client.opener=types.SimpleNamespace(open=lambda *a,**k:Response(b'abcd'))
        with self.assertRaisesRegex(ValueError,'honor OCI range'):client.fetch(dict(digest=sha(b'abcd'),size=4),1,2)
        req=oci.urllib.request.Request('https://registry-1.docker.io/blob',headers={'Authorization':'Bearer unit-only-token'})
        redirected=Redirect().redirect_request(req,None,302,'Found',{},'https://cdn.example/blob')
        self.assertIsNone(redirected.get_header('Authorization'))

    def test_private_install_preserves_existing_wrapper_and_real_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            mount=Path(tmp);path=mount/'env/deps/regctl/v0.11.5/regctl';path.parent.mkdir(parents=True)
            real=path.with_name('regctl.real');real.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n');real.chmod(0o755)
            original='#!/bin/sh\nif [ "${5-}" = sphinx ]; then echo retained-sphinx; exit 0; fi\nexec "'+str(real)+'" "$@"\n'
            path.write_text(original);path.chmod(0o755);real_before=real.read_bytes()
            oci.install(mount);saved=path.with_name('regctl.before-django')
            self.assertEqual(saved.read_text(),original);self.assertEqual(real.read_bytes(),real_before)
            self.assertIn('/asb-django-oci',path.read_text());self.assertIn(oci.PLATFORM_IMAGE,path.read_text())
            # Map only the container's executable path to its temporary host equivalent.
            local=mount/'local-wrapper';local.write_text(oci.creation_wrapper(saved));local.chmod(0o755)
            dest='ocidir:///workspace/env/image-cache/staging/owned/oci:latest'
            def invoke(args):return subprocess.run([str(local),*args],capture_output=True,text=True,timeout=5)
            p=invoke(['image','copy','--platform','local',oci.PLATFORM_IMAGE,dest]);self.assertEqual(p.returncode,0);self.assertEqual(p.stdout.splitlines()[4],'ocidir:///asb-django-oci:latest')
            self.assertEqual(invoke(['image','copy','--platform','local','sphinx',dest]).stdout.strip(),'retained-sphinx')
            self.assertEqual(invoke(['manifest','get',oci.IMAGE]).stdout.splitlines(),['manifest','get',oci.IMAGE])
            self.assertEqual(invoke(['image','copy','--platform','local',oci.IMAGE,dest]).returncode,2)
            self.assertEqual(invoke(['image','copy','--platform','local',oci.PLATFORM_IMAGE,'ocidir:///tmp/elsewhere']).returncode,2)

    def local_entry(self,root):
        meta=root/'metadata';meta.mkdir()
        (meta/'index.json').write_bytes((FIXTURE/'index.json').read_bytes())
        (meta/'manifest.json').write_bytes((FIXTURE/'manifest.json').read_bytes())
        (meta/'config.json').write_bytes(b'{"unit":"config"}\n')
        log=root/'real-calls';real=root/'fake-regctl'
        real.write_text('#!/bin/sh\nprintf "call\\n" >> "'+str(log)+'"\nprintf "%s\\n" "$@"\n')
        real.chmod(0o755)
        digest=json.loads((meta/'manifest.json').read_text())['config']['digest']
        entry=root/'entry';entry.write_text(oci.local_wrapper(real,meta,digest));entry.chmod(0o755)
        return meta,log,entry,digest

    def test_local_resolver_order_returns_raw_index_manifest_and_config_without_real_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta,log,entry,digest=self.local_entry(Path(tmp))
            index=subprocess.run([entry,'manifest','get','--format','raw-body',oci.IMAGE],capture_output=True,check=True,timeout=5).stdout
            self.assertEqual(index,(meta/'index.json').read_bytes())
            chosen=next(r for r in json.loads(index)['manifests'] if r.get('platform')==dict(os='linux',architecture='amd64'))
            image=oci.IMAGE.split('@')[0]+'@'+chosen['digest']
            manifest=subprocess.run([entry,'manifest','get','--format','raw-body',image],capture_output=True,check=True,timeout=5).stdout
            self.assertEqual(manifest,(meta/'manifest.json').read_bytes())
            self.assertEqual(json.loads(manifest)['config']['digest'],digest)
            config=subprocess.run([entry,'blob','get',oci.IMAGE.split('@')[0],digest],capture_output=True,check=True,timeout=5).stdout
            self.assertEqual(config,(meta/'config.json').read_bytes())
            self.assertFalse(log.exists(),'metadata must not call the saved executable')
            dest='ocidir:///workspace/env/image-cache/staging/owned/oci:latest'
            copy=subprocess.run([entry,'image','copy','--platform','local',image,dest],capture_output=True,check=True,text=True,timeout=5)
            self.assertEqual(copy.stdout.splitlines(),['image','copy','--platform','local','ocidir:///asb-django-oci:latest',dest])
            version=subprocess.run([entry,'version'],capture_output=True,check=True,text=True,timeout=5)
            self.assertEqual(version.stdout,'version\n');self.assertEqual(log.read_text(),'call\ncall\n')

    def test_local_unknown_or_missing_metadata_never_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta,log,entry,digest=self.local_entry(Path(tmp));dest='ocidir:///workspace/env/image-cache/staging/owned/oci:latest'
            unsupported=[[],['manifest','get',oci.IMAGE],['manifest','get','--format','body',oci.IMAGE],
                ['manifest','get','--format','raw-body',oci.IMAGE+'x'],['blob','get',oci.IMAGE,digest],
                ['blob','get',oci.IMAGE.split('@')[0],'sha256:'+'0'*64],
                ['image','copy','--platform','local',oci.IMAGE,dest],
                ['image','copy','--platform','local',oci.PLATFORM_IMAGE,'ocidir:///tmp/not-owned:latest'],
                ['artifact','list','--format','body',oci.PLATFORM_IMAGE],['--version']]
            for args in unsupported:
                with self.subTest(args=args):
                    result=subprocess.run([entry,*args],capture_output=True,timeout=5)
                    self.assertEqual(result.returncode,78);self.assertEqual(result.stdout,b'')
            for name,args in [('index.json',['manifest','get','--format','raw-body',oci.IMAGE]),
                              ('config.json',['blob','get',oci.IMAGE.split('@')[0],digest])]:
                (meta/name).unlink()
                self.assertNotEqual(subprocess.run([entry,*args],capture_output=True,timeout=5).returncode,0)
            self.assertFalse(log.exists())

    def test_local_install_preserves_old_entry_and_binary_and_copies_metadata_only(self):
        for executable_entry in (False,True):
            with self.subTest(executable_entry=executable_entry),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                with self.synthetic(root) as (index,manifest,bodies):
                    prepared=self.make_layout(root,index,manifest,bodies);mount=root/'mount'
                    entry=mount/'env/deps/regctl/v0.11.5/regctl';entry.parent.mkdir(parents=True)
                    original=b'\x7fELF-unit-only' if executable_entry else b'#!/bin/sh\nexit 99\n'
                    entry.write_bytes(original);real=entry.with_name('regctl.real');real.write_bytes(b'\x7fELF-other-unit-only')
                    receipt=oci.install(mount,local_metadata_layout=prepared/'oci')
                    self.assertFalse(receipt['remote_fallback']);self.assertTrue(receipt['local_metadata'])
                    self.assertEqual(entry.with_name('regctl.before-django').read_bytes(),original)
                    self.assertEqual(real.read_bytes(),b'\x7fELF-other-unit-only')
                    meta=mount/'env/django-local-metadata';self.assertEqual(sorted(p.name for p in meta.iterdir()),['config.json','index.json','manifest.json'])
                    self.assertEqual((meta/'index.json').read_bytes(),index);self.assertEqual((meta/'manifest.json').read_bytes(),manifest)
                    digest=json.loads(manifest)['config']['digest'];self.assertEqual((meta/'config.json').read_bytes(),bodies[digest])
                    binary='regctl.before-django' if executable_entry else 'regctl.real'
                    self.assertIn('/workspace/env/deps/regctl/v0.11.5/'+binary,entry.read_text())
                    subprocess.run(['sh','-n',entry],check=True,capture_output=True,timeout=5)

    def test_local_install_rejects_changed_config_before_replacing_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.synthetic(root) as (index,manifest,bodies):
                prepared=self.make_layout(root,index,manifest,bodies);mount=root/'mount'
                entry=mount/'env/deps/regctl/v0.11.5/regctl';entry.parent.mkdir(parents=True);entry.write_bytes(b'\x7fELF-original')
                digest=json.loads(manifest)['config']['digest'];(prepared/'oci/blobs/sha256'/digest[7:]).write_text('changed')
                with self.assertRaisesRegex(ValueError,'digest mismatch'):oci.install(mount,local_metadata_layout=prepared/'oci')
                self.assertEqual(entry.read_bytes(),b'\x7fELF-original');self.assertFalse(entry.with_name('regctl.before-django').exists())

    def test_native_local_metadata_is_explicit_only_for_django_schedule(self):
        tree=ast.parse(Path(validator.__file__).read_text())
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='django_oci_input' and
                    any(isinstance(c,ast.ImportFrom) and c.module=='django_oci' and any(a.name=='install' for a in c.names) for c in n.body))
        for schedule in (None,'grpo','bpo','tvcache'):
            values=dict(mount=Path('/owned'),django_schedule=schedule,django_oci_layout=Path('/verified/oci'),receipt={'django_oci_input':{}})
            with patch.object(oci,'install',return_value={'status':'INSTALLED'}) as install:
                exec(compile(ast.Module(body=branch.body,type_ignores=[]),'<mock native install>','exec'),values)
            expected=dict(local_metadata_layout=Path('/verified/oci')) if schedule else {}
            install.assert_called_once_with(Path('/owned'),**expected)

    def test_readonly_binding_and_explicit_native_guard(self):
        good=dict(Mounts=[dict(Type='bind',Destination='/asb-django-oci',Source='/owned/oci',RW=False)])
        oci.verify_mount(good,Path('/owned/oci'));good['Mounts'][0]['RW']=True
        with self.assertRaises(ValueError):oci.verify_mount(good,Path('/owned/oci'))
        with patch.object(validator.os,'geteuid',return_value=0),patch.object(validator.os,'posix_fallocate',side_effect=AssertionError('no allocation')):
            with self.assertRaisesRegex(ValueError,'requires explicit creation'):validator.main(types.SimpleNamespace(external_creation_oci_input=Path('/not-used')))

    def test_explicit_range_recovery_fetches_only_the_missing_tail(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(oci,'available',return_value=100*2**30):
            root=Path(tmp);store=root/'oci/blobs/sha256';store.mkdir(parents=True)
            body=b'a'*(8*2**20)+b'last!';row=dict(digest=sha(body),size=len(body));partial=store/(row['digest'][7:]+'.part')
            partial.write_bytes(body[:-5]+b'\0'*5)
            events=[dict(status='RESTART_BLOB',digest=row['digest']),dict(status='PASS',digest=row['digest'],offset=0,length=8*2**20)]
            (root/'download-events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
            checkpoint=oci.save_range_checkpoint(root,[row]);self.assertEqual(checkpoint['reusable_bytes'],8*2**20)
            loaded=oci.load_range_checkpoint(root,[row]);calls=[];recorded=[]
            def fetch(row,offset,length):calls.append((offset,length));return body[offset:offset+length]
            oci.download_blobs(store,[row],fetch,recorded.append,time.monotonic()+10,resume_ranges=loaded['ranges'])
            self.assertEqual(calls,[(8*2**20,5)]);self.assertTrue(oci.blob_valid(store/row['digest'][7:],row))
            self.assertIn('RESUME_RANGES',[e['status'] for e in recorded])

    def test_range_checkpoint_rejects_a_changed_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=root/'oci/blobs/sha256';store.mkdir(parents=True);row=dict(digest=sha(b'ab'),size=2)
            partial=store/(row['digest'][7:]+'.part');partial.write_bytes(b'ab')
            (root/'download-events.jsonl').write_text(json.dumps(dict(status='PASS',digest=row['digest'],offset=0,length=2))+'\n')
            oci.save_range_checkpoint(root,[row]);partial.write_bytes(b'cd')
            with self.assertRaisesRegex(ValueError,'identity differs'):oci.load_range_checkpoint(root,[row])

    def test_invalidated_layer_ranges_are_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=root/'oci/blobs/sha256';store.mkdir(parents=True);row=dict(digest=sha(b'ab'),size=2)
            (store/(row['digest'][7:]+'.part')).write_bytes(b'cd')
            rows=[dict(status='PASS',digest=row['digest'],offset=0,length=2),dict(status='INVALIDATED_BLOB',digest=row['digest'])]
            (root/'download-events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            self.assertEqual(oci.save_range_checkpoint(root,[row])['reusable_bytes'],0)

    def test_timeout_records_token_headers_body_and_deadline_stages(self):
        class Body(io.BytesIO):
            status=206;headers={'Content-Range':'bytes 0-1/2'}
            def read1(self,*args):raise TimeoutError('unit body timeout')
        for stage in ('token','response_headers','response_body','download_deadline'):
            with self.subTest(stage=stage):
                client=oci.DockerHub(time.monotonic()+30)
                client.token='unit-only-token';client.token_at=time.monotonic()
                if stage=='token':client.token=''
                if stage=='download_deadline':client.deadline=time.monotonic()-1
                def open_(*args,**kwargs):
                    if stage=='response_body':return Body(b'')
                    raise TimeoutError('unit connection timeout')
                client.opener=types.SimpleNamespace(open=open_)
                with self.assertRaises(TimeoutError) as captured:client.fetch(dict(digest=sha(b'ab'),size=2),0,2)
                self.assertEqual(captured.exception.oci_stage,stage)
        client=oci.DockerHub(time.monotonic()+30);client.token='unit-only-token';client.token_at=time.monotonic()
        def url_failure(*args,**kwargs):raise oci.urllib.error.URLError(TimeoutError('unit wrapped timeout'))
        client.opener=types.SimpleNamespace(open=url_failure)
        with self.assertRaises(oci.urllib.error.URLError) as captured:client.fetch(dict(digest=sha(b'ab'),size=2),0,2)
        self.assertEqual(captured.exception.oci_stage,'response_headers')

    def test_range_recovery_requires_explicit_resume(self):
        with self.assertRaisesRegex(ValueError,'requires --resume'):oci.prepare(types.SimpleNamespace(resume_ranges=True,resume=False))

    def test_outer_cli_forwards_new_flag_without_changing_default(self):
        for local in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);calls=[];args=['run','--output',str(root/'.artifacts/run'),'--upstream',str(root/'upstream')]
                if local:args+=['--oci-input',str(root/'.artifacts/django-oci/input')]
                def execute(command,**kw):calls.append(command);return types.SimpleNamespace(returncode=0)
                with patch.object(runner,'ROOT',root),patch.object(runner.os,'geteuid',return_value=0),patch.object(runner.subprocess,'run',side_effect=execute),patch.object(sys,'argv',args):self.assertEqual(runner.main(),0)
                self.assertEqual('--oci-input' in calls[0],local);self.assertIn('--setenv=ASB_HOST_FILE_SAMPLING=off',calls[0]);self.assertEqual(calls[-1][0],'systemctl')


if __name__=='__main__':unittest.main()
