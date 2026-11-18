"""Fixed C4 original scripts and payloads for approved seven-instance prefix reuse."""
import argparse
import csv
import json
from pathlib import Path
import subprocess

from external_workload import load
from prepare_python_schedule_matrix import ROOT,ROLES

BACKBONE=('inspect','implementation','full_roundtrip','boundaries_and_uv','final')
RANGES=dict(zip(BACKBONE,((1,5),(6,24),(25,27),(28,29),(30,36))))
for role in ROLES[1:]:RANGES[role+'_finish']=(25 if role.startswith('early_') else 30,36)
PATHS={'backbone':list(BACKBONE)}
PATHS.update({r:list(BACKBONE[:2 if r.startswith('early_') else 4])+[r+'_finish'] for r in ROLES[1:]})
EXPECTED={s:dict(actual_actions=252 if s=='grpo' else 93,logical_actions=252,
    saves=0 if s=='grpo' else 2,restores=0 if s=='grpo' else 6) for s in ('grpo','bpo','tvcache')}
NONZERO={3:2,11:1,12:1,16:1,18:1}
PAYLOADS=tuple(f'{i:03}.payload' for i in (4,6,7,8,9,10,17,19,23))
SOURCE_STATE_EXPR="{n:(Path('/app')/n).read_text() if (Path('/app')/n).is_file() else None for n in ['compress.py','decompress.py','pyproject.toml','uv.lock']}"
SOURCE_STATE_CODE="import json;from pathlib import Path;print(json.dumps("+SOURCE_STATE_EXPR+"))"


def volume_gib(schedule):
    assert schedule in EXPECTED
    return 80


RUNNER=r'''#!/bin/bash
set +e
cd /app || exit 93
umask 077
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
  (cd /app && /bin/bash "$MIXFS_WORKLOAD_ROOT/original/$(printf '%03d' "$original_step").sh") > "$out/stdout/$step.log" 2> "$out/stderr/$step.log"
  status=$?
  end_ns=$("$MIXFS_WORKLOAD_ROOT/clock-ns") || exit 94
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$branch" "$branch" "$round" "$step" "$status" "$((end_ns-start_ns))" >> "$out/steps.tsv"
done < "$ACTIONS_ROOT/$round/actions.tsv"
python3 "$MIXFS_WORKLOAD_ROOT/source-state.py" > "$out/source-state.json" || exit 94
cp "$ACTIONS_ROOT/$round/actions.tsv" "$out/actions.tsv" || exit 94
sample
printf 'waiting\t%s\t999\n' "$round" > /workspace/asb-runtime-monitor/phase
'''


def final_sources(files):
    before='RECORD_STRUCT = struct.Struct("<IIIIIq")'
    after='RECORD_STRUCT = struct.Struct("<IIIIIIq")'
    assert files['010.payload'].count(before)==1
    return {'compress.py':files['023.payload'],'decompress.py':files['010.payload'].replace(before,after),
            'pyproject.toml':files['004.payload']}


def prepare(upstream,output):
    cfg,replay,reference=load(ROOT/'configs/external-c4-light.json',upstream)
    assert subprocess.check_output(['git','-C',upstream,'rev-parse','HEAD'],text=True,timeout=10).strip()==cfg['revision']
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    workload=output/'workload';(workload/'original').mkdir(parents=True)
    for name in [*(f'{i:03}.sh' for i in range(1,37)),*PAYLOADS]:
        (workload/'original'/name).write_text(replay['files'][name])
    samples=[]
    for segment,(begin,end) in RANGES.items():
        directory=workload/'segments'/segment;directory.mkdir(parents=True)
        with (directory/'actions.tsv').open('w') as stream:
            writer=csv.writer(stream,delimiter='\t');writer.writerow(('step','original_step','exit_code'))
            actions=[]
            for i,original in enumerate(range(begin,end+1),1):
                code=int(reference[original-1]['exit_code']);assert code==NONZERO.get(original,0)
                writer.writerow((f'{i:03}',original,code));actions.append(dict(original_step=original,exit_code=code))
        samples.append(dict(role=segment[:-7] if segment.endswith('_finish') else 'backbone',segment=segment,actions=actions))
    (workload/'rollouts.json').write_text(json.dumps(PATHS,indent=2)+'\n')
    (workload/'agent-sampling.json').write_text(json.dumps(dict(segments=samples),indent=2)+'\n')
    (workload/'guest-runner.sh').write_text(RUNNER)
    (workload/'source-state.py').write_text(SOURCE_STATE_CODE+'\n')
    subprocess.run(['gcc','-O2','-Wall','-Wextra','-Werror','-static',Path(upstream)/'experiments/container-screening/harness/clock_ns.c','-o',workload/'clock-ns'],check=True,timeout=60)
    (output/'final-sources.json').write_text(json.dumps(final_sources(replay['files']),indent=2)+'\n')
    value=dict(cfg,upstream=str(Path(upstream).resolve()),source_image=cfg['image'],guest_cpu=2,guest_memory_mib=8192,
        workload=str(workload.relative_to(ROOT)),path=list(BACKBONE),checkpoint_after_original_steps=[24,29],expected=EXPECTED,
        workload_kind='controlled_same_path_prefix_reuse_not_native_seven_branches')
    (output/'config.json').write_text(json.dumps(value,indent=2)+'\n');validate(output);return value


