import csv
import ast
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import shlex
import subprocess
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from external_workload import C4,C4_WRITES,load,guest_runner,validate_timings,write_inputs,validate_c4_outputs


class ExternalWorkloadTests(unittest.TestCase):
    def test_guest_plot_uses_selected_task_title(self):
        from run_host_external_workload import guest_plot
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'raw';raw.mkdir();samples=raw/'samples';samples.mkdir();output=root/'plot';output.mkdir()
            (raw/'runtime-monitor.json').write_text(json.dumps(dict(profile='light',sessions=[dict(collector_reaped=True,raw_directory='samples',receipt=dict(samples=2,interval_seconds=.1))])))
            (samples/'guest-memory.tsv').write_text('monotonic_ns\tMemTotal_kib\tMemAvailable_kib\tCached_kib\n100\t8192\t4096\t512\n200\t8192\t3072\t1024\n')
            (raw/'tools.tsv').write_text('step\tduration_ns\n1\t100\n')
            result=guest_plot(raw,output,title='C4 resharding')
            self.assertEqual(result['samples'],2)
            svg=(output/'guest.svg').read_text()
            self.assertIn('C4 resharding: tool latency',svg);self.assertNotIn('Django',svg)

    def test_c4_inputs_include_nine_payloads_and_reject_missing_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg,run=self.fixture(root,C4)
            value,replay,expected=load(cfg,root)
            self.assertEqual(len(expected),36)
            self.assertEqual({i:int(r['exit_code']) for i,r in enumerate(expected,1) if int(r['exit_code'])},{3:2,11:1,12:1,16:1,18:1})
            target=root/'bundle';target.mkdir();write_inputs(target,replay,C4)
            self.assertEqual(len(list(target.iterdir())),45)
            self.assertEqual(len(list(target.glob('*.payload'))),9)
            for name,text in replay['files'].items():self.assertEqual((target/name).read_text(),text)
            replay['files'].pop('006.payload');(run/'replay-inputs.json').write_text(json.dumps(replay))
            with self.assertRaises(ValueError):load(cfg,root)

    def test_c4_shell_resets_cwd_and_variables_and_preserves_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=root/'app';app.mkdir();(app/'nested').mkdir();bundle=root/'input';bundle.mkdir()
            (bundle/'001.payload').write_text('owned-payload')
            (bundle/'001.sh').write_text('test "$PWD" = '+shlex.quote(str(app))+' || exit 99\nsaved=must-not-leak; cd nested\n')
            (bundle/'002.sh').write_text('test "$PWD" = '+shlex.quote(str(app))+' && test -z "$saved" || exit 99\ncat '+shlex.quote(str(bundle/'001.payload'))+'; touch made; umask; false\n')
            clock=bundle/'clock-ns';clock.write_text('#!/bin/sh\ndate +%s%N\n');clock.chmod(0o755)
            script=guest_runner(2,task_id=C4).replace('cd /app','cd '+shlex.quote(str(app))).replace('ASB_EXTERNAL_ROOT=/probe/input','ASB_EXTERNAL_ROOT='+shlex.quote(str(bundle)))
            path=bundle/'runner.sh';path.write_text(script)
            p=subprocess.run(['bash',path],capture_output=True,text=True,timeout=10)
            self.assertEqual(p.returncode,0,p.stderr)
            with (bundle/'tools.tsv').open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
            validate_timings(rows);self.assertEqual([int(r['exit_code']) for r in rows],[0,1])
            self.assertEqual((bundle/'logs/002.stdout').read_text(),'owned-payload0077\n')
            self.assertEqual((app/'made').stat().st_mode&0o777,0o600)
            clock.chmod(0o644)
            p=subprocess.run(['bash',path],capture_output=True,text=True,timeout=10)
            self.assertEqual(p.returncode,94)

    def test_c4_roundtrip_evidence_is_required_without_surviving_output_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs=Path(tmp)
            (logs/'033.stdout').write_text('CONSTRAINT VIOLATIONS: NONE\nFINAL VERDICT: BYTE-EXACT ROUNDTRIP ON c4_sample\n')
            (logs/'034.stdout').write_text('9898 files, structure + content\n')
            validate_c4_outputs(logs)
            (logs/'033.stdout').write_text('CONSTRAINT VIOLATIONS: [(bad)]\n')
            with self.assertRaises(ValueError):validate_c4_outputs(logs)
            (logs/'033.stdout').unlink()
            with self.assertRaises(FileNotFoundError):validate_c4_outputs(logs)

    def test_native_acceptance_checks_configured_task_actions_and_c4_evidence(self):
        root=Path(__file__).resolve().parents[1]
        for name,cfg_name,result_name in [('validate_isolated_agentenv.py','external_value','external_result'),('run_host_external_workload.py','cfg','task')]:
            tree=ast.parse((root/'scripts'/name).read_text())
            guard=next(n for n in ast.walk(tree) if isinstance(n,ast.Assert) and
                result_name+"['actions']" in ast.unparse(n) and cfg_name+"['actions']" in ast.unparse(n))
            extra=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and
                cfg_name+"['task_id']" in ast.unparse(n.test) and any(isinstance(a,ast.Subscript) and ast.unparse(a)==result_name+"['roundtrip_validation']" for a in ast.walk(n)))
            code=compile(ast.Module(body=[guard,extra],type_ignores=[]),'<actual result guards>','exec')
            for task_id,count in [('django__django-16819',28),(C4,36)]:
                result=dict(status='PASS',task_id=task_id,actions=count,strict_exit_match=True)
                if task_id==C4:result['roundtrip_validation']='PASS'
                cfg=dict(task_id=task_id,actions=count);ns={cfg_name:cfg,result_name:result,'C4':C4,'outer':{'status':'PASS'},'args':SimpleNamespace(external_guest_count=1),'guest_count':1}
                exec(code,ns)
                result['actions']=27
                with self.assertRaises(AssertionError):exec(code,ns)
                result['actions']=count;result['task_id']='wrong-task'
                with self.assertRaises(AssertionError):exec(code,ns)
                if task_id==C4:
                    result.update(task_id=task_id,roundtrip_validation='FAIL')
                    with self.assertRaises(AssertionError):exec(code,ns)

    def test_controller_routes_both_tasks_and_fails_missing_evidence_bad_exits_or_clock(self):
        import run_external_workload as controller
        class Monitor:
            def __init__(self,*args,**kwargs):self.active={}
            def start(self,sid,*args):self.active[sid]=True
            def stop(self,sid,*args):self.active.pop(sid)
        for task_id,fault in [('django__django-16819',None),(C4,None),(C4,'missing-evidence'),(C4,'bad-exit'),(C4,'clock'),(C4,'final-log-failure')]:
            with self.subTest(task_id=task_id,fault=fault),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);config,_=self.fixture(root,task_id);cfg=json.loads(config.read_text())
                cfg.update(repository='local-fixed-fixture',revision='unit-revision',image='unit-image',source_blob='unit-blob',validation_scope='fixture validation')
                config.write_text(json.dumps(cfg));args=SimpleNamespace(config=config,upstream=root,output=root/'out',config_home=root/'cfg',service_url='http://127.0.0.1:1',container='unit-unused')
                clock_source=root/'experiments/container-screening/harness/clock_ns.c';clock_source.parent.mkdir(parents=True);clock_source.write_text('/* local mocked compiler input */\n')
                seen=[];remote='/probe/input' if task_id==C4 else '/workspace/asb-external-tools'
                def fake_run(argv,**kwargs):
                    argv=list(map(str,argv));seen.append(argv);stdout=''
                    if argv[0]=='git':stdout=cfg['revision']
                    elif 'agentenv_cold_start.py' in argv[1]:stdout='00000000-0000-0000-0000-000000000001'
                    elif 'upload' in argv:
                        files=Path(argv[-2]);self.assertEqual(argv[-1],remote)
                        self.assertEqual(len(list(files.glob('*.payload'))),9 if task_id==C4 else 0)
                    elif 'download' in argv:
                        self.assertEqual(argv[-2],remote)
                        target=args.output/Path(remote).name;target.mkdir();logs=target/'logs';logs.mkdir()
                        _,_,expected=load(config,root)
                        with (target/'tools.tsv').open('w') as f:
                            w=csv.DictWriter(f,fieldnames=['step','start_monotonic_ns','end_monotonic_ns','duration_ns','exit_code'],delimiter='\t');w.writeheader()
                            for i,r in enumerate(expected,1):w.writerow(dict(step=i,start_monotonic_ns='' if fault=='clock' else i*100,end_monotonic_ns='' if fault=='clock' else i*100+50,duration_ns=0 if fault=='clock' else 50,exit_code=9 if fault=='bad-exit' and i==1 else r['exit_code']))
                        (logs/'033.stdout').write_text('' if fault=='missing-evidence' else 'CONSTRAINT VIOLATIONS: NONE\nFINAL VERDICT: BYTE-EXACT ROUNDTRIP ON c4_sample\n')
                        (logs/'034.stdout').write_text('9898 files, structure + content\n')
                    elif 'list' in argv:stdout='[]'
                    elif 'exec' in argv:
                        command=argv[-1]
                        if 'chmod 755' in command:stdout='100'
                        elif 'git hash-object' in command:stdout='/envs/testbed/bin/python\nunit-blob'
                    return SimpleNamespace(returncode=0,stdout=stdout)
                write_events=controller._write_events
                def event_write(out,events):
                    if fault=='final-log-failure' and events[-1]['stage']=='remaining-guests' and 'events_log_duration_ns' in events[-1]:
                        raise OSError('simulated final events flush failure')
                    write_events(out,events)
                with patch.object(controller.subprocess,'run',side_effect=fake_run),patch.object(controller,'RuntimeMonitor',Monitor),patch.object(controller,'_write_events',side_effect=event_write):
                    status=controller.run(args)
                value=json.loads((args.output/'result.json').read_text())
                self.assertEqual(status,0 if fault is None else 1,value);self.assertEqual(value['status'],'PASS' if fault is None else 'FAIL')
                self.assertTrue(value['cleanup']['sandbox_removed']);self.assertTrue(value['cleanup']['service_sandbox_list_empty'])
                self.assertEqual(value['call_timing']['version'],2)
                saved_events=json.loads((args.output/'events.json').read_text())
                if fault=='final-log-failure':
                    self.assertIn('simulated final events flush failure',value['events_final_flush_error'])
                    self.assertEqual(value['roundtrip_validation'],'PASS')
                    self.assertNotIn('events_log_duration_ns',saved_events[-1])
                else:
                    self.assertTrue(all('events_log_duration_ns' in event for event in saved_events))
                if fault is None:
                    self.assertEqual(value['call_timing']['replay'],next(event for event in saved_events if event['stage']=='replay'))
                self.assertTrue(any('chmod 755 '+remote+'/clock-ns' in a[-1] for a in seen))
                if task_id==C4:
                    self.assertFalse(any('git hash-object' in a[-1] for a in seen))
                    if fault is None:self.assertEqual(value['roundtrip_validation'],'PASS')
                elif fault is None:self.assertEqual(value['official_tests'],'NOT_RUN_ORIGIN_SYNTAX_ONLY')

    def test_non_executable_clock_stops_before_task_and_valid_clock_preserves_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'nested').mkdir()
            (root/'001.sh').write_text('saved=ok; cd nested\n')
            (root/'002.sh').write_text('test "$saved" = ok && test "$PWD" = '+shlex.quote(str(root/'nested'))+' || exit 99\nfalse\n')
            clock=root/'clock-ns';clock.write_text('#!/bin/sh\ndate +%s%N\n');clock.chmod(0o644)
            script=guest_runner(2).replace('cd /testbed || exit 93','cd '+shlex.quote(str(root))+' || exit 93').replace('ASB_EXTERNAL_ROOT=/workspace/asb-external-tools','ASB_EXTERNAL_ROOT='+shlex.quote(str(root)))
            (root/'runner.sh').write_text(script)
            failed=subprocess.run(['bash',root/'runner.sh'],capture_output=True,text=True,timeout=10)
            self.assertEqual(failed.returncode,94);self.assertIn('Permission denied',failed.stderr)
            self.assertEqual(len((root/'tools.tsv').read_text().splitlines()),1)
            clock.chmod(0o755)
            passed=subprocess.run(['bash',root/'runner.sh'],capture_output=True,text=True,timeout=10)
            self.assertEqual(passed.returncode,0,passed.stderr)
            with (root/'tools.tsv').open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
            validate_timings(rows);self.assertEqual([int(r['exit_code']) for r in rows],[0,1])

    def test_missing_or_inconsistent_tool_timing_is_not_zero(self):
        row=dict(step='1',start_monotonic_ns='',end_monotonic_ns='',duration_ns='0')
        with self.assertRaises(ValueError):validate_timings([row])
        row.update(start_monotonic_ns='100',end_monotonic_ns='120',duration_ns='0')
        with self.assertRaises(ValueError):validate_timings([row])

    def test_new_private_proxy_forwarding_uses_names_not_values(self):
        root=Path(__file__).resolve().parents[1]
        tree=ast.parse((root/'scripts/validate_isolated_agentenv.py').read_text())
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id=='external_config' and
            any(isinstance(c,ast.Constant) and c.value=='HTTP_PROXY' for c in ast.walk(n)))
        for enabled in (False,True):
            ns=dict(args=SimpleNamespace(external_sampling_control=False),task_endpoint_metrics=False,cython_schedule=None,external_config=enabled,external_value=dict(task_id='django__django-16819'),argv=[],os=SimpleNamespace(environ={'HTTP_PROXY':'private-value-not-for-argv'}))
            exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual proxy branch>','exec'),ns)
            if enabled:
                self.assertIn('HTTP_PROXY',ns['argv']);self.assertNotIn('private-value-not-for-argv',str(ns['argv']))
                self.assertTrue(any(v.startswith('NO_PROXY=127.0.0.1') for v in ns['argv']))
            else:self.assertEqual(ns['argv'],[])
        tree=ast.parse((root/'scripts/run_host_external_workload.py').read_text())
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.For) and
            any(isinstance(c,ast.Constant) and c.value=='HTTP_PROXY' for c in ast.walk(n)))
        ns=dict(command=[],os=SimpleNamespace(environ={'HTTP_PROXY':'private-value-not-for-argv'}))
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual systemd forwarding>','exec'),ns)
        self.assertEqual(ns['command'],['--setenv=HTTP_PROXY'])

    def fixture(self,root,task_id='django__django-16819'):
        run=root/'run';run.mkdir();files={};steps=[]
        count=36 if task_id==C4 else 28
        exits={3:2,11:1,12:1,16:1,18:1} if task_id==C4 else {17:1}
        for i in range(1,count+1):
            text='false\n' if i==17 else 'printf ok\n'
            tool='write' if task_id==C4 and i in C4_WRITES else 'bash'
            if tool=='write':
                text=f'cat /probe/input/{i:03}.payload > /app/output-{i}\n'
                files[f'{i:03}.payload']='published payload\n'
            files[f'{i:03}.sh']=text
            steps.append(dict(step=i,tool=tool,command=text,script_sha256=hashlib.sha256(text.encode()).hexdigest()))
        (run/'replay-inputs.json').write_text(json.dumps(dict(steps=steps,files=files)))
        with (run/'steps.csv').open('w') as f:
            w=csv.DictWriter(f,fieldnames=['step','exit_code','timed_out']);w.writeheader()
            w.writerows(dict(step=i,exit_code=exits.get(i,0),timed_out=False) for i in range(1,count+1))
        cfg=root/'cfg.json';cfg.write_text(json.dumps(dict(version=1,task_id=task_id,memory_mib=8192,actions=count,run='run')))
        return cfg,run

    def test_reference_nonzero_and_normalized_script_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg,_=self.fixture(root)
            value,replay,reference=load(cfg,root)
            self.assertEqual(len(replay['steps']),28);self.assertEqual(reference[16]['exit_code'],'1')
            script=guest_runner(28)
            self.assertIn('source "$ASB_EXTERNAL_ROOT/$ASB_EXTERNAL_FILE.sh"',script)
            self.assertIn('set +e',script);self.assertNotIn('freeze',script);self.assertNotIn('mincore',script)

    def test_tampered_or_missing_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg,run=self.fixture(root);path=run/'replay-inputs.json';x=json.loads(path.read_text());x['files']['001.sh']='changed'
            path.write_text(json.dumps(x))
            with self.assertRaises(AssertionError):load(cfg,root)
            x['steps'].pop();path.write_text(json.dumps(x))
            with self.assertRaises(ValueError):load(cfg,root)

    def test_unapproved_task_and_escaping_path_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg,_=self.fixture(root);x=json.loads(cfg.read_text());x['task_id']='other';cfg.write_text(json.dumps(x))
            with self.assertRaises(ValueError):load(cfg,root)
            x['task_id']='django__django-16819';x['run']='../outside';cfg.write_text(json.dumps(x))
            with self.assertRaises(ValueError):load(cfg,root)

    def test_eight_gib_creation_requires_explicit_new_opt_in(self):
        from agentenv_cold_start import create
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'aenv').mkdir()
            (root/'aenv/credentials').write_text('url="http://127.0.0.1:12345"\napi_key="unit-test"\n')
            with self.assertRaises(ValueError):create(root,'http://127.0.0.1:12345','image',memory_mib=8192)
            class Response:
                status=201
                def __enter__(self):return self
                def __exit__(self,*a):pass
                def read(self,n):return b'{"sandboxID":"00000000-0000-0000-0000-000000000001"}'
            def opened(request,**kw):
                self.assertEqual(json.loads(request.data)['memoryMB'],8192);return Response()
            with patch('agentenv_cold_start.urllib.request.build_opener',return_value=SimpleNamespace(open=opened)):
                self.assertEqual(create(root,'http://127.0.0.1:12345','image',memory_mib=8192,allow_8gib=True),'00000000-0000-0000-0000-000000000001')


if __name__=='__main__':unittest.main()
