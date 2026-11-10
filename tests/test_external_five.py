import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import external_five as five
from agentenv_cold_start import create
from external_workload import budgets


class FiveTests(unittest.TestCase):
    def test_runner_preserves_persistent_shell_nonzero_and_isolated_shell(self):
        for task, expected in [('apache__logging-log4j2', 'kept'), ('hf-model-inference', 'unset')]:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); (root/'logs').mkdir()
                (root/'001.sh').write_text('ASB_TEST_VALUE=kept; false\n')
                (root/'002.sh').write_text('echo "${ASB_TEST_VALUE:-unset}"\n')
                (root/'clock-ns').write_text('#!/usr/bin/env python3\nimport time\nprint(time.monotonic_ns())\n')
                (root/'clock-ns').chmod(0o755)
                with patch.dict(five.COUNTS, {task:2}), patch.dict(five.CWD, {task:tmp}):
                    script=five.runner(task).replace('/probe/input',tmp).replace('/probe/work',tmp+'/work')
                subprocess.run(['bash','-c',script],check=True)
                self.assertEqual((root/'logs/002.stdout').read_text().strip(),expected)
                rows=(root/'tools.tsv').read_text().splitlines()
                self.assertEqual(rows[1].split('\t')[-1],'1')
                self.assertEqual(rows[2].split('\t')[-1],'0')

    def test_compcert_barriers_wait_before_dependents(self):
        script=five.runner('compile-compcert')
        for before, bg in five.BARRIERS.items():
            self.assertLess(script.index(f'test -f /probe/work/async-{bg}.done'),script.index(f'bash /probe/input/{before:03}.sh'))
        self.assertEqual(script.count('while ! test -f'),9)
        b=budgets('compile-compcert')
        self.assertTrue(1800<b['replay']<b['controller']<b['native']<b['workflow'])

    def test_32g_requires_exact_compcert_and_optin_before_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            for kwargs in (dict(memory_mib=32768),dict(memory_mib=32768,allow_8gib=True),
                           dict(memory_mib=32768,allow_compcert=True)):
                with self.assertRaises(ValueError):
                    create(Path(tmp),'http://127.0.0.1:1234','wrong-image',**kwargs)
            with self.assertRaises(FileNotFoundError):
                create(Path(tmp),'http://127.0.0.1:1234',five.COMPCERT_IMAGE,
                       memory_mib=32768,allow_compcert=True,sandbox_timeout=16200)

    def test_pdf_evidence_both_runs_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'logs').mkdir()
            for step in (3,5):
                (root/'logs'/f'{step:03}.stdout').write_text('1127 specs, 0 failures, 56 pending')
                (root/'logs'/f'{step:03}.stderr').write_text('')
            five.validate_outputs('mozilla__pdf.js-20957',root)
            (root/'logs/005.stdout').write_text('1127 specs, 1 failures, 56 pending')
            with self.assertRaises(ValueError):five.validate_outputs('mozilla__pdf.js-20957',root)


class EndpointTests(unittest.TestCase):
    def test_endpoint_uses_one_bound_vmm_and_fails_changed_lower(self):
        from unittest.mock import Mock
        from contextlib import ExitStack
        import external_endpoint as endpoint
        with tempfile.TemporaryDirectory() as tmp:
            e=endpoint.Endpoint.__new__(endpoint.Endpoint)
            e.output=Path(tmp);e.sid='owned';e.vm={'pid':123,'start_ticks':'7'}
            e.binding=Mock();e.binding.resolve.return_value=e.vm;e.group=Path('/owned')
            obj=dict(path='/lower',device=1,inode=2,size=4096,mtime_ns=3)
            e.roots={'objects':[obj]}
            row=dict(obj,stable=True,resident_bytes=4096)
            with ExitStack() as stack:
                stack.enter_context(patch.object(endpoint,'read',return_value={'start_ticks':'7','pss_kib':9}))
                stack.enter_context(patch.object(endpoint,'counters',return_value={'current':12345,'file':2048}))
                stack.enter_context(patch.object(endpoint,'validate_rootfs_inventory'))
                query=stack.enter_context(patch.object(endpoint,'mincore_file',return_value=row))
                result=e.observe()
                self.assertEqual(result['pss']['denominator'],1)
                self.assertEqual(result['pss']['per_sandbox_bytes'],9*1024)
                self.assertEqual(result['p1']['resident_bytes'],4096)
                self.assertTrue(result['coverage_insufficient'])
                query.return_value=dict(row,inode=99)
                with self.assertRaises(ValueError):e.observe()


