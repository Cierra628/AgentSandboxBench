"""Pinned Django original-path prefix reuse; explicitly not native seven branches."""
import argparse
import csv
import json
from pathlib import Path
import shutil
import subprocess

from external_workload import load
from prepare_python_schedule_matrix import ROOT, ROLES

FILES = ('django/db/migrations/operations/models.py',)
HEAD = '08014edb162f905354929b72e281f68b6dba9225'
BACKBONE = ('read_repo', 'read_tests', 'edit_models', 'inspect_models', 'final_models')
RANGES = dict(zip(BACKBONE, ((1,9),(10,18),(19,22),(23,26),(27,28))))
for role in ROLES[1:]:
    RANGES[role+'_finish'] = (19,28) if role.startswith('early_') else (27,28)
PATHS = {'backbone': list(BACKBONE)}
PATHS.update({r:list(BACKBONE[:2 if r.startswith('early_') else 4])+[r+'_finish'] for r in ROLES[1:]})
EXPECTED = {s:dict(actual_actions=196 if s=='grpo' else 64,logical_actions=196,
    saves=0 if s=='grpo' else 2,restores=0 if s=='grpo' else 6) for s in ('grpo','bpo','tvcache')}


def volume_gib(schedule):
    return dict(grpo=48,bpo=80,tvcache=80)[schedule]

RUNNER = r'''#!/bin/bash
set +e
cd /testbed || exit 93
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/testbed
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
  source "$MIXFS_WORKLOAD_ROOT/original/$(printf '%03d' "$original_step").sh" > "$out/stdout/$step.log" 2> "$out/stderr/$step.log"
  status=$?
  end_ns=$("$MIXFS_WORKLOAD_ROOT/clock-ns") || exit 94
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$branch" "$branch" "$round" "$step" "$status" "$((end_ns-start_ns))" >> "$out/steps.tsv"
done < "$ACTIONS_ROOT/$round/actions.tsv"
git -C /testbed diff --binary > "$out/patch.diff" || exit 94
cp "$ACTIONS_ROOT/$round/actions.tsv" "$out/actions.tsv" || exit 94
sample
printf 'waiting\t%s\t999\n' "$round" > /workspace/asb-runtime-monitor/phase
'''


def prepare(upstream, output):
    cfg, replay, reference = load(ROOT/'configs/external-django16819-light.json',upstream)
    assert subprocess.check_output(['git','-C',upstream,'rev-parse','HEAD'],text=True,timeout=10).strip()==cfg['revision']
    assert (Path(upstream)/cfg['run']/'source-preflight.stdout').read_text().splitlines()[1]==HEAD
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    workload=output/'workload';(workload/'original').mkdir(parents=True)
    for i in range(1,29):(workload/'original'/f'{i:03}.sh').write_text(replay['files'][f'{i:03}.sh'])
    samples=[]
    for name,(start,end) in RANGES.items():
        directory=workload/'segments'/name;directory.mkdir(parents=True)
        actions=[]
        with (directory/'actions.tsv').open('w') as stream:
            writer=csv.writer(stream,delimiter='\t');writer.writerow(('step','original_step','exit_code'))
            for i,original in enumerate(range(start,end+1),1):
                code=int(reference[original-1]['exit_code']);writer.writerow((f'{i:03}',original,code))
                actions.append(dict(original_step=original,exit_code=code))
        role=name[:-7] if name.endswith('_finish') else 'backbone'
        samples.append(dict(role=role,segment=name,actions=actions))
    (workload/'rollouts.json').write_text(json.dumps(PATHS,indent=2)+'\n')
    (workload/'agent-sampling.json').write_text(json.dumps(dict(segments=samples),indent=2)+'\n')
    (workload/'guest-runner.sh').write_text(RUNNER)
    clock=Path(upstream)/'experiments/container-screening/harness/clock_ns.c'
    subprocess.run(['gcc','-O2','-Wall','-Wextra','-Werror','-static',clock,'-o',workload/'clock-ns'],check=True,timeout=60)
    shutil.copy2(Path(upstream)/cfg['run']/'final-source.patch',output/'reference.patch')
    value=dict(cfg,upstream=str(Path(upstream).resolve()),source_image=cfg['image'],base_commit=HEAD,guest_cpu=2,guest_memory_mib=8192,
        workload=str(workload.relative_to(ROOT)),path=PATHS['backbone'],
        workload_kind='controlled_same_path_prefix_reuse_not_native_seven_branches',
        checkpoint_after_original_steps=[18,26],expected=EXPECTED)
    (output/'config.json').write_text(json.dumps(value,indent=2)+'\n')
    validate(output)
    return value


def validate(prepared):
    prepared=Path(prepared).resolve();cfg=json.loads((prepared/'config.json').read_text())
    fixed=json.loads((ROOT/'configs/external-django16819-light.json').read_text())
    assert all(cfg.get(k)==v for k,v in fixed.items()), 'prepared Django configuration differs from fixed source'
    assert cfg['task_id']=='django__django-16819' and cfg['base_commit']==HEAD
    assert cfg['source_image']==fixed['image'] and cfg['guest_cpu']==2
    assert cfg['guest_memory_mib']==8192 and cfg['expected']==EXPECTED
    assert cfg['checkpoint_after_original_steps']==[18,26] and cfg['path']==PATHS['backbone']
    assert cfg['workload_kind']=='controlled_same_path_prefix_reuse_not_native_seven_branches'
    source=ROOT/cfg['workload'];assert source==prepared/'workload'
    assert json.loads((source/'rollouts.json').read_text())==PATHS
    samples=json.loads((source/'agent-sampling.json').read_text())['segments']
    upstream_reference=json.loads((Path(cfg['upstream'])/cfg['run']/'replay-inputs.json').read_text())
    assert (prepared/'reference.patch').read_bytes()==(Path(cfg['upstream'])/cfg['run']/'final-source.patch').read_bytes()
    for i in range(1,29):
        assert (source/'original'/f'{i:03}.sh').read_text()==upstream_reference['files'][f'{i:03}.sh']
    assert (source/'guest-runner.sh').read_text()==RUNNER
    expected_samples=[]
    for segment,(begin,end) in RANGES.items():
        role=segment[:-7] if segment.endswith('_finish') else 'backbone'
        expected_samples.append(dict(role=role,segment=segment,
            actions=[dict(original_step=i,exit_code=1 if i==17 else 0) for i in range(begin,end+1)]))
    assert samples==expected_samples
    for role,path in PATHS.items():
        original=[]
        for segment in path:
            with (source/'segments'/segment/'actions.tsv').open() as stream:entries=list(csv.DictReader(stream,delimiter='\t'))
            begin,end=RANGES[segment]
            assert [int(r['original_step']) for r in entries]==list(range(begin,end+1))
            assert [r['step'] for r in entries]==[f'{i:03}' for i in range(1,end-begin+2)]
            assert [int(r['exit_code']) for r in entries]==[1 if i==17 else 0 for i in range(begin,end+1)]
            original.extend(int(r['original_step']) for r in entries)
        assert original==list(range(1,29))
    return cfg,source,samples,list(RANGES)


def task_plan(prepared):
    cfg,source,_,_=validate(prepared)
    return dict(task_id=cfg['task_id'],profile='django16819',workload=cfg['workload'],expected=EXPECTED,
        workload_kind=cfg['workload_kind'],cpu_per_guest=2,memory_mib_per_guest=8192,
        initial_lower_reference_count=10,checkpoint_after_original_steps=[18,26])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();prepare(args.upstream,args.output)
    print('status=PREPARED business_runs=0 result_dir='+str(args.output))
