"""Instrument frozen step 025 without editing its action or stdout/stderr."""

def instrument_clock_only(text,clock_helper,*,split_commands=False,add_performance=False,add_trace2=False,open_close_library=None,directory_prewarm_helper=None,guest_diskstats=False,guest_drop_file_cache=False,guest_cache_state=False,guest_damon_state=False,guest_damon_format='vmstat'):
    import shlex
    assert not add_performance or split_commands=='bash'
    assert not add_trace2 or add_performance
    assert not open_close_library or add_trace2
    old='''  (cd /testbed && timeout --signal=TERM --kill-after=5s "${timeout_seconds}s" bash "$action") \\
    > "$out/stdout/$step.log" 2> "$out/stderr/$step.log"
  status=$?'''
    assert text.count(old)==1
    wrapped=old.replace('&& timeout','&& '+shlex.quote(str(clock_helper))+' "$out/git-025-clocks.json" timeout')
    # Capture the exit code immediately in either branch.
    setup=teardown=''
    if split_commands:
        assert split_commands in (True,'bash')
        # Export only for this action's bash. execvp in the C helper still runs
        # the real Git executable, rather than recursively calling the function.
        setup='''    git() {
      case "$1" in
        add|diff) HELPER "$git_clock_out/git-025-$1-clocks.json" git "$@" ;;
        *) command git "$@" ;;
      esac
    }
    git_clock_out="$out"
    export git_clock_out
    export -f git
'''.replace('HELPER',shlex.quote(str(clock_helper)))
        if split_commands=='bash':
            setup='''    git() {
      case "$1" in
        add|diff)
          local TIMEFORMAT=$'%R\\t%U\\t%S'
          { time command git "$@" 2>&3; } 3>&2 2> "$git_clock_out/git-025-$1-time.tsv"
          ;;
        *) command git "$@" ;;
      esac
    }
    git_clock_out="$out"
    export git_clock_out
    export -f git
'''
            if add_performance:
                setup=setup.replace("          local TIMEFORMAT",'''          if [[ $1 == add ]]; then
            local -x GIT_TRACE_PERFORMANCE="$git_clock_out/git-025-add-performance.txt"
          fi
          local TIMEFORMAT''')
            if add_trace2:
                setup=setup.replace('            local -x GIT_TRACE_PERFORMANCE=', '            local -x GIT_TRACE2_EVENT="$git_clock_out/git-025-add-events.jsonl"\n            local -x GIT_TRACE_PERFORMANCE=')
            if open_close_library:
                setup=setup.replace('            local -x GIT_TRACE_PERFORMANCE=',
                    '            local -x LD_PRELOAD='+shlex.quote(str(open_close_library))+'\n'
                    '            local -x DIRECTORY_PROFILE_DIR="$git_clock_out"\n'
                    '            local -x GIT_TRACE_PERFORMANCE=')
        teardown='\n    unset -f git\n    unset git_clock_out'
    if directory_prewarm_helper:
        assert split_commands=='bash' and add_trace2
        setup='    timeout --signal=TERM --kill-after=5s 25s python3 '+shlex.quote(str(directory_prewarm_helper))+' /testbed "$out/prewarm-directories.json"'+(' --diskstats' if guest_diskstats else '')+' || exit $?\n'+setup
    if guest_drop_file_cache:
        assert directory_prewarm_helper or guest_diskstats
        setup='    printf \'1\\n\' > /proc/sys/vm/drop_caches || exit $?\n    printf \'drop_caches=1 no_sync guest_only\\n\' > "$out/guest-file-cache-policy.txt"\n'+setup
    if guest_diskstats:
        # Bash builtins only: no extra guest process or binary loading.
        snapshot='    while IFS= read -r diskstats_line; do printf \'%s\\n\' "$diskstats_line"; done < /proc/diskstats > "$out/git-025-PHASE.diskstats"\n'
        setup+=snapshot.replace('PHASE','before')
        teardown+='\n'+snapshot.replace('PHASE','after').rstrip()+'\n    unset diskstats_line'
    assert not guest_damon_state or guest_cache_state
    initial=''
    if guest_cache_state:
        # $(<file) is Bash's builtin file read; no cat/awk or guest parser.
        snapshot="    printf '%s\\n' \"$(< /proc/vmstat)\" > \"$out/PREFIX.vmstat\"\n    printf '%s\\n' \"$(< /proc/meminfo)\" > \"$out/PREFIX.meminfo\"\n"
        if guest_damon_state:
            keys=('enabled','bytes_reclaim_tried_regions','bytes_reclaimed_regions','nr_reclaimed_regions')
            assert guest_damon_format in ('vmstat','separate')
            if guest_damon_format=='separate':
                snapshot+='    {\n'+''.join("      printf '"+key+"=%s\\n' \"$(< /sys/module/damon_reclaim/parameters/"+key+")\"\n" for key in keys)+'    } > \"$out/PREFIX.damon\"\n'
            else:
                memory=snapshot[snapshot.index("    printf '%s\\n' \"$(< /proc/meminfo)"):]
                snapshot="""    {
      printf '%s\\n' "$(< /proc/vmstat)"
"""+''.join("      printf 'damon_"+key+" %s\\n' \"$(< /sys/module/damon_reclaim/parameters/"+key+")\"\n" for key in keys)+'    } > "$out/PREFIX.vmstat"\n'+memory
        setup+=snapshot.replace('PREFIX','git-025-before')
        teardown+='\n'+snapshot.replace('PREFIX','git-025-after').rstrip()
        initial='  if [[ $step == 001 ]]; then\n'+snapshot.replace('PREFIX','cache-start')+'  fi\n'
    return text.replace(old,initial+'  if [[ $step == 025 ]]; then\n'+setup+wrapped+teardown+'\n  else\n'+old+'\n  fi')


