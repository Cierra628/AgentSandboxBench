"""Local counters and saved logs only; no sandbox services or devices."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from platform_tasks import configuration, commands
from sandbox_runtime_monitor import RuntimeMonitor, MEMFIELDS
from report_task_overview import write_report, write_tool_report


class LightTaskMetricsTests(unittest.TestCase):
    def test_three_plain_tasks_default_to_light_but_diagnostics_keep_full(self):
        for task in ('prettier6604', 'sphinx7590', 'xarray6992'):
            cfg=configuration(ROOT/f'configs/platform-{task}.json')
            self.assertTrue(cfg['light_monitor'])
            check, live=commands(cfg, Path('/unused'))
            self.assertIn('--light-monitor', check); self.assertIn('--light-monitor', live)
            diagnostic=configuration(ROOT/f'configs/platform-{task}-live-cache.json')
            self.assertFalse(diagnostic['light_monitor'])
            self.assertNotIn('--light-monitor', commands(diagnostic, Path('/unused'))[1])

    def test_explicit_full_mode_remains_available(self):
        cfg=json.loads((ROOT/'configs/platform-prettier6604.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'cfg.json'
            path.write_text(json.dumps(dict(cfg, light_monitor=False)))
            self.assertNotIn('--light-monitor', commands(configuration(path), Path('/unused'))[1])
            path.write_text(json.dumps(dict(cfg, light_monitor=1)))
            with self.assertRaises(ValueError): configuration(path)

    def test_three_monitor_off_configs_keep_timing_and_diagnostic_policies(self):
        for task in ('prettier6604','sphinx7590','xarray6992'):
            cfg=json.loads((ROOT/f'configs/platform-{task}.json').read_text())
            with tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'cfg.json';path.write_text(json.dumps(dict(cfg,monitor_mode='off')))
                effective=configuration(path)
                self.assertFalse(effective['light_monitor'])
                check,live=commands(effective,Path('/unused'))
                self.assertIn('--monitor-mode',check);self.assertNotIn('--light-monitor',live)
                self.assertIn('--prettier6604-monitor-mode' if task=='prettier6604' else '--python-monitor-mode',live)
                checked=subprocess.run(check,capture_output=True,text=True,timeout=30)
                self.assertEqual(checked.returncode,0,checked.stderr)
                path.write_text(json.dumps(dict(cfg,monitor_mode='off',light_monitor=True)))
                with self.assertRaises(ValueError):configuration(path)
        cfg=json.loads((ROOT/'configs/platform-xarray6992-live-cache.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'cfg.json';path.write_text(json.dumps(dict(cfg,monitor_mode='off')))
            with self.assertRaises(ValueError):configuration(path)

    def test_off_report_retains_failures_and_rejects_memory_samples(self):
        with tempfile.TemporaryDirectory() as temp:
            raw=Path(temp)/'raw';raw.mkdir();output=Path(temp)/'report';output.mkdir()
            (raw/'result.json').write_text(json.dumps(dict(status='PASS',task_id='fixture',monitor_mode='off',
                cleanup=dict(monitoring_disabled=True),official_task_status='FAIL',strict_replay_status='FAIL',guest_action_sum_ns=2)))
            (raw/'segments.tsv').write_text('rollout\tsegment\tsandbox_id\nbackbone\tread_repo\towned\n')
            segment=raw/'rollouts/backbone/read_repo';segment.mkdir(parents=True)
            (segment/'steps.tsv').write_text('step\texit_code\tduration_ns\n001\t141\t2\n')
            for name in ('guest-memory.tsv','guest-vmstat.tsv'):(segment/name).write_text('timestamp_ns\n')
            result=write_tool_report(raw,output)
            self.assertEqual(result['memory'],'NOT_MEASURED')
            self.assertEqual(result['official_task_status'],'FAIL');self.assertEqual(result['tool_duration_sum_ns'],2)
            self.assertFalse((output/'memory.tsv').exists())
            (segment/'guest-memory.tsv').write_text('timestamp_ns\n1\n')
            with self.assertRaisesRegex(AssertionError,'boundary samples'):write_tool_report(raw,output)

    def test_xarray_off_diagnostic_keeps_exit_checks(self):
        import hashlib
        from xarray6992_inputs import verify_diagnostic_replay
        with tempfile.TemporaryDirectory() as temp:
            raw=Path(temp);segment=raw/'rollouts/backbone/read_repo';segment.mkdir(parents=True)
            manifest=raw/'frozen-input/segments/read_repo';manifest.mkdir(parents=True)
            command='true';digest=hashlib.sha256(command.encode()).hexdigest()
            text=f'step\tcommand\tsha256\n001\t{command}\t{digest}\n'
            (manifest/'actions.tsv').write_text(text);(segment/'actions.tsv').write_text(text)
            (segment/'steps.tsv').write_text('branch\tround\tstep\texit_code\tduration_ns\nbackbone\tread_repo\t001\t0\t2\n')
            (segment/'stdout').mkdir();(segment/'stdout/001.log').write_text('')
            (segment/'guest-memory.tsv').write_text('timestamp_ns\n');(segment/'patch.diff').write_text('')
            cfg=dict(input_sha256={'segments/read_repo/actions.tsv':hashlib.sha256(text.encode()).hexdigest()},expected_exit_codes={'read_repo':[0]})
            result=verify_diagnostic_replay(raw,cfg,['read_repo'],sampling=False)
            self.assertEqual(result['action_count'],1);self.assertIsNone(result['guest_observed_peak_bytes'])
            with self.assertRaises(AssertionError):verify_diagnostic_replay(raw,cfg,['read_repo'])
            (segment/'steps.tsv').write_text('branch\tround\tstep\texit_code\tduration_ns\nbackbone\tread_repo\t001\t1\t2\n')
            with self.assertRaises(AssertionError):verify_diagnostic_replay(raw,cfg,['read_repo'],sampling=False)

    def test_controls_summary_preserves_signed_paired_changes(self):
        from run_light_monitor_controls import summarize,TASKS
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp);runs=[]
            for task in TASKS:
                for number,on in enumerate((9,11,13),1):
                    for mode,value in (('off',10),('on',on)):
                        runs.append(dict(task=task,round=number,mode=mode,patch='same',original_tests={'failed':1},
                            tool_seconds=value,frozen_replay_seconds=value,controller_seconds=value))
            (out/'runs.json').write_text(json.dumps(runs));result=summarize(out)
            self.assertEqual(result['run_count'],18)
            metric=result['tasks'][TASKS[0]]['tool_seconds']
            self.assertAlmostEqual(metric['paired_change_median_percent'],10)
            self.assertAlmostEqual(metric['paired_change_range_percent'][0],-10)
            runs[-1]['patch']='changed';(out/'runs.json').write_text(json.dumps(runs))
            with self.assertRaises(AssertionError):summarize(out)

    def test_meminfo_only_collector_and_three_metric_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); raw=root/'raw'; raw.mkdir(); proc=root/'proc'; proc.mkdir()
            # Any vmstat/CPU/I/O read would fail: only this fixture exists.
            values={key:10 for key in MEMFIELDS}; values.update(MemTotal=4096, MemAvailable=1024, Cached=512, Shmem=128)
            (proc/'meminfo').write_text(''.join(f'{k}: {v} kB\n' for k,v in values.items()))
            calls=[]
            def cli(label, *argv, timeout=15):
                import shutil
                calls.append((label, argv))
                if argv[0]=='upload':return ''
                if argv[0]=='download':
                    shutil.copytree(argv[2], Path(argv[3])/Path(argv[2]).name); return ''
                command=list(argv[2:])
                if 'collect' in command: command+=['--proc-root', str(proc)]
                return subprocess.check_output(command, text=True, timeout=timeout).strip()
            with patch('sandbox_runtime_monitor.GUEST_ROOT', str(root/'guest')), patch('sandbox_runtime_monitor.SCRIPT', str(ROOT/'scripts/sandbox_runtime_monitor.py')):
                monitor=RuntimeMonitor(raw, cli, container='must-not-contact-docker', light=True)
                try:
                    monitor.start('owned-test', 'backbone')
                    time.sleep(.35)
                finally:
                    if monitor.active:monitor.stop('owned-test')
            manifest=json.loads((raw/'runtime-monitor.json').read_text())
            self.assertEqual(manifest['profile'], 'light'); self.assertEqual(manifest['interval_seconds'], .1)
            self.assertFalse(manifest['host_vmm_requested']); self.assertIsNone(manifest['guest_performance_version'])
            session=manifest['sessions'][0]; directory=raw/session['raw_directory']
            self.assertTrue(session['collector_reaped']); self.assertEqual(session['receipt']['profile'], 'light')
            self.assertFalse((directory/'guest-vmstat.tsv').exists())
            self.assertFalse((directory/'guest-cpu.tsv').exists()); self.assertFalse((directory/'guest-diskstats.tsv').exists())
            self.assertFalse((raw/'host-vmm').exists())
            self.assertEqual(len(list((raw/'runtime-code').glob('*.py'))), 2)
            self.assertEqual(sum(label=='monitor-upload-bundle' for label,_ in calls), 1)
            self.assertFalse(any(label in ('monitor-upload','monitor-directory','monitor-reaped') for label,_ in calls))
            self.assertEqual(sum(label=='monitor-finish' for label,_ in calls),1)
            self.assertFalse(Path('/proc') .joinpath(str(session['receipt']['pid'])).exists())
            (raw/'result.json').write_text(json.dumps(dict(status='PASS', task_id='fixture',
                official_task_status='FAIL', strict_replay_status='FAIL', guest_action_sum_ns=2000000)))
            (raw/'segments.tsv').write_text('rollout\tsegment\tsandbox_id\nbackbone\tread_repo\towned-test\n')
            segment=raw/'rollouts/backbone/read_repo'; segment.mkdir(parents=True)
            (segment/'steps.tsv').write_text('step\texit_code\tduration_ns\n001\t141\t2000000\n')
            # An 80-second wall-clock correction must not alter elapsed time.
            memory_file=directory/'guest-memory.tsv'
            with memory_file.open() as stream:data=list(csv.DictReader(stream, delimiter='\t'))
            self.assertGreaterEqual(len(data),4)
            self.assertLess(int(data[1]['monotonic_ns'])-int(data[0]['monotonic_ns']),300_000_000)
            for row in data[1:]:row['timestamp_ns']=str(int(row['timestamp_ns'])+80_000_000_000)
            with memory_file.open('w') as stream:
                writer=csv.DictWriter(stream, fieldnames=list(data[0]), delimiter='\t')
                writer.writeheader();writer.writerows(data)
            output=root/'report'; output.mkdir()
            from report_sandbox_runtime import write_report as dispatch
            report=dispatch(raw, output)
            self.assertEqual(report['tool_count'], 1); self.assertEqual(report['tool_duration_sum_ns'], 2000000)
            self.assertEqual(report['official_task_status'], 'FAIL'); self.assertEqual(report['strict_replay_status'], 'FAIL')
            self.assertEqual(report['per_sandbox'][0]['memory_observed_peak_bytes'], 3072*1024)
            self.assertEqual(report['per_sandbox'][0]['cached_observed_peak_bytes'], 512*1024)
            self.assertTrue((output/'overview.png').exists()); self.assertFalse((output/'input-sha256.json').exists())
            with (output/'tools.tsv').open() as stream:
                self.assertEqual(next(csv.DictReader(stream, delimiter='\t'))['exit_code'], '141')
            with (output/'memory.tsv').open() as stream:display=list(csv.DictReader(stream, delimiter='\t'))
            self.assertLess(float(display[-1]['elapsed_seconds'])-float(display[0]['elapsed_seconds']), 3)
            # A gap must produce two plotted chunks, even at the 1-second cadence.
            from report_sandbox_runtime import curve_chunks
            data=[dict(timestamp_ns=str(n), monotonic_ns=str(n)) for n in (0, 10**9, 5*10**9)]
            self.assertEqual([len(c) for c in curve_chunks(data, 1500000000)], [2, 1])
            data[1]['timestamp_ns']=str(81*10**9);data[2]['timestamp_ns']=str(85*10**9)
            self.assertEqual([len(c) for c in curve_chunks(data, 1500000000, clock='monotonic_ns')], [2, 1])
            (raw/'result.json').write_text(json.dumps(dict(status='PASS', guest_action_sum_ns=3)))
            with self.assertRaisesRegex(AssertionError, 'timing total'):
                write_report(raw, root/'unused')


if __name__=='__main__':unittest.main()
