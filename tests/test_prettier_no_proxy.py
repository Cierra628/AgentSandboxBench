"""Fixed local metadata and toy cache tests; no live Docker, network, archives or guests."""
import ast
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import prettier_no_proxy as n
import agentenv_cold_start as cold
import run_host_file_workflow as entry
import platform_tasks
import host_persistent_input


class NoProxyTests(unittest.TestCase):
    def cache(self,root):
        configs=root/'image-cache/configs';configs.mkdir(parents=True)
        stem='sha256-'+n.DIGEST[7:]+'-image';lowers=[]
        for i in range(19):
            p=root/f'image-cache/commits/layer-{i}.commit';p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'toy layer')
            lowers.append(dict(file='/workspace/env/'+str(p.relative_to(root)),size=123))
        (configs/(stem+'.json')).write_text(json.dumps(dict(repoBlobUrl='',lowers=lowers)))
        (configs/(stem+'.metadata.json')).write_text('{}')
        tools=root/'deps/tools/0.1.1';tools.mkdir(parents=True);(tools/'image.json').write_text('{}')
        return configs/(stem+'.json')

    def metadata(self,path):
        path.mkdir();config=json.dumps(dict(architecture='amd64',os='linux',config=dict(Env=['PATH=/usr/bin']))).encode()
        manifest=json.dumps(dict(config=dict(size=len(config),digest='sha256:'+hashlib.sha256(config).hexdigest()),layers=[dict(size=1) for _ in range(19)])).encode()
        (path/'config.json').write_bytes(config);(path/'manifest.json').write_bytes(manifest)
        return 'sha256:'+hashlib.sha256(manifest).hexdigest()

    def test_environment_removal_and_fixed_mode_restrictions(self):
        original={k:'http://127.0.0.1:17891' for k in n.PROXY_KEYS}|dict(PATH='original')
        self.assertEqual(n.clean_environment(original),dict(PATH='original'));self.assertEqual(len(original),7)
        options=n.container_environment()
        for key in n.PROXY_KEYS:self.assertIn(key+'=',options)
        self.assertNotIn('17891',str(options))
        cfg=platform_tasks.configuration(ROOT/'configs/platform-prettier6604-host-current.json');n.require_mode(cfg,True)
        for change,persistent in [({},False),({'task_id':'sphinx-doc__sphinx-7590'},True),({'rollout':'grpo'},True),({'light_monitor':False},True),({'monitor_mode':'off'},True)]:
            with self.assertRaises(ValueError):n.require_mode(dict(cfg,**change),persistent)

    def test_missing_local_layers_metadata_or_remote_stack_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=self.cache(root);self.assertEqual(n.check_cache(root)['lower_count'],19)
            data=json.loads(p.read_text());original=p.read_text()
            data['repoBlobUrl']='https://registry.invalid/blobs';p.write_text(json.dumps(data))
            with self.assertRaises(ValueError):n.check_cache(root)
            p.write_text(original);(root/'image-cache/commits/layer-2.commit').unlink()
            with self.assertRaises(ValueError):n.check_cache(root)
            (root/'image-cache/commits/layer-2.commit').write_bytes(b'toy')
            (p.parent/p.name.replace('-image.json','-image.metadata.json')).unlink()
            with self.assertRaises(FileNotFoundError):n.check_cache(root)

    def test_metadata_requires_both_original_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'metadata';digest=self.metadata(path)
            with patch.object(n,'DIGEST',digest):
                n.metadata(path);(path/'config.json').write_bytes(b'wrong')
                with self.assertRaises(ValueError):n.metadata(path)
            with self.assertRaises(ValueError):n.metadata(path)

    def test_registry_wrapper_serves_only_exact_local_metadata_and_never_runs_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);digest=self.metadata(root/'metadata');config=json.loads((root/'metadata/manifest.json').read_text())['config']['digest']
            (root/'metadata/version.txt').write_text('VCSTag: v0.11.5\n')
            logs=root/'logs';logs.mkdir();path=root/'regctl'
            path.write_text(n.wrapper(root/'metadata',config).replace('/workspace/env/logs',str(logs)))
            for args,expected in [(['version'],'version.txt'),(['manifest','get','--format','raw-body',n.IMAGE],'manifest.json'),(['blob','get',n.REPOSITORY,config],'config.json')]:
                result=subprocess.run(['sh',path,*args],capture_output=True,timeout=3)
                self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout,(root/'metadata'/expected).read_bytes())
            for args in (['image','copy',n.IMAGE,'ocidir:///target'],['manifest','get','--format','raw-body','wrong-image'],['blob','get',n.REPOSITORY,'wrong-digest'],['manifest','get','; touch unexpected']):
                result=subprocess.run(['sh',path,*args],capture_output=True,timeout=3)
                self.assertEqual(result.returncode,78);self.assertIn(b'remote fallback disabled',result.stderr)
            self.assertEqual((logs/'local-no-proxy-regctl.log').read_text().splitlines(),['version','manifest','config','blocked','blocked','blocked','blocked'])
            self.assertFalse((root/'unexpected').exists())

    def test_install_changes_only_private_copy_and_missing_cache_preserves_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);seed=root/'seed';seed.mkdir();digest=self.metadata(seed/'local-no-proxy-metadata')
            with patch.object(n,'DIGEST',digest):self.cache(seed/'input/env')
            mount=root/'mount';entrypoint=mount/'env/deps/regctl/v0.11.5/regctl';entrypoint.parent.mkdir(parents=True);entrypoint.write_text('# original fixture\n')
            layer=seed/'input/env/image-cache/commits/layer-0.commit';layer.unlink()
            with patch.object(n,'DIGEST',digest),patch.object(n,'dependency_version',return_value='VCSTag: v0.11.5\n'):
                with self.assertRaises(ValueError):n.install(seed,mount)
                self.assertEqual(entrypoint.read_text(),'# original fixture\n');self.assertFalse(entrypoint.with_name('regctl.before-local-no-proxy').exists())
                layer.write_bytes(b'toy');value=n.install(seed,mount)
                self.assertFalse(value['guest_internet']);self.assertEqual(entrypoint.with_name('regctl.before-local-no-proxy').read_text(),'# original fixture\n')
                self.assertIn('API allowInternetAccess request only',value['guest_internet_scope'])
                self.assertNotIn('regctl.real',entrypoint.read_text())

    def test_dependency_version_requires_original_local_elf_and_exact_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'regctl';path.write_bytes(b'\x7fELFtoy')
            with patch.object(n.subprocess,'check_output',return_value='VCSTag: v0.11.5\n') as run:
                self.assertEqual(n.dependency_version(path),'VCSTag: v0.11.5\n')
                self.assertEqual(run.call_args.args[0],[str(path),'version']);self.assertFalse(any(k in run.call_args.kwargs['env'] for k in n.PROXY_KEYS))
            with patch.object(n.subprocess,'check_output',return_value='VCSTag: v0.11.55\n'):
                with self.assertRaises(ValueError):n.dependency_version(path)
            path.write_text('#!/bin/sh\n')
            with patch.object(n.subprocess,'check_output') as run:
                with self.assertRaises(ValueError):n.dependency_version(path)
                run.assert_not_called()

    def test_cold_request_guest_environment_network_and_default_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'aenv').mkdir();(root/'aenv/credentials').write_text('url="http://127.0.0.1:12345"\napi_key="unit-test"\n')
            bodies=[]
            class Response:
                status=201
                def __enter__(self):return self
                def __exit__(self,*a):pass
                def read(self,n):return b'{"sandboxID":"00000000-0000-0000-0000-000000000001"}'
            def opened(request,**kw):bodies.append(json.loads(request.data));return Response()
            with patch.object(cold.urllib.request,'build_opener',return_value=types.SimpleNamespace(open=opened)):
                cold.create(root,'http://127.0.0.1:12345',n.IMAGE)
                cold.create(root,'http://127.0.0.1:12345',n.IMAGE,local_no_proxy=True)
                with self.assertRaises(ValueError):cold.create(root,'http://127.0.0.1:12345','other',local_no_proxy=True)
            self.assertNotIn('envVars',bodies[0]);self.assertNotIn('allowInternetAccess',bodies[0])
            self.assertFalse(bodies[1]['allowInternetAccess']);self.assertEqual(bodies[1]['envVars'],{k:'' for k in n.PROXY_KEYS})
            self.assertEqual({k:v for k,v in bodies[1].items() if k not in ('envVars','allowInternetAccess')},bodies[0])

    def test_outer_forwards_new_mode_clears_proxy_and_keeps_prepare_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);seen=[]
            def run(argv,**kw):
                if argv[0]=='systemd-run':
                    self.assertIn('--setenv=ASB_LOCAL_NO_PROXY=on',argv);self.assertIn('--persistent-seed',argv)
                    for key in n.PROXY_KEYS:self.assertIn('--setenv='+key+'=',argv)
                    self.assertFalse(any(os.environ.get(k) for k in n.PROXY_KEYS));seen.append('group')
                return types.SimpleNamespace(returncode=0)
            env={k:'http://127.0.0.1:17891' for k in n.PROXY_KEYS}
            with patch.dict(os.environ,env),patch.object(entry,'ROOT',base),patch.object(sys,'argv',['entry',str(ROOT/'configs/platform-prettier6604-host-current.json'),'--persistent-input','--local-no-proxy']),patch('platform_tasks.preflight'),patch.object(entry.subprocess,'check_output',return_value='systemd'),patch.object(entry.subprocess,'run',side_effect=run),patch.object(host_persistent_input,'run_phase',side_effect=lambda action,*a,**k:seen.append(action)),patch.object(n,'prepare_metadata',side_effect=lambda p,**kw:seen.append('metadata')):
                self.assertEqual(entry.main(),0)
            self.assertEqual(seen,['metadata','prepare','group','release-source'])

    def test_native_branch_omits_hardcoded_proxy_and_overrides_container_image_defaults(self):
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        old=next(x for x in ast.walk(tree) if isinstance(x,ast.If) and ast.unparse(x.test)=='(prettier6604 or python_inputs) and (not (local_no_proxy or schedule_smoke))')
        new=next(x for x in ast.walk(tree) if isinstance(x,ast.If) and any(isinstance(c,ast.Name) and c.id=='container_environment' for c in ast.walk(x)) and 'schedule_smoke' in ast.unparse(x.test))
        code=compile(ast.Module(body=[old,new],type_ignores=[]),'<actual Docker environment>','exec')
        for local,smoke in ((False,False),(True,False),(False,True),(True,True)):
            enabled=local or smoke
            ns=dict(prettier6604=True,python_inputs=None,local_no_proxy=local,schedule_smoke=smoke,five_oci=None,density=None,argv=[],rootfs_short=False,private_control=False,full_rootfs_mode=False)
            exec(code,ns)
            self.assertEqual('17891' in str(ns['argv']),not enabled)
            if enabled:
                for key in n.PROXY_KEYS:self.assertIn(key+'=',ns['argv'])

    def test_missing_registry_metadata_stops_before_archive_restore_or_systemd_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);phases=[]
            with patch.dict(os.environ,{},clear=True),patch.object(entry,'ROOT',base),patch.object(sys,'argv',['entry',str(ROOT/'configs/platform-prettier6604-host-current.json'),'--persistent-input','--local-no-proxy']),patch('platform_tasks.preflight'),patch.object(entry.subprocess,'check_output',return_value='systemd'),patch.object(entry.subprocess,'run') as run,patch.object(host_persistent_input,'run_phase',side_effect=lambda action,*a,**k:phases.append(action)),patch.object(n,'prepare_metadata',side_effect=FileNotFoundError('missing local image metadata')):
                with self.assertRaises(FileNotFoundError):entry.main()
            run.assert_not_called();self.assertEqual(phases,['release-source'])


