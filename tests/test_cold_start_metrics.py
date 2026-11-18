"""Offline metric text, mocked HTTP and fake seven-guest commands only."""
import itertools
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import cold_start_metrics as metrics
import cold_start_sampling as runner
from run_startup_sampling_controls import experiment_orders,summarize_metric_pair


def fixture(count=7):
    lines=[]
    def hist(name,labels,seconds):
        for suffix,value in [('_count',count),('_sum',seconds)]:lines.append(f'{name}{suffix}{{{labels}}} {value}')
    for stage,seconds in [('resolve_rootfs',21),('resolve_attached_drives',.01),('create_sandbox',7)]:
        labels=f'status="ok",stage="{stage}",operation="create_cold"'
        hist(metrics.STAGE,labels,seconds)
        lines.append(f'{metrics.INFLIGHT}{{stage="{stage}",operation="create_cold"}} 0')
    hist(metrics.IMAGE,'status="ok",stage="manifest_fetch"',20)
    hist(metrics.HTTP,'route="/sandboxes-cold",status="2xx",method="POST",route_source="control_plane"',29)
    lines.append(f'{metrics.CACHE}{{result="hit",registry="example",image_format="oci"}} {count}')
    return '\n'.join(lines)+'\n'


class ColdStartMetricsTests(unittest.TestCase):
    def test_new_series_baseline_and_nested_stage_meaning(self):
        value=metrics.compare('# no cold events yet\n',fixture())
        self.assertEqual(value['stages']['resolve_rootfs'],dict(count=7,seconds=21,other_status_counts={}))
        self.assertAlmostEqual(value['http_other_processing_seconds'],.99)
        self.assertEqual(value['rootfs_other_than_manifest_seconds'],1)
        self.assertEqual(value['image_stages']['layer_convert']['status'],'NO_RECORDED_EVENT')

    def test_existing_baseline_subtracted_and_label_order_irrelevant(self):
        a=fixture(7);b=fixture(14)
        # Existing sums remain constant: 7 new zero-duration events are valid text.
        value=metrics.compare(a,b)
        self.assertEqual(value['http']['count'],7);self.assertEqual(value['http']['seconds'],0)
        self.assertEqual(metrics.parse('x 123\n'),{})

    def test_missing_final_required_series_and_wrong_counts_rejected(self):
        for text in ('',fixture(6),fixture().replace('resolve_rootfs','unrelated')):
            with self.subTest(text=text[:20]),self.assertRaises(ValueError):metrics.compare('',text)

    def test_duplicate_malformed_and_nonfinite_selected_metrics_rejected(self):
        line=fixture().splitlines()[0]
        for text in (line+'\n'+line,line.replace(' 7',' NaN'),line.replace(' 7',' -1'),line.replace('status="ok"','status=ok')):
            with self.subTest(text=text),self.assertRaises(ValueError):metrics.parse(text)

    def test_counter_reset_or_disappearance_rejected(self):
        with self.assertRaisesRegex(ValueError,'decreased'):metrics.compare(fixture(8),fixture(7))
        with self.assertRaisesRegex(ValueError,'disappeared'):metrics.compare(fixture(),fixture().replace('registry="example"','registry="different"'))

    def test_errors_and_cancellations_not_hidden_by_seven_successes(self):
        for status in ('error','canceled'):
            text=fixture()+f'{metrics.IMAGE}_count{{stage="manifest_fetch",status="{status}"}} 1\n'
            with self.assertRaisesRegex(ValueError,'error/cancellation|scope/count'):metrics.compare('',text)

    def test_inflight_baseline_and_final_must_be_empty(self):
        with self.assertRaises(ValueError):metrics.compare('',fixture().replace('} 0','} 1',1))
        baseline=f'{metrics.INFLIGHT}{{stage="resolve_rootfs",operation="create_cold"}} 1\n'
        with self.assertRaisesRegex(ValueError,'baseline'):metrics.compare(baseline,fixture())

    def test_capture_local_http_is_bounded_and_persists_actual_response(self):
        with tempfile.TemporaryDirectory() as name:
            out=Path(name);response=Mock(status=200);response.read.return_value=fixture().encode();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
            opener=Mock();opener.open.return_value=response
            with patch.object(metrics,'server_identity',return_value={'pid':1}),patch.object(metrics.urllib.request,'build_opener',return_value=opener):
                value=metrics.capture('http://127.0.0.1:1234','owned',out,'before')
            self.assertEqual(value['status'],'PASS');self.assertEqual((out/value['raw_file']).read_text(),fixture())
            opener.open.assert_called_once_with('http://127.0.0.1:1234/metrics',timeout=3)
            self.assertGreaterEqual(value['duration_ns'],value['get_duration_ns'])

    def test_capture_failure_and_unowned_url_never_fill_zero(self):
        with tempfile.TemporaryDirectory() as name:
            out=Path(name)
            with patch.object(metrics,'server_identity') as identity:
                value=metrics.capture('https://remote.example','owned',out,'before')
                identity.assert_not_called()
            self.assertEqual(value['status'],'FAIL');self.assertNotIn('response_bytes',value)
            with self.assertRaisesRegex(ValueError,'snapshot missing'):metrics.evaluate(out,value,value)

    def test_process_change_and_snapshot_overlap_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            out=Path(name);(out/'before.prom').write_text('');(out/'after.prom').write_text(fixture())
            before=dict(status='PASS',server_identity={'pid':1},raw_file='before.prom',start_monotonic_ns=1,end_monotonic_ns=2,get_duration_ns=1,duration_ns=1)
            after=dict(before,raw_file='after.prom',start_monotonic_ns=10,end_monotonic_ns=11)
            value=dict(server_metrics=metrics.evaluate(out,before,after),creation_begin_monotonic_ns=3,cold_begin_monotonic_ns=4,cold_end_monotonic_ns=8,all_creation_and_sampler_setup_returned_ns=9,postcheck_begin_monotonic_ns=12)
            self.assertEqual(metrics.audit(out,value)['status'],'PASS')
            value['cold_end_monotonic_ns']=20
            with self.assertRaisesRegex(ValueError,'overlaps'):metrics.audit(out,value)
            after['server_identity']={'pid':2}
            with self.assertRaisesRegex(ValueError,'process changed'):metrics.evaluate(out,before,after)

    def test_only_explicit_option_changes_six_runs_to_one_pair(self):
        self.assertEqual(experiment_orders(),[['off','on'],['on','off'],['off','on']])
        self.assertEqual(experiment_orders(True),[['off','on']])
        runs=[dict(mode=mode,status='PASS',actual_actions=0,created_guests=7,server_metrics={'status':'PASS'},cold_span_ns=10 if mode=='off' else 8) for mode in ('off','on')]
        self.assertEqual(summarize_metric_pair(runs)['cold_span']['difference_ns'],-2)
        with self.assertRaises(ValueError):summarize_metric_pair(runs[::-1])

    def test_normal_validator_rejects_new_option_before_allocating(self):
        import validate_isolated_agentenv as validator
        with patch.object(validator.os,'geteuid',return_value=0),patch.object(validator.os,'posix_fallocate') as allocate:
            with self.assertRaisesRegex(ValueError,'explicit startup'):validator.main(SimpleNamespace(startup_server_metrics=True))
            allocate.assert_not_called()

    def fake_run(self,requested,after_fails=False):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);scripts=root/'scripts';scripts.mkdir();(root/'configs').mkdir()
            (root/'configs/prettier6604-backbone.json').write_text('{"source_image":"fixture"}')
            for script in ['cold_start_sampling.py','agentenv_cold_start.py','sandbox_runtime_monitor.py','guest_memory_collector.py','cold_start_metrics.py']:
                shutil.copy2(ROOT/'scripts'/script,scripts/script)
            out=root/'.artifacts/storage-isolated-service/run/mount/controller/prettier6604';out.parent.mkdir(parents=True)
            seq=itertools.count();lists=itertools.count();calls=[];snapshots=[]
            def execute(argv,**kwargs):
                argv=list(map(str,argv));calls.append(argv)
                if any(s.endswith('/agentenv_cold_start.py') for s in argv):stdout=f'00000000-0000-0000-0000-{next(seq):012d}'
                elif argv[1]=='list':stdout=json.dumps(list(range(7)) if next(lists)==1 else [])
                elif argv[1]=='exec':stdout=argv[2]
                else:stdout=''
                return SimpleNamespace(stdout=stdout,returncode=0)
            def capture(url,container,directory,label):
                snapshots.append((label,len(calls)))
                return dict(status='FAIL' if after_fails and label=='after' else 'PASS',label=label)
            def evaluate(*args):
                if after_fails:raise ValueError('diagnostic unavailable')
                return {'status':'PASS','snapshots':list(args[1:])}
            binding=Mock();binding.resolve.side_effect=lambda sid:dict(pid=int(sid[-12:])+1,start_ticks='1')
            args=SimpleNamespace(output=out,config_home=root/'config',service_url='http://127.0.0.1:1',container='owned',mode='off',server_metrics=requested)
            with patch.object(runner,'ROOT',root),patch.object(runner.subprocess,'run',side_effect=execute),patch('host_sandbox_monitor.Binding',return_value=binding),patch.object(metrics,'capture',side_effect=capture),patch.object(metrics,'evaluate',side_effect=evaluate):
                code=runner.run(args)
            result=json.loads((out/'result.json').read_text())
            self.assertTrue(result['cleanup']['service_sandbox_list_empty']);self.assertEqual(len(result['cleanup']['guests']),7)
            self.assertTrue(all(row['deleted'] for row in result['cleanup']['guests']))
            if requested:
                self.assertEqual(snapshots,[('before',1),('after',8)])
                self.assertEqual(result['functional_status'],'PASS')
            else:self.assertEqual(snapshots,[]);self.assertNotIn('server_metrics',result)
            return code,result

    def test_existing_startup_mode_does_not_query_metrics(self):self.assertEqual(self.fake_run(False)[0],0)
    def test_new_path_has_two_reads_outside_creates(self):self.assertEqual(self.fake_run(True)[0],0)
    def test_after_query_failure_still_checks_and_deletes_guests(self):
        code,result=self.fake_run(True,after_fails=True)
        self.assertEqual(code,1);self.assertEqual(result['server_metrics']['status'],'FAIL')


if __name__=='__main__':unittest.main()
