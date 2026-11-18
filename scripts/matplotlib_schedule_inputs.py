"""Pinned Matplotlib same-path seven-instance replay with approved save points."""
import argparse
import csv
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess

from external_workload import load
from prepare_python_schedule_matrix import ROOT, ROLES

FILES = ('lib/matplotlib/axes/_axes.py',)
BACKBONE = ('inspect', 'investigate_and_first_fix', 'diagnose_tests', 'final_fix', 'final_tests')
RANGES = dict(zip(BACKBONE, ((1,7),(8,73),(74,85),(86,87),(88,95))))
for role in ROLES[1:]:RANGES[role+'_finish'] = (74 if role.startswith('early_') else 88,95)
PATHS = {'backbone':list(BACKBONE)}
PATHS.update({r:list(BACKBONE[:2 if r.startswith('early_') else 4])+[r+'_finish'] for r in ROLES[1:]})
EXPECTED = {s:dict(actual_actions=665 if s=='grpo' else 185,logical_actions=665,
    saves=0 if s=='grpo' else 2,restores=0 if s=='grpo' else 6) for s in ('grpo','bpo','tvcache')}
NONZERO = {29:1,42:1,47:1,48:1,63:1,74:4,78:1}
# The user approved starting with the displayed ~104 GiB (103.950 GiB measured).
# Interpret that integer display consistently: >=103.5 GiB rounds to >=104 GiB.
# Keep 80 GiB volume + 20 GiB host reserve; this task alone allows 3.5 GiB margin.
START_MARGIN_BYTES = 7 * 2**30 // 2


def volume_gib(schedule):
    assert schedule in EXPECTED
    return 80


def storage_components(schedule):
    assert schedule in EXPECTED
    restoring = schedule != 'grpo'
    return dict(writable_workload=28,ram_snapshots=16 if restoring else 0,
        image_layers=24 if restoring else 8,restore_scratch=4 if restoring else 0,margin=4,cleanup_reserve=.5)


RUNNER = r'''#!/bin/bash
set +e
cd /testbed || exit 93
umask 077
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/testbed
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false PAGER=cat MANPAGER=cat LESS=-R PIP_PROGRESS_BAR=off TQDM_DISABLE=1
export HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= http_proxy= https_proxy= all_proxy=
MIXFS_WORKLOAD_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export MIXFS_WORKLOAD_ROOT
out=${OUTPUT_DIR:?}; round=${ROUND:?}; branch=${BRANCH:?}
mkdir -p "$out/stdout" "$out/stderr" || exit 94
printf 'sandbox\tbranch\tround\tstep\texit_code\tduration_ns\n' > "$out/steps.tsv"
printf 'timestamp_ns\tMemTotal_kib\tMemAvailable_kib\tCached_kib\n' > "$out/guest-memory.tsv"
sample() {
  awk -v ts="$(date +%s%N)" '$1=="MemTotal:" {t=$2} $1=="MemAvailable:" {a=$2} $1=="Cached:" {c=$2} END {printf "%s\t%s\t%s\t%s\n",ts,t,a,c}' /proc/meminfo >> "$out/guest-memory.tsv"
}
sample
while IFS=$'\t' read -r step original_step expected; do
  [[ $step == step ]] && continue
  printf 'running\t%s\t%s\n' "$round" "$step" > /workspace/asb-runtime-monitor/phase
  start_ns=$("$MIXFS_WORKLOAD_ROOT/clock-ns") || exit 94
  (cd /testbed && /bin/bash "$MIXFS_WORKLOAD_ROOT/original/$(printf '%03d' "$original_step").sh") > "$out/stdout/$step.log" 2> "$out/stderr/$step.log"
  status=$?
  end_ns=$("$MIXFS_WORKLOAD_ROOT/clock-ns") || exit 94
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$branch" "$branch" "$round" "$step" "$status" "$((end_ns-start_ns))" >> "$out/steps.tsv"
done < "$ACTIONS_ROOT/$round/actions.tsv"
git -C /testbed diff HEAD --binary > "$out/patch.diff" || exit 94
cp "$ACTIONS_ROOT/$round/actions.tsv" "$out/actions.tsv" || exit 94
sample
printf 'waiting\t%s\t999\n' "$round" > /workspace/asb-runtime-monitor/phase
'''


def helper_paths(files):
    return sorted({p for i in range(1,74) for p in re.findall(r'/tmp/[A-Za-z0-9_.\-/]+',files[f'{i:03}.sh']) if p.endswith('.py')})


def boundary_code(prefix, helpers):
    return ("import json,subprocess;from pathlib import Path;"
        "git=lambda *a:subprocess.check_output(['git','-C','/testbed',*a],text=True);"
        "backup=Path('/testbed/lib/matplotlib/axes/_axes.py.backup');"
        "assert backup.read_text()==git('show','HEAD:lib/matplotlib/axes/_axes.py');"
        "print(json.dumps(dict(marker=Path('/workspace/python-schedule-marker').read_text(),"
        "head=git('rev-parse','HEAD'),patch=git('diff','HEAD','--binary'),index=git('diff','--cached','--binary'),"
        "status=git('status','--porcelain'),backup=backup.read_text(),helpers={p:Path(p).read_text() for p in "+repr(helpers)+"},"
        "steps={s:(Path('/workspace/artifacts/exp5/backbone')/s/'steps.tsv').read_text() for s in "+repr(prefix)+"})))")


