"""Toy files, local shell dispatch and mocked calls only; no Docker, network or guests."""
import ast
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import prettier_no_proxy as n
import agentenv_cold_start as cold
import platform_tasks as platform
import run_host_file_workflow as wrapper
import validate_isolated_agentenv as native
import host_persistent_input as persistent


def config():return platform.configuration(ROOT/'configs/platform-sphinx7590-host-current.json')
def tree(name):return ast.parse((ROOT/'scripts'/name).read_text())
def execute(nodes,values):exec(compile(ast.Module(body=nodes,type_ignores=[]),'<actual local-mode source>','exec'),values)


class SphinxLocalTests(unittest.TestCase):
    def metadata(self,path):
        path.mkdir(parents=True)
        data=json.dumps(dict(config=dict(size=8123,digest='sha256:'+'a'*64),layers=[dict(size=1) for _ in range(13)])).encode()
        (path/'manifest.json').write_bytes(data)
        return 'sha256:'+hashlib.sha256(data).hexdigest()

    def cache(self,root):
        folder=root/'image-cache/configs';folder.mkdir(parents=True)
        lowers=[]
        for i in range(13):
            p=root/f'image-cache/commits/layer-{i}.commit';p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'toy cache')
            lowers.append(dict(file='/workspace/env/'+str(p.relative_to(root)),size=p.stat().st_size))
        stem='sha256-'+n.SPHINX_DIGEST[7:]+'-image'
        p=folder/(stem+'.json');p.write_text(json.dumps(dict(repoBlobUrl='',lowers=lowers)))
        (folder/(stem+'.metadata.json')).write_text('{"base_context":{}}')
        tools=root/'deps/tools/0.1.1';tools.mkdir(parents=True);(tools/'image.json').write_text('{}')
        return p

    def test_metadata_needs_only_manifest_and_never_docker_or_config_blob(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);digest=self.metadata(root/'source')
            with patch.object(n,'SPHINX_DIGEST',digest),patch.object(n,'SPHINX_MANIFEST',root/'source/manifest.json'),patch.object(n.subprocess,'check_output') as run:
                n.prepare_metadata(root/'prepared',task_id=n.SPHINX_TASK)
                self.assertEqual(sorted(p.name for p in (root/'prepared').iterdir()),['manifest.json'])
                self.assertEqual(n.metadata(root/'prepared',task_id=n.SPHINX_TASK)['config']['size'],8123)
                run.assert_not_called()
                (root/'prepared/manifest.json').write_text('{}')
                with self.assertRaises(ValueError):n.metadata(root/'prepared',task_id=n.SPHINX_TASK)

    def test_wrapper_only_version_and_exact_sphinx_manifest_no_original_fallback(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.metadata(root/'metadata');(root/'metadata/version.txt').write_text('VCSTag: v0.11.5\n')
            logs=root/'logs';logs.mkdir();p=root/'regctl'
            p.write_text(n.wrapper(root/'metadata','sha256:'+'a'*64,task_id=n.SPHINX_TASK).replace('/workspace/env/logs',str(logs)))
            for args,file in ((['version'],'version.txt'),(['manifest','get','--format','raw-body',n.SPHINX_IMAGE],'manifest.json')):
                r=subprocess.run(['sh',p,*args],capture_output=True,timeout=3);self.assertEqual(r.returncode,0,r.stderr)
                self.assertEqual(r.stdout,(root/'metadata'/file).read_bytes())
            for args in (['blob','get',n.SPHINX_IMAGE.split('@')[0],'sha256:'+'a'*64],['image','copy',n.SPHINX_IMAGE,'ocidir:///target'],
                         ['manifest','get','--format','raw-body',n.IMAGE],['version','--format','json']):
                r=subprocess.run(['sh',p,*args],capture_output=True,timeout=3);self.assertEqual(r.returncode,78)
            self.assertEqual((logs/'local-no-proxy-regctl.log').read_text().splitlines(),['version','manifest']+['blocked']*4)
            self.assertNotIn('exec /workspace/env/deps',p.read_text());self.assertNotIn('regctl.real',p.read_text())

    def test_cache_rejects_missing_metadata_remote_duplicate_or_wrong_sized_lowers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=self.cache(root);original=p.read_text();value=json.loads(original)
            self.assertEqual(n.check_cache(root,task_id=n.SPHINX_TASK)['lower_count'],13)
            for change in ('remote','duplicate','count','size','target'):
                v=json.loads(original)
                if change=='remote':v['repoBlobUrl']='https://unwanted.invalid/'
                if change=='duplicate':v['lowers'][1]=v['lowers'][0]
                if change=='count':v['lowers'].pop()
                if change=='size':v['lowers'][0]['size']+=1
                if change=='target':v['lowers'][0]['targetFile']='remote-file'
                p.write_text(json.dumps(v))
                with self.subTest(change=change),self.assertRaises(ValueError):n.check_cache(root,task_id=n.SPHINX_TASK)
            p.write_text(original)
            metadata=p.with_name(p.name.replace('-image.json','-image.metadata.json'));metadata.unlink()
            with self.assertRaises(FileNotFoundError):n.check_cache(root,task_id=n.SPHINX_TASK)
            metadata.write_text('{}');(root/'image-cache/commits/layer-0.commit').unlink()
            with self.assertRaises(ValueError):n.check_cache(root,task_id=n.SPHINX_TASK)

    def test_install_uses_real_binary_and_does_not_execute_saved_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);seed=root/'seed';digest=self.metadata(seed/'local-no-proxy-metadata')
            with patch.object(n,'SPHINX_DIGEST',digest):self.cache(seed/'input/env')
            mount=root/'mount';p=mount/'env/deps/regctl/v0.11.5/regctl';p.parent.mkdir(parents=True);p.write_text('# old wrapper must never execute\n')
            real=p.with_name('regctl.real')
            with patch.object(n,'SPHINX_DIGEST',digest),patch.object(n,'dependency_version',return_value='VCSTag: v0.11.5\n') as version:
                with self.assertRaises(ValueError):n.install(seed,mount,task_id=n.SPHINX_TASK)
                version.assert_not_called();self.assertFalse(p.with_name('regctl.before-local-no-proxy').exists())
                real.write_bytes(b'\x7fELFtoy');value=n.install(seed,mount,task_id=n.SPHINX_TASK)
                version.assert_called_once_with(real);self.assertEqual(real.read_bytes(),b'\x7fELFtoy')
                self.assertEqual(p.with_name('regctl.before-local-no-proxy').read_text(),'# old wrapper must never execute\n')
                self.assertFalse((mount/'env/local-no-proxy/config.json').exists());self.assertEqual(value['image'],n.SPHINX_IMAGE)

    def test_explicit_mode_preserves_old_requests_and_accepts_only_two_4gib_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);(home/'aenv').mkdir();(home/'aenv/credentials').write_text('url="http://127.0.0.1:12345"\napi_key="test"\n')
            bodies=[]
            response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=None);response.status=201
            response.read.return_value=b'{"sandboxID":"00000000-0000-0000-0000-000000000001"}'
            def opened(request,**kw):bodies.append(json.loads(request.data));return response
            with patch.object(cold.urllib.request,'build_opener',return_value=SimpleNamespace(open=opened)):
                for enabled in (False,True):cold.create(home,'http://127.0.0.1:12345',n.SPHINX_IMAGE,local_no_proxy=enabled)
                cold.create(home,'http://127.0.0.1:12345',n.IMAGE,local_no_proxy=True)
                cold.create(home,'http://127.0.0.1:12345','legacy-image',memory_mib=8192,allow_8gib=True)
                for image,memory in (('unknown',4096),(n.SPHINX_IMAGE,8192)):
                    with self.assertRaises(ValueError):cold.create(home,'http://127.0.0.1:12345',image,memory_mib=memory,allow_8gib=True,local_no_proxy=True)
            self.assertNotIn('envVars',bodies[0]);self.assertNotIn('allowInternetAccess',bodies[0])
            for body in bodies[1:3]:self.assertFalse(body['allowInternetAccess']);self.assertEqual(body['envVars'],{k:'' for k in n.PROXY_KEYS})
            self.assertEqual(bodies[3]['memoryMB'],8192);self.assertNotIn('envVars',bodies[3])
            self.assertEqual({k:v for k,v in bodies[1].items() if k not in ('envVars','allowInternetAccess')},bodies[0])

    def test_outer_routes_sphinx_metadata_before_restoration_and_clears_proxies(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);events=[]
            def metadata(path,**kw):self.assertEqual(kw,dict(task_id=n.SPHINX_TASK));events.append('metadata')
            def phase(action,path,timeout,**kw):
                events.append(action)
                if action=='prepare':self.assertEqual(kw,dict(task_profile='sphinx7590'))
            def run(argv,**kw):
                if argv[0]=='systemd-run':
                    events.append('group');self.assertIn('--setenv=ASB_LOCAL_NO_PROXY=on',argv)
                    self.assertNotIn('--setenv=ASB_LOCAL_NO_PROXY_CHECK=on',argv)
                    for key in n.PROXY_KEYS:self.assertIn('--setenv='+key+'=',argv);self.assertFalse(os.environ.get(key))
                return SimpleNamespace(returncode=0)
            with patch.dict(os.environ,{k:'http://127.0.0.1:17891' for k in n.PROXY_KEYS}),patch.object(wrapper,'ROOT',base),\
                 patch.object(sys,'argv',['entry',str(ROOT/'configs/platform-sphinx7590-host-current.json'),'--persistent-input','--local-no-proxy']),\
                 patch.object(platform,'preflight'),patch.object(wrapper.subprocess,'check_output',return_value='systemd'),\
                 patch.object(wrapper.subprocess,'run',side_effect=run),patch.object(persistent,'run_phase',side_effect=phase),\
                 patch.object(n,'prepare_metadata',side_effect=metadata):self.assertEqual(wrapper.main(),0)
            self.assertEqual(events,['metadata','prepare','group','release-source'])

    def test_sphinx_short_option_and_other_modes_rejected_before_docker(self):
        cfg=config();n.require_mode(cfg,True)
        for change in ({'replay_policy':'strict'},{'task_id':'pydata__xarray-6992'},{'monitor_mode':'off'},{'checkpoint_after_step':29}):
            with self.subTest(change=change),self.assertRaises(ValueError):n.require_mode(dict(cfg,**change),True)
        with patch.object(sys,'argv',['entry',str(ROOT/'configs/platform-sphinx7590-host-current.json'),'--persistent-input','--local-no-proxy','--local-no-proxy-check']),patch.object(wrapper.subprocess,'check_output') as docker,contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):wrapper.main()
            docker.assert_not_called()

    def native_args(self,cfg):
        module=tree('validate_isolated_agentenv.py');entry=next(v for v in module.body if isinstance(v,ast.If) and ast.unparse(v.test)=="__name__ == '__main__'")
        nodes=[]
        for node in entry.body:
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='args' for t in node.targets):break
            nodes.append(node)
        values=dict(native.__dict__);execute(nodes,values)
        return values['parser'].parse_args(platform.commands(cfg,Path('/seed'),persistent_input=True)[1][2:])

    def test_native_guard_accepts_actual_cli_sphinx_flags_and_rejects_wrong_modes(self):
        nodes=next(v for v in tree('validate_isolated_agentenv.py').body if isinstance(v,ast.FunctionDef) and v.name=='main').body
        start=next(i for i,v in enumerate(nodes) if isinstance(v,ast.Assign) and ast.unparse(v.targets[0])=='local_no_proxy')
        end=next(i for i in range(start,len(nodes)) if isinstance(nodes[i],ast.If) and ast.unparse(nodes[i].test)=='persistent_seed')
        good=self.native_args(config())
        for change in ({},{'python_sphinx_pipeline_policy_version':1},{'python_task_profile':'xarray6992'},{'local_no_proxy_check':True}):
            args=SimpleNamespace(**(vars(good)|change));values=dict(os=os,args=args,persistent_seed=Path('/seed'),prettier6604=False)
            with patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'}):
                if change:
                    with self.assertRaises(ValueError):execute(nodes[start:end],values)
                else:
                    execute(nodes[start:end],values);self.assertEqual(values['local_task_id'],n.SPHINX_TASK)
                    self.assertEqual(persistent.native_option(args),Path('/seed'))

    def test_python_cold_create_and_proxy_probe_happen_before_sampling(self):
        nodes=next(v for v in tree('run_xarray6992.py').body if isinstance(v,ast.FunctionDef) and v.name=='run').body
        task_try=next(v for v in nodes if isinstance(v,ast.Try))
        body=task_try.body;start=next(i for i,v in enumerate(body) if isinstance(v,ast.Assign) and ast.unparse(v.targets[0])=='sid')
        end=next(i for i in range(start,len(body)) if isinstance(body[i],ast.If) and ast.unparse(body[i].test)=='cache_control')
        for enabled in (False,True):
            call=Mock(return_value='00000000-0000-0000-0000-000000000001');guest=Mock(return_value='guest-no-proxy-ready')
            values=dict(call=call,guest=guest,stage='cold-start',sys=sys,ROOT=ROOT,args=SimpleNamespace(config_home=Path('/cfg'),service_url='http://127.0.0.1:12345'),
                cfg={'source_image':n.SPHINX_IMAGE},re=re,controller=SimpleNamespace(live={}),local_no_proxy=enabled,result={},shlex=__import__('shlex'))
            execute(body[start:end],values)
            self.assertEqual('--local-no-proxy' in call.call_args.args[1],enabled)
            self.assertEqual(guest.call_count,1 if enabled else 0)
            if enabled:self.assertEqual(values['result']['local_no_proxy'],'PASS');self.assertIn('/opt/miniconda3/envs/testbed/bin/python',guest.call_args.args[1])
        guest.return_value='unexpected';values['result']={}
        with self.assertRaises(AssertionError):execute(body[start:end],values)
        self.assertIn(values['sid'],values['controller'].live)
        self.assertNotIn('local_no_proxy',values['result'])

    def test_python_mode_guard_and_child_environment_are_explicit_only(self):
        nodes=next(v for v in tree('run_xarray6992.py').body if isinstance(v,ast.FunctionDef) and v.name=='run').body
        start=next(i for i,v in enumerate(nodes) if isinstance(v,ast.Assign) and ast.unparse(v.targets[0])=='local_no_proxy')
        guard=nodes[start:start+2]
        original={k:'http://127.0.0.1:17891' for k in n.PROXY_KEYS}|{'PATH':'/original/bin'}
        for enabled in (False,True):
            values=dict(os=os,sphinx=True,light=True,pipeline=True,pipeline_version=2,args=SimpleNamespace(persistent_sampling=True,config_home='/cfg'),
                checkpoint=False,continuation=False,cache_control=False,ROOT=ROOT)
            with patch.dict(os.environ,dict(original,ASB_LOCAL_NO_PROXY='on' if enabled else 'off')):
                execute(guard,values)
                index=next(i for i,v in enumerate(nodes) if isinstance(v,ast.Assign) and ast.unparse(v.targets[0])=='env')
                execute(nodes[index:index+2],values)
            for key in n.PROXY_KEYS:self.assertEqual(key not in values['env'],enabled)
            self.assertIn('/original/bin',values['env']['PATH'])
            if enabled:
                values['sphinx']=False
                with patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'}),self.assertRaises(ValueError):execute(guard,values)

    def test_native_requires_guest_proof_and_exact_sphinx_regctl_categories(self):
        node=next(v for v in ast.walk(tree('validate_isolated_agentenv.py')) if isinstance(v,ast.If) and ast.unparse(v.test)=='local_no_proxy' and 'unexpected Python-task regctl operation' in ast.unparse(v))
        with tempfile.TemporaryDirectory() as tmp:
            mount=Path(tmp);log=mount/'env/logs/local-no-proxy-regctl.log';log.parent.mkdir(parents=True)
            for proof,commands,passed in [('PASS','version\nmanifest\n',True),('FAIL','version\nmanifest\n',False),('PASS','version\nblocked\n',False)]:
                log.write_text(commands);values=dict(local_no_proxy=True,task_result=dict(local_no_proxy=proof),receipt={'local_no_proxy':{}},mount=mount)
                if passed:execute([node],values);self.assertEqual(values['receipt']['local_no_proxy']['registry_commands'],['version','manifest'])
                else:
                    with self.assertRaises(AssertionError):execute([node],values)


if __name__=='__main__':unittest.main()
