"""Offline C4 sampling controls; all CLI, service and collector calls are replaced."""
import ast
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
import test_external_workload as fixtures
import run_external_workload as guest
import run_host_external_workload as host
import run_c4_sampling_controls as series
from external_workload import C4,load


class C4SamplingTests(unittest.TestCase):
    def fixture(self,root,task=C4):
        cfg,_=fixtures.ExternalWorkloadTests().fixture(root,task)
        value=json.loads(cfg.read_text());value.update(repository='local-fixture',revision='unit-revision',image='unit-image',source_blob='unit-blob',validation_scope='fixture')
        cfg.write_text(json.dumps(value));clock=root/'experiments/container-screening/harness/clock_ns.c';clock.parent.mkdir(parents=True);clock.write_text('/* mocked compiler */\n')
        return cfg

    def test_guest_off_has_no_collector_and_keeps_validation_timing_and_cleanup(self):
        for fault in (None,'bad-exit','clock','evidence','replay'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);cfg=self.fixture(root);out=root/'out';seen=[]
                args=types.SimpleNamespace(config=cfg,upstream=root,output=out,config_home=root/'cfg',service_url='http://127.0.0.1:1',container='unused',sampling='off')
                def cli(argv,**kw):
                    argv=list(map(str,argv));seen.append(argv);stdout='';status=0
                    if argv[0]=='git':stdout='unit-revision'
                    elif 'agentenv_cold_start.py' in argv[1]:stdout='00000000-0000-0000-0000-000000000001'
                    elif 'upload' in argv:
                        self.assertEqual(len(list(Path(argv[-2]).glob('*.sh'))),37);self.assertEqual(len(list(Path(argv[-2]).glob('*.payload'))),9)
                    elif 'download' in argv:
                        logs=out/'input/logs';logs.mkdir(parents=True)
                        _,_,ref=load(cfg,root)
                        with (out/'input/tools.tsv').open('w') as f:
                            w=csv.DictWriter(f,fieldnames=['step','start_monotonic_ns','end_monotonic_ns','duration_ns','exit_code'],delimiter='\t');w.writeheader()
                            for i,r in enumerate(ref,1):w.writerow(dict(step=i,start_monotonic_ns='' if fault=='clock' else i*100,end_monotonic_ns='' if fault=='clock' else i*100+50,duration_ns=50,exit_code=9 if fault=='bad-exit' and i==1 else r['exit_code']))
                        (logs/'033.stdout').write_text('' if fault=='evidence' else 'CONSTRAINT VIOLATIONS: NONE\nFINAL VERDICT: BYTE-EXACT ROUNDTRIP ON c4_sample\n')
                        (logs/'034.stdout').write_text('9898 files, structure + content\n')
                    elif 'list' in argv:stdout='[]'
                    elif 'exec' in argv:
                        if 'chmod 755' in argv[-1]:stdout='100'
                        if argv[-1]=='bash /probe/input/runner.sh' and fault=='replay':status=1
                    return types.SimpleNamespace(returncode=status,stdout=stdout)
                with patch.object(guest.subprocess,'run',side_effect=cli),patch.object(guest,'RuntimeMonitor',side_effect=AssertionError('off must not construct a collector')) as monitor,contextlib.redirect_stdout(io.StringIO()):
                    status=guest.run(args)
                value=json.loads((out/'result.json').read_text());monitor.assert_not_called()
                self.assertEqual(status,0 if fault is None else 1,value)
                self.assertEqual(value['memory'],'NOT_MEASURED');self.assertTrue(value['cleanup']['memory_sampling_disabled'])
                self.assertTrue(value['cleanup']['sandbox_removed'] and value['cleanup']['service_sandbox_list_empty'])
                self.assertFalse((out/'runtime-code').exists());self.assertFalse((out/'runtime-monitor.json').exists())
                self.assertFalse(any('monitor-' in str(a) for a in seen))
                if fault is None:self.assertEqual(value['actions'],36);self.assertEqual(value['roundtrip_validation'],'PASS')

    def test_unsupported_task_off_stays_rejected(self):
        cfg={'task_id':'unsupported'}
        with patch.object(guest,'load',return_value=(cfg,{},[])),patch.object(guest.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'limited to the explicit C4/Django/Cython'):guest.run(types.SimpleNamespace(config='unused',upstream='unused',sampling='off'))
        run.assert_not_called()
        with patch.object(host,'load',return_value=(cfg,{},[])),patch.object(host.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'limited to the explicit C4/Django/Cython'):host.grouped('unused','unused','off')
        run.assert_not_called()

    def test_outer_off_has_no_host_collector_or_memory_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=self.fixture(root);storage=root/'.artifacts/storage-isolated-service/unit';raw=storage/'external-workload';raw.mkdir(parents=True)
            task=dict(status='PASS',task_id=C4,actions=36,roundtrip_validation='PASS',sampling='off',memory='NOT_MEASURED',cleanup=dict(memory_sampling_disabled=True))
            (raw/'result.json').write_text(json.dumps(task))
            (storage/'result.json').write_text(json.dumps(dict(status='PASS',cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged')},checks={k:'PASS' for k in ('actual_writable_files_inside_volume','readonly_root_rejects_write','fixed_backing_allocation')},host_file_residency=dict(status='NOT_REQUESTED'))))
            def native(argv,**kw):
                self.assertIn('--external-workload-sampling',argv);self.assertNotIn('--light-monitor',argv)
                kw['stdout'].write('status=PASS result_dir='+str(storage)+'\n');kw['stdout'].flush()
                return types.SimpleNamespace(returncode=0)
            with patch.dict(os.environ,{'ASB_HOST_FILE_SAMPLING':'off'}),patch.object(host,'ROOT',root),patch.object(host,'available',return_value=80*2**30),patch.object(host.shutil,'copy2'),patch.object(host.subprocess,'run',side_effect=native),patch('host_file_monitor.measurement_group',return_value=(Path('/unit'),{})),patch('host_file_monitor.HostFileMonitor',side_effect=AssertionError('no collector')) as collector,patch('report_host_file.report',side_effect=AssertionError('no memory report')) as report,patch.object(host,'guest_plot',side_effect=AssertionError('no plot')) as plot,contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(host.grouped(cfg,root,'off'),0)
            collector.assert_not_called();report.assert_not_called();plot.assert_not_called()
            record=json.loads(next((root/'.artifacts/host-external-workload').glob('*/result.json')).read_text())
            self.assertEqual(record['collector']['status'],'NOT_REQUESTED');self.assertEqual(record['memory'],'NOT_MEASURED')
            self.assertNotIn('host',record);self.assertNotIn('guest',record)

    def test_outer_cli_forwards_same_explicit_mode_and_keeps_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=self.fixture(root)
            with patch.object(sys,'argv',['run_host_external_workload.py',str(cfg),str(root),'--sampling','off']),patch.object(host.os,'geteuid',return_value=0),patch.object(host.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as run:
                self.assertEqual(host.main(),0)
            argv=run.call_args_list[0].args[0]
            for token in ('--setenv=ASB_HOST_FILE_SAMPLING=off','--property=MemoryAccounting=yes','--property=IOAccounting=yes'):self.assertIn(token,argv)
            self.assertEqual(argv[-2:],['--sampling','off']);self.assertEqual(run.call_args_list[-1].args[0][:2],['systemctl','stop'])

    def test_actual_native_guards_check_both_modes_and_reject_mismatch(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'scripts/validate_isolated_agentenv.py').read_text())
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and any(isinstance(r,ast.Raise) and 'external host and guest sampling modes differ' in ast.unparse(r) for r in n.body))
        code=compile(ast.Module(body=[node],type_ignores=[]),'<native mode guard>','exec')
        for mode,hostmode,light,valid in [('on','on',True,True),('off','off',False,True),('on','off',True,False),('off','on',False,False),('off','off',True,False)]:
            ns=dict(external_config=True,external_sampling=mode,host_sampling=hostmode,args=types.SimpleNamespace(light_monitor=light))
            if valid:exec(code,ns)
            else:
                with self.assertRaises(ValueError):exec(code,ns)
        guard=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and 'external_off' in ast.unparse(n.test) and any(isinstance(r,ast.Raise) and 'sampling off is limited to the explicit C4/Django/Cython controls' in ast.unparse(r) for r in n.body))
        code=compile(ast.Module(body=[guard],type_ignores=[]),'<native C4-only guard>','exec')
        exec(code,dict(external_off=True,external_value={'task_id':C4}))
        exec(code,dict(external_off=True,external_value={'task_id':'django__django-16819'}))
        exec(code,dict(external_off=True,external_value={'task_id':'build-cython-ext'}))
        for task in ('unsupported',):
            with self.assertRaises(ValueError):exec(code,dict(external_off=True,external_value={'task_id':task}))

    def test_two_pair_summary_retains_negative_values_and_rejects_missing_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);runs=[]
            for i,((pair,mode),seconds) in enumerate(zip(series.ORDER,(50,51,48,50))):
                runs.append(dict(pair=pair,mode=mode,status='PASS',cgroup=str(i),guest_id=str(i),timings_seconds={f:seconds for f in ('tool_sum','tool_span','replay_cli','cold_create','controller')}))
            (out/'runs.json').write_text(json.dumps(runs));result=series.summarize(out)
            self.assertAlmostEqual(result['pairs'][0]['timings']['tool_sum']['change_percent'],2)
            self.assertAlmostEqual(result['pairs'][1]['timings']['tool_sum']['change_percent'],-4)
            self.assertAlmostEqual(result['summary']['tool_sum']['paired_change_median_percent'],-1)
            (out/'runs.json').write_text(json.dumps(runs[:-1]))
            with self.assertRaises(AssertionError):series.summarize(out)


if __name__=='__main__':unittest.main()
