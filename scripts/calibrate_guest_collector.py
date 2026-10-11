"""One owned VM, fixed-work guest collector controls; host periodic sampling disabled."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import tarfile
from statistics import mean

from guest_runner_lifecycle import collect, instrument
from persistent_guest_sampling import instrument as persistent

ITERATIONS=1_000_000_000
ORDER=('off','on','on','off')
TOTAL=4+32


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def schedule():return [dict(stage='smoke' if i<4 else 'measured',index=i,label=ORDER[i%4]) for i in range(TOTAL)]


def prepare(inputs):
    base=inputs/'collector-calibration';base.mkdir()
    fixture=base/'fixture';fixture.mkdir();(fixture/'sample').write_text('unchanged\n')
    for args in (['init','-q'],['config','user.name','fixture'],['config','user.email','fixture@localhost'],['add','-A'],['commit','-qm','fixture']):
        subprocess.run(['git','-C',str(fixture),*args],check=True,capture_output=True,timeout=15)
    original=(inputs/'lifecycle-original.sh').read_text();(base/'original.sh').write_text(original)
    actions=base/'actions';actions.mkdir()
    command=f'{shlex.quote(str(inputs/"command-clock"))} "$OUTPUT_DIR/fixed-work-clock.json" {shlex.quote(str(inputs/"fixed-work"))} {ITERATIONS}\n'
    (actions/'001.sh').write_text(command);(base/'actions.tsv').write_text('synthetic fixed operation count; not Exp1 actions\n')
    runners=base/'runners';runners.mkdir()
    for label in ('off','on'):
        source=persistent(original) if label=='on' else original
        (runners/(label+'.sh')).write_text(instrument(source,collector=label=='on').replace('/testbed',str(fixture)))
    for row in schedule():(base/f'case-{row["index"]:02d}'/'replay').mkdir(parents=True)
    metadata=dict(iterations=ITERATIONS,order=list(ORDER),total=TOTAL,fixture=str(fixture),
        inputs=str(inputs),original_sha256=sha(base/'original.sh'),action_sha256=sha(actions/'001.sh'),
        work_binary_sha256=sha(inputs/'fixed-work'),clock_binary_sha256=sha(inputs/'command-clock'),
        runner_sha256={label:sha(runners/(label+'.sh')) for label in ('off','on')},
        collector_sha256=sha(inputs/'calibration-collector.py'),sample_interval_seconds=.1,
        host_periodic_sampling=False,cache_policy='same warm fixture; no cache drop; fixed work each call',
        scope='synthetic single-step fixed CPU work; two guest clocks in both conditions; not task acceptance')
    (base/'metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')


def inspect_case(directory,label,controller_ns,expected_output=None):
    replay=directory/'replay';steps=list(csv.DictReader((replay/'steps.tsv').read_text().splitlines(),delimiter='\t'))
    assert len(steps)==1 and steps[0]['step']=='001' and steps[0]['exit_code']=='0'
    assert not (replay/'stderr/001.log').read_bytes() and not (replay/'patch.diff').read_bytes()
    output=(replay/'stdout/001.log').read_bytes()
    assert output.startswith(f'iterations={ITERATIONS} checksum='.encode())
    assert len(output)==len(f'iterations={ITERATIONS} checksum=')+17
    if expected_output is not None:assert output==expected_output,'fixed-work output changed'
    work=json.loads((replay/'fixed-work-clock.json').read_text())
    assert work['exit_code']==0 and work['monotonic_raw_ns']>0
    assert abs(work['realtime_ns']-work['monotonic_raw_ns'])<=2_000_000
    life=collect(replay,label=='on',int(steps[0]['duration_ns']),controller_ns)
    assert work['monotonic_raw_ns']<=int(steps[0]['duration_ns'])+2_000_000
    with (replay/'guest-memory.tsv').open() as stream:memory=list(csv.DictReader(stream,delimiter='\t'))
    row=dict(label=label,controller_ns=controller_ns,work_clock=work,lifecycle=life,
        output_sha256=sha(replay/'stdout/001.log'),step_ns=int(steps[0]['duration_ns']),sample_count=len(memory))
    if label=='on':
        receipt=json.loads((replay/'collector-status.json').read_text())
        assert receipt['status']=='PASS' and receipt['samples']==len(memory) and len(memory)>=3
        row['collector']=receipt
    else:assert not memory and not (replay/'collector-status.json').exists()
    return row


def summarize(rows):
    formal=[r for r in rows if r['stage']=='measured'];assert len(formal)==32
    result={}
    for metric,get in dict(work_ns=lambda r:r['work_clock']['monotonic_raw_ns'],
        guest_envelope_ns=lambda r:r['lifecycle']['clock']['monotonic_raw_ns'],
        controller_ns=lambda r:r['controller_ns']).items():
        values={label:[get(r) for r in formal if r['label']==label] for label in ('off','on')}
        groups=[]
        for start in range(0,32,4):
            group=formal[start:start+4];assert [r['label'] for r in group]==list(ORDER)
            a=mean(get(r) for r in group if r['label']=='off');b=mean(get(r) for r in group if r['label']=='on')
            groups.append(100*(b/a-1))
        a=mean(values['off']);b=mean(values['on'])
        result[metric]=dict(off_mean_ns=a,on_mean_ns=b,difference_ns=b-a,difference_percent=100*(b/a-1),
            within_vm_block_percent=groups,off_min_ns=min(values['off']),off_max_ns=max(values['off']),
            on_min_ns=min(values['on']),on_max_ns=max(values['on']))
    on=[r for r in formal if r['label']=='on']
    result['collector']=dict(startup_mean_ns=mean(r['lifecycle']['durations_ns']['collector_startup_ns'] for r in on),
        stop_mean_ns=mean(r['lifecycle']['durations_ns']['collector_stop_ns'] for r in on),
        process_cpu_mean_seconds=mean(r['collector']['cpu_user_seconds']+r['collector']['cpu_system_seconds'] for r in on),
        sample_count=sum(r['sample_count'] for r in on))
    return result


def audit(base,host_record):
    assert host_record['status']=='PASS' and len(host_record['runs'])==TOTAL
    meta=json.loads((base/'metadata.json').read_text());original=(base/'original.sh').read_text()
    assert meta['iterations']==ITERATIONS and meta['host_periodic_sampling'] is False
    assert sha(base/'original.sh')==meta['original_sha256'] and sha(base/'actions/001.sh')==meta['action_sha256']
    for label in ('off','on'):
        source=persistent(original) if label=='on' else original
        assert (base/'runners'/f'{label}.sh').read_text()==instrument(source,label=='on').replace('/testbed',meta['fixture'])
    expected=(base/'case-00/replay/stdout/001.log').read_bytes()
    for row,case in zip(host_record['runs'],schedule()):
        assert all(row[k]==v for k,v in case.items())
        checked=inspect_case(base/f'case-{row["index"]:02d}',row['label'],row['controller_ns'],expected)
        for k,v in checked.items():assert row[k]==v,k
    assert summarize(host_record['runs'])==host_record['analysis']
    return dict(status='PASS',short_call_count=TOTAL,formal_call_count=32,analysis=host_record['analysis'],
        inference='eight repeated ABBA blocks in one VM, descriptive only; not independent VM replications or task overhead verdict')


async def run(sb,out,tool,guest='/opt/asb-exp1'):
    record=dict(status='FAIL',runs=[]);error=None
    base=guest+'/collector-calibration'
    try:
        await tool(sb,'prepare-collector-calibration',f'python3 {guest}/calibrate_guest_collector.py --prepare {guest}',timeout=30)
        expected=None
        for case in schedule():
            index=case['index'];label=case['label'];directory=base+f'/case-{index:02d}/replay'
            env=dict(ACTIONS_DIR=base+'/actions',OUTPUT_DIR=directory,ACTION_TIMEOUT_SECONDS='15',
                MEMORY_SAMPLE_INTERVAL_SECONDS='0.1',GUEST_MEMORY_SAMPLING='1' if label=='on' else '0',
                DROP_GUEST_CACHES='0',START_STEP='001',END_STEP='026',ASB_GUEST_MEMORY_COLLECTOR=guest+'/calibration-collector.py')
            name=f'collector-case-{index:02d}-{label}'
            await tool(sb,name,f'{guest}/command-clock {directory}/lifecycle-envelope.json bash {base}/runners/{label}.sh',variables=env,timeout=30)
            # Retrieve this case before proceeding; failures cannot erase prior outputs.
            await tool(sb,'pack-'+name,f'tar -C {base}/case-{index:02d} -czf /dev/shm/asb-collector-case.tar.gz .')
            archive=out/f'collector-case-{index:02d}.tar.gz'
            archive.write_bytes(await sb.download_file('/dev/shm/asb-collector-case.tar.gz',timeout=30))
            case_dir=out/'collector-calibration-cases'/f'case-{index:02d}'
            case_dir.mkdir(parents=True)
            with tarfile.open(archive,'r:gz') as f:f.extractall(case_dir,filter='data')
            controller_ns=json.loads((out/(name+'.json')).read_text())['controller_elapsed_ns']
            row=inspect_case(case_dir,label,controller_ns,expected);row.update(case)
            if expected is None:expected=(case_dir/'replay/stdout/001.log').read_bytes()
            record['runs'].append(row)
            (out/'collector-calibration-progress.json').write_text(json.dumps(record,indent=2)+'\n')
        record.update(status='PASS',analysis=summarize(record['runs']))
    except BaseException as exc:error=exc;record['error']=repr(exc)
    (out/'collector-calibration-result.json').write_text(json.dumps(record,indent=2)+'\n')
    try:
        await tool(sb,'pack-collector-calibration',f'tar -C {base} -czf /dev/shm/asb-collector-calibration.tar.gz .')
        archive=out/'collector-calibration.tar.gz'
        archive.write_bytes(await sb.download_file('/dev/shm/asb-collector-calibration.tar.gz',timeout=30))
        with tarfile.open(archive,'r:gz') as f:f.extractall(out/'collector-calibration',filter='data')
    except BaseException as download_error:
        if error:raise BaseExceptionGroup('collector calibration and download failed',[error,download_error])
        raise
    if error:raise error
    checked=audit(out/'collector-calibration',record)
    (out/'collector-calibration-audit.json').write_text(json.dumps(checked,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',type=Path,required=True)
    prepare(parser.parse_args().prepare)
