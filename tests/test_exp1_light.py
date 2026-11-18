"""Local shell fixtures and a mocked whole controller; no VMs, Docker or task execution."""
import csv
import ast
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from exp1_light import configuration,instrument,mode
from guest_exp1_actions import run
from report_task_overview import tool_rows

WORKLOAD=ROOT/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke/workloads/prettier-14400'
RUNNER=WORKLOAD.parents[1]/'systems/agentenv/guest-runner.sh'


class Exp1LightTests(unittest.TestCase):
    def test_actual_service_check_accepts_exp1_label_and_rejects_other_run(self):
        tree=ast.parse((ROOT/'scripts/run_exp1_checkpoint.py').read_text())
        body=next(n for n in tree.body if isinstance(n,ast.Try)).body
        index=next(i for i,n in enumerate(body) if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and
                   n.test.id=='light' and n.body and isinstance(n.body[0],ast.Assert))
        code=compile(ast.Module(body=body[index-1:index+1],type_ignores=[]),'<actual Exp1 service check>','exec')
        info=dict(State={'Running':True},Config={'Labels':{'agentenv.experiment':'initial-smoke','agentsandboxbench.run_id':'owned'}})
        ns=dict(info=info,light=True,cfg={'expected_run_id':'owned'})
        exec(code,ns)
        ns['cfg']['expected_run_id']='other'
        with self.assertRaises(AssertionError):exec(code,ns)
        ns.update(light=False,cfg={});exec(code,ns)

    def test_config_and_mode_are_explicit_and_old_mode_is_unchanged(self):
        cfg=configuration(ROOT/'configs/platform-prettier14400-host-current.json')
        args=SimpleNamespace(exp1_light=True,light_monitor=True,host_file_monitor=True,host_current_only=True,immutable_cache_seed=Path('/seed'))
        self.assertTrue(mode(args,cfg));self.assertFalse(mode(SimpleNamespace(),None))
        for key in ('light_monitor','host_file_monitor','host_current_only','immutable_cache_seed'):
            with self.assertRaises(ValueError):mode(SimpleNamespace(**dict(vars(args),**{key:False})),cfg)
        with self.assertRaises(ValueError):configuration(ROOT/'configs/platform-exp1-isolated.json')

    def test_variant_only_replaces_original_loop_and_is_valid_shell(self):
        text=RUNNER.read_text();value=instrument(text)
        start=text.index('for action in "$actions"/*.sh; do\n');end=text.index('\nif [[ $end_step == 026 ]]; then',start)
        self.assertTrue(value.startswith(text[:start]));self.assertTrue(value.endswith(text[end:]))
        self.assertIn('guest_exp1_actions.py',value);self.assertNotIn('start_ns="$(date +%s%N)"',value)
        self.assertEqual(subprocess.run(['bash','-n'],input=value,text=True,capture_output=True).returncode,0)

    def test_real_local_timer_runs_all_actions_and_preserves_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);actions=root/'actions';actions.mkdir();out=root/'out';out.mkdir()
            for name in ('stdout','stderr'):(out/name).mkdir()
            (out/'steps.tsv').write_text('step\texit_code\tduration_ns\n')
            for i in range(1,27):(actions/f'{i:03}.sh').write_text('echo '+str(i)+'; exit '+('127' if i in (8,16) else '0')+'\n')
            phase=root/'phase';run(actions,out,60,cwd=root,phase_file=phase)
            with (out/'steps.tsv').open() as f:rows=list(csv.DictReader(f,delimiter='\t'))
            self.assertEqual(len(rows),26);self.assertTrue(all(int(r['duration_ns'])>0 for r in rows))
            self.assertEqual({r['step']:int(r['exit_code']) for r in rows if int(r['exit_code'])},{'008':127,'016':127})
            self.assertEqual((out/'stdout/026.log').read_text(),'26\n')
            self.assertIn('026',phase.read_text())
            (actions/'027.sh').write_text('true')
            with self.assertRaises(AssertionError):run(actions,out,60,cwd=root)

    def test_whole_actual_light_controller_and_report_layout_with_mocked_vm(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);server=base/'server';server.mkdir();runtime=base/'runtime';(runtime/'config/aenv').mkdir(parents=True)
            (server/'container-id.txt').write_text('owned');(server/'address.txt').write_text('127.0.0.1:12345')
            (runtime/'config/aenv/credentials').write_text('url="http://127.0.0.1:12345"\napi_key="test"\n')
            cfg=dict(server_result=str(server),runtime_dir=str(runtime),repository='IncrementalDAX_moti',trajectory_dir=str(WORKLOAD),
                output_root=str(base/'result'),checkpoint_output_root=str(base/'result'),checkpoint_after_step=None,
                sandbox_cpu=2,sandbox_memory_mib=4096,sample_interval_seconds=.1,action_timeout_seconds=60,light_monitor=True,
                expected_patch_sha256='f1cc23ac4f5ac1cff87eb7f8c54f8176c1a47cec37fa4cb6907b9dd8ad199af2',
                official_task=str(ROOT/'.artifacts/v1-closeout-inputs/official-task'),generate_analysis=False,expected_run_id='owned-run')
            path=base/'config.json';path.write_text(json.dumps(cfg));order=[]
            sid='01a180e0-296e-7142-9b2d-8d49361d4066'
            props={name:dict(javascript_formatted=True,idempotent=True) for name in ('svg','nested_svg','html_control')}
            class Monitor:
                def __init__(self,raw,*args,**kw):self.raw=raw;self.active={};self.sessions=[]
                def start(self,sid,role):self.active[sid]=True;self.sessions.append(dict(status='PASS',collector_reaped=True,sandbox_id=sid));order.append('start')
                def mark(self,*a):pass
                def stop(self,sid,*a):self.active.pop(sid);order.append('stop')
            def command(argv,**kw):
                argv=list(map(str,argv));text=''
                if argv[:2]==['docker','inspect']:text=json.dumps([dict(State={'Running':True,'Pid':os.getpid()},Config={'Labels':{'agentenv.experiment':'initial-smoke','agentsandboxbench.run_id':'owned-run'}},Image='test')])
                elif argv[:2]==['docker','exec']:text='{"ioEngine":0}'
                elif any('agentenv_cold_start.py' in s for s in argv):text=sid
                elif argv[:2]==['aenv','list'] or argv[:3]==['aenv','template','list']:text='[]'
                elif argv[:2]==['aenv','delete']:order.append('delete')
                elif argv[:2]==['aenv','download']:
                    out=Path(argv[-1])/'replay';out.mkdir();(out/'stdout').mkdir()
                    shutil.copy2(WORKLOAD/'actions.tsv',out/'actions.tsv');shutil.copy2(ROOT/'tests/fixtures/exp1-light/patch.diff',out/'patch.diff')
                    (out/'steps.tsv').write_text('step\texit_code\tduration_ns\n'+''.join(f'{i:03}\t{127 if i in (8,16) else 0}\t100\n' for i in range(1,27)))
                    (out/'stdout/024.log').write_text('    document.addEventListener("DOMContentLoaded", () => {\n      const node = document.getElementById("lastStroke");\n')
                elif argv[:2]==['aenv','exec']:
                    if 'sha256sum ' in argv[-1]:text='\n'.join(line.split('\t')[1]+'  path' for line in (WORKLOAD/'actions.tsv').read_text().splitlines())
                    elif 'check.cjs' in argv[-1]:text=json.dumps(props)
                    elif argv[-1]=='official-check':text='official output'
                return SimpleNamespace(returncode=0,stdout=text)
            official=dict(status='PASS',parser_results={'num_tests_passed':4,'num_tests_failed':0})
            with patch.object(sys,'argv',['controller',str(path)]),patch('signal.signal'),\
                 patch('subprocess.run',side_effect=command),patch('subprocess.Popen',side_effect=AssertionError('old monitor must not start')),\
                 patch('sandbox_runtime_monitor.RuntimeMonitor',Monitor),patch('exp1_official_task.guest_command',return_value='official-check'),\
                 patch('exp1_official_task.grade',return_value=official),self.assertRaises(SystemExit) as ended:
                runpy.run_path(str(ROOT/'scripts/run_exp1_checkpoint.py'),run_name='__main__')
            self.assertEqual(ended.exception.code,0)
            raw=next((base/'result/raw').iterdir());value=json.loads((raw/'result.json').read_text())
            self.assertEqual(value['status'],'PASS');self.assertEqual(value['guest_action_sum_ns'],2600)
            self.assertEqual(order,['start','stop','delete'])
            self.assertFalse((raw/'host/memory-samples.tsv').exists())
            (raw/'runtime-monitor.json').write_text(json.dumps({'sessions':[{'sandbox_id':sid}]}))
            tools,total=tool_rows(raw,value);self.assertEqual(len(tools),26);self.assertEqual(total,2600)
            self.assertEqual(tools[7]['exit_code'],127)


if __name__=='__main__':unittest.main()
