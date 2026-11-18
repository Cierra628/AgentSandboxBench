"""Two fixed local Prettier GRPO pairs: sampling, boundary vmstat or boundary meminfo."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import tomllib
import traceback
import urllib.request

from prettier_no_proxy import PROXY_KEYS,clean_environment
from probe_storage_boundary import identities
from run_light_monitor_controls import SOURCE,SOURCE_ID
from run_grpo_light_pair import inspect_run,rows,read,interval_union

ROOT=Path(__file__).resolve().parents[1]
IMAGE='sha256:db9eb044ae3d85f9b276b9d2e5dea28cb0be95263d217ea4989f095e1d828681'
ORDER=((1,'off'),(1,'on'),(2,'on'),(2,'off'))
BOUNDARY_ORDER=((1,'keep'),(1,'skip'),(2,'skip'),(2,'keep'))
MEMINFO_ORDER=((1,'separate'),(1,'merged'),(2,'merged'),(2,'separate'))
GIB=1024**3


def planned_runs(comparison='sampling'):
    if comparison not in ('sampling','boundary-vmstat','boundary-meminfo'):raise ValueError('unsupported comparison')
    result=[]
    for pair,variant in (MEMINFO_ORDER if comparison=='boundary-meminfo' else BOUNDARY_ORDER if comparison=='boundary-vmstat' else ORDER):
        mode='on' if comparison!='sampling' else variant
        skip=comparison=='boundary-meminfo' or comparison=='boundary-vmstat' and variant=='skip'
        merged=comparison=='boundary-meminfo' and variant=='merged'
        suffix='-merged-meminfo' if merged else '-skip-vmstat' if skip else '-off' if mode=='off' else ''
        result.append(dict(pair=pair,variant=variant,mode=mode,skip_vmstat=skip,
            config='configs/platform-prettier6604-grpo-host-current'+suffix+'.json'))
        if comparison=='boundary-meminfo':result[-1]['merge_meminfo']=merged
    return result


def check(work,mode,*,skip_vmstat=False,merge_meminfo=False):
    assert not skip_vmstat or mode=='on'
    assert not merge_meminfo or skip_vmstat
    r=inspect_run(work,mode)
    w=read(work/'result.json');storage=Path(w['storage_result_dir']);n=read(storage/'result.json')
    raw=Path(r['raw']);task=read(raw/'result.json');report=Path(r['report'])
    assert task.get('light_skip_boundary_vmstat',False)==skip_vmstat
    assert task.get('light_merge_boundary_meminfo',False)==merge_meminfo
    assert read(work/'effective-config.json').get('light_merge_boundary_meminfo',False)==merge_meminfo
    assert read(work/'effective-config.json').get('light_skip_boundary_vmstat',False)==skip_vmstat
    assert task['local_no_proxy']==w['local_no_proxy']['guest_check']=='PASS'
    assert task['grpo_setup_schedule']=='per-guest-setup'
    guest_ids=[v['sandbox_id'] for v in rows(raw/'sandbox-ids.tsv')]
    assert len(set(guest_ids))==len(guest_ids)==7
    assert set(task['local_no_proxy_guests'].values())==set(guest_ids)
    assert all(n['checks'][k]=='PASS' for k in ('actual_writable_files_inside_volume','readonly_root_rejects_write','fixed_backing_allocation'))
    assert n['readonly_commits_bytes']>0
    events=read(raw/'events.json')
    probes=[e for e in events if 'guest-no-proxy-check-' in e['stage']]
    assert len(probes)==7 and all(e['exit_code']==0 for e in probes)
    registry=(storage/'service-logs/local-no-proxy-regctl.log').read_text().splitlines()
    assert registry and set(registry)<={'version','manifest'}
    server=(storage/'service-logs/server.log').read_text().lower()
    assert 'downloading' not in server and 'pulling from container registry' not in server
    q=w['input_file_check']
    assert w['input_file_validation']=='PASS' and q['status']=='OBSERVED' and q['shortfall_bytes']==0
    assert q['unique_file_count']==105 and q['ownership']=='NOT_QUERIED'
    assert q['phase']=='AFTER_ISOLATED_RUNTIME_CLEANUP' and q['start_monotonic_ns']>=w['isolated_runtime_end_monotonic_ns']
    cache=Path(w['input_directory'])
    assert not (cache/'input').exists() and not (cache/'source.ext4').exists()
    assert n['host_file_creation']['host_sampling']==mode
    boundary={name:sum(len(rows(raw/'rollouts'/s['rollout']/s['segment']/name))
        for s in rows(raw/'segments.tsv')) for name in ('guest-memory.tsv','guest-vmstat.tsv')}
    sampling=dict(mode=mode,boundary_rows=boundary,pss='NOT_REQUESTED')
    if mode=='off':
        assert boundary==dict.fromkeys(('guest-memory.tsv','guest-vmstat.tsv'),0)
        assert w['host_file_monitor']==dict(status='NOT_REQUESTED',host_sampling='off')
        assert n['host_file_creation']['before_guest_counters'] is None
        assert not (work/'host-file').exists() and not (storage/'host-file-residency.json').exists()
        assert not list(report.rglob('*.png'))
        sampling.update(host_cpu_seconds=None,guest_cpu_seconds=None,memory='NOT_MEASURED')
    else:
        assert boundary=={'guest-memory.tsv':448,'guest-vmstat.tsv':0 if skip_vmstat else 448}
        assert w['host_file_monitor']['status']=='PASS' and not list((work/'host-file').glob('*pss*'))
        m=read(raw/'runtime-monitor.json');host=read(report/'host-file/summary.json')
        receipts=[s['receipt'] for s in m['sessions']]
        assert len(receipts)==7 and {s['sandbox_id'] for s in m['sessions']}==set(guest_ids)
        span=(host['current']['window_end_monotonic_ns']-host['current']['window_start_monotonic_ns'])/1e9
        sampling.update(host_samples=w['host_file_monitor']['samples'],host_cpu_seconds=w['host_file_monitor']['thread_cpu_ns']/1e9,
            host_cpu_percent=100*w['host_file_monitor']['thread_cpu_ns']/1e9/span,
            host_span_seconds=span,host_max_gap_seconds=host['maximum_sampling_gap_ns']/1e9,
            guest_samples=sum(s['samples'] for s in receipts),
            guest_cpu_seconds=sum(s['cpu_user_seconds']+s['cpu_system_seconds'] for s in receipts),
            guest_cpu_percent_per_session=[100*(s['cpu_user_seconds']+s['cpu_system_seconds'])/(s['elapsed_ns']/1e9) for s in receipts])
        r['host_memory']=dict(current_baseline_bytes=host['current']['before_guest_bytes'],current_peak_bytes=host['current_peak_bytes'],
            current_peak_increase_bytes=host['current']['after_service_ready_peak_increase_bytes'],file_peak_bytes=host['file_peak_bytes'])
    source=w['source_preparation'];imp=w['input_import']
    r['timings_ns'].update(grouped_workflow=w['workflow_elapsed_ns'],
        archive_preparation=source['end_monotonic_ns']-source['start_monotonic_ns'],
        input_import=imp['import_end_monotonic_ns']-imp['import_start_monotonic_ns'],
        final_input_query=q['end_monotonic_ns']-q['start_monotonic_ns'])
    # Per-guest setup overlaps creation: only the interval union is additive within this set.
    cold=[e for e in events if re.sub(r'^\d+-','',e['stage']).startswith('cold-')]
    r['cold_requests']=[dict(stage=e['stage'],duration_ns=e['duration_ns']) for e in cold]
    r['cold_request_union_ns']=interval_union(cold)
    r.update(guest_ids=guest_ids,sampling=sampling,registry_commands=registry,
        query_cpu_seconds=q['thread_cpu_ns']/1e9,input_files=q['unique_file_count'],input_resident_bytes=q['resident_bytes'],
        input_group_file_bytes=max(q['counters_before']['file'],q['counters_after']['file']),ownership=q['ownership'])
    return r


def comparisons(runs,*,comparison='sampling'):
    assert len(runs)==4
    if comparison!='boundary-meminfo':
        assert not any(r.get('light_merge_boundary_meminfo',False) for r in runs), 'existing comparisons retain separate boundary meminfo'
    if comparison not in ('sampling','boundary-vmstat','boundary-meminfo'):raise ValueError('unsupported comparison')
    boundary=comparison=='boundary-vmstat'
    if boundary:
        assert [r['mode'] for r in runs]==['on']*4
        assert [r['boundary_vmstat_mode'] for r in runs]==['keep','skip','skip','keep']
        assert [r['pair'] for r in runs]==[1,1,2,2]
        for r in runs:
            skipped=r['boundary_vmstat_mode']=='skip'
            assert r['sampling']['mode']=='on'
            assert r.get('light_skip_boundary_vmstat',False)==skipped
            assert r['sampling']['boundary_rows']=={'guest-memory.tsv':448,'guest-vmstat.tsv':0 if skipped else 448}
            if skipped:assert r['boundary_vmstat']==dict(status='NOT_MEASURED',reason='explicitly skipped boundary vmstat',row_count=0,boundary_memory_rows=448)
    meminfo=comparison=='boundary-meminfo'
    if meminfo:
        from sandbox_runtime_monitor import boundary_meminfo_metadata
        assert [r['mode'] for r in runs]==['on']*4
        assert [r['pair'] for r in runs]==[1,1,2,2]
        assert [r['boundary_meminfo_mode'] for r in runs]==['separate','merged','merged','separate']
        for r in runs:
            assert r['sampling']['mode']=='on' and r.get('light_skip_boundary_vmstat',False)
            assert r['sampling']['boundary_rows']=={'guest-memory.tsv':448,'guest-vmstat.tsv':0}
            assert r['boundary_vmstat']==dict(status='NOT_MEASURED',reason='explicitly skipped boundary vmstat',row_count=0,boundary_memory_rows=448)
            assert r.get('light_merge_boundary_meminfo',False)==(r['boundary_meminfo_mode']=='merged')
            if r['boundary_meminfo_mode']=='merged':assert r['boundary_meminfo']==boundary_meminfo_metadata(189,35)
    result=[]
    for number in (1,2):
        pair=[r for r in runs if r['pair']==number]
        field='boundary_meminfo_mode' if meminfo else 'boundary_vmstat_mode' if boundary else 'mode';a,b=('separate','merged') if meminfo else ('keep','skip') if boundary else ('off','on')
        off=next(r for r in pair if r[field]==a);on=next(r for r in pair if r[field]==b)
        assert off['paths']==on['paths'] and off['patches']==on['patches'] and off['official']==on['official']
        assert off['timings_ns'].keys()==on['timings_ns'].keys()
        result.append(dict(pair=number,order=[r[field] for r in pair],timings={
            k:dict(**{a+'_seconds':off['timings_ns'][k]/1e9,b+'_seconds':on['timings_ns'][k]/1e9},
                change_seconds=(on['timings_ns'][k]-off['timings_ns'][k])/1e9,
                change_percent=100*(on['timings_ns'][k]/off['timings_ns'][k]-1) if off['timings_ns'][k] else None)
            for k in off['timings_ns']}))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--comparison',choices=('sampling','boundary-vmstat','boundary-meminfo'),default='sampling',
        help='sampling: off/on; boundary-vmstat: keep/skip; boundary-meminfo: separate/merged memory reads, both vmstat skipped')
    args=parser.parse_args();out=args.output.resolve()
    if not out.is_relative_to(ROOT/'.artifacts') or out.exists():parser.error('output must be a new directory inside .artifacts')
    os.umask(0o077);out.mkdir(parents=True)
    env=clean_environment(os.environ);env.update(ASB_AGENTENV_SOURCE_SERVICE=SOURCE,NO_PROXY='*',no_proxy='*')
    state=dict(status='FAIL',stage='preflight',order=ORDER,runs=[],start_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
    plan=planned_runs(args.comparison)
    if args.comparison=='boundary-vmstat':state.update(comparison=args.comparison,order=BOUNDARY_ORDER)
    if args.comparison=='boundary-meminfo':state.update(comparison=args.comparison,order=MEMINFO_ORDER)
    def save():(out/'result.json').write_text(json.dumps(state,indent=2)+'\n')
    def command(argv,timeout=30):return subprocess.check_output(argv,env=env,text=True,stderr=subprocess.PIPE,timeout=timeout)
    def source():return json.loads(command(['docker','inspect',SOURCE]))[0]
    started=False
    try:
        s=source();assert s['Id']==SOURCE_ID and s['Image']==IMAGE and not s['State']['Running']
        values=dict(v.split('=',1) for v in s['Config']['Env'] if '=' in v)
        assert not any(values.get(k) for k in PROXY_KEYS)
        configs=[m['Source'] for m in s['Mounts'] if m['Destination']=='/workspace/config/default.toml'];assert len(configs)==1
        pool=tomllib.loads(Path(configs[0]).read_text())['pool']
        assert not pool['firecracker']['enabled'] and not pool['firecracker']['startup_prewarm'] and not pool['block']['enabled']
        assert not command(['docker','ps','-q']).strip() and not identities()
        state['source']=dict(id=SOURCE_ID,image=IMAGE,proxy_empty=True,pool_disabled=True);save()
        started=True;command(['docker','start',SOURCE]);port=values.get('API_ADDR','');assert port.startswith('127.0.0.1:')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            try:
                with opener.open('http://'+port+'/health',timeout=3) as response:
                    if response.status in (200,204):break
            except Exception:time.sleep(1)
        else:raise TimeoutError('source health')
        for index,item in enumerate(plan,1):
            pair=item['pair'];mode=item['mode'];tag=item['variant'] if args.comparison!='sampling' else mode
            free=shutil.disk_usage(ROOT).free
            mem=int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])*1024
            assert free>54*GIB and mem>64*GIB and not identities()
            cfg=item['config']
            argv=['bash','scripts/run-host-file-monitor.sh',cfg,'--persistent-input','--local-no-proxy']
            state.update(stage=f'run-{index}-{tag}',active_run=dict(index=index,pair=pair,mode=mode,command=argv,
                disk_available_bytes=free,mem_available_bytes=mem));save()
            print('status=RUNNING '+state['stage'],flush=True)
            logpath=out/f'run-{index}-{tag}.log'
            with logpath.open('x') as log:
                result=subprocess.run(argv,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=2250)
            state['active_run']['exit_code']=result.returncode;save()
            if result.returncode:raise RuntimeError('workflow failed; retain first failure and stop batch')
            matches={Path(p) for p in re.findall(r'status=PASS result_dir=(\S+)',logpath.read_text()) if Path(p).parent==ROOT/'.artifacts/platform-tasks'}
            assert len(matches)==1
            state['stage']=f'check-{index}-{tag}';save()
            row=check(matches.pop(),mode,**(dict(skip_vmstat=True) if item['skip_vmstat'] else {}),**(dict(merge_meminfo=True) if item.get('merge_meminfo') else {}));row.update(index=index,pair=pair)
            if args.comparison=='boundary-vmstat':row['boundary_vmstat_mode']=item['variant']
            if args.comparison=='boundary-meminfo':row['boundary_meminfo_mode']=item['variant']
            if state['runs']:
                assert row['patches']==state['runs'][0]['patches'] and row['official']==state['runs'][0]['official']
                assert not set(row['guest_ids'])&{sid for r in state['runs'] for sid in r['guest_ids']}
            state['runs'].append(row);save()
            print(f'status=PASS run={index} mode={mode}',flush=True)
        state.update(status='PASS',stage='complete',pairs=comparisons(state['runs'],comparison=args.comparison))
    except BaseException as exc:
        state['error']=repr(exc);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if started:
            try:
                s=source();assert s['Id']==SOURCE_ID and s['Image']==IMAGE
                if s['State']['Running']:command(['docker','stop','-t','10',SOURCE],30)
                state['source_restored_stopped']=not source()['State']['Running']
            except BaseException as exc:state.update(status='FAIL',source_cleanup_error=repr(exc))
        state.update(finish_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),disk_available_after_bytes=shutil.disk_usage(ROOT).free,
            remaining_firecrackers=identities(),remaining_running_containers=command(['docker','ps','-q']).split())
        if state['remaining_firecrackers'] or state['remaining_running_containers']:state['status']='FAIL'
        save()
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