class OciTests(unittest.TestCase):
    def test_log4j_rebuild_is_explicit_pinned_and_keeps_fixed_base(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'origin.json'
            fixed=dict(source_commit='fixed',containerfile_sha256='fixedfile',base_image='fixedbase')
            primary=dict(rebuilt_local_image=fixed,verification=dict(layers=2,rootfs_diff_ids=['base','old']))
            rebuilt=dict(status='VERIFIED',task_id='apache__logging-log4j2',rebuilt_local_image=fixed,
                         verification=dict(layers=2,rootfs_diff_ids=['base','new'],runtime_config_fields_match=True))
            cfg=dict(task_id='apache__logging-log4j2',log4j_rebuilt_origin=dict(path=str(path)))
            def save():
                path.write_text(json.dumps(rebuilt))
                cfg['log4j_rebuilt_origin']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            save()
            actual=five.rebuilt_log4j_origin(cfg,primary)
            self.assertEqual(actual['primary_verification'],primary['verification'])
            rebuilt['verification']['rootfs_diff_ids'][0]='wrong-base';save()
            with self.assertRaises(ValueError):five.rebuilt_log4j_origin(cfg,primary)
            rebuilt['verification']['rootfs_diff_ids'][0]='base';save()
            path.write_text(path.read_text()+' ')
            with self.assertRaises(ValueError):five.rebuilt_log4j_origin(cfg,primary)
            save();cfg['task_id']='hf-model-inference'
            with self.assertRaises(ValueError):five.rebuilt_log4j_origin(cfg,primary)

    def test_digest_verified_local_transport_and_no_remote_fallback(self):
        import hashlib
        import external_five_oci as oci
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=root/'blobs/sha256';store.mkdir(parents=True)
            def put(value):
                data=value if isinstance(value,bytes) else json.dumps(value).encode()
                digest='sha256:'+hashlib.sha256(data).hexdigest();(store/digest[7:]).write_bytes(data)
                return dict(digest=digest,size=len(data))
            layer=put(b'toy-layer');diff='sha256:'+'a'*64
            cfg=put(dict(os='linux',architecture='amd64',rootfs={'diff_ids':[diff]}))
            manifest=put(dict(schemaVersion=2,config=cfg,layers=[layer]))
            (root/'oci-layout').write_text(json.dumps({'imageLayoutVersion':'1.0.0'}))
            (root/'index.json').write_text(json.dumps({'manifests':[dict(manifest,annotations={'org.opencontainers.image.ref.name':'latest'})]}))
            origin={'verification':dict(manifest_digest=manifest['digest'],config_digest=cfg['digest'],layers=1,layer_bytes=9,rootfs_diff_ids=[diff])}
            receipt,_=oci.validate(root,origin);self.assertEqual(receipt['status'],'VERIFIED')
            metadata=root/'metadata';metadata.mkdir();(metadata/'version.txt').write_text('fixture-version')
            (metadata/'manifest.json').write_bytes((store/manifest['digest'][7:]).read_bytes())
            (metadata/'config.json').write_bytes((store/cfg['digest'][7:]).read_bytes())
            script=root/'regctl';script.write_text(oci.wrapper('/bin/false','localhost/image:fixed',manifest['digest'],cfg['digest'],base=str(metadata)));script.chmod(0o755)
            actual=subprocess.run([script,'manifest','get','--format','raw-body','localhost/image:fixed'],capture_output=True)
            self.assertEqual(actual.returncode,0);self.assertEqual(actual.stdout,(metadata/'manifest.json').read_bytes())
            self.assertEqual(subprocess.run([script,'manifest','get','--format','raw-body','other/image:latest'],capture_output=True).returncode,78)
            self.assertEqual(subprocess.run([script,'image','copy','--platform','local','localhost/image:fixed','ocidir:///tmp/unowned'],capture_output=True).returncode,78)
            (store/layer['digest'][7:]).write_bytes(b'corrupted')
            with self.assertRaises(ValueError):oci.validate(root,origin)


class ControllerTests(unittest.TestCase):
    def test_five_tasks_controller_preserves_checker_failure_and_cleanup(self):
        from types import SimpleNamespace
        import csv
        import shutil
        import run_external_workload as controller
        from contextlib import ExitStack, redirect_stdout
        import io
        for task in five.COUNTS:
            for fault in (None,'replay','exit','checker','endpoint'):
                with self.subTest(task=task,fault=fault),tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp);up=root/'up';clock=up/'experiments/container-screening/harness/clock_ns.c';clock.parent.mkdir(parents=True);clock.write_text('/* fake compiler */');(clock.parent/'file_reader.c').write_text('/* fake reader */')
                    args=SimpleNamespace(config=root/'config',upstream=up,output=root/'out',config_home=root/'credentials',service_url='http://127.0.0.1:1',container='owned',sampling='on')
                    expected_checker=141 if task=='apache__logging-log4j2' else 0
                    cfg=dict(task_id=task,actions=five.COUNTS[task],image=five.COMPCERT_IMAGE if task=='compile-compcert' else 'unit',memory_mib=32768 if task=='compile-compcert' else 8192,repository='fixture',revision='fixture-sha',run='primary',validation_scope='original')
                    replay=dict(files={'001.sh':'false\n'},origin={'verification':{'manifest_digest':five.COMPCERT_IMAGE.split('@')[1]}},validation=dict(command='exit '+str(expected_checker),exit_code=expected_checker,status='FAIL' if expected_checker else 'PASS',scope='original'))
                    primary=up/'primary';primary.mkdir()
                    args.config.write_text(json.dumps(cfg))
                    for name in ('steps.csv','validation.json','oci-origin.json','run.json'):(primary/name).write_text('{}')
                    reference=[dict(exit_code=1 if i==1 else 0) for i in range(1,cfg['actions']+1)]
                    calls=[];resources=[]
                    class Monitor:
                        def __init__(self,*a,**kw):self.active={};resources.append(self)
                        def start(self,sid,*a):self.active[sid]=1
                        def stop(self,sid,*a):self.active.pop(sid)
                    class Endpoint:
                        def __init__(self,*a):self.stopped=False;resources.append(self)
                        def observe(self):
                            if fault=='endpoint':raise ValueError('changed identity')
                            return dict(status='OBSERVED')
                        def stop(self):self.stopped=True;return dict(status='PASS')
                    def call(argv,**kw):
                        argv=list(map(str,argv));calls.append(argv);stdout='';code=0
                        if argv[0]=='git':stdout='fixture-sha'
                        elif len(argv)>1 and argv[1].endswith('agentenv_cold_start.py'):stdout='owned-sandbox'
                        elif 'upload' in argv:pass
                        elif 'download' in argv:
                            # Existing parent receives input/; a new destination
                            # itself receives the downloaded directory contents.
                            target=Path(argv[-1])
                            if target.exists():target=target/'input'
                            target.mkdir(parents=True,exist_ok=True)
                            if 'validation-download' in argv[-1]:
                                (target/'validation.exit').write_text(str(99 if fault=='checker' else expected_checker))
                                (target/'validation.stdout').write_text('\n'.join('target/classes/x'+str(i)+'.class' for i in range(5)))
                            else:
                                (target/'logs').mkdir(exist_ok=True)
                                with (target/'tools.tsv').open('w') as f:
                                    w=csv.DictWriter(f,fieldnames=['step','start_monotonic_ns','end_monotonic_ns','duration_ns','exit_code'],delimiter='\t');w.writeheader()
                                    for i,r in enumerate(reference,1):w.writerow(dict(step=i,start_monotonic_ns=i*100,end_monotonic_ns=i*100+50,duration_ns=50,exit_code=99 if fault=='exit' and i==1 else r['exit_code']))
                        elif 'exec' in argv:
                            if 'chmod 755' in argv[-1]:stdout='100'
                            if argv[-1]=='bash /probe/input/runner.sh':code=int(fault=='replay')
                        elif 'list' in argv:stdout='[]'
                        return SimpleNamespace(returncode=code,stdout=stdout)
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(controller,'load',return_value=(cfg,replay,reference)))
                        stack.enter_context(patch.object(controller.subprocess,'run',side_effect=call))
                        stack.enter_context(patch.object(controller,'RuntimeMonitor',Monitor))
                        stack.enter_context(patch('external_endpoint.Endpoint',Endpoint))
                        stack.enter_context(patch.object(five,'validate_outputs',return_value={'fixture':'PASS'}))
                        stack.enter_context(redirect_stdout(io.StringIO()))
                        status=controller.run(args)
                    result=json.loads((args.output/'result.json').read_text())
                    self.assertEqual(status,0 if fault is None else 1,result)
                    self.assertEqual(sum(a[-1]=='bash /probe/input/runner.sh' for a in calls),1)
                    self.assertTrue(result['cleanup']['sandbox_removed']);self.assertTrue(result['cleanup']['service_sandbox_list_empty'])
                    self.assertTrue(all(not r.active if isinstance(r,Monitor) else r.stopped for r in resources))
                    if fault is None:
                        self.assertEqual(result['original_validation']['actual_exit_code'],expected_checker)
                        if expected_checker:self.assertEqual(result['original_validation']['actual_status'],'FAIL')
                        stages=[e['stage'] for e in json.loads((args.output/'events.json').read_text())]
                        if task=='compile-compcert':
                            self.assertLess(stages.index('prepare-file-reader'),stages.index('replay'))
                        self.assertLess(stages.index('download-tools'),stages.index('original-validation'))
                        self.assertLess(result['task_end_monotonic_ns'],result['controller_elapsed_ns']+result['task_start_monotonic_ns'])
                    else:self.assertTrue((args.output/'failure.txt').exists())


