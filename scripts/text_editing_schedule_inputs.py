"""Pinned text editing: seven identical original paths with 33/46-step checkpoints."""
import argparse
import csv
import json
from pathlib import Path
import subprocess

from external_workload import load
from prepare_python_schedule_matrix import ROOT, ROLES

BACKBONE = ('inspect', 'explore', 'diagnose', 'derive_final', 'verify_final')
RANGES = dict(zip(BACKBONE, ((1,4),(5,33),(34,43),(44,46),(47,50))))
for role in ROLES[1:]:RANGES[role+'_finish'] = (34 if role.startswith('early_') else 47,50)
PATHS = {'backbone':list(BACKBONE)}
PATHS.update({r:list(BACKBONE[:2 if r.startswith('early_') else 4])+[r+'_finish'] for r in ROLES[1:]})
EXPECTED = {s:dict(actual_actions=350 if s=='grpo' else 113,logical_actions=350,
    saves=0 if s=='grpo' else 2,restores=0 if s=='grpo' else 6) for s in ('grpo','bpo','tvcache')}
NONZERO = {19:1,37:1,43:127}
AUXILIARY = ('001-read.py','002-read.py','005.payload','018.payload','027.payload','028.payload','048.payload','049.payload')
SMALL_FILES = ('/app/apply_macros.vim','/app/solve.sh','/tmp/y2.vim','/tmp/z1.vim','/tmp/z2.vim') + tuple('/probe/input/'+n for n in AUXILIARY)
DATA_FILES = ('/app/input.csv','/app/expected.csv','/app/output.csv','/tmp/test_input.csv','/tmp/z2.csv')
SOURCE_STATE_EXPR = "dict(scripts={n:Path(n).read_text() if Path(n).is_file() else None for n in "+repr(SMALL_FILES)+"},metadata={n:dict(size=Path(n).stat().st_size,mode=Path(n).stat().st_mode,mtime_ns=Path(n).stat().st_mtime_ns) if Path(n).is_file() else None for n in "+repr(DATA_FILES)+"})"
SOURCE_STATE_CODE = "import json;from pathlib import Path;print(json.dumps("+SOURCE_STATE_EXPR+"))"


def volume_gib(schedule):
    assert schedule in EXPECTED
    return 64


def storage_components(schedule):
    assert schedule in EXPECTED
    restoring = schedule != 'grpo'
    return dict(writable_workload=28,ram_snapshots=16 if restoring else 0,
        image_layers=3 if restoring else 1,restore_scratch=4 if restoring else 0,margin=4,cleanup_reserve=.5)