def validate(prepared):
    prepared=Path(prepared).resolve();cfg=json.loads((prepared/'config.json').read_text())
    fixed=json.loads((ROOT/'configs/external-c4-light.json').read_text());assert all(cfg.get(k)==v for k,v in fixed.items())
    assert cfg['source_image']==fixed['image'] and cfg['guest_cpu']==2 and cfg['guest_memory_mib']==8192
    assert cfg['expected']==EXPECTED and cfg['checkpoint_after_original_steps']==[24,29] and cfg['path']==list(BACKBONE)
    assert cfg['workload_kind']=='controlled_same_path_prefix_reuse_not_native_seven_branches'
    source=ROOT/cfg['workload'];assert source==prepared/'workload'
    upstream=json.loads((Path(cfg['upstream'])/cfg['run']/'replay-inputs.json').read_text())
    for name in [*(f'{i:03}.sh' for i in range(1,37)),*PAYLOADS]:assert (source/'original'/name).read_text()==upstream['files'][name]
    assert json.loads((prepared/'final-sources.json').read_text())==final_sources(upstream['files'])
    assert (source/'guest-runner.sh').read_text()==RUNNER and (source/'source-state.py').read_text()==SOURCE_STATE_CODE+'\n'
    assert json.loads((source/'rollouts.json').read_text())==PATHS
    samples=json.loads((source/'agent-sampling.json').read_text())['segments'];expected_samples=[]
    for segment,(begin,end) in RANGES.items():
        with (source/'segments'/segment/'actions.tsv').open() as stream:entries=list(csv.DictReader(stream,delimiter='\t'))
        assert entries==[dict(step=f'{i:03}',original_step=str(original),exit_code=str(NONZERO.get(original,0))) for i,original in enumerate(range(begin,end+1),1)]
        expected_samples.append(dict(role=segment[:-7] if segment.endswith('_finish') else 'backbone',segment=segment,
            actions=[dict(original_step=i,exit_code=NONZERO.get(i,0)) for i in range(begin,end+1)]))
    assert samples==expected_samples
    for path in PATHS.values():assert [i for s in path for i in range(RANGES[s][0],RANGES[s][1]+1)]==list(range(1,37))
    return cfg,source,samples,list(RANGES)


def task_plan(prepared):
    cfg,_,_,_=validate(prepared)
    return dict(task_id=cfg['task_id'],profile='c4',workload=cfg['workload'],expected=EXPECTED,workload_kind=cfg['workload_kind'],
        cpu_per_guest=2,memory_mib_per_guest=8192,initial_lower_reference_count=9,checkpoint_after_original_steps=[24,29])


def validate_role_outputs(raw,role,segments):
    for original,markers in {33:('CONSTRAINT VIOLATIONS: NONE','FINAL VERDICT: BYTE-EXACT ROUNDTRIP ON c4_sample'),34:('9898 files, structure + content',)}.items():
        selected=[s for s in segments if RANGES[s][0]<=original<=RANGES[s][1]];assert len(selected)==1
        segment=selected[0];step=original-RANGES[segment][0]+1
        text=(Path(raw)/'rollouts'/role/segment/'stdout'/f'{step:03}.log').read_text()
        assert all(marker in text for marker in markers),f'C4 {role} original step {original} result missing'
    return dict(status='PASS',original_final_steps=[33,34],sample_files=9898)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--upstream',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();prepare(a.upstream,a.output);print('status=PREPARED business_runs=0 result_dir='+str(a.output))
