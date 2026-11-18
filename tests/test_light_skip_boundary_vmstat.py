"""Temporary shell/CSV fixtures and mocks only; no services, devices or task payloads."""
import ast,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from sandbox_runtime_monitor import boundary_runner
from run_prettier6604_grpo import skip_boundary_vmstat_options,verify_path
from platform_tasks import configuration,commands
from run_grpo_light_pair import pair_configs,summarize
from report_grpo_create_setup_repeats import summarize as create_repeats
from report_grpo_light_repeats import summarize as old_repeats

VM_HEADER='timestamp_ns\tphase\tsandbox\tbranch\tround\tstep\tpgfault\tpgmajfault\tpgpgin\tpgpgout\n'
RUNNER=r'''#!/usr/bin/env bash
set -euo pipefail
out=$1
mkdir -p "$out"
round=segment
sandbox_label=role
branch=role
printf 'branch\tround\tstep\texit_code\tduration_ns\n' > "$out/steps.tsv"
printf 'timestamp_ns\tphase\tsandbox\tbranch\tround\tstep\tMemTotal_kib\tMemFree_kib\n' > "$out/guest-memory.tsv"
printf 'timestamp_ns\tphase\tsandbox\tbranch\tround\tstep\tpgfault\tpgmajfault\tpgpgin\tpgpgout\n' > "$out/guest-vmstat.tsv"
sample_memory() {
  local phase=$1 step=$2 timestamp_ns
  printf '1\t%s\trole\trole\tsegment\t%s\t4000\t1000\n' "$phase" "$step" >> "$out/guest-memory.tsv"
}
sample_vmstat() {
  local phase=$1 step=$2 timestamp_ns
  printf 'read\n' >> "$out/vmstat-reads"
  printf '1\t%s\trole\trole\tsegment\t%s\t1\t2\t3\t4\n' "$phase" "$step" >> "$out/guest-vmstat.tsv"
}
monitor_step() { :; }
sample_memory baseline 000
sample_vmstat baseline 000
for step in 001 002; do
  sample_memory before "$step"
  sample_vmstat before "$step"
  monitor_step "$step" & monitor_pid=$!
  set +e
  if [[ $step == 001 ]]; then true; else false; fi
  status=$?
  set -e
  kill "$monitor_pid" 2>/dev/null || true
  wait "$monitor_pid" 2>/dev/null || true
  sample_memory after "$step"
  sample_vmstat after "$step"
  printf 'role\tsegment\t%s\t%s\t1\n' "$step" "$status" >> "$out/steps.tsv"
done
sample_memory complete 999
sample_vmstat complete 999
'''


def write(path,value):path.write_text(json.dumps(value))


