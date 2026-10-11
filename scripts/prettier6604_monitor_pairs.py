"""Strict Prettier replay audit for matched monitoring runs; retain output differences."""
import csv
import hashlib
import json
from pathlib import Path
import re

from prettier6604_inputs import ROOT,validate,sha
from audit_prettier6604_official import inspect,reference,signature
from sandbox_runtime_monitor import boundary_runner
from task_metrics_prefix import without_sampling

JEST_STEPS={('core_verify','005'):(2,2,2),('backbone_finish','002'):(1,1,1),('backbone_finish','005'):(32,66,66)}
JEST_WARNING_NAMES={'ES6_ExportFrom_Source2','ES6_Default_NamedClass2','ES6_Named1'}


def _normalize_known_jest_warnings(text):
    pattern=re.compile(r'This warning is caused by a @providesModule declaration with the same name across two different files\.\n'
        r'jest-haste-map: @providesModule naming collision:\n'
        r'  Duplicate module name: ([^\n]+)\n'
        r'  Paths: ([^\n]+)\n(?:\n)?')
    matches=list(pattern.finditer(text))
    if not matches:return text
    names=[m.group(1) for m in matches]
    if set(names)!=JEST_WARNING_NAMES or len(names)!=len(JEST_WARNING_NAMES):return text
    for m in matches:
        name=m.group(1)
        expected=f'/testbed/tests/flow/es6modules/{name}.js collides with /testbed/tests/flow/declare_export/{name}.js'
        if m.group(2)!=expected:return text
    if any(text[a.end():b.start()] for a,b in zip(matches,matches[1:])):return text
    blocks=[m.group(0) for m in matches]
    start=matches[0].start();end=matches[-1].end()
    if start!=0:return text
    return ''.join(sorted(blocks,key=lambda b: re.search(r'Duplicate module name: ([^\n]+)',b).group(1)))+text[end:]


def _normalize_known_process_ids(text):
    return re.sub(r'\(node:\d+\)(?= \[DEP0128\])','(node:<pid>)',text)


def output_signature(content,segment,step,kind,suite_paths):
    text=content.decode()
    if (segment,step,kind)==('read_repo','004','stderr'):
        warning="(node:<pid>) [DEP0128] DeprecationWarning: Invalid 'main' field in '/testbed/node_modules/find-parent-dir/package.json' of 'find-parent-dir.js'. Please either fix that or report it to the module author\n(Use `node --trace-deprecation ...` to show where the warning was created)\n"
        candidate=_normalize_known_process_ids(text)
        if candidate==warning:text=candidate
    if (segment,step)==('core_verify','005') and kind=='stdout':text=_normalize_known_jest_warnings(text)
    if kind!='stdout' or (segment,step) not in JEST_STEPS:return hashlib.sha256(text.encode()).hexdigest()
    totals=JEST_STEPS[segment,step]
    for label,n in zip(('Test Suites','Tests','Snapshots'),totals):
        outcome='written' if label=='Snapshots' and (segment,step)==('backbone_finish','002') else 'passed'
        assert re.search(r'^'+label+r':\s+'+str(n)+' '+outcome+', '+str(n)+r' total$',text,re.M),f'changed Jest outcomes: {segment}/{step} {label}'
    lines=[]
    allowed={'tests/typescript/conformance/types/typeOperator/jsfmt.spec.js','tests/typescript/conformance/types/indexedAccesType/jsfmt.spec.js'} if segment=='core_verify' else {'tests/typescript/custom/parenthesizedType/jsfmt.spec.js'} if step=='002' else set(suite_paths)
    for line in text.splitlines(keepends=True):
        match=re.fullmatch(r'PASS (tests/\S+)(?: \(\d+(?:\.\d+)?s\))?\n?',line)
        if match:
            assert match[1] in allowed,'unexpected reported test path'
            continue # tail reports only the last completed suite, not the full set
        if re.fullmatch(r'Time:\s+\d+(?:\.\d+)?s(?:, estimated \d+s)?\n?',line):line='Time: <measured>\n'
        if re.fullmatch(r'Done in \d+(?:\.\d+)?s\.\n?',line):line='Done in <measured>.\n'
        if (segment,step)==('backbone_finish','002') and re.fullmatch(r'  ✓ parenthesizedType\.ts \(\d+ms\)\n?',line):line='  ✓ parenthesizedType.ts (<measured>)\n'
        lines.append(line)
    return hashlib.sha256(''.join(lines).encode()).hexdigest()


def inspect_continued_run(raw,mode):
    """Resume an audited live PASS after an offline checker error."""
    raw=raw.resolve()
    assert raw.is_relative_to(ROOT/'.artifacts/storage-isolated-service') and raw.name=='prettier6604'
    run=inspect_run(raw,mode)
    outer=json.loads((raw.parent/'result.json').read_text())
    assert outer['capacity_bytes']==8*2**30
    assert outer['readonly_replay_budget']['immutable_commits']=='external verified read-only RAM cache'
    code=raw.parent/'code';versions={}
    for name in ('run_prettier6604.py','validate_isolated_agentenv.py','prettier6604_inputs.py',
                 'audit_prettier6604_official.py','sandbox_runtime_monitor.py','guest_memory_collector.py',
                 'guest_performance.py','guest_diskstats.py','host_sandbox_monitor.py'):
        versions[name]=sha(code/name)
        assert versions[name]==sha(ROOT/'scripts'/name),'execution code changed: '+name
    return run,versions


