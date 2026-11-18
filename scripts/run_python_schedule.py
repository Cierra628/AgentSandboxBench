"""Explicit seven-role tasks, including approved Django original-path prefix reuse."""
import argparse
import asyncio
import csv
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from types import SimpleNamespace

from prepare_python_schedule_matrix import ROOT, ROLES, task_plan
from prettier_no_proxy import clean_environment, PROXY_KEYS
from run_prettier6604_grpo import execution_paths, prefix_tree_plan, joined, verify_process
from sandbox_runtime_monitor import RuntimeMonitor, boundary_runner
from host_file_monitor import HostFileMonitor
from host_sandbox_monitor import Binding
from host_prefix_compare import drives_for_vm
from host_rootfs_layers import rootfs_inventory
from host_rollout_shared import opened_regular_files
from schedule_smoke_metrics import observe
from xarray6992_inputs import frozen_shell, normalize_patch

EARLY2_POLICY = 'early2-dual-output-v1'
EARLY2_COMMAND = r'''cd /testbed && grep -n "def parse_expression\|def parse_declaration\|class DefinitionParser\|def __init__" sphinx/domains/cpp.py | head -20 && echo "=== test helper ===" && sed -n '1,60p' tests/test_domain_cpp.py'''
FIND_EXIT_POLICY = 'trace-find-exit-v1'
FIND_COMMAND = '''cd /testbed && ls /testbed/.tox 2>/dev/null; which python python3; python -c "import sys; print(sys.executable)"; ls /opt 2>/dev/null; find / -name "docutils" -maxdepth 6 -type d 2>/dev/null | head'''
FIND_STDOUT = b'/opt/miniconda3/bin/python\n/opt/miniconda3/bin/python3\n/opt/miniconda3/bin/python\nminiconda3\n'
XARRAY_FIND_EXIT_POLICY = 'trace-index-find-exit-v1'
XARRAY_FIND_COMMAND = '''cd /testbed && ls /opt/miniconda3/envs 2>/dev/null; /opt/miniconda3/bin/conda env list 2>/dev/null; find / -maxdepth 5 -name "numpy" -type d 2>/dev/null | head'''
XARRAY_FIND_STDOUT = b'testbed\n# conda environments:\n#\nbase                     /opt/miniconda3\ntestbed                  /opt/miniconda3/envs/testbed\n\n'


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def rows(path):
    with Path(path).open() as stream:
        return list(csv.DictReader(stream, delimiter='\t'))


def delete_snapshot(cli, template):
    # Snapshots are removed through the existing template API, not snapshot delete.
    return cli('delete-snapshot', 'template', 'delete', template, timeout=90)


def check_early2_pipeline(command, expected, actual, stdout, stderr, patch, reference_patch, actions):
    """The single user-approved conditional-output case; no command or exit rewrite."""
    assert command == EARLY2_COMMAND and expected == 141 and actual in (141, 0)
    assert reference_patch is not None and patch == reference_patch, 'early_2 source patch changed'
    assert stderr == b'', 'early_2 pipeline produced unexpected stderr'
    assert len(actions) == 5 and actions[3]['command'] == command and actions[3]['exit_code'] == 141
    prefix = actions[3]['output'].encode()
    next_command = actions[4]['command']
    assert next_command.startswith('cd /testbed && sed -n \'1,60p\' tests/test_domain_cpp.py && echo "=== DefinitionParser init ==="')
    marker = '=== DefinitionParser init ===\n'
    assert marker in actions[4]['output']
    helper = actions[4]['output'].split(marker, 1)[0].encode()
    assert len(prefix) == 1227 and len(helper) == 1787, 'pinned output reference changed'
    wanted = prefix if actual == 141 else prefix + b'=== test helper ===\n' + helper
    assert stdout == wanted, 'early_2 pipeline output differs from its exact branch'
    if actual != expected:
        return dict(segment='early_2_inspect', step='004', expected=expected, actual=actual,
                    policy=EARLY2_POLICY, output_branch='original grep plus original conditional echo/sed',
                    stdout_bytes=len(stdout), reference_prefix_bytes=len(prefix))
    return None


def check_find_exit(command, expected, actual, stdout, stderr, patch, reference_patch, *, profile='sphinx7590'):
    """User-approved environment-probe exception; suppressed errors remain unknown."""
    if profile == 'sphinx7590':
        wanted_command, wanted_output, segment, step, policy = FIND_COMMAND, FIND_STDOUT, 'trace_cpp_parser', '005', FIND_EXIT_POLICY
    elif profile == 'xarray6992':
        wanted_command, wanted_output, segment, step, policy = XARRAY_FIND_COMMAND, XARRAY_FIND_STDOUT, 'trace_index_state', '001', XARRAY_FIND_EXIT_POLICY
        assert patch == b'', 'Xarray find probe no longer has the original empty patch'
    else:
        raise AssertionError('unsupported find diagnostic task')
    assert command == wanted_command and expected == 0 and actual in (0, 1), 'unapproved find exit or command'
    assert stdout == wanted_output and stderr == b'', 'find probe output changed'
    assert reference_patch is not None and patch == reference_patch, 'find probe source patch changed'
    if actual != expected:
        return dict(segment=segment, step=step, expected=expected, actual=actual,
                    policy=policy, suppressed_find_error='NOT_RECORDED', stdout_bytes=len(stdout))
    return None


