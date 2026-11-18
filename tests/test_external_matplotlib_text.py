"""New fixed replays: synthetic commands and mocked lifecycle, no service or image."""
import ast
import contextlib
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from external_workload import MATPLOTLIB,TEXT_EDIT,NEW_TASKS,TEXT_READS,TEXT_WRITES,load,write_inputs,guest_runner,budgets,validate_timings,validate_new_outputs


def fixture(root,task):
    count=95 if task==MATPLOTLIB else 50
    nonzero={29:1,42:1,47:1,48:1,63:1,74:4,78:1} if task==MATPLOTLIB else {19:1,37:1,43:127}
    run=root/'run';run.mkdir();files={};steps=[]
    for i in range(1,count+1):
        tool='read' if task==TEXT_EDIT and i in TEXT_READS else 'write' if task==TEXT_EDIT and i in TEXT_WRITES else 'bash'
        name=f'{i:03}-read.py' if tool=='read' else f'{i:03}.payload'
        script='exec bash -lc '+shlex.quote('echo fixture' if tool=='bash' else 'cat /probe/input/'+name)+'\n'
        files[f'{i:03}.sh']=script
        if tool!='bash':files[name]='fixture-aux\n'
        steps.append(dict(step=i,tool=tool,script_sha256=hashlib.sha256(script.encode()).hexdigest()))
    (run/'replay-inputs.json').write_text(json.dumps(dict(files=files,steps=steps)))
    with (run/'steps.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=['step','exit_code','timed_out']);w.writeheader()
        for i in range(1,count+1):w.writerow(dict(step=i,exit_code=nonzero.get(i,0),timed_out='False'))
    (run/'final-source.patch').write_text('fixture complete staged + unstaged patch\n')
    cfg=root/'config.json';cfg.write_text(json.dumps(dict(version=1,task_id=task,actions=count,memory_mib=8192,
        run='run',repository='fixture',revision='unit-revision',image='fixed-image',source_blob='unit-blob',source_commit='unit-commit',validation_scope='original fixture')))
    clock=root/'experiments/container-screening/harness/clock_ns.c';clock.parent.mkdir(parents=True);clock.write_text('/* never compiled */\n')
    return cfg,run


def outputs(logs,task):
    logs.mkdir(exist_ok=True)
    if task==MATPLOTLIB:
        for i in (88,93):(logs/f'{i:03}.stdout').write_text('60 passed, 2 warnings in 1s\n')
        (logs/'095.stdout').write_text('COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n')
    else:
        (logs/'047.stdout').write_text('=== FULL FILE MATCHES EXPECTED ===\n')
        (logs/'050.stdout').write_text('=== SOLVE.SH OUTPUT MATCHES EXPECTED ===\n=== -S SCRIPT VARIANT ALSO MATCHES ===\n')


class NewExternalTests(unittest.TestCase):
    def test_fixed_inputs_and_auxiliary_files(self):
        for task,count in ((MATPLOTLIB,95),(TEXT_EDIT,50)):
            with self.subTest(task=task),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);cfg,run=fixture(root,task);value,replay,expected=load(cfg,root)
                target=root/'bundle';target.mkdir();write_inputs(target,replay,task)
                self.assertEqual(len(expected),count);self.assertEqual(len(list(target.iterdir())),count+(8 if task==TEXT_EDIT else 0))
                if task==TEXT_EDIT:
                    for name in ('001-read.py','005.payload'):
                        broken=json.loads(json.dumps(replay));broken['files'].pop(name)
                        (run/'replay-inputs.json').write_text(json.dumps(broken))
                        with self.assertRaises(ValueError):load(cfg,root)
                self.assertEqual(budgets(task)['replay'],900)
                if task==MATPLOTLIB:
                    (run/'final-source.patch').unlink()
                    with self.assertRaises(ValueError):load(cfg,root)

    def test_independent_exec_shells_do_not_terminate_runner_or_leak_state(self):
        for task in NEW_TASKS:
            with self.subTest(task=task),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);app=root/'app';app.mkdir();bundle=root/'input';bundle.mkdir()
                (bundle/'001.sh').write_text("exec bash -lc 'export LEAK=bad; cd /; exit 1'\n")
                (bundle/'002.sh').write_text('test -z "$LEAK" && test "$PWD" = '+shlex.quote(str(app))+' && test "$OMP_NUM_THREADS" = 2 && test -z "$HTTP_PROXY$all_proxy" || exit 99\numask\n')
                clock=bundle/'clock-ns';clock.write_text('#!/bin/sh\ndate +%s%N\n');clock.chmod(0o755)
                script=guest_runner(2,task_id=task).replace('/probe/input',str(bundle)).replace('cd /testbed','cd '+shlex.quote(str(app))).replace('cd /app','cd '+shlex.quote(str(app)))
                path=bundle/'runner.sh';path.write_text(script)
                env=dict(os.environ,HTTP_PROXY='http://unusable.invalid:17891',all_proxy='http://unusable.invalid:17891')
                p=subprocess.run(['bash',path],env=env,text=True,capture_output=True,timeout=10)
                self.assertEqual(p.returncode,0,p.stderr)
                with (bundle/'tools.tsv').open() as f:rows=list(csv.DictReader(f,delimiter='\t'))
                validate_timings(rows);self.assertEqual([int(r['exit_code']) for r in rows],[1,0])
                self.assertEqual((bundle/'logs/002.stdout').read_text(),'0077\n')

    def test_original_output_checks_reject_missing_markers_and_failed_tests(self):
        for task in NEW_TASKS:
            with self.subTest(task=task),tempfile.TemporaryDirectory() as tmp:
                logs=Path(tmp);outputs(logs,task);validate_new_outputs(logs,task)
                name='093.stdout' if task==MATPLOTLIB else '050.stdout'
                original=(logs/name).read_text();(logs/name).write_text('60 passed, 1 failed\n' if task==MATPLOTLIB else original.splitlines()[0])
                with self.assertRaises(ValueError):validate_new_outputs(logs,task)

    def test_controller_success_and_failures_preserve_cleanup(self):
        import run_external_workload as controller
        for task in NEW_TASKS:
            for fault in (None,'clock','exit','outputs','endpoint','pss-stop','functional','patch'):
                if task==TEXT_EDIT and fault=='patch':continue
                with self.subTest(task=task,fault=fault),tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp);cfg,run=fixture(root,task);seen=[];collectors=[]
                    args=SimpleNamespace(config=cfg,upstream=root,output=root/'out',config_home=root/'cfg',service_url='http://127.0.0.1:1',container='unused')
                    class Monitor:
                        def __init__(self,*a,**k):self.active={}
                        def start(self,sid,*a):self.active[sid]=True
                        def stop(self,sid,*a):self.active.pop(sid)
                    class Metrics:
                        def __init__(self,*a,pss_interval_seconds=1):
                            self.stopped=False;self.pss_interval_seconds=pss_interval_seconds;collectors.append(self)
                        def observe(self):
                            if fault=='endpoint':raise PermissionError('fixture smaps denial')
                            return dict(status='OBSERVED')
                        def stop(self):
                            self.stopped=True
                            if fault=='pss-stop':raise RuntimeError('fixture VMM sampler failed')
                            return dict(status='PASS',pss_samples=1,pss_interval_seconds=self.pss_interval_seconds)
                    def fake(argv,**kw):
                        argv=list(map(str,argv));seen.append((argv,kw));stdout='';code=0
                        if argv[0]=='git':stdout='unit-revision'
                        elif 'agentenv_cold_start.py' in argv[1]:
                            self.assertIn('--clean-guest-proxy',argv);stdout='unit-sid'
                        elif 'download' in argv:
                            target=args.output/'input';target.mkdir();outputs(target/'logs',task)
                            _,_,expected=load(cfg,root)
                            with (target/'tools.tsv').open('w') as f:
                                w=csv.DictWriter(f,fieldnames=['step','start_monotonic_ns','end_monotonic_ns','duration_ns','exit_code'],delimiter='\t');w.writeheader()
                                for i,r in enumerate(expected,1):w.writerow(dict(step=i,start_monotonic_ns=i*100,end_monotonic_ns=i*100+50,duration_ns=0 if fault=='clock' else 50,exit_code=99 if fault=='exit' and i==1 else r['exit_code']))
                            if fault=='outputs':(target/'logs'/('093.stdout' if task==MATPLOTLIB else '050.stdout')).write_text('missing result\n')
                        elif 'list' in argv:stdout='[]'
                        elif 'exec' in argv:
                            cmd=argv[-1]
                            if 'chmod 755' in cmd:stdout='100'
                            elif 'git hash-object' in cmd:stdout='/opt/miniconda3/envs/testbed/bin/python\nunit-commit\nunit-blob'
                            elif 'git diff HEAD --binary' in cmd:stdout='bad patch' if fault=='patch' else (run/'final-source.patch').read_text()
                            elif 'import matplotlib, numpy' in cmd or 'cmp -s /app/output.csv' in cmd:code=int(fault=='functional')
                        return SimpleNamespace(stdout=stdout,returncode=code)
                    with patch.object(controller.subprocess,'run',side_effect=fake),patch.object(controller,'RuntimeMonitor',Monitor),patch.object(controller,'ExternalMetrics',Metrics),patch.dict(os.environ,HTTP_PROXY='http://unusable.invalid:17891'),contextlib.redirect_stdout(io.StringIO()):
                        status=controller.run(args)
                    result=json.loads((args.output/'result.json').read_text())
                    self.assertEqual(status,0 if fault is None else 1,result)
                    self.assertTrue(result['cleanup']['sandbox_removed']);self.assertTrue(result['cleanup']['service_sandbox_list_empty'])
                    self.assertTrue(collectors[0].stopped)
                    self.assertTrue(all(kw['env']['HTTP_PROXY']=='' for _,kw in seen))
                    if fault is None:
                        self.assertEqual(result['actions'],95 if task==MATPLOTLIB else 50)
                        self.assertEqual(result['functional_validation'],'PASS')
                        self.assertEqual(result['measurement_status'],'PASS')
                        event=next(e for e in json.loads((args.output/'events.json').read_text()) if e['stage']=='download-tools')
                        self.assertEqual(result['schedule_end_monotonic_ns'],event['command_end_monotonic_ns'])
                        self.assertGreater(result['schedule_elapsed_ns'],result['actions_download_elapsed_ns'])
                        self.assertFalse(any('py_compile django' in a[-1] for a,_ in seen))
                        if task==MATPLOTLIB:self.assertEqual(result['patch_validation'],'PASS')

    def test_wrapper_completes_report_and_rejects_incomplete_metrics(self):
        import run_host_external_workload as host
        for task_id in NEW_TASKS:
            for fault in (None,'missing-pss','native-timeout'):
                with self.subTest(task=task_id,fault=fault),tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp);cfg,_=fixture(root,task_id)
                    storage=root/'.artifacts/storage-isolated-service/unit';raw=storage/'external-workload';raw.mkdir(parents=True)
                    pss=dict(processes=[{}],total_bytes=4096,per_sandbox_bytes=4096,start_monotonic_ns=300,end_monotonic_ns=310,cpu_ns=1)
                    p1=dict(files=[dict(device=1,inode=2,stable=True,resident_bytes=4096)],unique_files=1,resident_bytes=4096,
                        start_monotonic_ns=310,end_monotonic_ns=320,cpu_ns=1,counters_before=dict(file=1024),counters_after=dict(file=1024),net_shortfall_bytes=3072)
                    task=dict(status='PASS',task_id=task_id,actions=95 if task_id==MATPLOTLIB else 50,functional_validation='PASS',
                        endpoint_metrics=dict(status='OBSERVED',guest_count=1,pss=pss,p1=p1),host_vmm_collector=dict(status='PASS',pss_samples=0 if fault=='missing-pss' else 1,pss_interval_seconds=1,interval_seconds=.1),
                        schedule_start_monotonic_ns=100,schedule_end_monotonic_ns=300,schedule_elapsed_ns=200,actions_download_elapsed_ns=100,
                        time_scope='fixture window',tool_duration_sum_ns=50,controller_elapsed_ns=400)
                    (raw/'result.json').write_text(json.dumps(task))
                    outer=dict(status='PASS',cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged')},checks={k:'PASS' for k in ('actual_writable_files_inside_volume','readonly_root_rejects_write','fixed_backing_allocation')},host_file_residency=dict(status='NOT_REQUESTED'))
                    (storage/'result.json').write_text(json.dumps(outer))
                    class Collector:
                        def __init__(self,path,*a,**kw):
                            self.path=path;path.mkdir()
                            (path/'memory.tsv').write_text('monotonic_ns\tcurrent\n100\t1000\n200\t2000\n300\t1000\n')
                        def stop(self):return dict(status='PASS')
                    def native(argv,**kw):
                        self.assertEqual(argv[:2],[sys.executable,'-B']);self.assertTrue(argv[2].endswith('validate_isolated_agentenv.py'))
                        self.assertEqual(kw['timeout'],2100);self.assertEqual(kw['env']['HTTP_PROXY'],'')
                        if fault=='native-timeout':raise subprocess.TimeoutExpired(argv,2100)
                        kw['stdout'].write('status=PASS result_dir='+str(storage)+'\n');kw['stdout'].flush()
                        return SimpleNamespace(returncode=0)
                    with patch.dict(os.environ,ASB_HOST_FILE_SAMPLING='on',HTTP_PROXY='http://unusable.invalid:17891'),patch.object(host,'ROOT',root),patch.object(host,'available',return_value=80*2**30),patch.object(host.shutil,'copy2'),patch.object(host.subprocess,'run',side_effect=native),patch('host_file_monitor.measurement_group',return_value=(root/'cgroup',{})),patch('host_file_monitor.HostFileMonitor',Collector),patch('report_host_file.report',return_value={}),patch.object(host,'guest_plot',return_value={}),contextlib.redirect_stdout(io.StringIO()):
                        code=host.grouped(cfg,root)
                    result_path=next((root/'.artifacts/host-external-workload').glob('*/result.json'));result=json.loads(result_path.read_text())
                    self.assertEqual(code,0 if fault is None else 1,result)
                    self.assertEqual(result['collector']['status'],'PASS')
                    if fault is None:
                        self.assertEqual(result['task_window_current']['observed_peak_bytes'],2000)
                        self.assertIn('实际组外容量未知',(result_path.parent/'report/REPORT.md').read_text())
                        self.assertTrue(Path(result['published_report']).is_relative_to(root/'results'))
                        self.assertEqual(json.loads((Path(result['published_report'])/'measurements.json').read_text())['actions'],task['actions'])

    def test_new_proxy_values_are_empty_at_native_and_systemd_boundaries(self):
        root=Path(__file__).resolve().parents[1]
        tree=ast.parse((root/'scripts/validate_isolated_agentenv.py').read_text())
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='external_config' and 'NO_PROXY=127.0.0.1' in ast.unparse(n))
        code=compile(ast.Module(body=[branch],type_ignores=[]),'<actual docker proxy flags>','exec')
        for task in NEW_TASKS:
            ns=dict(external_config=True,external_value=dict(task_id=task),argv=[],os=os)
            exec(code,ns)
            for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):self.assertIn(key+'=',ns['argv'])
        import run_host_external_workload as host
        with tempfile.TemporaryDirectory() as tmp:
            cfg,_=fixture(Path(tmp),TEXT_EDIT);seen=[]
            with patch.object(sys,'argv',['entry',str(cfg),tmp]),patch.object(host.os,'geteuid',return_value=0),patch.object(host.subprocess,'run',side_effect=lambda argv,**kw:seen.append(argv) or SimpleNamespace(returncode=0)):
                self.assertEqual(host.main(),0)
            command=seen[0]
            for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):self.assertIn('--setenv='+key+'=',command)

    def test_callers_require_new_metrics_and_snapshot_dependencies(self):
        root=Path(__file__).resolve().parents[1]
        for name in ('run_host_external_workload.py','validate_isolated_agentenv.py'):
            tree=ast.parse((root/'scripts'/name).read_text())
            self.assertTrue(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='validate_result' for n in ast.walk(tree)))
            self.assertIn('SNAPSHOT_FILES',(root/'scripts'/name).read_text())


if __name__=='__main__':unittest.main()
