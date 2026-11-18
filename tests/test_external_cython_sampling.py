"""Cython sampling controls: temporary fixtures only, with all live calls replaced."""
import contextlib
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
import test_external_cython as fixtures
import run_host_external_workload as host
import run_c4_sampling_controls as series
import report_c4_sampling_controls as report
from external_workload import CYTHON,load,write_inputs,guest_runner


class CythonSamplingTests(unittest.TestCase):
    def saved_run(self,root,mode='off'):
        config,_=fixtures.fixture(root);cfg,replay,reference=load(config,root)
        folder=root/'configs';folder.mkdir();(folder/'external-cython-light.json').write_text(config.read_text())
        workflow=root/'workflow';workflow.mkdir();storage=root/'storage';raw=storage/'external-workload';bundle=raw/'input';bundle.mkdir(parents=True)
        write_inputs(bundle,replay,CYTHON);(bundle/'runner.sh').write_text(guest_runner(83,task_id=CYTHON))
        fixtures.outputs(bundle/'logs');logs=bundle/'logs'
        for step,text in {24:'gcc (test compiler) 1\nCython 3 | sympy 1 | pytest 9\n',25:'vispy 1\n',
                          26:'networkx 1\npeewee 1\nappdirs 1\nrequests 1\ntqdm 1\nmatplotlib 1\nplanarity 1\n',36:'setuptools 1\n'}.items():
            (logs/f'{step:03}.stdout').write_text(text)
        (raw/'extension-validation.txt').write_text('source_repo_commit: '+'a'*40+'\ncompiled extensions import successfully\n')
        (raw/'tools.tsv').write_text('step\tstart_monotonic_ns\tend_monotonic_ns\tduration_ns\texit_code\n'+''.join(f'{i}\t{i*100}\t{i*100+50}\t50\t{r["exit_code"]}\n' for i,r in enumerate(reference,1)))
        task=dict(status='PASS',task_id=CYTHON,actions=83,image=cfg['image'],sampling=mode,strict_exit_match=True,source_baseline='PASS',
            syntax_validation='NOT_REQUESTED_ORIGINAL_EXTENSION_CHECK',official_tests='NOT_RUN_ORIGINAL_18_TESTS_TWICE_AND_EXTENSION_CHECK',
            functional_validation='PASS',test_validation='PASS',extension_validation='PASS',guest_network_preflight='PASS',sandbox_id='own-guest',
            cleanup=dict(sandbox_removed=True,service_sandbox_list_empty=True,memory_sampling_disabled=mode=='off'),memory='NOT_MEASURED' if mode=='off' else None,
            replay_start_monotonic_ns=30,replay_end_monotonic_ns=8400,controller_elapsed_ns=10000)
        events=[dict(stage='cold-create',exit_code=0,duration_ns=1),dict(stage='guest-network',exit_code=0,start_monotonic_ns=10,duration_ns=1)]
        outer=dict(status='PASS',cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged')},
            checks={k:'PASS' for k in ('external_fixed_tools_and_extensions_and_tests','actual_writable_files_inside_volume','readonly_root_rejects_write','fixed_backing_allocation')},
            host_file_residency=dict(status='NOT_REQUESTED'),host_file_creation=dict(host_sampling=mode,before_guest_counters=None,cgroup='/unit'),observed_host_available_min_bytes=70*2**30)
        work=dict(status='PASS',sampling=mode,raw=str(raw),storage=str(storage),collector=dict(status='NOT_REQUESTED'),memory='NOT_MEASURED',owned_guest_proxy_stopped=True)
        if mode=='on':
            for stage in ('monitor-upload-bundle','monitor-ready','monitor-finish','monitor-download','monitor-collect-unit'):
                events.append(dict(stage=stage,exit_code=0,start_monotonic_ns=20,duration_ns=1))
            samples=raw/'samples';samples.mkdir();(samples/'guest-memory.tsv').write_text('monotonic_ns\n1\n9000\n')
            receipt=dict(status='PASS',interval_seconds=.1,samples=2,cpu_user_seconds=.01,cpu_system_seconds=.01,elapsed_ns=9000)
            manifest=dict(status='PASS',profile='light',host_vmm_requested=False,host_pss_interval_seconds=None,sessions=[dict(status='PASS',collector_reaped=True,raw_directory='samples',receipt=receipt)])
            series.write(raw/'runtime-monitor.json',manifest)
            hostdata=workflow/'host-file';hostdata.mkdir();(hostdata/'memory.tsv').write_text('monotonic_ns\n1\n10000\n')
            work.update(collector=dict(status='PASS',interval_seconds=.1,pss_interval_seconds=None,samples=2,thread_cpu_ns=1,counter_read_total_ns=1),
                host=dict(primary_metric='memory.current',pss_samples=0,mincore=dict(status='NOT_REQUESTED'),maximum_sampling_gap_ns=9999,current=dict(observed_peak_bytes=1024)),
                guest=dict(guest_used_peak_mib=1,guest_cached_peak_mib=1))
        series.write(raw/'result.json',task);series.write(raw/'events.json',events);series.write(storage/'result.json',outer);series.write(workflow/'result.json',work)
        for name in ('guest-proxy-rule.json','guest-proxy-address.json'):series.write(workflow/name,dict(status='REMOVED'))
        for stage,count in (('before-head',0),('after-head',2)):series.write(workflow/f'guest-proxy-counters-{stage}.json',dict(allowed_packets=count))
        return config,workflow,raw

    def test_inspector_accepts_both_modes_and_checks_original_cython_evidence(self):
        for mode in ('off','on'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);_,workflow,raw=self.saved_run(root,mode)
                with patch.object(series,'ROOT',root):value=series.inspect_run(workflow,mode,root,'cython')
                self.assertEqual(value['actions'],83);self.assertEqual(value['source_identity']['source_commit'],'a'*40)
                self.assertEqual(value['network_preflight_seconds'],1e-9)
                self.assertNotIn('call_timing',value)
                task=series.read(raw/'result.json');task['call_timing']=dict(version=2,replay=dict(command_duration_ns=17))
                series.write(raw/'result.json',task)
                with patch.object(series,'ROOT',root):timed=series.inspect_run(workflow,mode,root,'cython')
                self.assertEqual(timed.pop('call_timing'),task['call_timing']);self.assertEqual(timed,value)
                if mode=='off':self.assertEqual(value['memory'],'NOT_MEASURED');self.assertIsNone(value['sampling_cost'])
                else:self.assertEqual(value['sampling_cost']['guest_samples'],2)
                for name,bad in [('input/003-read.py',None),('input/031.payload','changed'),('input/logs/082.stdout','18 passed\n18 passed\n'),
                                 ('extension-validation.txt','compiled extensions import successfully\n')]:
                    path=raw/name;original=path.read_text()
                    if bad is None:path.unlink()
                    else:path.write_text(bad)
                    with patch.object(series,'ROOT',root):
                        with self.assertRaises((AssertionError,ValueError,FileNotFoundError)):series.inspect_run(workflow,mode,root,'cython')
                    path.write_text(original)
                task=series.read(raw/'result.json');task['extension_validation']='FAIL';series.write(raw/'result.json',task)
                with patch.object(series,'ROOT',root):
                    with self.assertRaises(AssertionError):series.inspect_run(workflow,mode,root,'cython')

    def test_off_inspector_rejects_sampler_artifacts_and_leaked_relay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,workflow,raw=self.saved_run(root)
            series.write(raw/'runtime-monitor.json',{})
            with patch.object(series,'ROOT',root):
                with self.assertRaises(AssertionError):series.inspect_run(workflow,'off',root,'cython')
            (raw/'runtime-monitor.json').unlink();series.write(workflow/'guest-proxy-rule.json',dict(status='ADDED'))
            with patch.object(series,'ROOT',root):
                with self.assertRaises(AssertionError):series.inspect_run(workflow,'off',root,'cython')

    def test_outer_cli_forwards_cython_off_without_real_systemd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg,_=fixtures.fixture(root)
            with patch.object(sys,'argv',['run_host_external_workload.py',str(cfg),str(root),'--sampling','off']),patch.object(host.os,'geteuid',return_value=0),patch.object(host.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as run:
                self.assertEqual(host.main(),0)
            command=run.call_args_list[0].args[0]
            self.assertIn('--setenv=ASB_HOST_FILE_SAMPLING=off',command);self.assertEqual(command[-2:],['--sampling','off'])
            self.assertEqual(run.call_args_list[0].kwargs['timeout'],2400)

    def test_reuses_cython_image_evidence_without_django_syntax_or_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,workflow,raw=self.saved_run(root)
            prior=root/'.artifacts'/series.PROFILES['cython']['prior'];prior.mkdir(parents=True)
            saved=dict(status='PASS',workflow=str(workflow),image_preflight=dict(status='PASS',image='unit-image',platform='linux/amd64',layers=7,compressed_bytes=303214315))
            series.write(prior/'result.json',saved);output=root/'output';output.mkdir()
            with patch.object(series,'ROOT',root),patch.object(series.subprocess,'run',side_effect=AssertionError('no live call')):
                series.image_evidence(output,'cython',dict(image='unit-image',task_id=CYTHON))
                saved['image_preflight']['platform']='linux/arm64';series.write(prior/'result.json',saved)
                with self.assertRaises(AssertionError):series.image_evidence(output,'cython',dict(image='unit-image',task_id=CYTHON))
            self.assertEqual(series.read(output/'image-evidence.json')['status'],'PASS')

    def test_version_drift_keeps_raw_times_but_excludes_pair_from_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp);runs=[]
            for i,((pair,mode),seconds) in enumerate(zip(series.ORDER,(100,110,90,100))):
                runs.append(dict(pair=pair,mode=mode,status='PASS',actions=83,cgroup=str(i),guest_id=str(i),source_identity=dict(source_commit='a'*40,Cython='3'),timings_seconds={f:seconds for f in ('tool_sum','tool_span','replay_cli','cold_create','controller')}))
            series.write(output/'plan.json',dict(task='cython'));series.write(output/'runs.json',runs)
            value=series.summarize(output);self.assertEqual(value['comparable_pairs'],2)
            self.assertAlmostEqual(value['pairs'][1]['timings']['tool_sum']['change_percent'],-10)
            runs[1]['source_identity']['Cython']='4';series.write(output/'runs.json',runs)
            value=series.summarize(output);self.assertEqual(value['comparable_pairs'],1)
            self.assertIsNone(value['pairs'][0]['timings']['tool_sum']['change_percent']);self.assertEqual(value['pairs'][0]['timings']['tool_sum']['on'],110)
            self.assertEqual(value['summary']['tool_sum']['on_mean_seconds'],90)
            runs[2]['source_identity']['source_commit']='b'*40;series.write(output/'runs.json',runs)
            value=series.summarize(output);self.assertEqual(value['comparable_pairs'],0);self.assertIsNone(value['summary']['tool_sum']['on_mean_seconds'])

    def test_cython_report_selects_correct_title_and_publishes_comparability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();output=root/'result';evidence=root/'docs/records/evidence';evidence.mkdir(parents=True)
            workflow=root/'20261031T000000Z-unit';workflow.mkdir();series.write(workflow/'config.json',dict(revision='unit-revision'))
            series.write(workflow/'result.json',dict(report=str(root/'plots')))
            series.write(source/'result.json',dict(status='PASS',source_after=dict(running=False)))
            runs=[]
            for i,(pair,mode) in enumerate(series.ORDER):
                raw=root/f'raw-{i}';raw.mkdir();(raw/'tools.tsv').write_text('fixture only\n')
                runs.append(dict(index=i+1,pair=pair,mode=mode,actions=83,timings_seconds=dict(tool_sum=1,cold_create=2,controller=3),sampling_cost=None,memory='NOT_MEASURED',minimum_available_gib=70,cleanup='PASS',workflow=str(workflow),raw=str(raw),source_identity=dict(source_commit='a'*40),network_preflight_seconds=1))
                if i==1:runs[-1]['call_timing']=dict(version=2,replay=dict(command_duration_ns=17))
            series.write(source/'runs.json',runs)
            with patch.object(report,'ROOT',root),patch.object(report,'summarize',return_value=dict(task_id=CYTHON,comparable_pairs=1)),patch.object(report,'timings_plot') as plot,patch.object(report,'host_task_plot'),patch.object(report.shutil,'copy2'):
                value=report.report(source,output)
            self.assertEqual(plot.call_args.args[2:],('Cython extensions',83))
            self.assertEqual(value['run_details'][0]['source_identity']['source_commit'],'a'*40)
            self.assertEqual(value['comparable_pairs'],1)
            self.assertNotIn('call_timing',value['run_details'][0])
            self.assertEqual(value['run_details'][1]['call_timing'],runs[1]['call_timing'])

    def test_series_dispatches_four_cython_runs_in_original_order_with_existing_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg,_=fixtures.fixture(root);configs=root/'configs';configs.mkdir()
            (configs/'external-cython-light.json').write_text(cfg.read_text());output=root/'.artifacts/controls';launched=[]
            def run(command,**kw):
                if command[:2]==['pgrep','-x']:return types.SimpleNamespace(returncode=1)
                if command[0]=='bash':
                    self.assertEqual(kw['timeout'],1500);self.assertEqual(Path(command[2]).name,'external-cython-light.json')
                    launched.append(command[-1]);workflow=root/'.artifacts/host-external-workload'/str(len(launched))
                    kw['stdout'].write('status=PASS result_dir='+str(workflow)+'\n')
                return types.SimpleNamespace(returncode=0)
            def inspect(workflow,mode,upstream,profile):
                self.assertEqual(profile,'cython');index=len(launched)
                return dict(status='PASS',mode=mode,cgroup=str(index),guest_id=str(index),source_identity=dict(source_commit='a'*40),timings_seconds={f:100+index for f in ('tool_sum','tool_span','replay_cli','cold_create','controller')})
            opener=types.SimpleNamespace(open=lambda *a,**k:contextlib.nullcontext(types.SimpleNamespace(status=200)))
            with patch.object(series,'ROOT',root),patch.object(sys,'argv',['controls','--task','cython','--upstream',str(root),'--output',str(output)]),patch.object(series,'source_state',return_value=dict(running=False)),patch.object(series,'available',return_value=80*2**30),patch.object(series,'image_evidence'),patch.object(series.shutil,'copy2'),patch.object(series.subprocess,'check_output',return_value=''),patch.object(series.subprocess,'run',side_effect=run),patch.object(series.urllib.request,'build_opener',return_value=opener),patch.object(series,'inspect_run',side_effect=inspect),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(series.main(),0)
            self.assertEqual(launched,['off','on','on','off'])
            self.assertEqual(series.read(output/'plan.json')['total_actions'],332)
            self.assertEqual(series.read(output/'result.json')['runs_completed'],4)


if __name__=='__main__':unittest.main()