def verify_segment(raw, workload, role, segment, samples, profile, *, branch_policy='original', reference_patch=None,
                   find_diagnostic=False, xarray_find_diagnostic=False):
    directory = Path(raw) / 'rollouts' / role / segment
    manifest = Path(workload) / 'segments' / segment / 'actions.tsv'
    assert (directory / 'actions.tsv').read_bytes() == manifest.read_bytes()
    actions = rows(manifest); steps = rows(directory / 'steps.tsv')
    sample = next((s for s in samples if s['role'] == role and s['segment'] == segment), None)
    if sample is None:
        sample = next(s for s in samples if s['role'] == 'backbone' and s['segment'] == segment)
    assert len(actions) == len(steps) == len(sample['actions'])
    differences = []
    for action, step, original in zip(actions, steps, sample['actions']):
        assert step['step'] == action['step']
        assert step['branch'] == role and step['round'] == segment
        assert int(step['duration_ns']) > 0
        actual, expected = int(step['exit_code']), original['exit_code']
        if (profile, role, segment, step['step'], branch_policy) == ('sphinx7590', 'early_2', 'early_2_inspect', '004', EARLY2_POLICY):
            difference = check_early2_pipeline(action['command'], expected, actual,
                (directory / 'stdout/004.log').read_bytes(), (directory / 'stderr/004.log').read_bytes(),
                (directory / 'patch.diff').read_bytes(), reference_patch, sample['actions'])
        elif find_diagnostic and (profile, segment, step['step']) == ('sphinx7590', 'trace_cpp_parser', '005'):
            difference = check_find_exit(action['command'], expected, actual,
                (directory / 'stdout/005.log').read_bytes(), (directory / 'stderr/005.log').read_bytes(),
                (directory / 'patch.diff').read_bytes(), reference_patch)
        elif xarray_find_diagnostic and (profile, segment, step['step']) == ('xarray6992', 'trace_index_state', '001'):
            difference = check_find_exit(action['command'], expected, actual,
                (directory / 'stdout/001.log').read_bytes(), (directory / 'stderr/001.log').read_bytes(),
                (directory / 'patch.diff').read_bytes(), reference_patch, profile=profile)
        elif profile in ('django16819','c4','matplotlib24177','text_editing','cython'):
            assert actual == expected, f'{profile} original step {action["original_step"]}: expected {expected}, actual {actual}'
            difference = None
        elif profile == 'sphinx7590':
            from sphinx7590_inputs import check_pipeline_exit
            difference = check_pipeline_exit(segment, step['step'], expected, actual, action['command'],
                (directory / 'stdout' / (step['step'] + '.log')).read_bytes(),
                (directory / 'stderr' / (step['step'] + '.log')).read_bytes(),
                (directory / 'patch.diff').read_bytes(), policy_version=2)
        else:
            from xarray6992_inputs import check_replay_exit
            difference = check_replay_exit(segment, step['step'], expected, actual, action['command'],
                (directory / 'stdout' / (step['step'] + '.log')).read_bytes(), True)
        if difference:
            differences.append(difference)
    assert rows(directory / 'guest-memory.tsv'), 'missing original boundary samples'
    return dict(action_count=len(steps), tool_ns=sum(int(s['duration_ns']) for s in steps),
                exit_differences=differences)