class ShortCheckTests(unittest.TestCase):
    RULES='''*filter
:FORWARD ACCEPT [0:0]
-A FORWARD -i tap0 -o vpeer -j AGENTENV-EGRESS
-A AGENTENV-EGRESS -i tap0 -o vpeer -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
-A AGENTENV-EGRESS -d 10.211.0.1/32 -i tap0 -o vpeer -p udp -m udp --dport 53 -j ACCEPT
-A AGENTENV-EGRESS -d 10.211.0.1/32 -i tap0 -o vpeer -p tcp -m tcp --dport 53 -j ACCEPT
-A AGENTENV-EGRESS -d 10.212.0.0/16 -i tap0 -o vpeer -j REJECT --reject-with icmp-port-unreachable
-A AGENTENV-EGRESS -i tap0 -o vpeer -j AGENTENV-USER-EGRESS
-A AGENTENV-USER-EGRESS -i tap0 -o vpeer -j REJECT --reject-with icmp-port-unreachable
COMMIT
'''
    def test_actual_rule_formats_and_default_destination(self):
        for text in (self.RULES,self.RULES.replace('-A AGENTENV-USER-EGRESS -i','-A AGENTENV-USER-EGRESS -d 0.0.0.0/0 -i')):
            self.assertEqual(n.network_rules(text)['status'],'PASS')

    def test_dns_exception_is_explicit_for_private_and_public_addresses(self):
        for address,public in (('10.211.0.1',False),('8.8.8.8',True)):
            value=n.network_rules(self.RULES.replace('10.211.0.1',address))
            self.assertEqual(value['dns_exception'],dict(address=address,port=53,protocols=['tcp','udp'],public=public))
            self.assertFalse(value['complete_egress_isolation'])

    def test_missing_deny_bypass_and_unknown_allowances_are_rejected(self):
        for text in (self.RULES.replace('-j REJECT --reject-with icmp-port-unreachable\nCOMMIT','-j ACCEPT\nCOMMIT'),
                     self.RULES.replace('-A FORWARD -i','-A FORWARD -j ACCEPT\n-A FORWARD -i'),
                     self.RULES.replace('--dport 53','--dport 17891'),
                     self.RULES.replace('10.211.0.1/32','8.8.8.0/24'),
                     self.RULES.replace('10.211.0.1/32','127.0.0.1/32'),
                     self.RULES.replace('-p tcp -m tcp','-p udp -m udp'),
                     self.RULES.replace('-A AGENTENV-EGRESS -d 10.211.0.1/32 -i tap0 -o vpeer -p tcp','-A AGENTENV-EGRESS -d 8.8.8.8/32 -i tap0 -o vpeer -p tcp'),
                     self.RULES.replace('-A AGENTENV-USER-EGRESS -i','-A AGENTENV-USER-EGRESS -d 1.2.3.4/32 -i'),
                     self.RULES.replace('COMMIT','-A AGENTENV-USER-EGRESS -j RETURN\nCOMMIT')):
            with self.subTest(text=text),self.assertRaises(ValueError):n.network_rules(text)

    def test_network_query_is_owned_readonly_and_identity_stable(self):
        vm=dict(pid=123,start_ticks='456',cgroup='0::/docker/owned-container\n')
        foreign=dict(pid=321,start_ticks='654',cgroup='0::/other-container\n')
        from probe_storage_boundary import identities
        def link(path):return 'net:[1]' if str(path)=='/proc/self/ns/net' else 'net:[2]'
        with patch('probe_storage_boundary.identities',side_effect=[[foreign,vm],[foreign,vm]]),patch.object(n.os,'readlink',side_effect=link):
            call=unittest.mock.Mock(return_value=self.RULES)
            self.assertEqual(n.check_network('owned-container',[],call)['status'],'PASS')
            self.assertEqual(call.call_args.args[1],['nsenter','-t','123','-n','--','iptables-nft-save','-t','filter'])
        with patch('probe_storage_boundary.identities',side_effect=[[vm],[]]),patch.object(n.os,'readlink',side_effect=link):
            with self.assertRaises(ValueError):n.check_network('owned-container',[],lambda *a:self.RULES)
        with patch('probe_storage_boundary.identities',return_value=[vm]):
            with self.assertRaises(ValueError):n.check_network('owned-container',[vm],lambda *a:self.fail('must not query preexisting guest'))

    def test_short_option_requires_no_proxy_before_any_docker_access(self):
        with patch.object(sys,'argv',['entry',str(ROOT/'configs/platform-prettier6604-host-current.json'),'--local-no-proxy-check']),patch.object(entry.subprocess,'check_output') as docker,contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):entry.main()
            docker.assert_not_called()

    def test_short_report_requires_cleanup_and_does_not_require_task_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);work=root/'work';work.mkdir();child=root/'child';child.mkdir()
            cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_firecracker_identities_unchanged','original_service_unchanged')}
            check=dict(status='PASS',actions=0,created_guests=1,network=dict(status='PASS',complete_egress_isolation=False,
                dns_exception=dict(address='8.8.8.8',port=53,protocols=['tcp','udp'],public=True)))
            outer=dict(status='PASS',cleanup=cleanup,local_no_proxy_check=check)
            (work/'result.json').write_text(json.dumps(dict(status='PASS',local_no_proxy_check=True,storage_result_dir=str(child))))
            (work/'effective-config.json').write_text(json.dumps(dict(backend='agentenv')))
            (child/'result.json').write_text(json.dumps(outer))
            platform_tasks.report(work,root/'report')
            self.assertEqual(json.loads((root/'report/summary.json').read_text())['actions'],0)
            summary=json.loads((root/'report/summary.json').read_text())
            self.assertEqual(summary['network'],check['network'])
            self.assertEqual(sorted(p.name for p in (root/'report').iterdir()),['result.json','summary.json'])
            outer['cleanup']['successful_fixture_removed']=False
            (child/'result.json').write_text(json.dumps(outer))
            with self.assertRaises(ValueError):platform_tasks.report(work,root/'bad-report')

    def test_native_short_branch_creates_once_checks_metadata_without_task_runner(self):
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        node=next(x for x in ast.walk(tree) if isinstance(x,ast.If) and ast.unparse(x.test)=='local_no_proxy_check' and x.body and isinstance(x.body[0],ast.ImportFrom))
        code=compile(ast.Module(body=node.body,type_ignores=[]),'<actual short branch>','exec')
        with tempfile.TemporaryDirectory() as tmp:
            mount=Path(tmp);log=mount/'env/logs/local-no-proxy-regctl.log';log.parent.mkdir(parents=True);log.write_text('version\nmanifest\n')
            sid='00000000-0000-0000-0000-000000000001';call=unittest.mock.Mock(return_value=sid)
            receipt=dict(address='http://127.0.0.1:12345',local_no_proxy={},checks={})
            ns=dict(mount=mount,ROOT=ROOT,sys=sys,re=__import__('re'),receipt=receipt,call=call,container='owned',before=[],env={},
                    inspect=lambda _:dict(Id='owned',Config=dict(Env=[k+'=' for k in n.PROXY_KEYS])),cli=lambda *a:'guest-no-proxy-ready')
            with patch.object(n,'check_network',return_value=dict(status='PASS')):exec(code,ns)
            self.assertEqual(call.call_count,1);self.assertEqual(ns['sid'],sid)
            self.assertIn('--local-no-proxy',call.call_args.args[1]);self.assertEqual(receipt['local_no_proxy_check']['actions'],0)
            self.assertEqual(receipt['local_no_proxy_check']['created_guests'],1)


if __name__=='__main__':unittest.main()