class RunnerAndAuditTests(unittest.TestCase):
    def test_actual_report_script_comparison_uses_option_and_rejects_other_changes(self):
        path=ROOT/'scripts/report_prettier6604_grpo.py';tree=ast.parse(path.read_text())
        assertion=next(n for n in ast.walk(tree) if isinstance(n,ast.Assert) and any(
            isinstance(c,ast.Call) and isinstance(c.func,ast.Name) and c.func.id=='boundary_runner' for c in ast.walk(n)))
        code=compile(ast.fix_missing_locations(ast.Module(body=[assertion],type_ignores=[])),str(path),'exec')
        for skip in (False,True):
            with self.subTest(skip=skip),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);original=root/'original.sh';original.write_text(RUNNER)
                executed=root/'runner.sh';text=boundary_runner(RUNNER,guest_phases=True,skip_vmstat=skip);executed.write_text(text)
                ns=dict(workload=root,name='runner.sh',original=original,variant={'phase_updates':'guest runner'},
                    boundary_runner=boundary_runner,skip_boundary_vmstat=skip,merge_boundary_meminfo=False)
                exec(code,ns)
                executed.write_text(text.replace('4000','3999'))
                with self.assertRaises(AssertionError):exec(code,ns)

    def execute(self,root,skip):
        phase=root/'monitor';phase.mkdir()
        output=root/'raw/rollouts/role/segment'
        with patch('sandbox_runtime_monitor.GUEST_ROOT',str(phase)):
            text=boundary_runner(RUNNER,guest_phases=True,skip_vmstat=skip)
        script=root/'runner.sh';script.write_text(text)
        process=subprocess.run(['bash',str(script),str(output)],capture_output=True,text=True,timeout=5)
        self.assertEqual(process.returncode,0,process.stderr)
        (output/'patch.diff').write_text('unchanged')
        (output/'actions.tsv').write_text('fixture')
        return root/'raw',output,phase,text

    def check(self,raw,skip):
        with patch('run_prettier6604_grpo.sha',return_value='fixture-integrity'),patch('run_prettier6604_grpo.sampled_segment',return_value=dict(actions=[dict(exit_code=0),dict(exit_code=1)])):
            return verify_path(raw,'role',['segment'],Path('/not-read'),[],{'segment':'segment'},skip_boundary_vmstat=skip)

    def test_shell_commands_memory_peak_and_phase_unchanged_vmstat_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);default=root/'default';default.mkdir();candidate=root/'candidate';candidate.mkdir()
            raw0,p0,phase0,t0=self.execute(default,False);raw1,p1,phase1,t1=self.execute(candidate,True)
            self.assertEqual((p0/'steps.tsv').read_bytes(),(p1/'steps.tsv').read_bytes())
            self.assertEqual((p0/'guest-memory.tsv').read_bytes(),(p1/'guest-memory.tsv').read_bytes())
            self.assertEqual(len((p0/'vmstat-reads').read_text().splitlines()),6)
            self.assertFalse((p1/'vmstat-reads').exists())
            self.assertEqual((p1/'guest-vmstat.tsv').read_text(),VM_HEADER)
            self.assertEqual((phase0/'phase').read_bytes(),(phase1/'phase').read_bytes())
            self.assertTrue((phase1/'phase').read_text().startswith('waiting\tsegment\t999'))
            a=self.check(raw0,False);b=self.check(raw1,True)
            self.assertEqual(a,b);self.assertEqual(b['guest_observed_peak_bytes'],3000*1024)
            self.assertEqual(b['action_count'],2)

    def test_candidate_rejects_missing_headers_rows_memory_or_wrong_exit(self):
        for case in ('vm-header','vm-row','mem-row','exit'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as tmp:
                raw,p,*_=self.execute(Path(tmp),True)
                if case=='vm-header':(p/'guest-vmstat.tsv').write_text('wrong\n')
                elif case=='vm-row':(p/'guest-vmstat.tsv').write_text(VM_HEADER+'1\tbefore\trole\trole\tsegment\t001\t1\t2\t3\t4\n')
                elif case=='mem-row':
                    target=p/'guest-memory.tsv';target.write_text('\n'.join(target.read_text().splitlines()[:-1])+'\n')
                else:
                    target=p/'steps.tsv';target.write_text(target.read_text().replace('002\t1\t1','002\t0\t1'))
                with self.assertRaises(AssertionError):self.check(raw,True)
        with self.assertRaises(ValueError):verify_path(Path('/not-read'),'role',[],None,None,None,sampling=False,skip_boundary_vmstat=True)


class EntryTests(unittest.TestCase):
    def test_only_explicit_sampled_new_schedule_accepts_option(self):
        self.assertFalse(skip_boundary_vmstat_options(SimpleNamespace(schedule='bpo')))
        good=dict(light_skip_boundary_vmstat=True,grpo_create_before_setup=True,light_monitor=True)
        self.assertTrue(skip_boundary_vmstat_options(SimpleNamespace(**good)))
        for change in ({'grpo_create_before_setup':False},{'monitor_mode':'off'},{'light_monitor':False},
                       {'schedule':'bpo'},{'role':'early_1'},{'host_file_monitor':True},{'damon_observe':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):skip_boundary_vmstat_options(SimpleNamespace(**(good|change)))
        self.assertTrue(skip_boundary_vmstat_options(SimpleNamespace(light_skip_boundary_vmstat=True,
            prettier6604_grpo_create_before_setup=True,prettier6604_monitor_mode='continuous',light_monitor=True)))

    def test_derived_configs_only_change_on_group_and_route_both_commands(self):
        originals={p:p.read_bytes() for p in (ROOT/'configs/platform-prettier6604-grpo-off.json',ROOT/'configs/platform-prettier6604-grpo-light.json')}
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);paths=pair_configs(out,True,skip_boundary_vmstat=True)
            for mode,path in paths.items():
                cfg=configuration(path);check,live=commands(cfg,Path('/unused'))
                self.assertEqual(cfg.get('light_skip_boundary_vmstat',False),mode=='on')
                self.assertEqual('--light-skip-boundary-vmstat' in check,mode=='on')
                self.assertEqual('--light-skip-boundary-vmstat' in live,mode=='on')
            self.assertTrue(all(p.read_bytes()==data for p,data in originals.items()))
        with tempfile.TemporaryDirectory() as tmp,self.assertRaises(ValueError):pair_configs(Path(tmp),skip_boundary_vmstat=True)

    def test_config_bool_scope_and_old_false_stay_valid(self):
        base=json.loads((ROOT/'configs/platform-prettier6604-grpo-light.json').read_text())
        for change in ({'light_skip_boundary_vmstat':1},{'grpo_create_before_setup':False},{'light_monitor':False},
                       {'monitor_mode':'off','light_monitor':False},{'host_file_monitor':True}):
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/'cfg.json';write(p,base|dict(grpo_create_before_setup=True,light_skip_boundary_vmstat=True)|change)
                with self.assertRaises(ValueError):configuration(p)
        for change in ({'rollout':'bpo'},{'rollout':'tvcache','light_monitor':False},{}):
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/'cfg.json';write(p,base|dict(light_skip_boundary_vmstat=False)|change)
                self.assertEqual(configuration(p)['rollout'],change.get('rollout','grpo'))

    def test_both_real_backend_argument_rebuilds_include_flag(self):
        path=ROOT/'scripts/validate_isolated_agentenv.py';tree=ast.parse(path.read_text())
        found=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.If) and isinstance(node.test,ast.Name) and node.test.id=='skip_boundary_vmstat':
                for statement in node.body:
                    if isinstance(statement,ast.AugAssign) and isinstance(statement.target,ast.Name):
                        ns=dict(skip_boundary_vmstat=True,check_args=[],task_args=[])
                        exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),str(path),'exec'),ns)
                        variable=statement.target.id;self.assertEqual(ns[variable],['--light-skip-boundary-vmstat']);found.append(variable)
        self.assertEqual(sorted(found),['check_args','task_args'])
        self.assertLess(path.read_text().index('skip_boundary_vmstat_options(args)'),path.read_text().index('seed_value=None'))

    def test_invalid_pair_option_stops_before_resource_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'must-not-exist'
            result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/run_grpo_light_pair.py'),'--light-skip-boundary-vmstat','--output',str(out)],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,2);self.assertIn('requires --grpo-create-before-setup',result.stderr)
            self.assertFalse(out.exists())

    def test_pair_plan_and_on_off_variant_must_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);runs=[dict(mode=m,paths={},patches={},official={},sessions=[],grpo_setup_schedule='create-all-before-setup',
                timings_ns=dict(controller=v,created_to_controller_end_ns=v-1,create_to_setup_ns=1)) for m,v in [('off',10),('on',11)]]
            plan=dict(order=['off','on'],grpo_setup_schedule='create-all-before-setup',light_skip_boundary_vmstat=True)
            write(out/'plan.json',plan);write(out/'runs.json',runs)
            with self.assertRaises(AssertionError):summarize(out)
            runs[1]['light_skip_boundary_vmstat']=True;write(out/'runs.json',runs)
            self.assertTrue(summarize(out)['light_skip_boundary_vmstat'])
            runs[0]['light_skip_boundary_vmstat']=True;write(out/'runs.json',runs)
            with self.assertRaises(AssertionError):summarize(out)

    def test_old_repeat_entrances_reject_candidate(self):
        for schedule,function in [('per-guest-setup',old_repeats),('create-all-before-setup',create_repeats)]:
            with tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp);write(out/'plan.json',dict(order=['off','on'],grpo_setup_schedule=schedule,light_skip_boundary_vmstat=True))
                write(out/'result.json',dict(status='PASS',completed=2,source_returned_to_stopped=True))
                write(out/'runs.json',[dict(mode='off'),dict(mode='on')]);write(out/'comparison.json',dict(grpo_setup_schedule=schedule))
                with self.assertRaisesRegex(AssertionError,'boundary'):
                    function([out],expected_pairs=1) if function is old_repeats else function([out,Path('second'),Path('third')])


if __name__=='__main__':unittest.main()