def run(args):
    branch_policy = getattr(args, 'branch_pipeline_policy', 'original')
    find_diagnostic = getattr(args, 'find_exit_diagnostic', False)
    xarray_find_diagnostic = getattr(args, 'xarray_find_exit_diagnostic', False)
    if xarray_find_diagnostic and args.task_profile != 'xarray6992':
        raise ValueError('Xarray find exit diagnostic is Xarray seven-role only')
    if find_diagnostic and args.task_profile != 'sphinx7590':
        raise ValueError('find exit diagnostic is Sphinx seven-role only')
    if branch_policy != 'original' and args.task_profile != 'sphinx7590':
        raise ValueError('early2 dual-output policy is Sphinx seven-role only')
    django = args.task_profile == 'django16819'
    c4 = args.task_profile == 'c4'
    matplotlib = args.task_profile == 'matplotlib24177'
    text_editing = args.task_profile == 'text_editing'
    cython = args.task_profile == 'cython'
    use_relay = getattr(args,'guest_relay',False)
    if use_relay and not cython:raise ValueError('multi-guest relay is explicit Cython only')
    external = django or c4 or matplotlib or text_editing or cython
    if django:
        import django_schedule_inputs as profile
        metadata = profile.task_plan(args.inputs)
    elif c4:
        import c4_schedule_inputs as profile
        metadata = profile.task_plan(args.inputs)
    elif matplotlib:
        import matplotlib_schedule_inputs as profile
        metadata = profile.task_plan(args.inputs)
    elif cython:
        import cython_schedule_inputs as profile
        metadata = profile.task_plan(args.inputs)
    elif text_editing:
        import text_editing_schedule_inputs as profile
        metadata = profile.task_plan(args.inputs)
    elif args.task_profile == 'sphinx7590':
        import sphinx7590_inputs as profile
    else:
        import xarray6992_inputs as profile
    if not external:metadata = task_plan(ROOT, args.task_profile)
    cfg, source, samples, names = profile.validate(args.inputs)
    assert str(source.relative_to(ROOT)) == metadata['workload']
    expected = metadata['expected'][args.schedule]
    if args.check_only:
        print('status=VALIDATED task=' + args.task_profile + ' schedule=' + args.schedule +
              ' actual_actions=' + str(expected['actual_actions']))
        return 0
    out = args.output.resolve()
    assert args.config_home.resolve() == out.parent / 'config'
    out.mkdir(exist_ok=False)
    env = clean_environment(dict(os.environ, XDG_CONFIG_HOME=str(args.config_home),
        PATH=str(ROOT / 'runtime/bin') + ':' + os.environ['PATH'], HOST_MONITOR_LIGHT='1'))
    shutil.copytree(source, out / 'frozen-input')
    workload = out / 'frozen-input'
    runner = workload / 'guest-runner.sh'
    shutil.copy2(runner, runner.with_name('guest-runner.original.sh'))
    if not external:runner.write_text(boundary_runner(runner.read_text(), guest_phases=True))
    if cython:
        from external_guest_proxy import direct_environment_script,environment_script
        (out/'guest-env.sh').write_text(environment_script(os.environ['ASB_EXTERNAL_GUEST_RELAY_URL']) if use_relay else direct_environment_script())
    started = time.monotonic_ns(); events = []; lock = threading.Lock()
    result = dict(status='FAIL', task_id=cfg['task_id'], schedule=args.schedule, checks={}, paths={},
                  actual_action_count=0, logical_action_count=expected['logical_actions'], expected=expected,
                  strict_replay_status='FAIL', diagnostic_status='INCOMPLETE',
                  host_scope='experiment_slice_cgroup', legacy_host_scope='private_service_cgroup', metadata=metadata)
    result['branch_pipeline_policy'] = branch_policy
    if external:result['workload_kind'] = cfg['workload_kind']
    result['find_exit_policy'] = XARRAY_FIND_EXIT_POLICY if xarray_find_diagnostic else FIND_EXIT_POLICY if find_diagnostic else 'original'
    if args.task_profile == 'sphinx7590':
        result['sphinx_pipeline_policy_version'] = 2
    controller = None; host = None
    stage = 'service-identity'

    def call(label, argv, timeout=180, *, preserve_output=False):
        with lock:
            index = len(events)
            row = dict(index=index, stage=label, start_monotonic_ns=time.monotonic_ns(), returncode=None)
            events.append(row)
        try:
            with (out / f'{index:04}-{label}.stderr').open('x') as log:
                p = subprocess.run(list(map(str, argv)), cwd=ROOT, env=env,
                    stdout=subprocess.PIPE, stderr=log, text=True, timeout=timeout)
            returned = time.monotonic_ns()
            with lock:
                row.update(command_end_monotonic_ns=returned, returncode=p.returncode)
            (out / f'{index:04}-{label}.stdout').write_text(p.stdout)
            if p.returncode:
                raise RuntimeError(f'{label}: exit {p.returncode}; see {index:04}-{label}.stderr')
            return p.stdout if preserve_output else p.stdout.strip()
        finally:
            with lock:
                row['end_monotonic_ns'] = time.monotonic_ns()
                write(out / 'calls.json', events)

    def cli(label, *argv, timeout=180, preserve_output=False):
        return call(label, [ROOT / 'runtime/bin/aenv', *argv], timeout, preserve_output=preserve_output)

    def guest(label, sid, command, timeout=180, preserve_output=False):
        if django or matplotlib:command = 'export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/testbed; '+command
        return cli(label, 'exec', sid, 'bash', '-lc', command, timeout=timeout, preserve_output=preserve_output)

    runtime = RuntimeMonitor(out, cli, container=args.container)
    def interrupted(signum, frame):
        raise RuntimeError('owned Python schedule interrupted: ' + str(signum))
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, interrupted)
    try:
        info = json.loads(call('service-inspect', ['docker', 'inspect', '--format',
            '{"state":{{json .State}},"labels":{{json .Config.Labels}}}', args.container], 15))
        assert info['state']['Running']
        assert info['labels']['agentsandboxbench.run_id'] == out.parents[2].name
        assert info['labels']['agentenv.experiment'] == 'isolated-storage-check'
        binding = Binding(args.container, out)
        result['cgroup'] = str(binding.cgroup)
        host = HostFileMonitor(out / 'host-file', binding.cgroup, include_pss=False,
            scope='Private service cgroup and descendants; excludes outer controller and RAM input preparation.')
        result.update(legacy_cgroup=str(binding.cgroup), experiment_memory_context=host.experiment_context)
        assert json.loads(cli('list-before', 'list', '--output', 'json')) == []
        vendor = ROOT / 'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork'
        sys.path.insert(0, str(vendor))
        spec = importlib.util.spec_from_file_location('python_schedule_controller', vendor / 'controller.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        module.WORKLOAD = workload
        def shell(*argv, capture=True):
            argv = list(argv)
            if argv[0] == 'aenv':
                argv[0] = ROOT / 'runtime/bin/aenv'
            if not external and len(argv) > 3 and argv[1] == 'exec' and argv[-1].endswith('bash /workspace/mixfs-workload/guest-runner.sh'):
                argv[-1] = frozen_shell(argv[-1])
            return call('controller-' + str(argv[1]), argv, 960 if text_editing or cython else 660)
        module.shell = shell
        executed = execution_paths(json.loads((workload / 'rollouts.json').read_text()), args.schedule)
        trie = prefix_tree_plan(json.loads((workload / 'rollouts.json').read_text()))
        oracle = 'sphinx7590_check.py' if args.task_profile == 'sphinx7590' else 'xarray6992_check.py'
        python = '/opt/miniconda3/envs/testbed/bin/python'

        def boundary(sid, prefix):
            code = "import json,subprocess;from pathlib import Path;print(json.dumps(dict(marker=Path('/workspace/python-schedule-marker').read_text(),patch=subprocess.check_output(['git','-C','/testbed','diff','--binary'],text=True),steps={s:(Path('/workspace/artifacts/exp5/backbone')/s/'steps.tsv').read_text() for s in " + repr(prefix) + "})))"
            if c4:
                code = "import json;from pathlib import Path;print(json.dumps(dict(marker=Path('/workspace/python-schedule-marker').read_text(),source="+profile.SOURCE_STATE_EXPR+",steps={s:(Path('/workspace/artifacts/exp5/backbone')/s/'steps.tsv').read_text() for s in "+repr(prefix)+"})))"
            if text_editing or cython:
                code = profile.boundary_code(prefix)
            if matplotlib:
                code = profile.boundary_code(prefix,cfg['checkpoint_helper_paths'])
            value = json.loads(guest('boundary', sid, 'python3 -c ' + shlex.quote(code)))
            if django:
                backup = json.loads(guest('backup-boundary', sid, "python3 -c " + shlex.quote(
                    "from pathlib import Path;import json,subprocess;p=Path('/testbed/django/db/migrations/operations/models.py.backup');print(json.dumps(dict(exists=p.exists(),matches_base=p.read_bytes()==subprocess.check_output(['git','-C','/testbed','show','HEAD:django/db/migrations/operations/models.py']) if p.exists() else None)))")))
                assert backup['exists'] == (len(prefix)==4)
                assert not backup['exists'] or backup['matches_base']
                value['backup'] = backup
            return value

        class PythonPaths(module.Controller):
            async def physical_memory(self, label):
                return None

            async def start(self, role, *, template=None, parent=''):
                begin = time.monotonic_ns()
                if template:
                    sid = await asyncio.to_thread(cli, 'restore-' + role, 'start', template, '--detach', '--timeout', '1800', timeout=180)
                else:
                    sid = await asyncio.to_thread(call, 'cold-' + role, [sys.executable, '-B', ROOT / 'scripts/agentenv_cold_start.py',
                        '--config-home', args.config_home, '--service-url', args.service_url,
                        '--image', cfg['source_image'],
                        *(['--memory-mib','8192','--allow-8gib','--clean-guest-proxy'] if external else ['--local-no-proxy'])], 610)
                assert re.fullmatch(r'[a-fA-F0-9-]{36}', sid) and sid not in self.live
                self.live[sid] = sid
                self.by_role[role] = sid
                self.record('sandbox-ids.tsv', f'{role}\t{sid}\t{parent}\t{template or "root"}')
                self.record('events.tsv', f'{time.time_ns()}\tstart\troot\t{parent}\t{sid}\t{template or ""}\t{time.monotonic_ns()-begin}')
                await asyncio.to_thread(cli, 'ready-' + role, 'exec', sid, 'true')
                probe = 'import os; assert not any(os.environ.get(k) for k in ' + repr(PROXY_KEYS) + ')'
                await asyncio.to_thread(guest, 'proxy-' + role, sid, 'python3 -c ' + shlex.quote(probe))
                if cython and use_relay:
                    from cython_schedule_proxy import allow
                    relay_state=Path(os.environ['ASB_EXTERNAL_GUEST_RELAY_STATE'])
                    receipt=await asyncio.to_thread(allow,relay_state,sid,binding,source_pool=os.environ['ASB_EXTERNAL_GUEST_RELAY_POOL'])
                    write(out/('relay-'+role+'.json'),receipt)
                if template:
                    saved = self.checkpoints[template]
                    assert saved['parent'] == parent and sid != parent
                    state = await asyncio.to_thread(boundary, sid, saved['prefix'])
                    assert state == saved['boundary'], 'restored source/progress/marker changed'
                    process = dict(line.split('=', 1) for line in (await asyncio.to_thread(guest, 'restored-process-' + role, sid,
                        'sleep .3; bash /workspace/exp1-process-probe.sh')).splitlines())
                    verified = verify_process(saved['process'], process)
                    write(out / ('restored-' + role + '.json'), dict(status='PASS', boundary=state, process=verified, template=template))
                else:
                    source_probe = '' if c4 or text_editing or cython else "import subprocess,json;from pathlib import Path;head=subprocess.check_output(['git','-C','/testbed','rev-parse','HEAD'],text=True).strip();assert head==" + repr(cfg['base_commit']) + ";assert all((Path('/testbed')/p).read_bytes()==subprocess.check_output(['git','-C','/testbed','show','HEAD:'+p]) for p in " + repr(list(profile.FILES)) + ");print(json.dumps(dict(head=head,status=subprocess.check_output(['git','-C','/testbed','status','--porcelain'],text=True))))"
                    if c4:
                        source_probe = "import json,shutil;from pathlib import Path;assert Path('/app/c4_sample').is_dir();assert all(shutil.which(x) for x in ['python','python3','uv']);print(json.dumps(dict(status='PASS',scope='Fixed image, sample directory and tools; no dataset read')))"
                    if matplotlib:
                        source_probe = "import json,subprocess,shutil;head=subprocess.check_output(['git','-C','/testbed','rev-parse','HEAD'],text=True).strip();blob=subprocess.check_output(['git','-C','/testbed','hash-object','lib/matplotlib/axes/_axes.py'],text=True).strip();assert head=="+repr(cfg['source_commit'])+" and blob=="+repr(cfg['source_blob'])+";assert shutil.which('python')=='/opt/miniconda3/envs/testbed/bin/python';print(json.dumps(dict(status='PASS',head=head,source_blob=blob)))"
                    if text_editing:
                        source_probe = "import json,shutil;from pathlib import Path;assert all(shutil.which(x) for x in ['bash','python3','awk','cmp','vim']);assert all(Path(n).stat().st_size==s for n,s in [('/app/input.csv',51066691),('/app/expected.csv',38066688)]);print(json.dumps(dict(status='PASS',scope='Fixed image, CSV sizes and tools; no CSV contents read')))"
                    if cython:
                        source_probe = "import json,shutil;from pathlib import Path;assert not Path('/app/pyknotid').exists();assert all(shutil.which(x) for x in ['bash','python','python3','pip','git']);print(json.dumps(dict(status='PASS',scope='Fixed image, absent clone destination and required tools; no source or NumPy pre-read')))"
                    source_state = json.loads(await asyncio.to_thread(guest, 'source-' + role, sid, 'python3 -c ' + shlex.quote(source_probe)))
                    write(out / ('source-' + role + '.json'), source_state)
                    await asyncio.to_thread(cli, 'upload-workload-' + role, 'upload', sid, workload, '/workspace/mixfs-workload')
                    if external:
                        if c4 or text_editing or cython:
                            await asyncio.to_thread(guest,'prepare-probe-'+role,sid,'mkdir -p /probe')
                            await asyncio.to_thread(cli,'upload-payloads-'+role,'upload',sid,workload/'original','/probe/input')
                        if cython:
                            await asyncio.to_thread(cli,'upload-env-'+role,'upload',sid,out/'guest-env.sh','/probe/input/guest-env.sh')
                            from external_guest_proxy import NETWORK_CHECK,capture_relay_counters
                            directory=Path(os.environ['ASB_EXTERNAL_GUEST_RELAY_STATE'])/'guests'/sid if use_relay else None
                            before=await asyncio.to_thread(capture_relay_counters,directory,'before-head') if use_relay else None
                            ready=await asyncio.to_thread(guest,'network-'+role,sid,NETWORK_CHECK,timeout=55)
                            assert ready=='guest-network-ready'
                            if use_relay:
                                after=await asyncio.to_thread(capture_relay_counters,directory,'after-head')
                                assert before['allowed_packets'] is not None and after['allowed_packets']>before['allowed_packets'],'Cython HEAD did not use its owned rule'
                        await asyncio.to_thread(guest,'prepare-clock-'+role,sid,'chmod 755 /workspace/mixfs-workload/clock-ns && /workspace/mixfs-workload/clock-ns')
                        baseline = dict(status='PASS',scope='Fixed Cython image and tools; dynamic Git/pip unchanged' if cython else 'Fixed text editing image, CSV metadata and tools; no CSV contents read' if text_editing else 'Fixed Matplotlib source and interpreter; no extra tests' if matplotlib else 'Fixed C4 image and tools' if c4 else 'Pinned HEAD and unchanged source; no extra Django tests')
                    else:
                        await asyncio.to_thread(cli, 'upload-oracle-' + role, 'upload', sid, ROOT / 'scripts' / oracle, '/workspace/' + oracle)
                        await asyncio.to_thread(cli, 'upload-resolver-' + role, 'upload', sid, ROOT / 'scripts/xarray6992_test_ids.py', '/workspace/xarray6992_test_ids.py')
                        baseline = json.loads(await asyncio.to_thread(guest, 'baseline-' + role, sid, 'cd /testbed && ' + python + ' /workspace/' + oracle + ' --baseline'))
                    assert baseline['status'] == 'PASS'
                    write(out / ('baseline-' + role + '.json'), baseline)
                    vm = binding.resolve(sid)
                    roots = rootfs_inventory({role: vm}, {role: drives_for_vm(vm)}, opened_regular_files(binding.cgroup), {})
                    initial = [r for r in roots['guests'][role]['lowers'] if r['status'] == 'IDENTIFIED']
                    assert len({(r['device'], r['inode']) for r in initial}) == metadata['initial_lower_reference_count']
                    if external:assert sum(r['size'] for r in initial) <= (2 if cython else 1 if text_editing else 8 if matplotlib else 4)*2**30, 'Original image exceeds disk reservation'
                    if self.initial is None:
                        self.initial = initial
                        write(out / 'initial-image-layers.json', initial)
                    assert {(r['device'], r['inode']) for r in self.initial} == {(r['device'], r['inode']) for r in initial}
                    if args.schedule != 'grpo':
                        await asyncio.to_thread(cli, 'upload-probe', 'upload', sid, ROOT / 'scripts/exp1-process-probe.sh', '/workspace/exp1-process-probe.sh')
                        self.root_process = dict(line.split('=', 1) for line in (await asyncio.to_thread(guest, 'process-start', sid,
                            'bash /workspace/exp1-process-probe.sh start')).splitlines())
                await asyncio.to_thread(runtime.start, sid, role, 'restored' if template else 'ready')
                return sid

            async def run_segment(self, sandbox, role, segment):
                result.setdefault('first_tool_start_monotonic_ns', time.monotonic_ns())
                self.by_role[role] = sandbox
                begin = time.monotonic_ns()
                await super().run_segment(sandbox, role, segment)
                end = time.monotonic_ns()
                reference_patch = None
                if (branch_policy == EARLY2_POLICY and role == 'early_2' and segment == 'early_2_inspect' or
                        find_diagnostic and segment == 'trace_cpp_parser' or
                        xarray_find_diagnostic and segment == 'trace_index_state'):
                    prefix_role = role if args.schedule == 'grpo' else 'backbone'
                    reference_patch = (out / 'rollouts' / prefix_role / 'read_repo/patch.diff').read_bytes()
                value = verify_segment(out, workload, role, segment, samples, args.task_profile,
                    branch_policy=branch_policy, reference_patch=reference_patch, find_diagnostic=find_diagnostic,
                    xarray_find_diagnostic=xarray_find_diagnostic)
                result['paths'].setdefault(role, dict(action_count=0, tool_ns=0, segments=[], exit_differences=[]))
                r = result['paths'][role]
                r['action_count'] += value['action_count']; r['tool_ns'] += value['tool_ns']
                r['segments'].append(segment); r['exit_differences'] += value['exit_differences']
                r.setdefault('first_segment_start_monotonic_ns', begin); r['last_segment_end_monotonic_ns'] = end

            async def capture_agentenv_template(self, sandbox, point):
                offset = 2 if '2' in point and point.startswith('save-after') else 4 if point.startswith('save-after') else trie['forks'][point]['offset']
                prefix = self.rollouts['backbone'][:offset]
                marker = json.dumps(dict(prefix=prefix, next_segments=[self.rollouts[r][offset] for r in ROLES if len(self.rollouts[r]) > offset]))
                await asyncio.to_thread(guest, 'checkpoint-marker', sandbox, 'printf %s ' + shlex.quote(marker) + ' > /workspace/python-schedule-marker')
                saved = await asyncio.to_thread(boundary, sandbox, prefix)
                process = dict(line.split('=', 1) for line in (await asyncio.to_thread(guest, 'checkpoint-process', sandbox,
                    'bash /workspace/exp1-process-probe.sh')).splitlines())
                assert all(process[k] == self.root_process[k] for k in ('pid', 'start_ticks', 'boot_id'))
                await asyncio.to_thread(runtime.stop, sandbox, 'checkpoint')
                try:
                    template = await super().capture_agentenv_template(sandbox, point)
                finally:
                    await asyncio.to_thread(runtime.start, sandbox, 'backbone', 'after-checkpoint')
                self.checkpoints[template] = dict(parent=sandbox, prefix=prefix, boundary=saved, process=process)
                write(out / ('checkpoint-' + str(offset) + '.json'), dict(template=template, **self.checkpoints[template]))
                return template

            async def do_grpo(self):
                roots = await joined(self.start(role) for role in ROLES)
                await joined(self.run_path(sid, role, self.rollouts[role]) for sid, role in zip(roots, ROLES))

            async def do_bpo(self):
                root = await self.start('backbone'); path = self.rollouts['backbone']
                await self.run_path(root, 'backbone', path[:2])
                early = await self.capture_agentenv_template(root, 'save-after-backbone-2')
                await self.run_path(root, 'backbone', path[2:4])
                late = await self.capture_agentenv_template(root, 'save-after-backbone-4')
                await self.run_path(root, 'backbone', path[4:])
                children = await joined(self.start(role, template=early if role.startswith('early_') else late, parent=root) for role in ROLES[1:])
                await joined(self.run_path(sid, role, executed[role]) for sid, role in zip(children, ROLES[1:]))

            async def fork(self, sandbox, count, point, role, *, event='fork'):
                planned = trie['forks'][point]
                assert planned['parent_role'] == role and len(planned['child_roles']) == count
                template = await self.capture_agentenv_template(sandbox, point)
                children = await joined(self.start(r, template=template, parent=sandbox) for r in planned['child_roles'])
                return sandbox, children

        controller = PythonPaths(SimpleNamespace(system='agentenv', raw_dir=out, timeout=900 if text_editing or cython else 600))
        controller.by_role = {}; controller.checkpoints = {}; controller.initial = None
        stage = 'seven-role-' + args.schedule
        result['schedule_start_monotonic_ns'] = time.monotonic_ns()
        asyncio.run(getattr(controller, 'do_' + args.schedule)())
        result['original_paths_end_monotonic_ns'] = time.monotonic_ns()
        assert set(controller.by_role) == set(ROLES) and len(set(controller.by_role.values())) == 7
        for role in ROLES:
            assert result['paths'][role]['segments'] == executed[role]
        result['actual_action_count'] = sum(p['action_count'] for p in result['paths'].values())
        assert result['actual_action_count'] == expected['actual_actions']
        result['checks']['original_actions_order_and_exit_policy'] = 'PASS'
        stage = 'endpoint-cache-and-pss'
        from host_schedule_files import permission_evidence
        result['mincore_permission'] = permission_evidence()
        result['schedule_smoke_metrics'] = observe(args.container, out, controller.by_role, result['original_paths_end_monotonic_ns'],
            expanded=True, cache_layers=True, block_cache_method='cachestat', split_ram=True,
            initial_files=controller.initial, initial_file_count=metadata['initial_lower_reference_count'],
            initial_reference_mode='all_active_image_layer_copies')
        assert result['schedule_smoke_metrics']['status'] == 'OBSERVED'
        result['checks']['endpoint_identity_and_queries'] = 'PASS'
        stage = 'final-state-and-official'
        evaluation_input = out / 'evaluation-input'; evaluation_input.mkdir()
        if not external:
            for name in ('swebench-task.json', 'required-tests.json', 'test.patch'):
                shutil.copy2(args.inputs / name, evaluation_input / name)
            task = json.loads((args.inputs / 'swebench-task.json').read_text())
            f2p, p2p = json.loads(task['FAIL_TO_PASS']), json.loads(task['PASS_TO_PASS'])
        async def final_check(role):
            sid = controller.by_role[role]; path = executed[role]
            if cython:
                from external_workload import CYTHON_VALIDATION
                validation=profile.validate_role_outputs(out,role,path)
                actual=json.loads(await asyncio.to_thread(guest,'source-final-'+role,sid,'python3 -c '+shlex.quote(profile.SOURCE_STATE_CODE)))
                archived=json.loads((out/'rollouts'/role/path[-1]/'source-state.json').read_text())
                assert actual==archived, 'Cython live source differs from archived final segment'
                assert actual['head']==profile.SOURCE_COMMIT
                assert actual['patch'].strip()==(args.inputs/'reference.patch').read_text().strip(), 'Cython tracked patch differs from original step 79'
                assert all(v is None for v in actual['scripts'].values()), 'Original final cleanup of fix scripts missing'
                assert all(len(v)==1 for v in actual['extensions'].values()), 'Source-tree compiled extension missing/ambiguous'
                check=await asyncio.to_thread(guest,'extensions-'+role,sid,CYTHON_VALIDATION)
                assert 'source_repo_commit: '+profile.SOURCE_COMMIT in check and check.splitlines()[-1]=='compiled extensions import successfully'
                grade=dict(status='PASS',scope='Original NumPy 2.3.0 snippet, two 18-test contexts and compiled imports; no hidden verifier',test_pass_counts=[18,18])
                result['paths'][role].update(official=grade,functional_validation='PASS',original_output_validation=validation,extension_validation='PASS',patch_matches_archived_guest=True,patch_reference='Original step 79, 12 tracked files; independently compared at final state')
                destination=out/'evaluation'/role;destination.mkdir(parents=True)
                write(destination/'receipt.json',dict(status='PASS',original_outputs=validation,extensions=check,source_commit=actual['head'],official=grade))
                return
            if text_editing:
                validation = profile.validate_role_outputs(out,role,path)
                actual = json.loads(await asyncio.to_thread(guest,'source-final-'+role,sid,'python3 -c '+shlex.quote(profile.SOURCE_STATE_CODE)))
                archived = json.loads((out/'rollouts'/role/path[-1]/'source-state.json').read_text())
                assert actual==archived, 'Text editing live state differs from archived final segment'
                fixed = json.loads((args.inputs/'final-scripts.json').read_text())
                assert all(actual['scripts'][k]==v for k,v in fixed.items()), 'Text editing final scripts changed'
                check = await asyncio.to_thread(guest,'functional-'+role,sid,'test -x /app/solve.sh && test -f /app/output.csv && cmp -s /app/output.csv /app/expected.csv')
                grade = dict(status='NOT_RUN',scope='Original byte-exact output and both solve variants; no hidden verifier')
                result['paths'][role].update(official=grade,functional_validation='PASS',output_comparison='BYTE_EXACT',original_output_validation=validation,final_scripts_match=True)
                destination=out/'evaluation'/role;destination.mkdir(parents=True)
                write(destination/'receipt.json',dict(status='PASS',original_outputs=validation,output_comparison='BYTE_EXACT',final_scripts_match=True,official=grade,output=check))
                return
            if c4:
                actual = json.loads(await asyncio.to_thread(guest,'source-final-'+role,sid,'python3 -c '+shlex.quote(profile.SOURCE_STATE_CODE)))
                archived = json.loads((out/'rollouts'/role/path[-1]/'source-state.json').read_text())
                assert actual==archived, 'C4 live source differs from archived final segment'
                fixed = json.loads((args.inputs/'final-sources.json').read_text())
                assert all(actual[k]==v for k,v in fixed.items()), 'C4 final source differs from original reference'
                validation = profile.validate_role_outputs(out,role,path)
                check = await asyncio.to_thread(guest,'syntax-'+role,sid,'test -s /app/compress.py && test -s /app/decompress.py && python3 -m py_compile /app/compress.py /app/decompress.py')
                grade = dict(status='NOT_RUN',scope='Original C4 sample roundtrip, constraints and syntax; no hidden verifier')
                result['paths'][role].update(official=grade,syntax_validation='PASS',patch_matches_archived_guest=True,
                    patch_reference='Original final compress.py/decompress.py/pyproject.toml',roundtrip_validation=validation)
                destination=out/'evaluation'/role;destination.mkdir(parents=True)
                write(destination/'receipt.json',dict(status='PASS',syntax_validation='PASS',roundtrip=validation,official=grade,output=check))
                return
            actual = await asyncio.to_thread(guest, 'patch-' + role, sid, 'git -C /testbed diff '+('HEAD ' if matplotlib else '')+'--binary', preserve_output=True)
            archived = (out / 'rollouts' / role / path[-1] / 'patch.diff').read_text()
            assert normalize_patch(actual) == normalize_patch(archived), 'guest differs from archived branch patch'
            if matplotlib:
                reference=(args.inputs/'reference.patch').read_text()
                assert actual.strip()==archived.strip()==reference.strip(), 'Matplotlib full staged/unstaged final patch differs'
                validation=profile.validate_role_outputs(out,role,path)
                check=await asyncio.to_thread(guest,'imports-'+role,sid,"cd /testbed && git diff --stat && python -c 'import matplotlib, numpy; print(matplotlib.__version__, numpy.__version__)'")
                grade=dict(status='PASS',scope='Original histogram tests at 88 and 93 only; no hidden verifier',test_pass_counts=[60,60])
                result['paths'][role].update(official=grade,functional_validation='PASS',patch_matches_archived_guest=True,
                    patch_reference='Original full git diff HEAD --binary, including staged backup',original_test_validation=validation)
                destination=out/'evaluation'/role;destination.mkdir(parents=True)
                write(destination/'receipt.json',dict(status='PASS',tests=validation,patch_reference='original final-source.patch',imports=check,official=grade))
                return
            if django:
                assert normalize_patch(actual) == normalize_patch((args.inputs/'reference.patch').read_text()), 'Django final patch differs from original reference'
                check = await asyncio.to_thread(guest,'syntax-'+role,sid,
                    'cd /testbed && git rev-parse HEAD && git diff --stat && python -m py_compile django/db/migrations/operations/models.py')
                assert check.splitlines()[0] == cfg['base_commit']
                destination=out/'evaluation'/role;destination.mkdir(parents=True)
                grade=dict(status='NOT_RUN',scope='Original Django source patch and syntax check only; no official test suite')
                result['paths'][role].update(official=grade,syntax_validation='PASS',patch_matches_archived_guest=True,
                    patch_reference='fixed original Django final patch, all seven roles')
                write(destination/'receipt.json',dict(status='PASS',syntax_validation='PASS',patch_reference='original final-source.patch',output=check,official=grade))
                return
            if role == 'backbone':
                selected = await asyncio.to_thread(guest, 'main-patch', sid, 'git -C /testbed diff -- ' + ' '.join(profile.FILES), preserve_output=True)
                assert normalize_patch(selected) == normalize_patch((args.inputs / 'reference.patch').read_text())
                invariant = json.loads(await asyncio.to_thread(guest, 'main-invariant', sid, 'cd /testbed && ' + python + ' /workspace/' + oracle))
                assert invariant['status'] == 'PASS'
            await asyncio.to_thread(cli, 'evaluation-upload-' + role, 'upload', sid, evaluation_input, '/workspace/evaluation-input')
            evaluation = json.loads(await asyncio.to_thread(guest, 'official-' + role, sid,
                'cd /testbed && ' + python + ' /workspace/' + oracle + ' --official /workspace/evaluation-input', timeout=180))
            destination = out / 'evaluation' / role; destination.mkdir(parents=True)
            await asyncio.to_thread(cli, 'evaluation-download-' + role, 'download', sid, '/workspace/evaluation-input', destination)
            xml = destination / 'evaluation-input/official.xml'
            grade = profile.grade_xml(xml.read_text(), f2p + p2p, f2p, p2p, evaluation['exit_code'], evaluation.get('test_id_aliases'))
            if role == 'backbone':
                assert (grade['passed'], grade['failed']) == ((24, 1) if args.task_profile == 'sphinx7590' else (945, 12))
                result['official'] = grade
            result['paths'][role].update(official=grade, patch_matches_archived_guest=True,
                patch_reference='fixed backbone reference' if role == 'backbone' else 'original commands and archived live branch; no independent full-patch reference')
            write(destination / 'receipt.json', evaluation)
        asyncio.run(joined(final_check(role) for role in ROLES))
        with (out / 'events.tsv').open() as stream:
            scheduled_events = list(csv.DictReader(stream, delimiter='\t'))
        saves = [r for r in scheduled_events if r['event'] == 'checkpoint']
        restores = [r for r in scheduled_events if r['event'] == 'start' and r['parent']]
        assert (len(saves), len(restores)) == (expected['saves'], expected['restores'])
        result.update(status='PASS', diagnostic_status='PASS', replay=dict(action_count=result['actual_action_count']),
            save_restore=dict(saves=saves, restores=restores))
        if cython:result.update(strict_replay_status='PASS',functional_validation='PASS',official_tests='ORIGINAL_TWO_18_PASS_CONTEXTS_AND_COMPILED_IMPORTS_NO_HIDDEN_VERIFIER')
        if text_editing:result.update(strict_replay_status='PASS',functional_validation='PASS',official_tests='NOT_RUN_ORIGINAL_BYTE_EXACT_OUTPUT_AND_TWO_SOLVE_VARIANTS')
        if c4:result.update(strict_replay_status='PASS',functional_validation='PASS',official_tests='NOT_RUN_ORIGINAL_SAMPLE_ROUNDTRIP_ONLY')
        if django:result.update(strict_replay_status='PASS',functional_validation='PASS',official_tests='NOT_RUN_ORIGIN_SYNTAX_ONLY')
        if matplotlib:result.update(strict_replay_status='PASS',functional_validation='PASS',official_tests='ORIGINAL_HISTOGRAM_TESTS_60_PASSED_TWICE_NO_HIDDEN_VERIFIER')
        result['checks']['all_branch_results_archived_and_backbone_preserved'] = 'PASS'
    except BaseException as error:
        result.update(status='FAIL', error=repr(error), failed_stage=stage)
        (out / 'failure.txt').write_text(traceback.format_exc())
    finally:
        cleanup = {}
        if controller is not None:
            errors = []
            if c4:result['cleanup_guest_delete_timeout_seconds'] = 90
            for sid in list(controller.live):
                try:
                    runtime.stop(sid, 'cleanup')
                except Exception as error:
                    errors.append(repr(error))
                try:
                    cli('delete-guest', 'delete', sid, timeout=90 if c4 else 60)
                    controller.live.pop(sid)
                except Exception as error:
                    errors.append(repr(error))
            for template in controller.agentenv_templates:
                try:
                    delete_snapshot(cli, template)
                except Exception as error:
                    errors.append(repr(error))
            cleanup['owned_guests_removed'] = not controller.live
            cleanup['snapshots_removed'] = not errors
            try:
                cleanup['service_sandbox_list_empty'] = json.loads(cli('list-after', 'list', '--output', 'json')) == []
                if (c4 or matplotlib or text_editing or cython) and not cleanup['service_sandbox_list_empty']:
                    raise RuntimeError('External sandbox list not empty after cleanup')
            except Exception as error:
                cleanup['service_sandbox_list_empty'] = False
                errors.append(repr(error))
            if errors:
                result.update(status='FAIL', cleanup_errors=errors)
        if host is not None:
            try:
                result['host_collector'] = host.stop()
            except Exception as error:
                result.update(status='FAIL', host_collector_error=repr(error))
        runtime.write()
        monitor = json.loads((out / 'runtime-monitor.json').read_text())
        cleanup['samplers_stopped'] = not runtime.active and monitor['status'] == 'PASS'
        if not cleanup.get('samplers_stopped'):
            result['status'] = 'FAIL'
        result['verified_action_count'] = sum(p['action_count'] for p in result['paths'].values())
        result['actual_action_count'] = sum(len(rows(p)) for p in (out / 'rollouts').glob('*/*/steps.tsv'))
        result['actual_action_count_scope'] = 'complete actual calls' if 'original_paths_end_monotonic_ns' in result else 'downloaded actual-call subtotal; undownloaded calls unknown'
        result.update(cleanup=cleanup, controller_elapsed_ns=time.monotonic_ns() - started)
        if 'original_paths_end_monotonic_ns' in result:
            result['schedule_elapsed_ns'] = result['original_paths_end_monotonic_ns'] - result['schedule_start_monotonic_ns']
            result['actions_download_elapsed_ns'] = result['original_paths_end_monotonic_ns'] - result['first_tool_start_monotonic_ns']
        write(out / 'result.json', result)
    print('status=' + result['status'] + ' result_dir=' + str(out), flush=True)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--task-profile', choices=('sphinx7590', 'xarray6992','django16819','c4','matplotlib24177','text_editing','cython'), required=True)
    parser.add_argument('--schedule', choices=('grpo', 'bpo', 'tvcache'), required=True)
    parser.add_argument('--branch-pipeline-policy', choices=('original', EARLY2_POLICY), default='original')
    parser.add_argument('--find-exit-diagnostic', action='store_true', help='Explicit trace_cpp_parser/005 exact-output 0/1 diagnostic; strict FAIL retained')
    parser.add_argument('--xarray-find-exit-diagnostic', action='store_true', help='Explicit trace_index_state/001 exact-output 0/1 diagnostic; strict FAIL retained')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--guest-relay',action='store_true',help='Approved Cython only; per-instance rules, outer-owned relay')
    for name in ('container', 'service-url'):
        parser.add_argument('--' + name)
    for name in ('config-home', 'output'):
        parser.add_argument('--' + name, type=Path)
    raise SystemExit(run(parser.parse_args()))
