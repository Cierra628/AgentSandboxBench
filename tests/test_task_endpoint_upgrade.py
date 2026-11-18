"""Opt-in upgrade routing and precise call timing; synthetic calls only."""
import ast
import contextlib
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import platform_tasks as platform
import run_external_workload as external
from external_workload import C4,load
import test_external_workload as fixtures


class EndpointUpgradeTests(unittest.TestCase):
    def test_python_configs_opt_in_without_changing_old_commands(self):
        for profile in ('sphinx7590','xarray6992'):
            old=platform.configuration(ROOT/f'configs/platform-{profile}-host-current.json')
            new=platform.configuration(ROOT/f'configs/platform-{profile}-endpoint-metrics.json')
            self.assertEqual(new,dict(old,task_endpoint_metrics=True))
            check,live=platform.commands(new,Path('/fixture'));a,b=platform.commands(old,Path('/fixture'))
            self.assertEqual([x for x in check if x!='--endpoint-metrics'],a)
            self.assertEqual([x for x in live if x!='--task-endpoint-metrics'],b)
            for change in ({'monitor_mode':'off','light_monitor':False},{'task_endpoint_metrics':1},{'host_current_only':False}):
                with tempfile.TemporaryDirectory() as tmp:
                    p=Path(tmp)/'config.json';p.write_text(json.dumps(dict(new,**change)))
                    with self.assertRaises(ValueError):platform.configuration(p)

    def test_python_call_end_precedes_logging_and_default_retains_old_fields(self):
        tree=ast.parse((ROOT/'scripts/run_xarray6992.py').read_text())
        run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run')
        node=next(n for n in run.body if isinstance(n,ast.FunctionDef) and n.name=='call')
        wrapper=ast.parse('def exercise():\n event_counter=0\n return []').body[0]
        wrapper.body[1:]=[node,*ast.parse("call('fixture',['never-executed']); return events").body]
        ast.fix_missing_locations(wrapper)
        code=compile(ast.Module(body=[wrapper],type_ignores=[]),'<actual timing function>','exec')
        for enabled in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                ns=dict(out=Path(tmp),ROOT=ROOT,env={},endpoint=enabled,time=time,json=json,event_lock=threading.Lock(),events=[],subprocess=SimpleNamespace(PIPE=subprocess.PIPE,run=lambda *a,**k:SimpleNamespace(stdout='fixture',returncode=0)))
                exec(code,ns);event=ns['exercise']()[0]
                self.assertEqual('command_end_monotonic_ns' in event,enabled)
                if enabled:self.assertLessEqual(event['command_end_monotonic_ns'],event['start_monotonic_ns']+event['duration_ns'])

    def test_c4_upgraded_controller_keeps_original_validation_and_stops_collectors(self):
        for fault in (None,'endpoint','output','off','parallel-off','parallel-on'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);config,_=fixtures.ExternalWorkloadTests().fixture(root,C4);cfg=json.loads(config.read_text());cfg.update(repository='fixture',revision='unit-revision',image='unit-image',validation_scope='original C4 checks');config.write_text(json.dumps(cfg))
                clock=root/'experiments/container-screening/harness/clock_ns.c';clock.parent.mkdir(parents=True);clock.write_text('/* mock */')
                args=SimpleNamespace(config=config,upstream=root,output=root/'out',config_home=root/'cfg',service_url='http://127.0.0.1:1',container='fixture',endpoint_metrics=fault not in ('off','parallel-off'),sampling_control=True,concurrent_c4=fault in ('parallel-off','parallel-on'),sampling='off' if fault in ('off','parallel-off') else 'on',**({'vmm_pss_interval_seconds':2} if fault=='parallel-on' else {}))
                closed=[];intervals=[]
                class Monitor:
                    def __init__(self,*a,**kw):self.active={}
                    def start(self,sid,*a):self.active[sid]=True
                    def stop(self,sid,*a):self.active.pop(sid)
                class Metrics:
                    def __init__(self,*a,**kw):self_interval=kw['pss_interval_seconds'];intervals.append(self_interval)
                    def observe(self):
                        if fault=='endpoint':raise PermissionError('fixture PSS denied')
                        return dict(status='OBSERVED')
                    def stop(self):closed.append(True);return dict(status='PASS',pss_samples=1)
                def fake(argv,**kw):
                    argv=list(map(str,argv));stdout='';self.assertEqual(kw['env']['HTTP_PROXY'],'')
                    if argv[0]=='git':stdout='unit-revision'
                    elif 'agentenv_cold_start.py' in argv[1]:self.assertIn('--clean-guest-proxy',argv);stdout='sid'
                    elif 'download' in argv:
                        p=args.output/'input';p.mkdir();(p/'logs').mkdir();_,_,expected=load(config,root)
                        with (p/'tools.tsv').open('w') as f:
                            w=csv.DictWriter(f,fieldnames=['step','start_monotonic_ns','end_monotonic_ns','duration_ns','exit_code'],delimiter='\t');w.writeheader()
                            for i,r in enumerate(expected,1):w.writerow(dict(step=i,start_monotonic_ns=i*100,end_monotonic_ns=i*100+50,duration_ns=50,exit_code=r['exit_code']))
                        (p/'logs/033.stdout').write_text('bad' if fault=='output' else 'CONSTRAINT VIOLATIONS: NONE\nFINAL VERDICT: BYTE-EXACT ROUNDTRIP ON c4_sample\n')
                        (p/'logs/034.stdout').write_text('9898 files, structure + content\n')
                    elif 'list' in argv:
                        self.assertFalse(args.concurrent_c4);stdout='[]'
                    elif 'chmod 755' in argv[-1]:stdout='100'
                    return SimpleNamespace(returncode=0,stdout=stdout)
                with patch.object(external.subprocess,'run',side_effect=fake),patch.object(external,'RuntimeMonitor',Monitor),patch.object(external,'ExternalMetrics',Metrics),patch.dict(os.environ,HTTP_PROXY='http://unused.invalid:17891'),contextlib.redirect_stdout(io.StringIO()):code=external.run(args)
                v=json.loads((args.output/'result.json').read_text());self.assertEqual(code,0 if fault in (None,'off','parallel-off','parallel-on') else 1,v);self.assertEqual(bool(closed),fault not in ('off','parallel-off'))
                if fault in ('off','parallel-off'):
                    self.assertEqual(v['memory'],'NOT_MEASURED');self.assertNotIn('endpoint_metrics',v);self.assertGreater(v['schedule_elapsed_ns'],0)
                self.assertTrue(v['cleanup']['sandbox_removed'])
                if closed:self.assertEqual(intervals,[2 if fault=='parallel-on' else 1])
                if fault=='parallel-on':self.assertIn('every 2s',v['measurement'])
                if fault is None:self.assertEqual(v['roundtrip_validation'],'PASS');self.assertEqual(v['functional_validation'],'PASS');self.assertEqual(v['measurement_status'],'PASS')


if __name__=='__main__':unittest.main()
