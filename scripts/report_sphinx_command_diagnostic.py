"""Audit short command diagnostics and export CPU/time evidence without raw logs."""
import argparse
import json
from pathlib import Path
import statistics

from sphinx7590_inputs import ROOT,validate,sha
from run_sphinx_command_diagnostic import check_trace
from task_metrics_prefix import without_sampling


def process_summary(directory,ticks):
    groups={};scan_rows=[]
    for line in (directory/'processes.jsonl').read_text().splitlines():
        row=json.loads(line);scan_rows.append(row)
        for p in row['processes']:
            # Shell command strings mention both names; only classify executables.
            words=p['command'].split();first=Path(words[0]).name if words else ''
            kind='conda' if first not in ('bash','timeout') and any(Path(w).name=='conda' for w in words[:2]) else 'pip' if first not in ('bash','timeout') and any(Path(w).name=='pip' for w in words[:2]) else None
            if kind is None:continue
            key=(kind,p['pid'],p['start_ticks'])
            if key not in groups:groups[key]=dict(kind=kind,first_ns=row['elapsed_ns'],last_ns=row['elapsed_ns'],
                first_ticks=p['user_ticks']+p['system_ticks'],last_ticks=p['user_ticks']+p['system_ticks'],
                states={},wchans={},socket_samples=0,inet_observed=False,inet_socket_samples=0,inet_states={},first_read_bytes=p['read_bytes'],last_read_bytes=p['read_bytes'])
            g=groups[key];g['last_ns']=row['elapsed_ns'];g['last_ticks']=p['user_ticks']+p['system_ticks'];g['last_read_bytes']=p['read_bytes']
            for k,s in (('states',p['state']),('wchans',p['wchan'])):g[k][s]=g[k].get(s,0)+1
            g['socket_samples']+=p['socket_fds']>0
            if 'inet_socket_states' in p:
                g['inet_observed']=True
                g['inet_socket_samples']+=bool(p['inet_socket_states'])
                for state in p['inet_socket_states']:g['inet_states'][state]=g['inet_states'].get(state,0)+1
    for g in groups.values():
        g['observed_span_seconds']=(g['last_ns']-g['first_ns'])/1e9
        g['observed_cpu_seconds']=(g['last_ticks']-g['first_ticks'])/ticks
        g['observed_read_bytes_delta']=g['last_read_bytes']-g['first_read_bytes']
        if not g['inet_observed']:g['inet_socket_samples']=None;g['inet_states']=None
    return dict(processes=list(groups.values()),scans=len(scan_rows),
        scope='observed executable spans/counters only; startup/exit and threads may be missed; socket samples include Unix sockets, not proof of network wait')


