"""Separate guest CPU/block I/O from owned host VMM CPU/process I/O/residency."""
import csv
import json
from pathlib import Path

from guest_performance import CPUFIELDS


def rows(path):
    with path.open() as f:return list(csv.DictReader(f,delimiter='\t'))


def rates(data,fields,scale=1):
    result=[];invalid=0
    for a,b in zip(data,data[1:]):
        dt=(int(b['monotonic_ns'])-int(a['monotonic_ns']))/1e9
        delta=[int(b[k])-int(a[k]) for k in fields]
        valid=0<dt<=.5 and all(d>=0 for d in delta)
        result.append(dict(timestamp_ns=int(b['timestamp_ns']),values=[scale*d/dt for d in delta] if valid else None))
        invalid+=not valid
    return result,invalid


def guest_cpu(data):
    result=[];invalid=0
    for a,b in zip(data,data[1:]):
        delta={k:int(b[k+'_ticks'])-int(a[k+'_ticks']) for k in CPUFIELDS}
        dt=(int(b['monotonic_ns'])-int(a['monotonic_ns']))/1e9
        total=sum(delta.values());valid=0<dt<=.5 and all(v>=0 for v in delta.values()) and total>0 and a['cpu_count']==b['cpu_count']
        busy=sum(delta[k] for k in ('user','nice','system','irq','softirq'))
        result.append(dict(timestamp_ns=int(b['timestamp_ns']),values=[100*busy/total,100*delta['iowait']/total] if valid else None))
        invalid+=not valid
    return result,invalid


def check_ids(data,session,duplicate_time=False):
    assert all(r['sandbox_id']==session['sandbox_id'] and r['role']==session['role'] and r['session_id']==session['session_id'] for r in data),'performance rows have stale sandbox/session labels'
    assert all(int(b['monotonic_ns'])>=int(a['monotonic_ns']) if duplicate_time else int(b['monotonic_ns'])>int(a['monotonic_ns']) for a,b in zip(data,data[1:]))


def audit(raw,groups):
    result={}
    manifest=json.loads((raw/'runtime-monitor.json').read_text()) if (raw/'runtime-monitor.json').exists() else {}
    for sid,group in groups.items():
        value=dict(guest=[],host=[])
        for session in group['sessions']:
            directory=raw/session['raw_directory'];receipt=session['receipt']
            if manifest.get('guest_performance_version')==1:assert receipt.get('performance_version')==1,'requested guest performance samples are missing'
            if receipt.get('performance_version')==1:
                cpu=rows(directory/'guest-cpu.tsv');disks=rows(directory/'guest-diskstats.tsv');memory=rows(directory/'guest-memory.tsv')
                check_ids(cpu,session);check_ids(disks,session,duplicate_time=True)
                assert len(cpu)==len(memory)==receipt['samples']
                assert [r['monotonic_ns'] for r in cpu]==[r['monotonic_ns'] for r in memory]
                assert disks and set(r['monotonic_ns'] for r in disks)==set(r['monotonic_ns'] for r in cpu)
                value['guest'].append(dict(cpu=cpu,disks=disks))
            if 'host_directory' in session:
                directory=raw/session['host_directory']
                assert directory.resolve().is_relative_to((raw/'host-vmm').resolve())
                receipt=json.loads((directory/'collector-status.json').read_text());data=rows(directory/'host-vmm.tsv')
                assert receipt==session['host_receipt'] and receipt['status']=='PASS' and not receipt['error']
                assert len(data)==receipt['samples']>=2 and sum(r['pss_kib']!='' for r in data)==receipt['pss_samples']>=1
                check_ids(data,session)
                assert all(r['pid']==str(receipt['identity']['pid']) and r['start_ticks']==receipt['identity']['start_ticks'] for r in data)
                value['host'].append(dict(data=data,receipt=receipt))
            elif manifest.get('host_vmm_requested'):raise AssertionError('requested per-VMM samples are missing')
        result[sid]=value
    return result


