"""Audit syscall logs while retaining their observer-effect and overlap limits."""
import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from report_git_step_diagnostic import report as audit_git

LINE=re.compile(r'^(\d+\.\d+)\s+(\w+)\((.*)\)\s+=\s+(.*?)\s+<(\d+\.\d+)>$')
RESUME=re.compile(r'^(\d+\.\d+)\s+<\.\.\. (\w+) resumed>(.*?)\s+=\s+(.*?)\s+<(\d+\.\d+)>$')


def parse_file(path):
    rows=[];pending=None
    for line in path.read_text().splitlines():
        m=LINE.match(line)
        if m:
            stamp,name,args,result,duration=m.groups()
        elif line.endswith('<unfinished ...>'):
            assert pending is None
            pending=line;continue
        elif RESUME.match(line):
            m=RESUME.match(line);assert pending is not None
            resumed_stamp,name,tail,result,duration=m.groups()
            start=re.match(r'^(\d+\.\d+)\s+(\w+)\((.*)<unfinished \.\.\.>$',pending)
            assert start and start[2]==name
            stamp=start[1];args=start[3]+tail;pending=None
        elif '--- SIG' in line or '+++ ' in line or not line.strip():
            continue
        else:raise AssertionError(f'unparsed syscall line in {path.name}: {line[:120]}')
        rows.append(dict(timestamp=float(stamp),syscall=name,args=args,result=result,duration_seconds=float(duration)))
    assert pending is None,'unfinished syscall at end'
    assert rows,'empty syscall file'
    return rows


def summarize(rows):
    grouped=defaultdict(list)
    for row in rows:grouped[row['syscall']].append(row)
    return {name:dict(count=len(v),summed_duration_ms=1000*sum(x['duration_seconds'] for x in v),maximum_duration_ms=1000*max(x['duration_seconds'] for x in v),error_count=sum(x['result'].startswith('-1') for x in v)) for name,v in grouped.items()}


def report(directory):
    directory=Path(directory).resolve()
    raw_result=json.loads((directory/'result.json').read_text());assert raw_result['git_syscalls']
    git=audit_git(directory);runs=[]
    for source,row in zip(raw_result['runs'],git['runs']):
        replay=Path(source['raw_dir'])/'replay'
        files=sorted(replay.glob('git-025-syscalls.*'));assert files
        all_rows={int(p.suffix[1:]):parse_file(p) for p in files if p.read_text().strip()}
        events=[json.loads(v) for v in (replay/'git-025-events.jsonl').read_text().splitlines()]
        start=next(e for e in events if e['event']=='start' and e['argv']==['git','add','-A'])
        pid=int(re.search(r'-P([0-9a-fA-F]+)$',start['sid'])[1],16);assert pid in all_rows
        leave=next(e for e in events if e['event']=='region_leave' and e['sid']==start['sid'] and e.get('label')=='read_directory')
        end=datetime.fromisoformat(leave['time'].replace('Z','+00:00')).timestamp();begin=end-leave['t_rel']
        chosen=[x for x in all_rows[pid] if x['timestamp']<end and x['timestamp']+x['duration_seconds']>begin]
        longest=sorted(all_rows[pid],key=lambda x:x['duration_seconds'],reverse=True)[:10]
        runs.append(dict(position=row['position'],label=row['label'],command_clock=row['command_clock'],git_add_seconds=row['commands'][0]['wall_seconds'],directory_scan_seconds=leave['t_rel'],thread_log_count=len(files),all_threads=summarize([x for v in all_rows.values() for x in v]),git_main=summarize(all_rows[pid]),git_directory_region=summarize(chosen),longest_git_main_calls=[dict(syscall=x['syscall'],duration_ms=1000*x['duration_seconds']) for x in longest],seccomp_probe_enabled='seccomp filter enabled' in (Path(source['raw_dir'])/'syscall-probe.stderr').read_text(),log_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}))
    summary=dict(status='PASS',runs=runs,requested_syscall_scope=raw_result.get('syscall_scope','all-files'),scope='syscall diagnostic only',limitations=['strace changes syscall/worker scheduling; durations include observer effects and scheduling, not pure storage wait','Thread durations overlap; do not sum them into end-to-end elapsed time','Region selection uses guest realtime timestamps with microsecond precision; overlapping boundary calls are retained','No host runnable-wait measurement here; host attribution remains unmeasured','Raw syscall logs are local only; file buffers not uploaded'])
    (directory/'syscall-report.json').write_text(json.dumps(summary,indent=2)+'\n')
    lines=['# Git 系统调用诊断','', '| 次序 | 命令单调耗时 (ms) | Git add (ms) | 目录扫描 (ms) | 线程日志数 |','| --- | ---: | ---: | ---: | ---: |']
    for row in runs:lines.append(f"| {row['position']} | {row['command_clock']['monotonic_raw_ns']/1e6:.1f} | {row['git_add_seconds']*1000:.1f} | {row['directory_scan_seconds']*1000:.1f} | {row['thread_log_count']} |")
    lines+=['','系统调用耗时含跟踪与调度影响，不当成纯磁盘等待；多线程区间重叠，不相加当任务总耗时。原动作及最终结果独立核对通过，监控开销未判定。','']
    (directory/'syscall-report.md').write_text('\n'.join(lines))
    print(f'status=PASS result_dir={directory}')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    report(parser.parse_args().directory)
