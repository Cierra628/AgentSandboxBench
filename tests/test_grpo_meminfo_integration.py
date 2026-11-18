"""Offline parsers, saved-context assertions, synthetic tables and two tiny local actions only."""
import ast
import copy
from collections import Counter
import csv
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import platform_tasks as platform
import host_persistent_input as persistent
import run_prettier6604_grpo as grpo
import sandbox_runtime_monitor as monitor
import validate_isolated_agentenv as native
import run_grpo_local_no_proxy_controls as controls
from report_grpo_local_no_proxy_controls import publish
from assess_grpo_local_vmstat import parser,execute,tree
from probe_boundary_meminfo_timer import HEADER,VM_HEADER,MEMINFO
import test_grpo_local_vmstat_implementation as previous
from run_grpo_local_no_proxy_controls import comparisons

MEMORY=r'''sample_memory() {
  local phase=$1 step=$2 timestamp_ns
  timestamp_ns="$(date +%s%N)"
  awk -v ts="$timestamp_ns" -v phase="$phase" -v sandbox="$sandbox_label" -v branch="$branch" -v round="$round" -v step="$step" '
    BEGIN { split("MemTotal MemFree Buffers Cached Shmem AnonPages Active_file Inactive_file Dirty", wanted); for (i in wanted) keep[wanted[i]]=1 }
    $1 ~ /:$/ { key=$1; sub(/:$/, "", key); gsub(/[()]/, "_", key); sub(/_$/, "", key); if (key in keep) value[key]=$2 }
    END { printf "%s\t%s\t%s\t%s\t%s\t%s",ts,phase,sandbox,branch,round,step; for(i=1;i<=9;i++) printf "\t%s",value[wanted[i]]+0; printf "\n" }
  ' /proc/meminfo >> "$out/guest-memory.tsv"
}
'''
ORIGINAL='''#!/usr/bin/env bash
set -euo pipefail
out=$1
meminfo=$2
export MIXFS_WORKLOAD_ROOT=$3
round=segment
branch=role
sandbox_label=role
printf %s '''+shlex.quote(HEADER)+''' > "$out/guest-memory.tsv"
printf %s '''+shlex.quote(VM_HEADER)+''' > "$out/guest-vmstat.tsv"
printf 'branch\\tround\\tstep\\texit_code\\tduration_ns\\n' > "$out/steps.tsv"
'''+MEMORY+r'''
sample_vmstat() {
  local phase=$1 step=$2 timestamp_ns
  :
}
monitor_step() { :; }
sample_memory baseline 000
sample_vmstat baseline 000
for step in 001 002; do
  sample_memory before "$step"
  sample_vmstat before "$step"
  monitor_step "$step" & monitor_pid=$!
  start_ns="$(python3 -c 'import time; print(time.monotonic_ns())')"
  set +e
  if [[ $step == 001 ]]; then printf 'out-001\n'; printf 'err-001\n' >&2; true; else printf 'out-002\n'; printf 'err-002\n' >&2; false; fi
  status=$?
  set -e
  end_ns="$(python3 -c 'import time; print(time.monotonic_ns())')"
  kill "$monitor_pid" 2>/dev/null || true
  wait "$monitor_pid" 2>/dev/null || true
  sample_memory after "$step"
  sample_vmstat after "$step"
  printf '%s\t%s\t%s\t%s\t%s\n' "$branch" "$round" "$step" "$status" "$((end_ns-start_ns))" >> "$out/steps.tsv"
done
sample_memory complete 999
sample_vmstat complete 999
'''


def candidate():return platform.configuration(ROOT/'configs/platform-prettier6604-grpo-host-current-merged-meminfo.json')

def metadata():return monitor.boundary_meminfo_metadata(189,35)


def meminfo_runs(runs):
    for r,item in zip(runs,controls.planned_runs('boundary-meminfo'),strict=True):
        r.pop('boundary_vmstat_mode',None)
        r.update(mode='on',boundary_meminfo_mode=item['variant'],light_skip_boundary_vmstat=True,
            light_merge_boundary_meminfo=item['merge_meminfo'],boundary_vmstat=dict(previous.META))
        r['sampling']['mode']='on';r['sampling']['boundary_rows']={'guest-memory.tsv':448,'guest-vmstat.tsv':0}
        if item['merge_meminfo']:r['boundary_meminfo']=metadata()
    return runs


