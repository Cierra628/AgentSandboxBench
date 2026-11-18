"""Pure saved-file/mock checks; no services, devices, real cgroups or network."""
import contextlib
from datetime import datetime,timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_creation_controls as controls
import validate_isolated_agentenv as validator


class HostCreationTests(unittest.TestCase):
    def fixture_run(self,fault=None):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);mount=root/'mount';log=mount/'env/logs/server.log';log.parent.mkdir(parents=True);log.write_text('')
            cfg=root/'config.json';cfg.write_text(json.dumps(dict(task_id=controls.DJANGO,memory_mib=8192,image='fixed-image')))
            args=types.SimpleNamespace(config=cfg,output=root/'output',mount=mount,config_home=root/'credentials',service_url='http://127.0.0.1:1')
            active=[];commands=[];created=[];collectors=[]
            class Collector:
                def __init__(self,path,group,*,include_pss):
                    self.path=path;self.asserted=not include_pss;path.mkdir(parents=True);collectors.append(self)
                def stop(self):return dict(status='FAIL' if fault=='collector' else 'PASS',samples=2,thread_cpu_ns=1,counter_read_total_ns=1)
            def command(argv,**kwargs):
                argv=list(map(str,argv));commands.append(argv);stdout='';code=0
                if 'agentenv_cold_start.py' in argv[2]:
                    if fault=='create' and len(created)==1:return types.SimpleNamespace(returncode=1,stdout='')
                    sid=f'00000000-0000-0000-0000-{len(created)+1:012d}';created.append(sid);active.append(sid)
                    time.sleep(.001)
                    now=time.time_ns()
                    stamp=lambda ns:datetime.fromtimestamp(ns/1e9,timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
                    part=''
                    if len(created)==1 or fault=='conversion' and len(created)==2:part+=stamp(now-400000)+' INFO '+controls.COPY_MARKER+'\n'
                    if not (fault=='cache-evidence' and len(created)==2):part+=stamp(now-300000)+' INFO image resolved to overlaybd config\n'
                    if fault=='cache-evidence':log.with_name('ublk-daemon.log').write_text('')
                    part+=stamp(now-200000)+' INFO creating sandbox timeout=Some(1800s) sandbox_id='+sid+'\n'
                    part+=stamp(now-100000)+' INFO sandbox launch completed sandbox_id='+sid+'\n'
                    with log.open('a') as f:f.write(part)
                    stdout=sid
                elif 'delete' in argv:active.remove(argv[-1])
                elif 'list' in argv:stdout=json.dumps(active)
                else:self.assertEqual(argv[:2],['sync','-f'])
                return types.SimpleNamespace(returncode=code,stdout=stdout)
            with patch.object(controls,'measurement_group',return_value=(root/'group','unit')),patch.object(controls,'counters',return_value={'current':1,'file_dirty':0}),patch.object(controls,'HostFileMonitor',Collector),patch.object(controls.subprocess,'run',side_effect=command),contextlib.redirect_stdout(io.StringIO()):
                code=controls.run(args)
            result=json.loads((args.output/'result.json').read_text());events=json.loads((args.output/'events.json').read_text())
            trace=args.output/'preparation-processes.jsonl'
            self.assertTrue(trace.exists());self.assertFalse(any(t.name=='image-preparation-trace' for t in threading.enumerate()))
            self.assertFalse(active);self.assertTrue(result['cleanup']['known_guests_deleted']);self.assertTrue(result['cleanup']['service_sandbox_list_empty'])
            if fault=='cache-evidence':
                raw=json.loads((args.output/'run-1-off-observation.json').read_text())
                self.assertEqual(raw['creation']['exit_code'],0);self.assertEqual(raw['baseline']['current'],1)
                self.assertNotIn('log_phases',raw)
            self.assertFalse(any(x in ('exec','upload','download') for command in commands for x in command))
            self.assertEqual(sum(e['stage'].endswith('-sync') for e in events),len(created)+(fault=='create'))
            for event in events:
                self.assertEqual(event['duration_ns'],event['end_monotonic_ns']-event['start_monotonic_ns'])
                self.assertGreaterEqual(event['end_timestamp_ns'],event['timestamp_ns'])
            if fault:
                self.assertEqual(code,1);self.assertEqual(result['status'],'FAIL');self.assertLess(len(created),5)
            else:
                self.assertEqual(code,0);self.assertEqual(result['actions'],0);self.assertEqual(len(created),5)
                self.assertEqual([r['mode'] for r in result['runs']],list(controls.ORDER));self.assertEqual(len(collectors),2)
                for row in result['runs']:
                    self.assertFalse(row['log_phases']['converted_layer_copy_started'])
                    if row['mode']=='off':self.assertIsNone(row['collector']['samples']);self.assertIsNone(row['collector']['thread_cpu_ns'])
            return result

    def test_one_preparation_four_creates_no_guest_commands_and_real_off(self):self.fixture_run()
    def test_missing_conversion_stops_after_first_measured_guest_and_deletes_it(self):
        r=self.fixture_run('conversion');self.assertEqual(len(r['guests']),2);self.assertEqual(r['runs'],[])
    def test_create_failure_keeps_prior_cleanup_and_does_not_continue(self):self.fixture_run('create')
    def test_collector_failure_is_not_pass_and_guest_is_deleted(self):self.fixture_run('collector')

    def test_preparation_trace_error_is_reported_and_thread_is_reaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(controls,'pids',side_effect=OSError('owned proc state unavailable')):
                trace=controls.PreparationTrace(Path(tmp)/'trace.jsonl',Path(tmp)/'group')
                with self.assertRaisesRegex(RuntimeError,'owned proc state unavailable'):trace.stop()
                self.assertFalse(trace.thread.is_alive())

    def test_incomplete_phase_logs_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'missing or duplicated'):
            controls.creation_phases('', 'sid',{},prepared=True)
        with self.assertRaisesRegex(ValueError,'missing again'):
            controls.creation_phases(controls.COPY_MARKER,'sid',{},prepared=True)

    def test_cached_creation_accepts_real_image_ready_evidence_without_fake_resolution_time(self):
        source=Path(__file__).parent/'fixtures/django-cache-hit'
        event=dict(timestamp_ns=1793378627420364142,end_timestamp_ns=1793378633453936304,duration_ns=6033571895)
        result=controls.creation_phases((source/'server.log').read_text(),'00000000-0000-0000-0000-000000000001',event,prepared=True,daemon_text=(source/'ublk.log').read_text())
        self.assertEqual(result['resolution_log'],'ABSENT_CACHE_IMAGE_CONFIRMED')
        self.assertEqual(result['phases_wall_ns']['before_creating'],5388102858)
        self.assertNotIn('before_resolved',result['phases_wall_ns']);self.assertNotIn('resolved',result['server_lines'])
        self.assertFalse(result['converted_layer_copy_started'])

    def test_missing_resolution_still_requires_matching_in_window_image_evidence(self):
        source=Path(__file__).parent/'fixtures/django-cache-hit';text=(source/'server.log').read_text();daemon=(source/'ublk.log').read_text()
        event=dict(timestamp_ns=1793378627420364142,end_timestamp_ns=1793378633453936304,duration_ns=6033571895)
        for bad in ['',daemon.replace('63e2e4d9','00000000'),daemon.replace('16:43:53.434677','16:43:54.434677'),daemon+daemon]:
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError,'matching image-ready'):controls.creation_phases(text,'00000000-0000-0000-0000-000000000001',event,prepared=True,daemon_text=bad)
        with self.assertRaisesRegex(ValueError,'resolved'):controls.creation_phases(text,'00000000-0000-0000-0000-000000000001',event,prepared=False,daemon_text=daemon)

    def test_successful_create_observation_survives_checker_failure(self):self.fixture_run('cache-evidence')

    def test_validator_flag_requires_explicit_off_before_allocation(self):
        with patch.object(validator.os,'geteuid',return_value=0),patch.object(validator.os,'posix_fallocate',side_effect=AssertionError('no device')):
            for args in [types.SimpleNamespace(external_creation_controls=True),types.SimpleNamespace(external_creation_controls=True,external_workload_config='x',external_workload_upstream='y')]:
                with self.assertRaisesRegex(ValueError,'creation controls require'):validator.main(args)


if __name__=='__main__':unittest.main()