def add_performance_records(text, argv=('git','add','-A')):
    """Keep native Git durations separate: nested timers are not additive."""
    import re
    import math
    records=[]
    for line in text.splitlines():
        match=re.fullmatch(r'(\d{2}:\d{2}:\d{2}\.\d+)\s+(\S+)\s+performance:\s+([0-9]+\.[0-9]+) s:\s+(.+)',line)
        assert match,line
        timestamp,source,seconds,label=match.groups()
        duration=float(seconds);assert math.isfinite(duration)
        records.append(dict(timestamp=timestamp,source=source,seconds=duration,label=label))
    totals=[r for r in records if r['label'].startswith('git command:')]
    assert len(totals)==1 and totals[0]['label']=='git command: '+' '.join(argv)
    assert totals[0]['seconds']>0
    stages=[r for r in records if not r['label'].startswith('git command:')]
    assert stages and all(r['seconds']<=totals[0]['seconds']+.001 for r in stages)
    return dict(command_seconds=totals[0]['seconds'],records=records,
                longest_recorded_stage=max(stages,key=lambda r:r['seconds']),
                scope='Native Git timers may nest or omit intervals; do not sum as a complete breakdown')


def performance_marker_gaps(performance):
    """Differences between log timestamps, not measured operation durations."""
    def ns(value):
        hours,minutes,seconds=value.split(':');whole,fraction=seconds.split('.')
        return (int(hours)*3600+int(minutes)*60+int(whole))*10**9+int(fraction.ljust(9,'0')[:9])
    gaps=[]
    records=performance['records']
    for before,after in zip(records,records[1:]):
        delta=(ns(after['timestamp'])-ns(before['timestamp']))%(86400*10**9)
        assert delta<=performance['command_seconds']*10**9+3000000
        gaps.append(dict(before=before['label'],after=after['label'],elapsed_ns=delta))
    return gaps


def add_trace2_records(text,performance,argv=('git','add','-A')):
    """Check one successful add, paired directory boundaries and scan counts."""
    import json
    import math
    from datetime import datetime
    events=[json.loads(line) for line in text.splitlines()]
    starts=[e for e in events if e['event']=='start'];exits=[e for e in events if e['event']=='exit']
    assert len(starts)==len(exits)==1 and starts[0]['argv']==list(argv) and exits[0]['code']==0
    assert {e['sid'] for e in events}=={starts[0]['sid']}
    total=exits[0]['t_abs'];assert math.isfinite(total) and total>0
    regions=[{k:e[k] for k in ('category','label','t_rel','nesting','thread')} for e in events if e['event']=='region_leave']
    assert all(math.isfinite(r['t_rel']) and 0<=r['t_rel']<=total+.003 for r in regions)
    boundaries=[e for e in events if e['event'] in ('region_enter','region_leave') and e.get('category')=='dir' and e.get('label')=='read_directory']
    assert [e['event'] for e in boundaries]==['region_enter','region_leave']
    begin,end=boundaries
    assert begin['thread']==end['thread']=='main' and begin['nesting']==end['nesting']
    elapsed=(datetime.fromisoformat(end['time'].replace('Z','+00:00'))-datetime.fromisoformat(begin['time'].replace('Z','+00:00'))).total_seconds()
    assert elapsed>=0 and abs(elapsed-end['t_rel'])<.003
    counts={e['key']:int(e['value']) for e in events if e['event']=='data' and e.get('category')=='read_directory' and e['key'] in ('directories-visited','paths-visited')}
    assert set(counts)=={'directories-visited','paths-visited'} and all(v>=0 for v in counts.values())
    # Compare the largest native marker gap to the recorded directory interval.
    # Time-of-day offsets handle midnight; this is a realtime log comparison.
    gap=max(performance_marker_gaps(performance),key=lambda r:r['elapsed_ns'])
    def seconds(value):
        h,m,s=value.split(':');return int(h)*3600+int(m)*60+float(s)
    marker=next(r for r in performance['records'] if r['label']==gap['before'])
    offset=(seconds(marker['timestamp'])-seconds(begin['time'][11:-1])+43200)%86400-43200
    length=gap['elapsed_ns']/1e9
    overlap=max(0,min(offset+length,elapsed)-max(offset,0))
    return dict(command_seconds=total,regions=regions,directory=dict(seconds=end['t_rel'],enter_time=begin['time'],leave_time=end['time'],realtime_interval_seconds=elapsed,counts=counts),
                largest_marker_gap=dict(**gap,overlap_with_directory_seconds=overlap,overlap_fraction=overlap/length if length else None),
                scope='Nested regions are not additive; boundary overlap uses realtime log timestamps; no I/O or blocking attribution')


