"""Offline interval routing and virtual-clock collection; no real proc or services."""
import ast
import csv
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import host_sandbox_monitor as monitor
import run_host_external_workload as host
import run_external_workload as external
import run_c4_parallel as parallel
from external_workload import C4


def function(name,function):
    tree=ast.parse((ROOT/'scripts'/name).read_text())
    return next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==function)


def execute(nodes,namespace):
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<actual command routing>','exec'),namespace)


class PssIntervalTests(unittest.TestCase):
    def test_interval_validation_precedes_binding_and_thread(self):
        for value in (0,-1,'nan','inf','-inf',1e-12,1e308,'bad'):
            binding=Mock()
            with self.subTest(value=value),self.assertRaises((ValueError,TypeError)):
                monitor.HostMonitor(binding,'sid','role','token',Path('/never-created'),'tools',pss_interval_seconds=value)
            binding.resolve.assert_not_called()
        for value in (1,2,'2',.5):self.assertEqual(monitor.validate_pss_interval(value),float(value))

    def test_actual_loop_keeps_point_one_counters_and_records_requested_pss(self):
        # Constructor compatibility, without starting a real sampling thread.
        with tempfile.TemporaryDirectory() as tmp,patch.object(monitor.threading,'Thread') as thread:
            with patch.object(monitor.threading.Event,'wait',return_value=True):
                obj=monitor.HostMonitor(Mock(), 'sid','role','token',Path(tmp)/'default','tools')
            self.assertEqual(obj.pss_interval_seconds,1)
            thread.return_value.start.assert_called_once()
        for interval,expected in ((1,[0,1,2,3,4]),(2,[0,2,4])):
            with self.subTest(interval=interval),tempfile.TemporaryDirectory() as tmp:
                clock=SimpleNamespace(now=0)
                class Event:
                    def is_set(self):return clock.now>=4_100_000_000
                    def wait(self,seconds):clock.now+=round(seconds*1e9)
                obj=monitor.HostMonitor.__new__(monitor.HostMonitor)
                obj.directory=Path(tmp);obj.sid='sid';obj.role='backbone';obj.token='fixture';obj.phase='tools'
                obj.vmm=dict(pid=123,start_ticks='5',boot_id='fixture');obj.binding=SimpleNamespace(owned=lambda pid:True)
                obj.stop_event=Event();obj.ready=Mock();obj.pss_interval_seconds=interval
                pss_times=[]
                def read(pid,*,pss):
                    value=dict(start_ticks='5',rss_kib=8)
                    if pss:pss_times.append(clock.now/1e9);value.update(pss_kib=4,pss_scan_duration_ns=0)
                    return value
                with patch.object(monitor,'time',SimpleNamespace(monotonic_ns=lambda:clock.now,time_ns=lambda:clock.now)),patch.object(monitor,'identity',return_value=obj.vmm),patch.object(monitor,'read',side_effect=read),patch.object(monitor.resource,'getrusage',return_value=SimpleNamespace(ru_utime=0,ru_stime=0)):
                    obj.collect()
                receipt=json.loads((obj.directory/'collector-status.json').read_text())
                self.assertEqual(receipt['status'],'PASS');self.assertEqual(receipt['pss_interval_seconds'],interval)
                self.assertEqual(receipt['interval_seconds'],.1);self.assertEqual(receipt['samples'],42)
                self.assertEqual(pss_times,expected);self.assertEqual(receipt['pss_samples'],len(expected))
                with (obj.directory/'host-vmm.tsv').open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
                self.assertTrue(all(int(b['monotonic_ns'])-int(a['monotonic_ns'])==100_000_000 for a,b in zip(rows,rows[1:])))

    def test_public_reentry_and_invalid_combinations_before_services(self):
        for interval in (1,2):
            argv=['host','fixture-config','fixture-upstream','--endpoint-metrics','--sampling-control','--guest-count','2']
            if interval!=1:argv+=['--vmm-pss-interval-seconds',str(interval)]
            commands=[]
            def run(command,**kwargs):commands.append(command);return SimpleNamespace(returncode=0)
            with patch.object(sys,'argv',argv),patch.object(host,'load',return_value=({'task_id':C4},None,None)),patch.object(host.os,'geteuid',return_value=0),patch.object(host.subprocess,'run',side_effect=run):
                self.assertEqual(host.main(),0)
            entry=commands[0];self.assertEqual(entry[0],'systemd-run')
            if interval==1:self.assertNotIn('--vmm-pss-interval-seconds',entry)
            else:self.assertEqual(entry[entry.index('--vmm-pss-interval-seconds')+1],'2.0')
            start=entry.index(str(Path(host.__file__).resolve()))
            with patch.object(sys,'argv',entry[start:]),patch.object(host,'load',return_value=({'task_id':C4},None,None)),patch.object(host,'grouped',return_value=0) as grouped:
                self.assertEqual(host.main(),0)
            self.assertEqual(grouped.call_args.kwargs['vmm_pss_interval_seconds'],interval)
        for extra in ([],['--endpoint-metrics','--sampling','off']):
            with patch.object(sys,'argv',['host','config','upstream','--vmm-pss-interval-seconds','2',*extra]),patch.object(host,'load',return_value=({'task_id':C4},None,None)),patch.object(host.subprocess,'run') as run:
                with self.assertRaises(ValueError):host.main()
                run.assert_not_called()

    def test_actual_commands_forward_to_native_coordinator_and_each_child(self):
        # Execute the existing command-building statements with synthetic arguments.
        # The capture replaces subprocess execution; no native main or service is run.
        for interval in (1,2):
            with tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp);captured=[]
                def run(command,**kwargs):captured.append(list(map(str,command)));return SimpleNamespace(returncode=0)
                grouped=function('run_host_external_workload.py','grouped')
                body=next(n.body for n in grouped.body if isinstance(n,ast.Try))
                begin=next(i for i,n in enumerate(body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='command' for t in n.targets))
                end=next(i for i in range(begin,len(body)) if isinstance(body[i],ast.With))
                ns=dict(ROOT=ROOT,sys=sys,Path=Path,json=json,out=out,config=out/'config',upstream=out/'upstream',metrics_on=True,sampling='on',endpoint_metrics=True,sampling_control=True,guest_count=2,delete_trace=False,guest_relay_proxy=None,oci_input=None,pss_interval=interval,cfg={'task_id':C4},budgets=lambda task:{'native':5},extra={},subprocess=SimpleNamespace(run=run,STDOUT=-2))
                execute(body[begin:end+1],ns)
                native=captured.pop()
                self.assertEqual('--vmm-pss-interval-seconds' in native,interval!=1)
                if interval!=1:self.assertEqual(float(native[native.index('--vmm-pss-interval-seconds')+1]),interval)
                # Native -> concurrent controller: evaluate the actual call expression.
                main=function('validate_isolated_agentenv.py','main')
                call=next(n for n in ast.walk(main) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='call' and any(isinstance(k,ast.Name) and k.id=='external_budgets' for k in ast.walk(n)))
                args=SimpleNamespace(external_guest_count=2,external_sampling_control=True)
                ns=dict(sys=sys,ROOT=ROOT,args=args,stage='external-workload',external_config=out/'config',external_upstream=out/'upstream',replay=out/'output',container='fixture',mount=out,receipt={'address':'fixture'},external_off=False,task_endpoint_metrics=True,vmm_pss_interval=interval,external_value={'task_id':C4},external_budgets=lambda task:{'controller':5},env={},call=lambda stage,command,*a,**kw:captured.append(list(map(str,command))))
                execute([call],ns);coordinator=captured.pop()
                self.assertTrue(any(x.endswith('/run_c4_parallel.py') for x in coordinator))
                self.assertEqual('--vmm-pss-interval-seconds' in coordinator,interval!=1)
                if interval!=1:self.assertEqual(float(coordinator[coordinator.index('--vmm-pss-interval-seconds')+1]),interval)
                # Concurrent controller -> both children, using the real creation loop.
                run_body=function('run_c4_parallel.py','run')
                loop=next(n for n in ast.walk(run_body) if isinstance(n,ast.For) and ast.unparse(n.iter)=='range(2)')
                children=[]
                def popen(command,**kwargs):captured.append(command);return Mock()
                args=SimpleNamespace(config=out/'config',upstream=out/'upstream',config_home=out/'cfg',service_url='fixture',container='fixture',sampling='on',endpoint_metrics=True)
                ns=dict(args=args,ROOT=ROOT,sys=sys,out=out,pss_interval=interval,subprocess=SimpleNamespace(Popen=popen,STDOUT=-2),env={},children=children,write=lambda *a:None)
                execute([loop],ns)
                try:
                    self.assertEqual(len(captured),2)
                    for command in captured:
                        self.assertEqual('--vmm-pss-interval-seconds' in command,interval!=1)
                        if interval!=1:self.assertEqual(command[command.index('--vmm-pss-interval-seconds')+1],'2')
                finally:
                    for _,log,_ in children:log.close()

    def test_direct_children_reject_unconnected_interval_before_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=SimpleNamespace(config='fixture',upstream='fixture',output=Path(tmp)/'must-not-exist',sampling='on',endpoint_metrics=False,vmm_pss_interval_seconds=2)
            with patch.object(external,'load',return_value=({'task_id':C4},None,None)),self.assertRaises(ValueError):external.run(args)
            self.assertFalse(args.output.exists())
            with self.assertRaises(ValueError):parallel.run(args)
            self.assertFalse(args.output.exists())

    def test_native_rejects_unsupported_paths_and_unsampled_mode(self):
        main=function('validate_isolated_agentenv.py','main')
        early=next(n for n in main.body if isinstance(n,ast.If) and ast.unparse(n.test).startswith('vmm_pss_interval != 1'))
        branch=next(n for n in main.body if isinstance(n,ast.If) and ast.unparse(n.test)=='external_config')
        endpoint=next(n for n in branch.body if isinstance(n,ast.If) and ast.unparse(n.test).startswith('vmm_pss_interval != 1'))
        base=dict(vmm_pss_interval=2,external_config='fixture',external_off=False,creation_only=False,density=None)
        execute([early],dict(base))
        for change in ({'external_config':None},{'external_off':True},{'creation_only':True},{'density':{'task':'c4'}}):
            with self.assertRaises(ValueError):execute([early],dict(base,**change))
        for enabled,task,allowed in ((False,C4,False),(True,C4,True),(False,'large-scale-text-editing',True)):
            ns=dict(vmm_pss_interval=2,task_endpoint_metrics=enabled,external_value={'task_id':task})
            if allowed:execute([endpoint],ns)
            else:
                with self.assertRaises(ValueError):execute([endpoint],ns)
        # With the default interval these new guards impose no new preconditions.
        execute([early],dict(base,vmm_pss_interval=1,external_config=None))


if __name__=='__main__':unittest.main()