class GrpoMeminfoIntegrationTests(unittest.TestCase):
    def test_config_and_native_chain_change_only_one_option(self):
        baseline=previous.cfg(True);cfg=candidate()
        self.assertEqual({k for k in cfg.keys()|baseline.keys() if cfg.get(k)!=baseline.get(k)},{'light_merge_boundary_meminfo'})
        with patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'},clear=True):
            namespaces=[]
            for value in (baseline,cfg):
                persistent.single_config(value,local_no_proxy=True)
                check,live=platform.commands(value,Path('/unused'),persistent_input=True)
                namespaces.append(parser().parse_args(live[2:]))
                self.assertTrue(native.ordinary_current_only(namespaces[-1]))
            self.assertEqual({k for k in vars(namespaces[0]) if vars(namespaces[0])[k]!=vars(namespaces[1])[k]},{'light_merge_boundary_meminfo'})
            self.assertTrue(grpo.merge_boundary_meminfo_options(namespaces[1]))
            self.assertIn('--light-merge-boundary-meminfo',check)
            guard=next(n for n in ast.walk(tree('validate_isolated_agentenv.py')) if isinstance(n,ast.If) and ast.unparse(n.test)=='merge_boundary_meminfo' and any(isinstance(v,ast.Name) and v.id=='merge_boundary_meminfo_options' for v in ast.walk(n)))
            execute(guard,dict(merge_boundary_meminfo=True,args=namespaces[1]))
            for name in ('check_args','task_args'):
                block=next(n for n in ast.walk(tree('validate_isolated_agentenv.py')) if isinstance(n,ast.If) and ast.unparse(n.test)=='merge_boundary_meminfo' and any(isinstance(v,ast.Name) and v.id==name for v in ast.walk(n)))
                ns=dict(merge_boundary_meminfo=True,**{name:[]});execute(block,ns)
                self.assertEqual(ns[name],['--light-merge-boundary-meminfo'])

    def test_config_rejects_other_modes_and_non_boolean(self):
        base=json.loads((ROOT/'configs/platform-prettier6604-grpo-host-current-merged-meminfo.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json'
            for change in ({'light_merge_boundary_meminfo':1},{'light_skip_boundary_vmstat':False},{'light_monitor':False},
                {'monitor_mode':'off','light_monitor':False},{'rollout':'single'},{'rollout':'bpo'},
                {'grpo_create_before_setup':True},{'host_current_only':False},{'damon_observe':True},
                {'host_prefix_count':7},{'host_shared_files':True}):
                path.write_text(json.dumps(base|change))
                with self.subTest(change=change),self.assertRaises(ValueError):platform.configuration(path)
        with self.assertRaises(ValueError):persistent.single_config(candidate())

    def test_inner_guard_is_stricter_than_old_vmstat_option(self):
        args=dict(schedule='grpo',light_monitor=True,monitor_mode='continuous',light_skip_boundary_vmstat=True,light_merge_boundary_meminfo=True)
        with patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'},clear=True):
            self.assertTrue(grpo.merge_boundary_meminfo_options(SimpleNamespace(**args)))
            for change in ({'grpo_create_before_setup':True},{'light_skip_boundary_vmstat':False},{'schedule':'bpo'},
                {'monitor_mode':'off'},{'light_monitor':False},{'role':'late_1'},{'host_shared_files':True}):
                with self.subTest(change=change),self.assertRaises(ValueError):grpo.merge_boundary_meminfo_options(SimpleNamespace(**(args|change)))
        with patch.dict(os.environ,{},clear=True),self.assertRaises(ValueError):grpo.merge_boundary_meminfo_options(SimpleNamespace(**args))
        self.assertFalse(grpo.merge_boundary_meminfo_options(SimpleNamespace()))
        for kwargs in ({'guest_phases':False,'skip_vmstat':True},{'guest_phases':True,'skip_vmstat':False}):
            with self.assertRaises(ValueError):monitor.boundary_runner(ORIGINAL,merge_meminfo=True,**kwargs)

    def test_real_transform_two_actions_keep_rows_exit_and_reduce_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);mem=root/'meminfo';mem.write_text(MEMINFO)
            shutil.copy2(ROOT/'scripts/prototype_boundary_memory.py',root/'prototype_boundary_memory.py')
            tools={name:shutil.which(name) for name in ('bash','date','awk','python3')};shim=root/'bin';shim.mkdir()
            for name in ('date','awk','python3'):
                p=shim/name;p.write_text('#!'+tools['bash']+'\nprintf "%s\\n" '+name+' >> "$COUNT_FILE"\nexec '+shlex.quote(tools[name])+' "$@"\n');p.chmod(0o755)
            results=[]
            for merge in (False,True):
                out=root/str(merge);out.mkdir();phase=out/'monitor';phase.mkdir()
                with patch.object(monitor,'GUEST_ROOT',str(phase)):
                    text=monitor.boundary_runner(ORIGINAL,guest_phases=True,skip_vmstat=True,merge_meminfo=merge)
                text=text.replace('/proc/meminfo','"$meminfo"')
                script=out/'runner.sh';script.write_text(text)
                env=dict(PATH=str(shim)+':/usr/bin:/bin',LC_ALL='C',HOME=str(out),PYTHONDONTWRITEBYTECODE='1',COUNT_FILE=str(out/'calls'))
                proc=subprocess.run([tools['bash'],str(script),str(out),str(mem),str(root)],env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(proc.returncode,0,proc.stderr)
                self.assertEqual(proc.stdout,'out-001\nout-002\n');self.assertEqual(proc.stderr,'err-001\nerr-002\n')
                with (out/'guest-memory.tsv').open() as f:memory=list(csv.DictReader(f,delimiter='\t'))
                with (out/'steps.tsv').open() as f:steps=list(csv.DictReader(f,delimiter='\t'))
                self.assertEqual([int(r['exit_code']) for r in steps],[0,1]);self.assertTrue(all(int(r['duration_ns'])>0 for r in steps))
                self.assertEqual(len(memory),6);self.assertEqual((phase/'phase').read_text(),'waiting\tsegment\t999\n')
                results.append([{k:v for k,v in r.items() if k!='timestamp_ns'} for r in memory])
                self.assertEqual(dict(Counter((out/'calls').read_text().splitlines())),{'date':2 if merge else 6,'awk':2 if merge else 6,'python3':4})
            self.assertEqual(*results)

    def test_full_189_action_shape_keeps_448_rows_and_strict_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw=Path(tmp);roles=previous.ROLES;counts=[5,5,5,6,6];names={f's{i}':f's{i}' for i in range(5)}
            for role in roles:
                for i,count in enumerate(counts):
                    seg=f's{i}';p=raw/'rollouts'/role/seg;p.mkdir(parents=True)
                    (p/'steps.tsv').write_text('branch\tround\tstep\texit_code\tduration_ns\n'+''.join(f'{role}\t{seg}\t{n:03}\t0\t1\n' for n in range(1,count+1)))
                    phases=[('baseline','000')]+[(phase,f'{n:03}') for n in range(1,count+1) for phase in ('before','after')]+[('complete','999')]
                    (p/'guest-memory.tsv').write_text(HEADER+''.join(f'1793000000000000000\t{phase}\t{role}\t{role}\t{seg}\t{step}\t4096\t1024\t64\t512\t32\t128\t111\t222\t0\n' for phase,step in phases))
                    (p/'guest-vmstat.tsv').write_text(VM_HEADER);(p/'patch.diff').write_text('fixture')
            with patch('run_prettier6604_grpo.sha',return_value='fixture'),patch('run_prettier6604_grpo.sampled_segment',side_effect=lambda samples,role,seg:dict(actions=[dict(exit_code=0)]*counts[int(seg[1:])])):
                values=[grpo.verify_path(raw,role,list(names),raw,[],names,skip_boundary_vmstat=True,merge_boundary_meminfo=True) for role in roles]
                self.assertEqual(sum(v['action_count'] for v in values),189)
                self.assertEqual(metadata()['boundary_rows'],448);self.assertEqual(metadata()['date_awk_calls_removed'],756)
                p=raw/'rollouts/backbone/s0/guest-memory.tsv';p.write_text(p.read_text().replace('Dirty_kib','Missing_kib'))
                with self.assertRaises(AssertionError):grpo.verify_path(raw,'backbone',list(names),raw,[],names,skip_boundary_vmstat=True,merge_boundary_meminfo=True)
            with self.assertRaises(ValueError):grpo.verify_path(raw,'backbone',[],raw,[],{},merge_boundary_meminfo=True)

    def test_saved_helper_metadata_and_missing_source_are_checked(self):
        block=next(n for n in ast.walk(tree('report_prettier6604_grpo.py')) if isinstance(n,ast.If) and ast.unparse(n.test)=='merge_boundary_meminfo' and any(isinstance(v,ast.Name) and v.id=='boundary_meminfo_metadata' for v in ast.walk(n)))
        with tempfile.TemporaryDirectory() as tmp:
            raw=Path(tmp);(raw/'frozen-input').mkdir();(raw/'code').mkdir()
            for directory in ('frozen-input','code'):(raw/directory/'prototype_boundary_memory.py').write_text('fixture source')
            result=dict(action_count=189,segment_count=35,boundary_meminfo=metadata(),checks={'boundary_meminfo_timer_merge_preserved':'PASS'})
            ns=dict(merge_boundary_meminfo=True,raw=raw,result=result)
            execute(block,ns)
            (raw/'code/prototype_boundary_memory.py').write_text('different')
            with self.assertRaises(AssertionError):execute(block,ns)
            (raw/'code/prototype_boundary_memory.py').unlink()
            with self.assertRaises(FileNotFoundError):execute(block,ns)
            result['boundary_meminfo']['boundary_rows']=447
            with self.assertRaises(AssertionError):execute(block,ns)

    def test_report_reconstructs_actual_script_and_rejects_tampering(self):
        assertion=next(n for n in ast.walk(tree('report_prettier6604_grpo.py')) if isinstance(n,ast.Assert) and any(isinstance(v,ast.Call) and isinstance(v.func,ast.Name) and v.func.id=='boundary_runner' for v in ast.walk(n)))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/'original';original.write_text(ORIGINAL);target=root/'runner';target.write_text(monitor.boundary_runner(ORIGINAL,guest_phases=True,skip_vmstat=True,merge_meminfo=True))
            ns=dict(workload=root,name='runner',original=original,variant={'phase_updates':'guest runner'},boundary_runner=monitor.boundary_runner,skip_boundary_vmstat=True,merge_boundary_meminfo=True)
            execute(assertion,ns);target.write_text(target.read_text().replace('ended=time.monotonic_ns()','ended=0'))
            with self.assertRaises(AssertionError):execute(assertion,ns)

    def test_freezing_upload_and_report_propagation_are_on_the_actual_paths(self):
        nodes=tree('run_prettier6604_grpo.py')
        branches=[n for n in ast.walk(nodes) if isinstance(n,ast.If) and ast.unparse(n.test)=='merge_boundary_meminfo' and any(isinstance(v,ast.Call) and isinstance(v.func,ast.Attribute) and v.func.attr=='copy2' for v in ast.walk(n))]
        self.assertEqual(len(branches),2)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);(out/'frozen-input').mkdir();code=out/'code';code.mkdir()
            for branch in branches:execute(branch,dict(merge_boundary_meminfo=True,ROOT=ROOT,out=out,code=code,shutil=shutil))
            self.assertEqual((out/'frozen-input/prototype_boundary_memory.py').read_bytes(),(code/'prototype_boundary_memory.py').read_bytes())
        source=(ROOT/'scripts/run_prettier6604_grpo.py').read_text()
        self.assertIn("out/'frozen-input','/workspace/mixfs-workload'",source)
        block=next(n for n in ast.walk(tree('report_task_overview.py')) if isinstance(n,ast.If) and ast.unparse(n.test)=="result.get('light_merge_boundary_meminfo')")
        ns=dict(result={'light_merge_boundary_meminfo':True,'boundary_meminfo':metadata()},summary={});execute(block,ns)
        self.assertEqual(ns['summary'],ns['result'])

    def test_old_control_definitions_do_not_mix_merged_results(self):
        runs=previous.toy_runs();runs[1]['light_merge_boundary_meminfo']=True
        with self.assertRaises(AssertionError):comparisons(runs,comparison='boundary-vmstat')

    def test_new_comparison_has_only_meminfo_difference_and_keeps_signs(self):
        plan=controls.planned_runs('boundary-meminfo')
        self.assertEqual([p['variant'] for p in plan],['separate','merged','merged','separate'])
        self.assertTrue(all(p['mode']=='on' and p['skip_vmstat'] for p in plan))
        runs=meminfo_runs(previous.toy_runs());pairs=comparisons(runs,comparison='boundary-meminfo')
        self.assertEqual(pairs[0]['timings']['tool_sum']['change_seconds'],-1)
        self.assertEqual(pairs[1]['timings']['tool_sum']['change_seconds'],1)
        for change in ({'light_skip_boundary_vmstat':False},{'light_merge_boundary_meminfo':False},
                       {'boundary_meminfo':{}},{'boundary_meminfo_mode':'separate'}):
            broken=copy.deepcopy(runs);broken[1].update(change)
            with self.subTest(change=change),self.assertRaises(AssertionError):comparisons(broken,comparison='boundary-meminfo')

    def test_new_comparison_report_labels_both_vmstat_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=previous.synthetic_report_fixture(root)
            value=json.loads((source/'result.json').read_text());value['comparison']='boundary-meminfo'
            value['runs']=meminfo_runs(value['runs']);value['pairs']=comparisons(value['runs'],comparison='boundary-meminfo')
            (source/'result.json').write_text(json.dumps(value));out=root/'report';result=publish(source,out)
            self.assertEqual(result['comparison'],'boundary-meminfo')
            self.assertEqual([r['boundary_meminfo_mode'] for r in result['runs']],['separate','merged','merged','separate'])
            self.assertTrue(all(r['light_skip_boundary_vmstat'] and r['mode']=='on' for r in result['runs']))
            text=(out/'REPORT.md').read_text();self.assertIn('合并减独立',text);self.assertIn('两组vmstat均仅表头',text)
            self.assertNotIn('关闭没有运行',text);self.assertEqual(len(list(out.glob('*.png'))),13)

    def test_new_driver_forwards_flags_and_stops_on_first_failure(self):
        for fail in (False,True):
            with self.subTest(fail=fail),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);config=root/'pool.toml';config.write_text('[pool.firecracker]\nenabled=false\nstartup_prewarm=false\n[pool.block]\nenabled=false\n')
                running=[False];launched=[];checked=[];runs=meminfo_runs(previous.toy_runs());out=root/'.artifacts/batch'
                def command(argv,**kw):
                    if argv[:2]==['docker','inspect']:return json.dumps([dict(Id=controls.SOURCE_ID,Image=controls.IMAGE,State={'Running':running[0]},Config={'Env':['API_ADDR=127.0.0.1:12345']},Mounts=[dict(Source=str(config),Destination='/workspace/config/default.toml')])])
                    if argv[:2]==['docker','ps']:return ''
                    if argv[:2]==['docker','start']:running[0]=True;return ''
                    if argv[:2]==['docker','stop']:running[0]=False;return ''
                    raise AssertionError(argv)
                def launch(argv,**kw):
                    launched.append(argv);kw['stdout'].write('status=PASS result_dir='+str(root/'.artifacts/platform-tasks'/str(len(launched)))+'\n')
                    return SimpleNamespace(returncode=1 if fail else 0)
                def check(path,mode,**kw):
                    i=len(checked);checked.append(kw);row=copy.deepcopy(runs[i]);row.pop('boundary_meminfo_mode')
                    row['guest_ids']=[f'toy-{i}-{r}' for r in previous.ROLES];return row
                original=Path.read_text
                def read_text(path,*args,**kw):
                    if str(path)=='/proc/meminfo':return 'MemAvailable: 100000000 kB\n'
                    return original(path,*args,**kw)
                from unittest.mock import MagicMock
                response=MagicMock();response.__enter__.return_value.status=200
                with patch.object(controls,'ROOT',root),patch.object(sys,'argv',['entry','--comparison','boundary-meminfo','--output',str(out)]),patch.object(controls.subprocess,'check_output',side_effect=command),patch.object(controls.subprocess,'run',side_effect=launch),patch.object(controls,'check',side_effect=check),patch.object(controls,'identities',return_value=[]),patch.object(controls.shutil,'disk_usage',return_value=SimpleNamespace(free=100*2**30)),patch.object(controls.urllib.request,'build_opener',return_value=SimpleNamespace(open=lambda *args,**kw:response)),patch.object(Path,'read_text',read_text):
                    code=controls.main()
                self.assertEqual(code,1 if fail else 0);self.assertEqual(len(launched),1 if fail else 4);self.assertFalse(running[0])
                if not fail:
                    self.assertEqual(checked,[dict(skip_vmstat=True),dict(skip_vmstat=True,merge_meminfo=True),dict(skip_vmstat=True,merge_meminfo=True),dict(skip_vmstat=True)])
                    result=json.loads((out/'result.json').read_text());self.assertEqual(result['status'],'PASS');self.assertEqual(result['comparison'],'boundary-meminfo')


if __name__=='__main__':unittest.main()
