"""Offline Cython paths: fixtures, mocked CLI/relay and temporary toy scripts only."""
import ast
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from external_workload import CYTHON,CYTHON_READS,CYTHON_WRITES,CYTHON_VALIDATION,C4,budgets,load,write_inputs,guest_runner,validate_timings,validate_cython_outputs
from external_guest_proxy import GuestProxy,environment_script,upstream_port


def fixture(root):
    run=root/'published';run.mkdir();files={};steps=[]
    exits={41:1,57:1,68:1,72:1,78:129}
    for i in range(1,84):
        tool='read' if i in CYTHON_READS else 'write' if i in CYTHON_WRITES else 'bash'
        command='printf ok\n'
        if tool=='read':
            command=f'python3 /probe/input/{i:03}-read.py\n';files[f'{i:03}-read.py']='print("toy read")\n'
        elif tool=='write':
            command=f'cat /probe/input/{i:03}.payload > /app/toy.py\n';files[f'{i:03}.payload']='print("toy payload")\n'
        files[f'{i:03}.sh']=command
        steps.append(dict(step=i,tool=tool,command=command,script_sha256=hashlib.sha256(command.encode()).hexdigest()))
    (run/'replay-inputs.json').write_text(json.dumps(dict(steps=steps,files=files)))
    with (run/'steps.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=['step','exit_code','timed_out']);w.writeheader()
        w.writerows(dict(step=i,exit_code=exits.get(i,0),timed_out=False) for i in range(1,84))
    cfg=root/'cfg.json';cfg.write_text(json.dumps(dict(version=1,task_id=CYTHON,memory_mib=8192,actions=83,run='published',
        repository='local-fixture',revision='unit-revision',image='unit-image',validation_scope='fixture tests and extension imports')))
    clock=root/'experiments/container-screening/harness/clock_ns.c';clock.parent.mkdir(parents=True);clock.write_text('/* mocked compiler input */\n')
    return cfg,run


def outputs(logs):
    logs.mkdir(exist_ok=True)
    (logs/'082.stdout').write_text('=== tests from /app/pyknotid (source-tree context) ===\n18 passed, 2 warnings in 1s\nsource-tree chelpers: /app/example.so\n=== tests from /tmp (site-packages context) ===\n18 passed in 1s\ninstalled pkg: /usr/local/site-packages/example\n')
    (logs/'076.stdout').write_text('SNIPPET OK - alexander_polynomial(-1) = 3\n')
    (logs/'083.stdout').write_text('final check: numpy 2.3.0\n')


class CythonTests(unittest.TestCase):
    def test_complete_inputs_and_missing_read_or_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg,run=fixture(root);value,replay,expected=load(cfg,root)
            self.assertEqual(len(expected),83)
            target=root/'bundle';target.mkdir();write_inputs(target,replay,CYTHON)
            self.assertEqual(len(list(target.iterdir())),105)
            self.assertEqual(len(list(target.glob('*-read.py'))),20);self.assertEqual(len(list(target.glob('*.payload'))),2)
            for name,text in replay['files'].items():self.assertEqual((target/name).read_text(),text)
            for missing in ('003-read.py','031.payload'):
                broken=json.loads(json.dumps(replay));broken['files'].pop(missing);(run/'replay-inputs.json').write_text(json.dumps(broken))
                with self.assertRaises(ValueError):load(cfg,root)

    def test_test_contexts_reject_pipeline_false_success_and_version_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs=Path(tmp);outputs(logs);validate_cython_outputs(logs)
            original=(logs/'082.stdout').read_text()
            for bad in (original.replace('18 passed','17 passed',1),original.replace('18 passed','18 passed, 1 failed',1),
                        original.replace('source-tree context','wrong-context'),original+'Traceback (most recent call last)\n',
                        original+'ERROR: lost test output\n'):
                (logs/'082.stdout').write_text(bad)
                with self.assertRaises(ValueError):validate_cython_outputs(logs)
            (logs/'082.stdout').write_text(original)
            (logs/'083.stdout').write_text('final check: numpy 2.3.01\n')
            with self.assertRaises(ValueError):validate_cython_outputs(logs)

    def test_original_extension_check_rejects_python_fallback(self):
        code=CYTHON_VALIDATION.split("<<'PYCHECK'\n",1)[1].rsplit('\nPYCHECK',1)[0]
        parent=types.ModuleType('pyknotid');space=types.ModuleType('pyknotid.spacecurves')
        parent.cinvariants=types.SimpleNamespace(__file__='cinvariants.so')
        space.chelpers=types.SimpleNamespace(__file__='chelpers.so');space.ccomplexity=types.SimpleNamespace(__file__='ccomplexity.so')
        with patch.dict(sys.modules,{'pyknotid':parent,'pyknotid.spacecurves':space}),patch('subprocess.check_output',return_value='unit-commit'),contextlib.redirect_stdout(io.StringIO()):
            exec(compile(code,'<original extension validation>','exec'),{})
            space.chelpers.__file__='chelpers.py'
            with self.assertRaises(AssertionError):exec(compile(code,'<original extension validation>','exec'),{})

    def test_budgets_are_nested_and_old_tasks_keep_original_values(self):
        for task in (C4,'django__django-16819'):
            self.assertEqual(budgets(task),dict(replay=240,controller=720,native=1050,workflow=1200))
            self.assertNotIn('guest-env.sh',guest_runner(2,task_id=task))
        b=budgets(CYTHON)
        self.assertEqual(b,dict(replay=900,controller=1800,native=2100,workflow=2400))
        self.assertGreater(b['controller'],610+b['replay']);self.assertGreater(b['native'],b['controller']+120)
        self.assertGreater(b['workflow'],b['native']+150)

    def test_actual_callers_require_cython_success_and_use_nested_budgets(self):
        root=Path(__file__).resolve().parents[1];stages=set()
        for name in ('run_external_workload.py','run_host_external_workload.py','validate_isolated_agentenv.py'):
            tree=ast.parse((root/'scripts'/name).read_text())
            for n in ast.walk(tree):
                if isinstance(n,ast.Subscript) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id in ('budgets','external_budgets'):
                    value=eval(compile(ast.Expression(n),'<actual budget expression>','eval'),{'budgets':budgets,'external_budgets':budgets,'cfg':{'task_id':CYTHON},'external_value':{'task_id':CYTHON}})
                    self.assertEqual(value,budgets(CYTHON)[n.slice.value]);stages.add(n.slice.value)
            if name!='run_external_workload.py':
                key='task' if name.startswith('run_host') else 'external_result';config='cfg' if key=='task' else 'external_value'
                node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and config+"['task_id']" in ast.unparse(n.test) and
                    any(isinstance(a,ast.Subscript) and ast.unparse(a)==key+"['functional_validation']" for a in ast.walk(n)))
                code=compile(ast.Module(body=[node],type_ignores=[]),'<actual Cython success guard>','exec')
                result=dict(functional_validation='PASS')
                ns={key:result,config:{'task_id':CYTHON},'CYTHON':CYTHON,'proxy':types.SimpleNamespace(check=lambda:None),'endpoint_metrics':False};exec(code,ns)
                result['functional_validation']='FAIL'
                with self.assertRaises(AssertionError):exec(code,ns)
        self.assertEqual(stages,{'replay','controller','native','workflow'})

    def test_proxy_configuration_uses_private_relay_not_guest_loopback(self):
        self.assertEqual(upstream_port({'HTTP_PROXY':'http://127.0.0.1:17891'}),17891)
        for url in ('http://user:secret@127.0.0.1:1','http://192.0.2.1:80',''):
            with self.assertRaises(ValueError) as error:upstream_port({'HTTP_PROXY':url})
            self.assertNotIn('secret',str(error.exception))
        env=environment_script('http://172.17.0.1:34567')
        self.assertIn('GIT_CONFIG_KEY_0=http.version',env);self.assertIn('HTTP/1.1',env)
        self.assertIn('HTTP_PROXY=http://172.17.0.1:34567',env);self.assertNotIn('git config',env)
        with self.assertRaises(ValueError):environment_script('http://127.0.0.1:17891')

    def test_owned_proxy_lifecycle_without_opening_sockets_or_starting_processes(self):
        for timeout in (False,True):
            with self.subTest(timeout=timeout),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);out=root/'out';out.mkdir();up=root/'up';(up/'scripts').mkdir(parents=True)
                (up/'scripts/agentenv_workload_proxy.py').write_text('# fixture, never executed\n')
                child=MagicMock(pid=1234);child.poll.return_value=None
                child.wait.side_effect=[subprocess.TimeoutExpired('owned relay',5),0] if timeout else None
                sock=MagicMock();sock.__enter__.return_value.getsockname.return_value=('192.0.2.17',34567)
                def opened(argv,**kwargs):
                    self.assertIn('--address',argv);self.assertNotIn('0.0.0.0',argv)
                    (out/'guest-proxy-ready.json').write_text(json.dumps(dict(pid=1234,address='192.0.2.17',port=34567)))
                    return child
                addresses=json.dumps([dict(addr_info=[dict(family='inet',local='172.17.0.1',prefixlen=16)])])
                with patch.dict('os.environ',{'HTTP_PROXY':'http://127.0.0.1:17891'}),patch('external_guest_proxy.subprocess.check_output',side_effect=[addresses,'[]']) as ip,patch('external_guest_proxy.subprocess.run') as change,patch('external_guest_proxy.socket.socket',return_value=sock),patch('external_guest_proxy.subprocess.Popen',side_effect=opened):
                    proxy=GuestProxy(out,up,proxy_url='http://127.0.0.1:17891') if timeout else GuestProxy(out,up);self.assertEqual(proxy.start(),'http://192.0.2.17:34567');proxy.check();proxy.stop()
                self.assertEqual(ip.call_count,2)
                self.assertEqual([c.args[0] for c in change.call_args_list],[['ip','addr','add','192.0.2.17/32','dev','lo'],['ip','addr','del','192.0.2.17/32','dev','lo']])
                self.assertEqual(json.loads((out/'guest-proxy-address.json').read_text())['status'],'REMOVED')
                child.terminate.assert_called_once();self.assertEqual(child.kill.call_count,int(timeout));self.assertIsNone(proxy.process)

    def test_small_shell_preserves_read_order_and_independent_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=root/'app';app.mkdir();(app/'nested').mkdir();bundle=root/'input';bundle.mkdir()
            (app/'sample').write_text('toy-data')
            (bundle/'guest-env.sh').write_text(environment_script('http://172.17.0.1:34567'))
            (bundle/'001-read.py').write_text('from pathlib import Path\nprint(Path("sample").read_text())\n')
            (bundle/'001.sh').write_text('python3 '+shlex.quote(str(bundle/'001-read.py'))+'\n')
            (bundle/'002.sh').write_text('cd nested; saved=must-not-leak\n')
            (bundle/'003.sh').write_text('test -z "$saved" && test "$GIT_CONFIG_VALUE_0" = HTTP/1.1 || exit 99\nfalse\n')
            clock=bundle/'clock-ns';clock.write_text('#!/bin/sh\ndate +%s%N\n');clock.chmod(0o755)
            script=guest_runner(3,task_id=CYTHON).replace('/probe/input',str(bundle)).replace('cd /app','cd '+shlex.quote(str(app)))
            path=bundle/'runner.sh';path.write_text(script)
            p=subprocess.run(['bash',path],capture_output=True,text=True,timeout=10)
            self.assertEqual(p.returncode,0,p.stderr)
            with (bundle/'tools.tsv').open() as f:rows=list(csv.DictReader(f,delimiter='\t'))
            validate_timings(rows);self.assertEqual([int(r['exit_code']) for r in rows],[0,0,1]);self.assertEqual((bundle/'logs/001.stdout').read_text(),'toy-data\n')

    def test_proxy_start_failure_removes_only_its_new_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out';out.mkdir();up=root/'up';(up/'scripts').mkdir(parents=True)
            (up/'scripts/agentenv_workload_proxy.py').write_text('# never executed\n')
            sock=MagicMock();sock.__enter__.return_value.getsockname.return_value=('198.51.100.17',34567)
            # The first candidate belongs to someone else and must be left untouched.
            routes=json.dumps([dict(dst='192.0.2.0/24')])
            with patch.dict('os.environ',{'HTTP_PROXY':'http://127.0.0.1:17891'}),patch('external_guest_proxy.subprocess.check_output',side_effect=['[]',routes]),patch('external_guest_proxy.socket.socket',return_value=sock),patch('external_guest_proxy.subprocess.run') as change,patch('external_guest_proxy.subprocess.Popen',side_effect=OSError('mock start failure')):
                proxy=GuestProxy(out,up)
                with self.assertRaises(OSError):proxy.start()
                proxy.stop()
            self.assertEqual([c.args[0] for c in change.call_args_list],[['ip','addr','add','198.51.100.17/32','dev','lo'],['ip','addr','del','198.51.100.17/32','dev','lo']])

    def test_controller_pass_and_real_failure_boundaries_cleanup_owned_resources(self):
        import run_external_workload as controller
        for sampling,upgraded,fault in ((s,u,f) for s,u in (('on',False),('off',False),('on',True)) for f in (None,'network','counter','tests','extensions','clock','bad-exit')):
            with self.subTest(sampling=sampling,fault=fault),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);cfg,_=fixture(root);args=types.SimpleNamespace(config=cfg,upstream=root,output=root/'out',config_home=root/'cfg',service_url='http://127.0.0.1:1',container='unused',sampling=sampling,endpoint_metrics=upgraded,guest_relay=upgraded)
                seen=[]
                class Monitor:
                    def __init__(self,*a,**kw):self.active={}
                    def start(self,sid,*a):self.active[sid]=True
                    def stop(self,sid,*a):self.active.pop(sid)
                class Metrics:
                    def __init__(self,*a,pss_interval_seconds=1):
                        self.pss_interval_seconds=pss_interval_seconds
                    def observe(self):return dict(status='OBSERVED')
                    def stop(self):return dict(status='PASS',pss_samples=1)
                def fake(argv,**kwargs):
                    if upgraded:self.assertEqual(kwargs['env']['HTTP_PROXY'],'')
                    argv=list(map(str,argv));seen.append((argv,kwargs));stdout='';status=0
                    if argv[0]=='git':stdout='unit-revision'
                    elif 'agentenv_cold_start.py' in argv[1]:stdout='00000000-0000-0000-0000-000000000001'
                    elif 'upload' in argv:
                        bundle=Path(argv[-2]);self.assertEqual(argv[-1],'/probe/input');self.assertEqual(len(list(bundle.glob('*-read.py'))),20);self.assertEqual(len(list(bundle.glob('*.payload'))),2);self.assertTrue((bundle/'guest-env.sh').is_file());self.assertIn('HTTP_PROXY=http://192.0.2.17:34567',(bundle/'guest-env.sh').read_text())
                    elif 'download' in argv:
                        target=args.output/'input';target.mkdir();logs=target/'logs';outputs(logs)
                        _,_,reference=load(cfg,root)
                        with (target/'tools.tsv').open('w') as f:
                            w=csv.DictWriter(f,fieldnames=['step','start_monotonic_ns','end_monotonic_ns','duration_ns','exit_code'],delimiter='\t');w.writeheader()
                            for i,r in enumerate(reference,1):w.writerow(dict(step=i,start_monotonic_ns='' if fault=='clock' else i*100,end_monotonic_ns='' if fault=='clock' else i*100+50,duration_ns=0 if fault=='clock' else 50,exit_code=9 if fault=='bad-exit' and i==1 else r['exit_code']))
                        if fault=='tests':(logs/'082.stdout').write_text('18 passed\n18 passed\n')
                    elif 'list' in argv:stdout='[]'
                    elif 'exec' in argv:
                        command=argv[-1]
                        if 'chmod 755' in command:stdout='100'
                        elif 'guest-network-ready' in command:
                            stdout='guest-network-ready';status=int(fault=='network')
                        elif 'compiled extensions import successfully' in command:
                            stdout='source_repo_commit: unit-commit\ncompiled extensions import successfully';status=int(fault=='extensions')
                    return types.SimpleNamespace(returncode=status,stdout=stdout)
                with patch.object(controller.subprocess,'run',side_effect=fake),patch.object(controller,'RuntimeMonitor',side_effect=Monitor if sampling=='on' else AssertionError('off must not create sampler')) as monitor,patch.object(controller,'ExternalMetrics',Metrics),patch.object(controller,'allow_guest_relay',return_value={'status':'ADDED'}),patch.object(controller,'capture_relay_counters',side_effect=lambda output,stage:dict(allowed_packets=0 if stage=='before-head' or fault=='counter' else 2)),patch.dict('os.environ',{'ASB_EXTERNAL_GUEST_RELAY_URL':'http://192.0.2.17:34567','ASB_EXTERNAL_GUEST_RELAY_STATE':str(root/'relay')}),contextlib.redirect_stdout(io.StringIO()):
                    result=controller.run(args)
                value=json.loads((args.output/'result.json').read_text())
                self.assertEqual(result,0 if fault is None else 1,value)
                if sampling=='off':
                    monitor.assert_not_called();self.assertEqual(value['memory'],'NOT_MEASURED');self.assertTrue(value['cleanup']['memory_sampling_disabled'])
                    self.assertFalse((args.output/'runtime-monitor.json').exists())
                self.assertTrue(value['cleanup']['sandbox_removed']);self.assertTrue(value['cleanup']['service_sandbox_list_empty'])
                self.assertFalse(any('git hash-object' in argv[-1] for argv,kw in seen))
                replay=[kw for argv,kw in seen if argv[-1]=='bash /probe/input/runner.sh']
                if fault in ('network','counter'):self.assertFalse(replay)
                else:self.assertEqual(replay[0]['timeout'],900)
                if fault is None:
                    self.assertEqual(value['functional_validation'],'PASS')
                    if upgraded:self.assertEqual(value['measurement_status'],'PASS');self.assertEqual(value['guest_network_preflight'],'PASS')
                    self.assertEqual(value['syntax_validation'],'NOT_REQUESTED_ORIGINAL_EXTENSION_CHECK')

    def test_outer_workflow_owns_relay_cleanup_on_success_failure_and_timeout(self):
        import run_host_external_workload as host
        for sampling,fault in ((s,f) for s in ('on','off') for f in (None,'native-failure','native-timeout','proxy-cleanup')):
            with self.subTest(sampling=sampling,fault=fault),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);cfg,_=fixture(root);proxies=[]
                storage=root/'.artifacts/storage-isolated-service/unit';raw=storage/'external-workload';raw.mkdir(parents=True)
                (raw/'result.json').write_text(json.dumps(dict(status='PASS',task_id=CYTHON,actions=83,functional_validation='PASS',sampling=sampling,memory='NOT_MEASURED' if sampling=='off' else None,cleanup=dict(memory_sampling_disabled=sampling=='off'))))
                (storage/'result.json').write_text(json.dumps(dict(status='PASS',cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged')},checks={k:'PASS' for k in ('actual_writable_files_inside_volume','readonly_root_rejects_write','fixed_backing_allocation')},host_file_residency=dict(status='NOT_REQUESTED'))))
                class Collector:
                    def __init__(self,*a,**kw):pass
                    def stop(self):return dict(status='PASS')
                class Proxy:
                    def __init__(self,*a):self.stopped=False;proxies.append(self)
                    def start(self):return 'http://192.0.2.17:34567'
                    def check(self):pass
                    def stop(self):
                        self.stopped=True
                        if fault=='proxy-cleanup':raise RuntimeError('mock cleanup failed')
                def native(argv,**kw):
                    self.assertEqual(kw['timeout'],2100)
                    self.assertEqual('--light-monitor' in argv,sampling=='on');self.assertEqual('--external-workload-sampling' in argv,sampling=='off')
                    self.assertEqual(kw['env']['ASB_EXTERNAL_GUEST_RELAY_URL'],'http://192.0.2.17:34567')
                    if fault=='native-timeout':raise subprocess.TimeoutExpired(argv,2100)
                    kw['stdout'].write('status=PASS result_dir='+str(storage)+'\n');kw['stdout'].flush()
                    return types.SimpleNamespace(returncode=int(fault=='native-failure'))
                with patch.dict('os.environ',{'ASB_HOST_FILE_SAMPLING':sampling}),patch.object(host,'ROOT',root),patch.object(host,'available',return_value=80*2**30),patch.object(host.shutil,'copy2'),patch.object(host.subprocess,'run',side_effect=native),patch.object(host,'GuestProxy',Proxy),patch('host_file_monitor.measurement_group',return_value=(Path('/unit'),{})),patch('host_file_monitor.HostFileMonitor',side_effect=Collector if sampling=='on' else AssertionError('off must not create host sampler')) as monitor,patch('report_host_file.report',side_effect=(lambda *a,**k:{}) if sampling=='on' else AssertionError('off must not report memory')),patch.object(host,'guest_plot',side_effect=(lambda *a,**k:{}) if sampling=='on' else AssertionError('off must not plot memory')),contextlib.redirect_stdout(io.StringIO()):
                    result=host.grouped(cfg,root,sampling)
                if sampling=='off':monitor.assert_not_called()
                self.assertEqual(result,0 if fault is None else 1);self.assertTrue(proxies[0].stopped)
                record=json.loads(next((root/'.artifacts/host-external-workload').glob('*/result.json')).read_text())
                if fault=='proxy-cleanup':self.assertIn('guest_proxy_cleanup_error',record)
                else:self.assertTrue(record['owned_guest_proxy_stopped'])


if __name__=='__main__':unittest.main()
