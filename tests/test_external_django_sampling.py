"""Offline Django control checks; no real services, guests, network or business data."""
import contextlib
import csv
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import test_external_c4_sampling as c4_tests
import run_external_workload as guest
import run_host_external_workload as host
import run_c4_sampling_controls as series
from external_workload import load,write_inputs,guest_runner

DJANGO='django__django-16819'


class DjangoSamplingTests(unittest.TestCase):
    def fixture(self,root):
        cfg=c4_tests.C4SamplingTests().fixture(root,DJANGO)
        value=json.loads(cfg.read_text());published=root/value['run']/'replay-inputs.json';replay=json.loads(published.read_text())
        replay['files']['session-server.sh']='# Original transport helper, not a business input\n';published.write_text(json.dumps(replay))
        return cfg

    def tools(self,path,cfg,root,fault=None):
        _,_,ref=load(cfg,root)
        with path.open('w') as f:
            writer=csv.DictWriter(f,fieldnames=['step','start_monotonic_ns','end_monotonic_ns','duration_ns','exit_code'],delimiter='\t');writer.writeheader()
            for i,r in enumerate(ref,1):writer.writerow(dict(step=i,start_monotonic_ns='' if fault=='clock' else i*100,end_monotonic_ns='' if fault=='clock' else i*100+50,duration_ns=50,exit_code=9 if fault=='exit' and i==1 else r['exit_code']))

    def test_django_off_keeps_persistent_runner_and_validation_with_failure_cleanup(self):
        for fault in (None,'clock','exit','replay','source','syntax'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);cfg=self.fixture(root);out=root/'out';seen=[]
                args=types.SimpleNamespace(config=cfg,upstream=root,output=out,config_home=root/'config',service_url='http://127.0.0.1:1',container='unused',sampling='off')
                def cli(argv,**kw):
                    argv=list(map(str,argv));seen.append(argv);stdout='';code=0
                    if argv[0]=='git':stdout='unit-revision'
                    elif 'agentenv_cold_start.py' in argv[1]:stdout='00000000-0000-0000-0000-000000000001'
                    elif 'upload' in argv:
                        self.assertEqual(argv[-1],'/workspace/asb-external-tools');bundle=Path(argv[-2]);self.assertFalse((bundle/'session-server.sh').exists())
                        self.assertEqual(len(list(bundle.glob('*.sh'))),29)
                        text=(bundle/'runner.sh').read_text();self.assertIn('source "$ASB_EXTERNAL_ROOT/$ASB_EXTERNAL_FILE.sh"',text);self.assertIn('safe.directory',text);self.assertNotIn('(cd /app',text)
                    elif 'download' in argv:
                        self.assertEqual(argv[-2],'/workspace/asb-external-tools');bundle=out/'asb-external-tools';bundle.mkdir();self.tools(bundle/'tools.tsv',cfg,root,fault)
                    elif 'list' in argv:stdout='[]'
                    elif 'exec' in argv:
                        command=argv[-1]
                        if 'git hash-object' in command:
                            stdout='/envs/testbed/bin/python\nunit-blob';code=int(fault=='source')
                        elif 'chmod 755' in command:stdout='100'
                        elif 'runner.sh' in command:code=int(fault=='replay')
                        elif 'python -m py_compile' in command:code=int(fault=='syntax')
                    return types.SimpleNamespace(returncode=code,stdout=stdout)
                with patch.object(guest.subprocess,'run',side_effect=cli),patch.object(guest,'RuntimeMonitor',side_effect=AssertionError('no sampler')) as monitor,contextlib.redirect_stdout(io.StringIO()):
                    code=guest.run(args)
                value=json.loads((out/'result.json').read_text());self.assertEqual(code,0 if fault is None else 1,value);monitor.assert_not_called()
                self.assertTrue(value['cleanup']['sandbox_removed'] and value['cleanup']['service_sandbox_list_empty'] and value['cleanup']['memory_sampling_disabled'])
                self.assertEqual(value['memory'],'NOT_MEASURED');self.assertFalse((out/'runtime-code').exists())
                if fault is None:self.assertEqual(value['actions'],28);self.assertEqual(value['syntax_validation'],'PASS');self.assertEqual(value['official_tests'],'NOT_RUN_ORIGIN_SYNTAX_ONLY')

    def test_django_outer_off_without_c4_marker_and_without_memory_report(self):
        for failed in (False,True):
            with self.subTest(failed=failed),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);cfg=self.fixture(root);storage=root/'.artifacts/storage-isolated-service/unit';raw=storage/'external-workload';raw.mkdir(parents=True)
                task=dict(status='PASS',task_id=DJANGO,actions=28,sampling='off',memory='NOT_MEASURED',cleanup=dict(memory_sampling_disabled=True))
                (raw/'result.json').write_text(json.dumps(task));(storage/'result.json').write_text(json.dumps(dict(status='PASS',cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged')},checks={k:'PASS' for k in ('actual_writable_files_inside_volume','readonly_root_rejects_write','fixed_backing_allocation')},host_file_residency={'status':'NOT_REQUESTED'})))
                def native(argv,**kw):
                    self.assertIn('--external-workload-sampling',argv);self.assertNotIn('--light-monitor',argv);kw['stdout'].write('status=PASS result_dir='+str(storage)+'\n');kw['stdout'].flush();return types.SimpleNamespace(returncode=int(failed))
                with patch.dict(os.environ,{'ASB_HOST_FILE_SAMPLING':'off'}),patch.object(host,'ROOT',root),patch.object(host,'available',return_value=80*2**30),patch.object(host.shutil,'copy2'),patch.object(host.subprocess,'run',side_effect=native),patch('host_file_monitor.measurement_group',return_value=(Path('/unit'),{})),patch('host_file_monitor.HostFileMonitor',side_effect=AssertionError('no collector')) as monitor,patch.object(host,'guest_plot',side_effect=AssertionError('no plot')) as plot,contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(host.grouped(cfg,root,'off'),int(failed))
                monitor.assert_not_called();plot.assert_not_called()

    def test_django_inspector_uses_only_28_business_inputs_and_original_syntax_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=self.fixture(root);config=root/'configs/external-django16819-light.json';config.parent.mkdir();config.write_text(cfg.read_text())
            workflow=root/'workflow';workflow.mkdir();storage=root/'storage';raw=storage/'external-workload';bundle=raw/'asb-external-tools';bundle.mkdir(parents=True)
            value,replay,_=load(cfg,root);write_inputs(bundle,replay,DJANGO);(bundle/'runner.sh').write_text(guest_runner(28,task_id=DJANGO));self.assertFalse((bundle/'session-server.sh').exists());self.tools(raw/'tools.tsv',cfg,root)
            task=dict(status='PASS',task_id=DJANGO,actions=28,image='unit-image',sampling='off',strict_exit_match=True,source_baseline='PASS',syntax_validation='PASS',official_tests='NOT_RUN_ORIGIN_SYNTAX_ONLY',sandbox_id='own-guest',cleanup=dict(sandbox_removed=True,service_sandbox_list_empty=True,memory_sampling_disabled=True),memory='NOT_MEASURED',replay_start_monotonic_ns=1,replay_end_monotonic_ns=10,controller_elapsed_ns=20)
            (raw/'result.json').write_text(json.dumps(task));(raw/'events.json').write_text(json.dumps([dict(stage='cold-create',exit_code=0,duration_ns=1)]))
            outer=dict(status='PASS',cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged')},checks={k:'PASS' for k in ('external_frozen_tools_and_original_syntax_check','actual_writable_files_inside_volume','readonly_root_rejects_write','fixed_backing_allocation')},host_file_residency={'status':'NOT_REQUESTED'},host_file_creation=dict(host_sampling='off',before_guest_counters=None,cgroup='/unit'),observed_host_available_min_bytes=32*2**30)
            (storage/'result.json').write_text(json.dumps(outer));(workflow/'result.json').write_text(json.dumps(dict(status='PASS',sampling='off',raw=str(raw),storage=str(storage),collector={'status':'NOT_REQUESTED'},memory='NOT_MEASURED')))
            with patch.object(series,'ROOT',root):result=series.inspect_run(workflow,'off',root,'django16819')
            self.assertEqual(result['actions'],28);self.assertEqual(result['memory'],'NOT_MEASURED')
            self.assertFalse((raw/'input').exists());self.assertNotIn('roundtrip_validation',task)
            (bundle/'017.sh').unlink()
            with patch.object(series,'ROOT',root):
                with self.assertRaises(FileNotFoundError):series.inspect_run(workflow,'off',root,'django16819')

    def test_django_image_evidence_reuses_creation_without_requiring_c4_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);prior=root/'.artifacts'/series.PROFILES['django16819']['prior'];prior.mkdir(parents=True);work=root/'work';work.mkdir();raw=root/'raw';raw.mkdir();out=root/'out';out.mkdir()
            (prior/'result.json').write_text(json.dumps(dict(status='PASS',workflow=str(work))));(work/'result.json').write_text(json.dumps(dict(status='PASS',raw=str(raw))));(raw/'result.json').write_text(json.dumps(dict(status='PASS',image='unit-image',task_id=DJANGO,source_baseline='PASS',syntax_validation='PASS')))
            with patch.object(series,'ROOT',root),patch.object(series.subprocess,'run',side_effect=AssertionError('no network')):
                series.image_evidence(out,'django16819',dict(image='unit-image',task_id=DJANGO))
            self.assertEqual(json.loads((out/'image-evidence.json').read_text())['status'],'PASS');self.assertFalse((out/'manifest.json').exists())

    def test_saved_c4_plan_and_results_keep_their_original_meaning(self):
        source=Path(__file__).resolve().parents[1]/'.artifacts/external-c4-sampling-controls-20261030'
        if not (source/'comparison.json').exists():self.skipTest('local saved C4 evidence not available')
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            for name in ('runs.json','plan.json'):(out/name).write_text((source/name).read_text())
            self.assertEqual(series.summarize(out),json.loads((source/'comparison.json').read_text()))

    def test_django_plot_labels_and_summary_do_not_claim_c4_sample_or_36_steps(self):
        import report_c4_sampling_controls as report
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);runs=[dict(pair=p,mode=m,cgroup=str(i),guest_id=str(i),status='PASS',actions=28,timings_seconds={f:10+i for f in ('tool_sum','tool_span','replay_cli','cold_create','controller')}) for i,(p,m) in enumerate(series.ORDER)]
            (out/'runs.json').write_text(json.dumps(runs));(out/'plan.json').write_text(json.dumps(dict(task='django16819')))
            self.assertEqual(series.summarize(out)['task_id'],DJANGO)
            report.timings_plot(runs,out,'Django #16819',28);svg=(out/'timings.svg').read_text()
            self.assertIn('Django #16819',svg);self.assertIn('sum of 28 calls',svg);self.assertNotIn('sum of 36 calls',svg)


if __name__=='__main__':unittest.main()