class ReportAndBudgetTests(unittest.TestCase):
    def test_long_window_and_phase_statistics_keep_distinct_quantities(self):
        from datetime import datetime
        from report_external_five import sample_stats
        window=five.runtime_window('compile-compcert')
        duration=(datetime.fromisoformat(window['end_utc'])-datetime.fromisoformat(window['start_utc'])).total_seconds()
        self.assertGreater(duration,budgets('compile-compcert')['controller'])
        self.assertLess(duration,budgets('compile-compcert')['native']-180)
        self.assertGreater(duration,1200)
        rows=[{'monotonic_ns':str(i),'current':str(100*i),'file':str(10*i)} for i in range(1,6)]
        result=sample_stats(rows,('current','file'),2,4)
        self.assertEqual(result['quantities']['current'],dict(observed_peak_bytes=400,sample_median_bytes=300))
        self.assertEqual(result['quantities']['file'],dict(observed_peak_bytes=40,sample_median_bytes=30))
        with self.assertRaises(ValueError):sample_stats(rows,('current',),99,100)

    def test_report_requires_receipts_and_retains_failed_original_checker(self):
        import csv
        import report_external_five as reporting
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out';raw=root/'raw';native=root/'native';dest=root/'report'
            for p in (out/'host-file',raw/'vmm',native,dest):p.mkdir(parents=True)
            def save(p,value):p.write_text(json.dumps(value))
            def tsv(p,rows):
                with p.open('w') as f:
                    w=csv.DictWriter(f,fieldnames=list(rows[0]),delimiter='\t');w.writeheader();w.writerows(rows)
            receipt=dict(status='PASS',samples=5)
            endpoint=dict(pss=dict(denominator=1,per_sandbox_bytes=10000,cpu_ns=1,start_monotonic_ns=5,end_monotonic_ns=6),p1=dict(resident_bytes=20000,unique_files=2,cpu_ns=2,start_monotonic_ns=6,end_monotonic_ns=7),coverage_insufficient=True,cgroup='/unit')
            task=dict(status='PASS',measurement_status='COMPLETE',task_id='apache__logging-log4j2',actions=1,endpoint=endpoint,vmm_collector=receipt,task_start_monotonic_ns=2,task_end_monotonic_ns=4,task_duration_ns=2,replay_start_monotonic_ns=2,replay_end_monotonic_ns=3,download_start_monotonic_ns=3,controller_elapsed_ns=9,original_validation=dict(actual_status='FAIL',actual_exit_code=141,exit_code=141,scope='original artifacts only'),functional_evidence={'compilation':'PASS'},snapshot_save='NOT_OCCURRED',snapshot_restore='NOT_OCCURRED',cleanup={'sandbox_removed':True})
            save(raw/'endpoint-setup.json',dict(status='PASS',cpu_ns=1));save(raw/'result.json',task);save(raw/'runtime-monitor.json',{'sessions':[{'receipt':receipt}]})
            save(native/'result.json',dict(host_file_creation={'before_guest_counters':{'current':50,'file':10}},cleanup={'owned_container_removed':True}))
            save(out/'result.json',{'outer_elapsed_ns':10});save(out/'host-file/result.json',receipt);save(raw/'events.json',[])
            tsv(out/'host-file/memory.tsv',[dict(monotonic_ns=i,current=i*100,file=i*10,anon=i,kernel=i,shmem=i,file_dirty=0,file_writeback=0) for i in range(1,6)])
            tsv(raw/'tools.tsv',[dict(step=1,start_monotonic_ns=1,end_monotonic_ns=6,duration_ns=5,exit_code=1)])
            tsv(raw/'vmm/host-vmm.tsv',[dict(monotonic_ns=i,pss_kib=i) for i in (2,3,4)])
            value=reporting.report(out,raw,native,dest)
            self.assertEqual(value['original_validation']['actual_status'],'FAIL')
            self.assertEqual(value['host']['replay']['quantities']['current']['observed_peak_bytes'],300)
            self.assertEqual(value['tool_duration_sum_ns'],5)
            self.assertTrue((dest/'pss.png').is_file())
            save(out/'host-file/result.json',dict(status='FAIL'))
            with self.assertRaises(ValueError):reporting.report(out,raw,native,dest)