def instrument(text,clock_helper=None,syscall_helper=None,syscall_scope="all-files"):
    anchor='  start_ns="$(date +%s%N)"'
    assert text.count(anchor)==1
    text=text.replace(anchor,'''  if [[ $step == 025 ]]; then
    cat /proc/stat > "$out/git-025-before.stat"
    cat /proc/vmstat > "$out/git-025-before.vmstat"
    export GIT_TRACE2_EVENT="$out/git-025-events.jsonl"
    export GIT_TRACE2_PERF="$out/git-025-perf.txt"
    export GIT_TRACE_PERFORMANCE="$out/git-025-performance.txt"
  fi
'''+anchor)
    old='''  (cd /testbed && timeout --signal=TERM --kill-after=5s "${timeout_seconds}s" bash "$action") \\
    > "$out/stdout/$step.log" 2> "$out/stderr/$step.log"
  status=$?'''
    new='''  if [[ $step == 025 ]]; then
    TIMEFORMAT=$'%R\\t%U\\t%S'
    { time (cd /testbed && timeout --signal=TERM --kill-after=5s "${timeout_seconds}s" bash "$action" \\
      > "$out/stdout/$step.log" 2> "$out/stderr/$step.log"); } 2> "$out/git-025-time.tsv"
    status=$?
    unset GIT_TRACE2_EVENT GIT_TRACE2_PERF GIT_TRACE_PERFORMANCE
  else
'''+old+'''
  fi'''
    assert text.count(old)==1;text=text.replace(old,new)
    anchor='  end_ns="$(date +%s%N)"'
    assert text.count(anchor)==1
    text=text.replace(anchor,anchor+'''
  if [[ $step == 025 ]]; then
    cat /proc/stat > "$out/git-025-after.stat"
    cat /proc/vmstat > "$out/git-025-after.vmstat"
  fi''')
    if clock_helper is not None:
        import shlex
        target='{ time (cd /testbed && timeout'
        assert text.count(target)==1
        text=text.replace(target,'{ time (cd /testbed && '+shlex.quote(str(clock_helper))+' "$out/git-025-clocks.json" timeout')
    if syscall_helper is not None:
        import shlex
        target='"${timeout_seconds}s" bash "$action"'
        assert text.count(target)==2
        assert syscall_scope in ('all-files','reads')
        selected='read,pread64,getdents64' if syscall_scope=='reads' else '%file,read,pread64,getdents64,fsync,fdatasync,futex,wait4,waitid,poll,ppoll,select,pselect6,mmap,munmap'
        options=' --seccomp-bpf' if syscall_scope=='reads' else ''
        trace=shlex.quote(str(syscall_helper))+options+' -qq -ff -ttt -T -yy -s 160 -e raw=read,pread64 -e trace='+selected+' -o "$out/git-025-syscalls"'
        text=text.replace(target,'"${timeout_seconds}s" '+trace+' bash "$action"',1)
    return text


def open_close_records(directory,open_only=False):
    """Normal-exit libc summaries only; absence is not a zero-duration sample."""
    import json
    files=list(directory.glob('directory-*.json'))
    assert len(files)==1, 'expected exactly one add directory summary'
    value=json.loads(files[0].read_text())
    assert value['schema']==1 and value['pid']>0
    assert files[0].name==f"directory-{value['pid']}.json"
    calls=value['calls']
    assert set(calls)==({'opendir'} if open_only else {'opendir','closedir'})
    for call in calls.values():
        assert set(call)=={'count','total_ns','max_ns','null_with_errno','null_without_errno'}
        assert all(type(v) is int and v>=0 for v in call.values())
        assert call['count']>0 and call['total_ns']>=call['max_ns']>0
        assert call['null_with_errno']<=call['count'] and call['null_without_errno']==0
    if not open_only:
        assert calls['closedir']['null_with_errno']==0
        assert calls['opendir']['count']-calls['opendir']['null_with_errno']==calls['closedir']['count']
    return value
