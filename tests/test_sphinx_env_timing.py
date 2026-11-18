"""Local toy subprocesses and mocked lifecycle only; no service, task or device access."""
import ast
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from guest_sphinx_env_timing import COMMAND,PARTS,capture_parts
import sphinx_env_timing as timing
import run_sphinx_command_diagnostic as driver


class EnvTimingTests(unittest.TestCase):
    def test_real_manifest_plan_and_stopped_runner_leave_original_unchanged(self):
        workload=ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/workloads/sphinx-7590-rl-fork'
        cfg=json.loads((ROOT/'configs/sphinx7590-backbone.json').read_text())
        self.assertEqual(timing.plan(workload,cfg)['prefix_actions'],11)
        self.assertEqual('; '.join(c for _,c in PARTS),COMMAND)
        original=(workload/'guest-runner.sh').read_text()
        stopped=timing.prefix_runner(original)
        self.assertEqual(subprocess.run(['bash','-n'],input=stopped,text=True,capture_output=True).returncode,0)
        self.assertEqual(original,stopped.replace('  if [[ $round == trace_cpp_parser && $step == 006 ]]; then break; fi\n',''))

    def test_stop_occurs_before_target_but_other_segment_keeps_its_actions(self):
        text="""round=$1
while IFS=$'\\t' read -r step rest; do
  [[ $step == step ]] && continue
  printf '%s\\n' "$step"
done <<'EOF'
step
001
002
003
004
005
006
007
EOF
"""
        for segment,count in (('trace_cpp_parser',5),('read_repo',7)):
            value=subprocess.check_output(['bash','-s',segment],input=timing.prefix_runner(text),text=True)
            self.assertEqual(value.splitlines(),[f'{i:03}' for i in range(1,count+1)])

    def test_whole_and_split_preserve_bytes_and_final_nonzero_exit(self):
        parts=(('ls','printf A; printf e >&2; false'),('conda','printf B; false'),('pip','printf C; false'))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            whole=capture_parts((('whole','; '.join(c for _,c in parts)),),root/'whole',cwd=root,timeout=5)
            split=capture_parts(parts,root/'split',cwd=root,timeout=5)
            for value in (whole,split):
                self.assertEqual(value['status'],'CAPTURED');self.assertEqual(value['exit_code'],1)
                self.assertEqual(value['elapsed_ns'],value['parts_elapsed_sum_ns']+value['between_parts_ns'])
                self.assertFalse(value['process_scans'])
                self.assertFalse(any('sha256' in key for key in value))
            self.assertEqual([p['exit_code'] for p in split['parts']],[1,1,1])
            for kind in ('stdout','stderr'):
                self.assertEqual((root/'whole'/kind).read_bytes(),(root/'split'/kind).read_bytes())
            self.assertEqual((root/'split/stdout').read_bytes(),b'ABC')
            self.assertEqual((root/'split/stderr').read_bytes(),b'e')

    def test_overall_timeout_stops_child_and_does_not_run_next_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            value=capture_parts((('ls','exec sleep 5'),('pip','touch should-not-exist')),root/'out',cwd=root,timeout=.05)
            self.assertEqual(value['status'],'TIMEOUT');self.assertEqual(len(value['parts']),1)
            self.assertLess(value['exit_code'],0);self.assertFalse((root/'should-not-exist').exists())

    def test_capture_audit_rejects_changed_exit_output_and_time(self):
        parts=(('ls','printf A'),('conda','printf B'),('pip','printf C; false'))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out';reference=root/'reference/rollouts/backbone/trace_cpp_parser'
            for kind,data in (('stdout',b'ABC'),('stderr',b'')):
                (reference/kind).mkdir(parents=True);(reference/kind/'006.log').write_bytes(data)
            value=capture_parts(parts,out,cwd=root,timeout=5);value['mode']='split'
            def save(v):(out/'result.json').write_text(json.dumps(v))
            save(value);cfg={'source_image':'fixture-image','expected_exit_codes':{'trace_cpp_parser':[0]*5+[1,0]}}
            with patch.object(timing,'PARTS',parts):
                self.assertEqual(timing.check_capture(out,root/'reference',cfg,'split'),value)
                for changed in (dict(value,exit_code=0),dict(value,between_parts_ns=-1),dict(value,mode='whole')):
                    save(changed)
                    with self.assertRaises(AssertionError):timing.check_capture(out,root/'reference',cfg,'split')
                save(value);(out/'stdout').write_bytes(b'wrong')
                with self.assertRaises(AssertionError):timing.check_capture(out,root/'reference',cfg,'split')

    def test_prefix_audit_does_not_allow_find_failure_or_target_warmup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'raw';reference=root/'reference'
            directory=raw/'rollouts/backbone/trace_cpp_parser';directory.mkdir(parents=True)
            expected=reference/'rollouts/backbone/trace_cpp_parser';expected.mkdir(parents=True)
            frozen=raw/'frozen-input/segments/trace_cpp_parser';frozen.mkdir(parents=True)
            for p in (directory,expected,frozen):(p/'actions.tsv').write_text('same frozen manifest')
            for kind in ('stdout','stderr'):
                for p in (directory,expected):
                    (p/kind).mkdir()
                    for i in range(1,6):(p/kind/f'{i:03}.log').write_bytes(b'output')
            for p in (directory,expected):(p/'patch.diff').write_bytes(b'patch')
            for name in ('guest-memory.tsv','guest-vmstat.tsv'):(directory/name).write_text('timestamp_ns\n')
            codes=[0,0,1,1,0]
            def steps(values):
                (directory/'steps.tsv').write_text('step\texit_code\tbranch\tround\tduration_ns\n'+''.join(f'{i:03}\t{code}\tbackbone\ttrace_cpp_parser\t1\n' for i,code in enumerate(values,1)))
            cfg={'expected_exit_codes':{'trace_cpp_parser':codes+[1,0]}}
            steps(codes);self.assertTrue(timing.check_prefix(raw,reference,cfg)['target_not_previously_executed'])
            steps(codes+[1])
            with self.assertRaises(AssertionError):timing.check_prefix(raw,reference,cfg)
            steps(codes[:4]+[1])
            with self.assertRaises(AssertionError):timing.check_prefix(raw,reference,cfg)

    def test_native_new_option_is_separate_and_old_mode_has_no_new_preconditions(self):
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        start=next(i for i,n in enumerate(main.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='env_command_timing' for t in n.targets))
        code=compile(ast.Module(body=main.body[start:start+4],type_ignores=[]),'<actual environment timing dispatch>','exec')
        ns=dict(args=SimpleNamespace(python_env_command_timing='split'),python_command_diagnostic=True,python_find_diagnostic=False)
        good=dict(ns);exec(code,good);self.assertEqual(good['short_args'],['--env-command-timing','split'])
        for changes in ({'python_command_diagnostic':False},{'python_find_diagnostic':True},
                        {'args':SimpleNamespace(python_env_command_timing='split',light_monitor=True)},
                        {'args':SimpleNamespace(python_env_command_timing='split',host_file_monitor=True)}):
            with self.assertRaises(ValueError):exec(code,dict(ns,**changes))
        old=dict(ns,args=SimpleNamespace(light_monitor=True,host_file_monitor=True));exec(code,old)
        self.assertEqual(old['short_args'],[])

    def test_new_report_keeps_diagnostic_scope_and_requires_outer_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'.artifacts/storage-isolated-service/owned/python-backbone';raw.mkdir(parents=True)
            cfg={};base='  [[ $step == step ]] && continue\n';frozen=raw/'frozen-input';frozen.mkdir()
            (frozen/'guest-runner.original.sh').write_text('original runner')
            (raw/'env-prefix-base.sh').write_text(base);(frozen/'guest-runner.sh').write_text(timing.prefix_runner(base))
            for name in ('task-before.json','task-after.json','task-oracle-before.json','task-oracle-after.json'):
                (raw/name).write_text('{"status":"PASS"}')
            cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed',
                'original_firecracker_identities_unchanged','original_service_unchanged')}
            outer={'status':'PASS','cleanup':cleanup};(raw.parent/'result.json').write_text(json.dumps(outer))
            part=dict(name='whole',command=COMMAND,exit_code=1,elapsed_ns=1,child_user_seconds=0,child_system_seconds=0,child_minor_faults=0,child_major_faults=0)
            value={'parts':[part]};replay={'action_count':11}
            result=dict(status='PASS',command_diagnostic=True,env_command_timing='whole',
                checks={'env_command_timing_and_original_prefix':'PASS'},cleanup={'service_sandbox_list_empty':True},
                official_task_status='NOT_RUN',strict_prefix_status='FAIL',replay=replay,env_timing=value,scope='diagnostic only')
            (raw/'result.json').write_text(json.dumps(result))
            with patch('sphinx7590_inputs.ROOT',root),patch('sphinx7590_inputs.validate',return_value=(cfg,None,None,None)),\
                patch('task_metrics_prefix.check_prefix',return_value={'strict_prefix_status':'FAIL'}),\
                patch('task_metrics_prefix.without_sampling',return_value=base),\
                patch.object(timing,'check_prefix',return_value=replay),patch.object(timing,'check_capture',return_value=value):
                summary=timing.report(raw,root/'.artifacts/report')
                self.assertEqual(summary['prefix_actions'],11);self.assertEqual(summary['official_task_status'],'NOT_RUN')
                self.assertEqual(summary['strict_replay_status'],'NOT_RUN')
                self.assertTrue((root/'.artifacts/report/timing.tsv').exists())
                self.assertFalse((root/'.artifacts/report/input-sha256.json').exists())
                outer['cleanup']['mount_and_loop_removed']=False;(raw.parent/'result.json').write_text(json.dumps(outer))
                with self.assertRaises(AssertionError):timing.report(raw,root/'.artifacts/bad-report')
                self.assertFalse((root/'.artifacts/bad-report').exists())

    def test_mocked_new_driver_one_target_old_driver_repeats_and_failure_cleans_up(self):
        for mode,failed in (('whole',False),('split',False),('split',True),(None,False)):
            with self.subTest(mode=mode,failed=failed),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);workload=root/'workload';reference=root/'reference';calls=[]
                cfg={'source_image':'fixture-image','expected_exit_codes':{'trace_cpp_parser':[0]*5+[1,0]}}
                for segment,count in (('read_repo',6),('trace_cpp_parser',7)):
                    p=workload/'segments'/segment;p.mkdir(parents=True)
                    (p/'actions.tsv').write_text('step\tcommand\n'+''.join(f'{i:03}\t{COMMAND if segment=="trace_cpp_parser" and i==6 else "true"}\n' for i in range(1,count+1)))
                (reference/'prepared-input').mkdir(parents=True)
                (reference/'prepared-input/config.json').write_text(json.dumps(cfg))
                (reference/'result.json').write_text(json.dumps({'checks':{'actions_hash_order_expected_failures':'PASS'}}))
                scripts=root/'scripts';scripts.mkdir()
                for name in ('guest_command_diagnostic.py','guest_sphinx_env_timing.py','sphinx_env_timing.py'):(scripts/name).write_text('fixture source')
                out=root/'.artifacts/storage-isolated-service/owned/mount/controller/python-backbone'
                out.parent.mkdir(parents=True)
                args=SimpleNamespace(inputs=root/'inputs',reference=reference,check_only=False,output=out,config_home=root/'config',container='owned',service_url='http://127.0.0.1:1',env_command_timing=mode)
                receipt={'status':'CAPTURED','mode':mode,'exit_code':1}
                def command(argv,**kwargs):
                    argv=list(map(str,argv));calls.append(argv)
                    if argv[0]=='docker':value={'agentsandboxbench.run_id':'owned','agentenv.experiment':'isolated-storage-check'}
                    elif any('agentenv_cold_start.py' in x for x in argv):return SimpleNamespace(returncode=0,stdout=b'owned-sid\n')
                    elif 'list' in argv:value=[]
                    elif any('--output /workspace/asb-command/target' in x for x in argv):value=receipt
                    else:return SimpleNamespace(returncode=0,stdout=b'')
                    return SimpleNamespace(returncode=0,stdout=json.dumps(value).encode())
                after=[]
                def prefix(*a,**kw):
                    p=out/'frozen-input';p.mkdir();(p/'guest-runner.sh').write_text('  [[ $step == step ]] && continue\n')
                    (out/'sampling-variant.json').write_text('{}')
                    (out/'prefix-result.json').write_text('{"strict_prefix_status":"FAIL"}')
                    return lambda:after.append(True),{}
                class Controller:
                    def __init__(self,*a):pass
                    async def run_path(self,*a):calls.append(['fake-trace-prefix'])
                with patch.object(driver,'ROOT',root),patch.object(driver,'validate',return_value=(cfg,workload,{},{})),\
                    patch.object(driver.subprocess,'run',side_effect=command),patch.object(driver.signal,'signal'),\
                    patch.object(driver,'run_prefix',side_effect=prefix),patch.object(driver,'controller_module',return_value=SimpleNamespace(Controller=Controller)),\
                    patch.object(driver,'check_trace',return_value={'action_count':13}),\
                    patch.object(driver,'run_command_repeats') as repeats,\
                    patch.object(timing,'check_prefix',return_value={'action_count':11}),\
                    patch.object(timing,'check_capture',side_effect=RuntimeError('fixture audit failure') if failed else None,return_value=receipt):
                    code=driver.run(args)
                    self.assertEqual(code,1 if failed else 0,(out/'failure.txt').read_text() if (out/'failure.txt').exists() else '')
                targets=[a for a in calls if any('--output /workspace/asb-command/target' in x for x in a)]
                self.assertEqual(len(targets),1 if mode else 0)
                self.assertEqual(repeats.call_count,0 if mode else 1)
                self.assertTrue(any('delete' in a for a in calls));self.assertTrue(any('list' in a for a in calls))
                result=json.loads((out/'result.json').read_text())
                self.assertTrue(result['cleanup']['service_sandbox_list_empty'])
                if mode and not failed:
                    self.assertEqual(after,[True]);self.assertEqual(result['official_task_status'],'NOT_RUN')
                    self.assertEqual(result['strict_prefix_status'],'FAIL')
                    self.assertEqual(result['replay']['action_count'],11)


if __name__=='__main__':unittest.main()