class OuterTests(unittest.TestCase):
    def test_compcert_native_budget_oci_forwarding_and_cleanup_on_failure(self):
        import run_host_external_workload as host
        from types import SimpleNamespace
        from contextlib import ExitStack,redirect_stdout
        import io
        for fault in ('native-fail','timeout'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);cfg=dict(task_id='compile-compcert',actions=318)
                collectors=[]
                class Collector:
                    def __init__(self,*a,**kw):self.stopped=False;collectors.append(self)
                    def stop(self):self.stopped=True;return dict(status='PASS')
                def native(argv,**kw):
                    self.assertEqual(argv[:3],['timeout','-k','120s'])
                    self.assertIn(str(budgets('compile-compcert')['native']-180)+'s',argv)
                    self.assertEqual(argv[argv.index('--external-five-oci')+1],str(root/'oci'))
                    self.assertEqual(kw['timeout'],budgets('compile-compcert')['native'])
                    if fault=='timeout':raise subprocess.TimeoutExpired(argv,kw['timeout'])
                    return SimpleNamespace(returncode=1)
                with ExitStack() as stack:
                    stack.enter_context(patch.object(host,'ROOT',root))
                    stack.enter_context(patch.object(host,'load',return_value=(cfg,{},[])))
                    stack.enter_context(patch.object(host,'available',return_value=80*2**30))
                    stack.enter_context(patch.object(host.shutil,'copy2'))
                    stack.enter_context(patch.object(host.subprocess,'run',side_effect=native))
                    stack.enter_context(patch('host_file_monitor.measurement_group',return_value=(Path('/unit'),{})))
                    stack.enter_context(patch('host_file_monitor.HostFileMonitor',Collector))
                    stack.enter_context(redirect_stdout(io.StringIO()))
                    result=host.grouped(root/'cfg',root,'on',root/'oci')
                self.assertEqual(result,1)
                self.assertTrue(collectors[0].stopped)
                saved=json.loads(next((root/'results/external-five').glob('*/result.json')).read_text())
                self.assertEqual(saved['status'],'FAIL')
                self.assertTrue(next((root/'results/external-five').glob('*/failure.txt')).is_file())


if __name__=='__main__':unittest.main()