def write_report(raw,groups,output,origin):
    data=audit(raw,groups);summary=dict(status='NOT_MEASURED',per_sandbox=[],
        sources=dict(guest_cpu='/proc/stat: first eight aggregate counters; guest ticks already included in user/nice',
            guest_io='/proc/diskstats per device; sectors × 512 bytes; partitions never summed with parent devices',
            host_cpu='/proc/<owned VMM pid>/stat, 100% = one CPU core',
            host_io='/proc/<owned VMM pid>/io: attributed block bytes, excludes shared provider processes',
            host_memory='VmRSS at 100 ms; smaps_rollup PSS at 1 s; proportional residency, not unique physical cache'),
        limits=['Guest CPU includes all guest work and sampling; normalized to total guest CPU capacity.',
                'Guest block I/O, host process I/O and backing-service I/O have different scopes; do not add them.',
                'No interpolation across snapshot sessions, clock gaps or counter decreases.',
                'Host phase labels are lifecycle labels only; guest local per-step changes are not queried remotely.',
                'PSS sampling is slower than lightweight counters; its read cost is recorded; collection impact not statistically validated.'])
    import matplotlib.pyplot as plt
    measured=[sid for sid,d in data.items() if d['guest'] or d['host']]
    for sid in measured:
        value=data[sid];record=dict(sandbox_id=sid,role=groups[sid]['role'],guest_cpu_status='NOT_MEASURED',guest_io_status='NOT_MEASURED',host_vmm_status='NOT_MEASURED',guest_devices={},host_sessions=[])
        fig,axes=plt.subplots(5,1,figsize=(11,13),constrained_layout=True)
        def plot(ax,points,index,label,color,max_gap=.5):
            # NaN breaks lines at invalid rates instead of filling missing samples.
            x=[];y=[];last=None
            for r in points:
                now=r['timestamp_ns'];stamp=(now-origin)/1e9
                if last is not None and not 0<(now-last)/1e9<=max_gap:x.append(stamp);y.append(float('nan'))
                x.append(stamp);y.append(r['values'][index] if r['values'] is not None else float('nan'));last=now
            ax.plot(x,y,label=label,color=color)
        guest_peaks=[]
        for chunk in value['guest']:
            points,invalid=guest_cpu(chunk['cpu']);guest_peaks.extend(r['values'][0] for r in points if r['values'] is not None)
            plot(axes[0],points,0,'busy (% of VM capacity)','C0');plot(axes[0],points,1,'iowait (% of VM capacity)','C1')
            record['guest_cpu_status']='MEASURED';record['guest_io_status']='MEASURED'
            record['guest_cpu_invalid_intervals']=record.get('guest_cpu_invalid_intervals',0)+invalid
            devices={}
            for row in chunk['disks']:devices.setdefault(row['device'],[]).append(row)
            for i,(name,series) in enumerate(devices.items()):
                assert len({(r['major'],r['minor'],r['is_partition']) for r in series})==1,'block-device identity changed'
                points,invalid=rates(series,['sectors_read','sectors_written'],512/2**20)
                active=any(r['values'] and any(r['values']) for r in points)
                info=record['guest_devices'].setdefault(name,dict(major=series[0]['major'],minor=series[0]['minor'],is_partition=series[0]['is_partition'],
                    session_deltas=[],invalid_intervals=0))
                info['session_deltas'].append(dict(read_bytes=(int(series[-1]['sectors_read'])-int(series[0]['sectors_read']))*512 if not invalid else None,
                    write_bytes=(int(series[-1]['sectors_written'])-int(series[0]['sectors_written']))*512 if not invalid else None))
                info['invalid_intervals']+=invalid
                if active:
                    plot(axes[1],points,0,name+' read','C'+str(i%10));plot(axes[1],points,1,name+' write','C'+str((i+3)%10))
        record['guest_cpu_observed_peak_percent']=max(guest_peaks) if guest_peaks else None
        for chunk in value['host']:
            series=chunk['data'];receipt=chunk['receipt'];record['host_vmm_status']='MEASURED'
            cpu_points,invalid=rates(series,['user_ticks','system_ticks'],100/receipt['clock_ticks'])
            combined=[dict(timestamp_ns=r['timestamp_ns'],values=[sum(r['values'])] if r['values'] else None) for r in cpu_points]
            plot(axes[2],combined,0,'VMM CPU (% of one core)','C0')
            io_points,io_invalid=rates(series,['read_bytes','write_bytes'],1/2**20)
            plot(axes[3],io_points,0,'VMM attributed read','C0');plot(axes[3],io_points,1,'VMM attributed write','C1')
            rss=[dict(timestamp_ns=int(r['timestamp_ns']),values=[int(r['rss_kib'])/1024]) for r in series]
            pss=[dict(timestamp_ns=int(r['timestamp_ns']),values=[int(r['pss_kib'])/1024]) for r in series if r['pss_kib']!='']
            plot(axes[4],rss,0,'VMM RSS','C0');plot(axes[4],pss,0,'VMM PSS (1 s)','C1',max_gap=1.5)
            record['host_sessions'].append(dict(session_id=receipt['session_id'],vmm_identity=receipt['identity'],sample_count=len(series),
                pss_sample_count=len(pss),cpu_invalid_intervals=invalid,io_invalid_intervals=io_invalid,
                observed_rss_peak_bytes=max(int(r['rss_kib'])*1024 for r in series),
                observed_pss_peak_bytes=max(int(r['pss_kib'])*1024 for r in series if r['pss_kib']!=''),
                observed_cpu_peak_percent=max((r['values'][0] for r in combined if r['values']),default=None),
                max_scan_duration_ns=receipt['max_scan_duration_ns'],
                max_pss_scan_duration_ns=max(int(r['pss_scan_duration_ns']) for r in series if r['pss_scan_duration_ns']!=''),
                collector_cpu_seconds=receipt['cpu_user_seconds']+receipt['cpu_system_seconds']))
        titles=['Guest CPU utilization','Guest per-device block I/O','Owned host VMM CPU','Owned host VMM process I/O','Owned host VMM residency']
        for ax,title,unit in zip(axes,titles,['% of VM capacity','MiB/s','% of one core','MiB/s','MiB']):
            ax.set(title=title,xlabel='Recorded wall-clock seconds',ylabel=unit);ax.grid(alpha=.25)
            if ax.lines:ax.legend(fontsize=8)
            else:ax.text(.5,.5,'Not measured / no observed device traffic',ha='center',transform=ax.transAxes)
        fig.suptitle(groups[sid]['role']+' / '+sid+'; separate guest and host scopes')
        fig.savefig(output/('performance-'+sid+'.svg'),metadata={'Date':None});plt.close(fig)
        summary['per_sandbox'].append(record)
    if measured:summary['status']='MEASURED'
    return summary