def prepare(upstream, output):
    cfg,replay,reference = load(ROOT/'configs/external-matplotlib24177-light.json',upstream)
    assert subprocess.check_output(['git','-C',upstream,'rev-parse','HEAD'],text=True,timeout=10).strip()==cfg['revision']
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    workload=output/'workload';(workload/'original').mkdir(parents=True)
    for i in range(1,96):(workload/'original'/f'{i:03}.sh').write_text(replay['files'][f'{i:03}.sh'])
    samples=[]
    for segment,(begin,end) in RANGES.items():
        directory=workload/'segments'/segment;directory.mkdir(parents=True)
        actions=[]
        with (directory/'actions.tsv').open('w') as stream:
            writer=csv.writer(stream,delimiter='\t');writer.writerow(('step','original_step','exit_code'))
            for i,original in enumerate(range(begin,end+1),1):
                code=int(reference[original-1]['exit_code']);assert code==NONZERO.get(original,0)
                writer.writerow((f'{i:03}',original,code));actions.append(dict(original_step=original,exit_code=code))
        samples.append(dict(role=segment[:-7] if segment.endswith('_finish') else 'backbone',segment=segment,actions=actions))
    for name,value in [('rollouts.json',PATHS),('agent-sampling.json',dict(segments=samples))]:
        (workload/name).write_text(json.dumps(value,indent=2)+'\n')
    (workload/'guest-runner.sh').write_text(RUNNER)
    subprocess.run(['gcc','-O2','-Wall','-Wextra','-Werror','-static',Path(upstream)/'experiments/container-screening/harness/clock_ns.c','-o',workload/'clock-ns'],check=True,timeout=60)
    shutil.copy2(Path(upstream)/cfg['run']/'final-source.patch',output/'reference.patch')
    value=dict(cfg,upstream=str(Path(upstream).resolve()),source_image=cfg['image'],base_commit=cfg['source_commit'],
        guest_cpu=2,guest_memory_mib=8192,workload=str(workload.relative_to(ROOT)),path=list(BACKBONE),
        checkpoint_after_original_steps=[73,87],checkpoint_helper_paths=helper_paths(replay['files']),expected=EXPECTED,
        workload_kind='controlled_same_path_prefix_reuse_not_native_seven_branches')
    (output/'config.json').write_text(json.dumps(value,indent=2)+'\n');validate(output);return value


def validate(prepared):
    prepared=Path(prepared).resolve();cfg=json.loads((prepared/'config.json').read_text())
    fixed=json.loads((ROOT/'configs/external-matplotlib24177-light.json').read_text());assert all(cfg.get(k)==v for k,v in fixed.items())
    assert cfg['source_image']==fixed['image'] and cfg['base_commit']==fixed['source_commit']
    assert cfg['guest_cpu']==2 and cfg['guest_memory_mib']==8192 and cfg['expected']==EXPECTED
    assert cfg['checkpoint_after_original_steps']==[73,87] and cfg['path']==list(BACKBONE)
    assert cfg['workload_kind']=='controlled_same_path_prefix_reuse_not_native_seven_branches'
    source=ROOT/cfg['workload'];assert source==prepared/'workload'
    original=Path(cfg['upstream'])/cfg['run'];upstream=json.loads((original/'replay-inputs.json').read_text())
    assert (prepared/'reference.patch').read_bytes()==(original/'final-source.patch').read_bytes()
    assert cfg['checkpoint_helper_paths']==helper_paths(upstream['files'])
    for i in range(1,96):
        script=(source/'original'/f'{i:03}.sh').read_text();assert script==upstream['files'][f'{i:03}.sh']
        argv=shlex.split(script);assert argv[:3]==['exec','bash','-lc'] and len(argv)==4
    assert (source/'guest-runner.sh').read_text()==RUNNER
    assert json.loads((source/'rollouts.json').read_text())==PATHS
    samples=json.loads((source/'agent-sampling.json').read_text())['segments'];expected_samples=[]
    for segment,(begin,end) in RANGES.items():
        with (source/'segments'/segment/'actions.tsv').open() as stream:entries=list(csv.DictReader(stream,delimiter='\t'))
        assert entries==[dict(step=f'{i:03}',original_step=str(original),exit_code=str(NONZERO.get(original,0))) for i,original in enumerate(range(begin,end+1),1)]
        expected_samples.append(dict(role=segment[:-7] if segment.endswith('_finish') else 'backbone',segment=segment,
            actions=[dict(original_step=i,exit_code=NONZERO.get(i,0)) for i in range(begin,end+1)]))
    assert samples==expected_samples
    for path in PATHS.values():assert [i for s in path for i in range(RANGES[s][0],RANGES[s][1]+1)]==list(range(1,96))
    return cfg,source,samples,list(RANGES)


def task_plan(prepared):
    cfg,_,_,_=validate(prepared)
    return dict(task_id=cfg['task_id'],profile='matplotlib24177',workload=cfg['workload'],expected=EXPECTED,
        workload_kind=cfg['workload_kind'],cpu_per_guest=2,memory_mib_per_guest=8192,
        initial_lower_reference_count=10,checkpoint_after_original_steps=[73,87])


def validate_role_outputs(raw, role, segments):
    for original in (88,93,95):
        selected=[s for s in segments if RANGES[s][0]<=original<=RANGES[s][1]];assert len(selected)==1
        segment=selected[0];step=original-RANGES[segment][0]+1
        text=(Path(raw)/'rollouts'/role/segment/'stdout'/f'{step:03}.log').read_text()
        if original==95:
            assert 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' in text, f'Matplotlib {role} final marker missing'
        else:
            assert re.findall(r'\b(\d+) passed\b',text)==['60'] and not re.search(r'\b[1-9]\d* (?:failed|errors?)\b|(?m:^ERROR\b|^FAILED\b)',text), f'Matplotlib {role} step {original} requires 60 passed'
    return dict(status='PASS',original_final_steps=[88,93,95],test_pass_counts=[60,60])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--upstream',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();prepare(args.upstream,args.output);print('status=PREPARED business_runs=0 result_dir='+str(args.output))