def report(raw,output):
    raw=raw.resolve();output=output.resolve()
    assert raw.is_relative_to(ROOT/'.artifacts/storage-isolated-service')
    assert output.is_relative_to(ROOT/'.artifacts') and not output.is_relative_to(raw) and not raw.is_relative_to(output)
    def hashes():return {str(p.relative_to(raw)):sha(p) for p in sorted(raw.rglob('*')) if p.is_file()}
    before=hashes();result=json.loads((raw/'result.json').read_text());outer=json.loads((raw.parent/'result.json').read_text())
    outer_sha=sha(raw.parent/'result.json')
    assert result['status']==outer['status']=='PASS' and result['command_diagnostic']
    assert all(outer['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed',
        'original_firecracker_identities_unchanged','original_service_unchanged'))
    assert result['cleanup']['service_sandbox_list_empty']
    cfg,_,samples,names=validate(raw/'prepared-input');reference=json.loads((raw/'prepared-input/config.json').read_text())
    assert cfg==reference
    # The original verified prefix outputs are retained in this complete run.
    reference_raw=ROOT/'.artifacts/storage-isolated-service/20261009T055637Z-3955483/python-backbone'
    assert check_trace(raw,reference_raw,cfg,samples,names)==result['replay']
    assert (raw/'frozen-input/guest-runner.sh').read_text()==without_sampling((raw/'frozen-input/guest-runner.original.sh').read_text())
    assert sha(raw/'frozen-input/guest-runner.original.sh')==cfg['input_sha256']['guest-runner.sh']
    assert json.loads((raw/'task-before.json').read_text())==json.loads((raw/'task-after.json').read_text())
    assert json.loads((raw/'task-oracle-before.json').read_text())==json.loads((raw/'task-oracle-after.json').read_text())
    for k in ('thirteen_original_actions_outputs_and_patch','repeats_outputs_exits_and_source_unchanged'):assert result['checks'][k]=='PASS'
    values=[]
    for i,expected in enumerate(result['repetitions'],1):
        directory=raw/'asb-command'/f'repeat-{i:02}';value=json.loads((directory/'result.json').read_text())
        assert value==expected and value['status']=='CAPTURED' and value['profile']==(i%2==0)
        assert value['command_sha256']==result['frozen_command_sha256'] and value['exit_code']==1
        for kind in ('stdout','stderr'):
            assert value[kind+'_sha256']==sha(directory/kind)==sha(reference_raw/'rollouts/backbone/trace_cpp_parser'/kind/'006.log')
        value=dict(value,repeat=i,cpu_seconds=value['child_user_seconds']+value['child_system_seconds'])
        value['process_observations']=process_summary(directory,value['clock_ticks'])
        assert value['process_observations']['scans']==value['scans']
        values.append(value)
    assert len(values)==6
    controls=[]
    for i,expected in enumerate(result.get('version_check_controls',[]),1):
        directory=raw/'asb-command'/f'version-control-{i:02}';value=json.loads((directory/'result.json').read_text())
        assert value==expected and value['status']=='CAPTURED' and value['profile'] and value['exit_code']==1
        assert value['pip_version_check_disabled']==(i in (2,3)) and value['inherited_pip_disable_version_check'] is None
        assert value['command_sha256']==result['frozen_command_sha256']
        for kind in ('stdout','stderr'):assert value[kind+'_sha256']==sha(directory/kind)==sha(reference_raw/'rollouts/backbone/trace_cpp_parser'/kind/'006.log')
        controls.append(dict(value,control=i,cpu_seconds=value['child_user_seconds']+value['child_system_seconds'],
                             process_observations=process_summary(directory,value['clock_ticks'])))
    if controls:assert len(controls)==4
    metadata=json.loads((raw/'pip-local-code.json').read_text()) if controls else None
    prefix=raw/'asb-command/segment';prefix_receipt=json.loads((prefix/'result.json').read_text())
    assert prefix_receipt['status']=='CAPTURED' and prefix_receipt['exit_code']==0
    summary=dict(status='PASS',source_directory=str(raw.relative_to(ROOT)),scope=result['scope'],
        frozen_command_sha256=result['frozen_command_sha256'],action_count=13,raw_files=len(before),raw_hashes_unchanged=True,
        prefix_observations=process_summary(prefix,prefix_receipt['clock_ticks']),repetitions=values,version_check_controls=controls,
        pip_version=metadata['version'] if metadata else None,
        ranges={mode:dict(elapsed_min_seconds=min(v['elapsed_ns']/1e9 for v in values if v['profile']==profile),
                         elapsed_max_seconds=max(v['elapsed_ns']/1e9 for v in values if v['profile']==profile),
                         cpu_mean_seconds=statistics.mean(v['cpu_seconds'] for v in values if v['profile']==profile),
                         observer_cpu_mean_seconds=statistics.mean(v['observer_cpu_seconds'] for v in values if v['profile']==profile))
                for mode,profile in [('unprofiled',False),('profiled',True)]},
        limits=['Same VM and sequential warm repetitions; not a monitoring on/off matched study or cold-cache comparison.',
                '25 ms process scans themselves add diagnostic cost; observer CPU is separate.',
                'Wait channels/socket descriptors do not by themselves establish I/O or network causality.',
                'No complete task tests, physical page cache or full trajectory replay performed.'])
    output.mkdir(parents=True,exist_ok=False)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (output/'input-sha256.json').write_text(json.dumps(before,indent=2)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['svg.hashsalt']='sphinx-command-diagnostic-v1'
    if controls:
        fig,(ax,control_ax)=plt.subplots(1,2,figsize=(12,3.5),constrained_layout=True)
        control_ax.bar([v['control'] for v in controls],[v['elapsed_ns']/1e9 for v in controls],
            color=['#4477aa' if not v['pip_version_check_disabled'] else '#55aa77' for v in controls])
        control_ax.plot([v['control'] for v in controls],[v['cpu_seconds'] for v in controls],'ko-',label='Command + child CPU time')
        control_ax.set(xticks=[1,2,3,4],xticklabels=['Default','Disabled','Disabled','Default'],
            ylabel='Seconds',title='Only pip version-check environment changes')
        control_ax.legend()
    else:fig,ax=plt.subplots(figsize=(8,3),constrained_layout=True)
    ax.bar([v['repeat'] for v in values],[v['elapsed_ns']/1e9 for v in values],color=['#4477aa' if not v['profile'] else '#cc8844' for v in values],label='Elapsed time')
    ax.plot([v['repeat'] for v in values],[v['cpu_seconds'] for v in values],'ko-',label='Command + child CPU time')
    ax.set(xlabel='Repeat (odd: no process scans; even: process scans)',ylabel='Seconds',title='Same frozen command, one VM; warm diagnostic repeats')
    ax.legend();fig.savefig(output/'command-timing.svg',metadata={'Date':None});plt.close(fig)
    assert hashes()==before and sha(raw.parent/'result.json')==outer_sha,'inputs changed during report'
    print('status=PASS result_dir='+str(output))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('raw',type=Path);p.add_argument('output',type=Path)
    args=p.parse_args();report(args.raw,args.output)
