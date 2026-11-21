"""Instrument only a validated working copy of the frozen guest runner."""
def gated_runner(source, *, tool_time=False):
    loop_end = 'done < "$manifest"'
    if source.count(loop_end) != 1:
        raise ValueError('expected one frozen action loop')
    # out is fresh and specific to this sandbox, role and segment.
    ready = '''  token="$branch:$round:$step"
  printf '%s\\n' "$token" > "$out/$step.ready.tmp"
  mv "$out/$step.ready.tmp" "$out/$step.ready"
  deadline=$((SECONDS+60))
  until [[ -f "$out/$step.go" ]]; do
    if ((SECONDS >= deadline)); then echo "host release timeout: $token" >&2; exit 124; fi
    sleep 0.02
  done
  [[ $(cat "$out/$step.go") == "$token" ]] || exit 125
'''
    if tool_time:
        ready = ready.replace("printf '%s\\n' \"$token\"", "printf '%s %s\\n' \"$token\" \"$end_ns\"")
    stamp = '  set +e\n'
    if source.count(stamp) != 1: raise ValueError("expected one action start")
    source = source.replace(stamp, '  printf "%s\\n" "$start_ns" > "$out/$step.start"\n' + stamp)
    return source.replace(loop_end, ready + loop_end)
