"""Two fixed local actions compare boundary memory methods; never starts a sandbox."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
from prototype_boundary_memory import START_CODE,END_CODE
ORIGINAL_CODE='import time; print(time.monotonic_ns())'
MEMINFO='''Unrelated: 555 kB
Inactive(file): 222 kB
Cached: 512 kB
MemFree: 1024 kB
MemTotal: 4096 kB
Buffers: 64 kB
Shmem: 32 kB
AnonPages: 128 kB
Active(file): 111 kB
'''
HEADER='timestamp_ns\tphase\tsandbox\tbranch\tround\tstep\tMemTotal_kib\tMemFree_kib\tBuffers_kib\tCached_kib\tShmem_kib\tAnonPages_kib\tActive_file_kib\tInactive_file_kib\tDirty_kib\n'
VM_HEADER='timestamp_ns\tphase\tsandbox\tbranch\tround\tstep\tpgfault\tpgmajfault\tpgpgin\tpgpgout\n'


def rows(path):
    with path.open() as stream:return list(csv.DictReader(stream,delimiter='\t'))


def reference_function(text):
    begin=text.index('sample_memory() {\n');end=text.index('\n}\n',begin)+len('\n}')
    function=text[begin:end]
    assert function.count('/proc/meminfo')==1 and function.count('/workspace/asb-runtime-monitor')==2
    assert function.count('date +%s%N')==1 and function.count('  awk ')==1
    return function.replace('/proc/meminfo','"$meminfo"').replace('/workspace/asb-runtime-monitor','"$monitor_dir"')


def timer_command(code,phase):
    return 'python3 -c '+shlex.quote(code)+' "$helper_dir" '+phase+' "$step" "$out/guest-memory.tsv" "$monitor_dir/phase" "$meminfo" "$sandbox_label" "$branch" "$round"'


def fixture_script(function,merged):
    before='  sample_memory before "$step"\n  printf "running\\t%s\\t%s\\n" "$round" "$step" > "$monitor_dir/phase"\n  start_ns="$(python3 -c '+shlex.quote(ORIGINAL_CODE)+')"'
    after='  end_ns="$(python3 -c '+shlex.quote(ORIGINAL_CODE)+')"\n  sample_memory after "$step"'
    if merged:
        before='  start_ns="$('+timer_command(START_CODE,'before')+')"'
        after='  end_ns="$('+timer_command(END_CODE,'after')+')"'
    return '''#!/usr/bin/env bash
set -euo pipefail
out=$1
monitor_dir=$2
meminfo=$3
helper_dir=$4
round=segment
branch=role
sandbox_label=role
printf %s '''+shlex.quote(HEADER)+''' > "$out/guest-memory.tsv"
printf %s '''+shlex.quote(VM_HEADER)+''' > "$out/guest-vmstat.tsv"
printf 'branch\tround\tstep\texit_code\tduration_ns\n' > "$out/steps.tsv"
capture_phase() {
  local p r s
  IFS=$'\\t' read -r p r s < "$monitor_dir/phase"
  printf '%s\\t%s\\t%s\\t%s\\n' "$1" "$p" "$r" "$s" >> "$out/phases.tsv"
}
'''+function+'''
printf 'checkpoint\tphase\tround\tstep\n' > "$out/phases.tsv"
sample_memory baseline 000
capture_phase baseline
for step in 001 002; do
  if [[ $step == 001 ]]; then
    command='printf "out-001\\n"; printf "err-001\\n" >&2; true'
  else
    command='printf "out-002\\n"; printf "err-002\\n" >&2; false'
  fi
  printf '%s\\t%s\\n' "$step" "$command" >> "$out/actions.tsv"
'''+before+'''
  set +e
  bash -o pipefail -c "$command" > "$out/stdout/$step.log" 2> "$out/stderr/$step.log"
  status=$?
  set -e
'''+after+'''
  capture_phase after
  printf '%s\\t%s\\t%s\\t%s\\t%s\\n' "$branch" "$round" "$step" "$status" "$((end_ns-start_ns))" >> "$out/steps.tsv"
done
sample_memory complete 999
capture_phase complete
'''


def inspect(directory):
    memory=rows(directory/'guest-memory.tsv');steps=rows(directory/'steps.tsv')
    assert list(memory[0])==HEADER.rstrip('\n').split('\t')
    assert [(r['phase'],r['step']) for r in memory]==[('baseline','000'),('before','001'),('after','001'),('before','002'),('after','002'),('complete','999')]
    assert all([int(r[k]) for k in list(r)[6:]]==[4096,1024,64,512,32,128,111,222,0] for r in memory)
    assert all(r['sandbox']==r['branch']=='role' and r['round']=='segment' for r in memory)
    assert all(re.fullmatch(r'\d{19}',r['timestamp_ns']) for r in memory)
    assert all(int(b['timestamp_ns'])>=int(a['timestamp_ns']) for a,b in zip(memory,memory[1:]))
    assert [int(r['exit_code']) for r in steps]==[0,1]
    assert [r['step'] for r in steps]==['001','002'] and all(int(r['duration_ns'])>0 for r in steps)
    assert (directory/'guest-vmstat.tsv').read_text()==VM_HEADER
    for step in ('001','002'):
        assert (directory/f'stdout/{step}.log').read_text()==f'out-{step}\n'
        assert (directory/f'stderr/{step}.log').read_text()==f'err-{step}\n'
    phases=rows(directory/'phases.tsv')
    assert [r['phase'] for r in phases]==['baseline','after','after','waiting']
    calls=Counter((directory/'calls.log').read_text().splitlines())
    assert set(calls)=={'date','awk','python3'}
    return dict(memory=memory,steps=steps,phases=phases,calls=dict(calls),
        boundary_nonfree_peak_bytes=max((int(r['MemTotal_kib'])-int(r['MemFree_kib']))*1024 for r in memory))


def probe(out,runner):
    # Only the inspected memory function and timer lines are reused; task actions are not executed.
    text=runner.read_text();function=reference_function(text)
    assert 'sample_vmstat() { :; }' in text
    assert text.count('python3 -c '+shlex.quote(ORIGINAL_CODE))==2
    out.mkdir(parents=True,exist_ok=False);(out/'meminfo.txt').write_text(MEMINFO)
    shutil.copy2(ROOT/'scripts/prototype_boundary_memory.py',out/'prototype_boundary_memory.py')
    tools={name:shutil.which(name) for name in ('bash','date','awk','python3')};assert all(tools.values())
    shim=out/'bin';shim.mkdir()
    for name in ('date','awk','python3'):
        path=shim/name
        path.write_text('#!'+tools['bash']+'\nprintf "%s\\n" '+shlex.quote(name)+' >> "$COUNT_FILE"\nexec '+shlex.quote(tools[name])+' "$@"\n')
        path.chmod(0o755)
    outputs=[]
    for method in ('reference','merged'):
        work=out/method;work.mkdir();(work/'stdout').mkdir();(work/'stderr').mkdir();monitor=work/'monitor';monitor.mkdir()
        script=work/'runner.sh';script.write_text(fixture_script(function,method=='merged'))
        environment=dict(PATH=str(shim)+':/usr/bin:/bin',HOME=str(work),LC_ALL='C',
            PYTHONDONTWRITEBYTECODE='1',COUNT_FILE=str(work/'calls.log'),NO_PROXY='*',no_proxy='*')
        command=[tools['bash'],str(script),str(work),str(monitor),str(out/'meminfo.txt'),str(out)]
        process=subprocess.run(command,env=environment,text=True,capture_output=True,timeout=15)
        (work/'runner.stdout').write_text(process.stdout);(work/'runner.stderr').write_text(process.stderr)
        assert process.returncode==0,method+': '+process.stderr
        outputs.append(inspect(work))
    a,b=outputs
    assert [{k:v for k,v in r.items() if k!='timestamp_ns'} for r in a['memory']]==[{k:v for k,v in r.items() if k!='timestamp_ns'} for r in b['memory']]
    assert [{k:v for k,v in r.items() if k!='duration_ns'} for r in a['steps']]==[{k:v for k,v in r.items() if k!='duration_ns'} for r in b['steps']]
    assert a['phases']==b['phases'] and a['boundary_nonfree_peak_bytes']==b['boundary_nonfree_peak_bytes']
    assert (out/'reference/actions.tsv').read_bytes()==(out/'merged/actions.tsv').read_bytes()
    assert a['calls']=={'date':6,'awk':6,'python3':4} and b['calls']=={'date':2,'awk':2,'python3':4}
    value=dict(status='PASS_LOCAL_TWO_ACTION_PROTOTYPE',scope='Fixed meminfo and synthetic success/failure actions only; no sandbox or real proc counters',
        source_runner=str(runner),methods=['reference','merged'],per_method_actions=2,per_method_boundary_rows=6,
        action_exit_codes=[0,1],stdout_stderr_equal=True,memory_values_and_metadata_equal=True,dynamic_timestamps_compared=False,
        dynamic_duration_values_compared=False,phase_checkpoints_equal=True,final_phase='waiting',missing_Dirty_written_as_zero=True,
        reference_calls=a['calls'],merged_calls=b['calls'],date_awk_calls_removed=8,timer_python_calls_unchanged=4,
        observed_boundary_nonfree_peak_bytes=b['boundary_nonfree_peak_bytes'],
        prototype_implementation='scripts/prototype_boundary_memory.py',runtime_entry_integrated=False,
        guest_performance='NOT_MEASURED',timing_scope='Whole command interval retains Python timer startup; sample body outside captured monotonic timestamps',
        new_live_guests=0,new_live_actions=0,runtime_settings_changed=False)
    (out/'result.json').write_text(json.dumps(value,indent=2)+'\n')
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runner',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();value=probe(args.output.resolve(),args.runner.resolve())
    print('status='+value['status']+' result_dir='+str(args.output))


if __name__=='__main__':main()
