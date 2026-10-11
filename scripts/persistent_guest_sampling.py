"""Create an explicit runner variant; preserve all frozen action execution lines."""
def instrument(text):
    begin=text.index('sample_memory() {')
    end=text.index('if [[ $start_step == 001 ]]; then',begin)
    functions='''sample_memory() {
  printf '%s\\t%s\\n' "$1" "$2" > "$out/.memory-phase"
}
monitor_pid=''
stop_guest_collector() {
  local original_rc=$?
  trap - EXIT
  if [[ -n $monitor_pid ]]; then
    kill -TERM "$monitor_pid" 2>/dev/null || true
    if ! wait "$monitor_pid"; then original_rc=1; fi
  fi
  exit "$original_rc"
}
if [[ $guest_sampling == 1 ]]; then
  rm -f "$out/.memory-ready"
  sample_memory preparation 000
  python3 "${ASB_GUEST_MEMORY_COLLECTOR:-/workspace/guest-memory-collector.py}" "$out" "$sample_interval" &
  monitor_pid=$!
  trap stop_guest_collector EXIT
  for attempt in $(seq 1 100); do
    test -s "$out/.memory-ready" && break
    kill -0 "$monitor_pid" || exit 1
    sleep 0.01
  done
  test -s "$out/.memory-ready" || exit 1
fi

'''
    text=text[:begin]+functions+text[end:]
    start='    monitor_step "$step" &\n    monitor_pid=$!'
    stop='    kill "$monitor_pid" 2>/dev/null || true\n    wait "$monitor_pid" 2>/dev/null || true\n'
    assert text.count(start)==text.count(stop)==1
    return text.replace(start,'    sample_memory running "$step"').replace(stop,'')


def instrument_host(text,collector):
    import shlex
    anchor='aenv upload "$sandbox_id" "$script_dir/guest-runner.sh" /workspace/guest-runner.sh'
    assert text.count(anchor)==1
    return text.replace(anchor,anchor+'\naenv upload "$sandbox_id" '+shlex.quote(str(collector))+' /workspace/guest-memory-collector.py')