def inspect_first_off(raw):
    return inspect_continued_run(raw,'off')


def inspect_run(raw,mode):
    cfg,workload,_=validate();result=json.loads((raw/'result.json').read_text())
    outer=json.loads((raw.parent/'result.json').read_text())
    assert result['status']==outer['status']=='PASS' and result['monitor_mode']==mode
    assert result['task_id']==cfg['task_id'] and result['checkpoint_restore']=='NOT_RUN'
    assert all(result['checks'][k]=='PASS' for k in ('smoke_and_unfixed_oracle','frozen_order_hashes_exit_codes_patch_and_key_outputs','independent_properties','official_outcome_matches_full_replay'))
    assert result['cleanup']['sandbox_removed']
    assert all(outer['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_firecracker_identities_unchanged','original_service_unchanged'))
    assert json.loads((raw/'effective-config.json').read_text())['source_image']==cfg['source_image']
    original=raw/'frozen-input/guest-runner.original.sh';runner=raw/'frozen-input/guest-runner.sh'
    assert sha(original)==cfg['runner_sha256']
    assert runner.read_text()==(boundary_runner(original.read_text(),guest_phases=True) if mode=='continuous' else without_sampling(original.read_text()))
    initial=json.loads((raw/'source-smoke.json').read_text());assert initial['base_commit']==cfg['base_commit']
    assert len(initial['suite_paths'])==32 and len(set(initial['suite_paths']))==32
    outputs={};normalized={};steps=[]
    for segment in cfg['path']:
        directory=raw/'rollouts/backbone'/segment
        assert sha(directory/'actions.tsv')==cfg['manifest_sha256'][segment]==sha(workload/'segments'/segment/'actions.tsv')
        with (directory/'steps.tsv').open() as f:rows=list(csv.DictReader(f,delimiter='\t'))
        assert [r['step'] for r in rows]==[f'{i:03}' for i in range(1,len(rows)+1)]
        assert [int(r['exit_code']) for r in rows]==cfg['expected_exit_codes'][segment]
        steps.extend(rows)
        for row in rows:
            for kind in ('stdout','stderr'):
                path=directory/kind/(row['step']+'.log');key=f"{segment}/{kind}/{path.name}"
                outputs[key]=sha(path);normalized[key]=output_signature(path.read_bytes(),segment,row['step'],kind,initial['suite_paths'])
        for name in ('guest-memory.tsv','guest-vmstat.tsv'):
            with (directory/name).open() as f:memory=list(csv.DictReader(f,delimiter='\t'))
            assert bool(memory)==(mode=='continuous')
    assert len(steps)==result['action_count']==29
    patch=raw/'rollouts/backbone/backbone_finish/patch.diff'
    assert sha(patch)==cfg['reference_patch_sha256']==result['final_patch_sha256']
    final=json.loads((raw/'final-state.json').read_text())
    after=json.loads((raw/'source-after-tests.json').read_text())
    assert final['base_commit']==after['base_commit']==cfg['base_commit'] and final['files']==after['files']
    assert json.loads((raw/'independent-check.json').read_text())['status']=='PASS'
    effective=json.loads((raw/'effective-config.json').read_text())
    official=inspect(Path(effective['official_task']),(raw/'official-task-tests.stdout').read_text())
    assert official==json.loads((raw/'official-task-result.json').read_text())
    assert signature(official)==signature(reference(raw/'expected-official-audit.json'))
    if mode=='off':
        assert result['cleanup']['monitoring_disabled'] and not (raw/'runtime-monitor.json').exists()
        assert not (raw/'host/memory-samples.tsv').exists() and not (raw/'host-vmm').exists()
    else:
        assert result['cleanup']['monitor_stopped']
        from report_sandbox_runtime import samples
        from report_sandbox_performance import audit
        groups,_=samples(raw);assert len(groups)==1
        assert all(v['guest'] and v['host'] for v in audit(raw,groups).values())
    return dict(raw=str(raw),mode=mode,configuration=cfg,official=signature(official),
        final_patch_sha256=sha(patch),final_files=final,source_before=initial,
        tool_output_signatures=normalized,original_output_hashes=outputs,
        guest_action_seconds=sum(int(r['duration_ns']) for r in steps)/1e9,
        frozen_replay_seconds=result['frozen_replay_elapsed_ns']/1e9,lifecycle_seconds=result['controller_elapsed_ns']/1e9,
        observed_volume_used_peak_bytes=outer['observed_volume_used_peak_bytes'])