RUNNER = r'''#!/bin/bash
set +e
cd /app || exit 93
umask 077
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


def boundary_code(prefix):
    return ("import json;from pathlib import Path;print(json.dumps(dict(marker=Path('/workspace/python-schedule-marker').read_text(),"
        "source="+SOURCE_STATE_EXPR+",steps={s:(Path('/workspace/artifacts/exp5/backbone')/s/'steps.tsv').read_text() for s in "+repr(prefix)+"})))")


def prepare(upstream, output):
    cfg,replay,reference = load(ROOT/'configs/external-text-editing-light.json',upstream)
    assert subprocess.check_output(['git','-C',upstream,'rev-parse','HEAD'],text=True,timeout=10).strip()==cfg['revision']
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    workload=output/'workload';(workload/'original').mkdir(parents=True)
    for name in [*(f'{i:03}.sh' for i in range(1,51)),*AUXILIARY]:
        (workload/'original'/name).write_text(replay['files'][name])
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
    (workload/'source-state.py').write_text(SOURCE_STATE_CODE+'\n')
    subprocess.run(['gcc','-O2','-Wall','-Wextra','-Werror','-static',Path(upstream)/'experiments/container-screening/harness/clock_ns.c','-o',workload/'clock-ns'],check=True,timeout=60)
    (output/'final-scripts.json').write_text(json.dumps({'/app/apply_macros.vim':replay['files']['048.payload'],'/app/solve.sh':replay['files']['049.payload']},indent=2)+'\n')
    value=dict(cfg,upstream=str(Path(upstream).resolve()),source_image=cfg['image'],
        guest_cpu=2,guest_memory_mib=8192,workload=str(workload.relative_to(ROOT)),path=list(BACKBONE),
        checkpoint_after_original_steps=[33,46],expected=EXPECTED,
        workload_kind='controlled_same_path_prefix_reuse_not_native_seven_branches')
    (output/'config.json').write_text(json.dumps(value,indent=2)+'\n');validate(output);return value


def validate(prepared):
    prepared=Path(prepared).resolve();cfg=json.loads((prepared/'config.json').read_text())
    fixed=json.loads((ROOT/'configs/external-text-editing-light.json').read_text());assert all(cfg.get(k)==v for k,v in fixed.items())
    assert cfg['source_image']==fixed['image']
    assert cfg['guest_cpu']==2 and cfg['guest_memory_mib']==8192 and cfg['expected']==EXPECTED
    assert cfg['checkpoint_after_original_steps']==[33,46] and cfg['path']==list(BACKBONE)
    assert cfg['workload_kind']=='controlled_same_path_prefix_reuse_not_native_seven_branches'
    source=ROOT/cfg['workload'];assert source==prepared/'workload'
    original=Path(cfg['upstream'])/cfg['run'];upstream=json.loads((original/'replay-inputs.json').read_text())
    assert json.loads((prepared/'final-scripts.json').read_text())=={'/app/apply_macros.vim':upstream['files']['048.payload'],'/app/solve.sh':upstream['files']['049.payload']}
    for name in [*(f'{i:03}.sh' for i in range(1,51)),*AUXILIARY]:
        assert (source/'original'/name).read_text()==upstream['files'][name]
    assert (source/'source-state.py').read_text()==SOURCE_STATE_CODE+'\n'
    assert (source/'guest-runner.sh').read_text()==RUNNER
    assert json.loads((source/'rollouts.json').read_text())==PATHS
    samples=json.loads((source/'agent-sampling.json').read_text())['segments'];expected_samples=[]
    for segment,(begin,end) in RANGES.items():
        with (source/'segments'/segment/'actions.tsv').open() as stream:entries=list(csv.DictReader(stream,delimiter='\t'))
        assert entries==[dict(step=f'{i:03}',original_step=str(original),exit_code=str(NONZERO.get(original,0))) for i,original in enumerate(range(begin,end+1),1)]
        expected_samples.append(dict(role=segment[:-7] if segment.endswith('_finish') else 'backbone',segment=segment,
            actions=[dict(original_step=i,exit_code=NONZERO.get(i,0)) for i in range(begin,end+1)]))
    assert samples==expected_samples
    for path in PATHS.values():assert [i for s in path for i in range(RANGES[s][0],RANGES[s][1]+1)]==list(range(1,51))
    return cfg,source,samples,list(RANGES)


def task_plan(prepared):
    cfg,_,_,_=validate(prepared)
    return dict(task_id=cfg['task_id'],profile='text_editing',workload=cfg['workload'],expected=EXPECTED,
        workload_kind=cfg['workload_kind'],cpu_per_guest=2,memory_mib_per_guest=8192,
        initial_lower_reference_count=8,checkpoint_after_original_steps=[33,46])


def validate_role_outputs(raw, role, segments):
    markers={47:['=== FULL FILE MATCHES EXPECTED ==='],50:['=== SOLVE.SH OUTPUT MATCHES EXPECTED ===','=== -S SCRIPT VARIANT ALSO MATCHES ===']}
    for original,wanted in markers.items():
        selected=[s for s in segments if RANGES[s][0]<=original<=RANGES[s][1]];assert len(selected)==1
        segment=selected[0];step=original-RANGES[segment][0]+1
        text=(Path(raw)/'rollouts'/role/segment/'stdout'/f'{step:03}.log').read_text()
        assert all(m in text for m in wanted),f'Text editing {role} original step {original} result missing'
    return dict(status='PASS',original_final_steps=[47,50],comparison='BYTE_EXACT_ORIGINAL_DIFFS')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--upstream',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();prepare(args.upstream,args.output);print('status=PREPARED business_runs=0 result_dir='+str(args.output))
