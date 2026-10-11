"""Frozen Exp1 workload for the existing owned-VM PFN measurement lifecycle."""
import csv
import hashlib
import json
import re
from pathlib import Path
import shutil
import time

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke'
WORKLOAD=EXP/'workloads/prettier-14400'
RUNNER=EXP/'systems/agentenv/guest-runner.sh'
PATCH='f1cc23ac4f5ac1cff87eb7f8c54f8176c1a47cec37fa4cb6907b9dd8ad199af2'


def sources():
    return [Path(__file__),RUNNER,WORKLOAD/'actions.tsv',
            ROOT/'scripts/exp1_independent_check.cjs',*sorted((WORKLOAD/'actions').glob('*.sh'))]


def verify_replay(directory):
    rows=list(csv.DictReader((directory/'steps.tsv').open(),delimiter='\t'))
    assert [row['step'] for row in rows]==[f'{i:03d}' for i in range(1,27)]
    assert {row['step']:int(row['exit_code']) for row in rows if int(row['exit_code'])}=={'008':127,'016':127}
    assert (directory/'actions.tsv').read_bytes()==(WORKLOAD/'actions.tsv').read_bytes()
    assert '    document.addEventListener("DOMContentLoaded", () => {' in (directory/'stdout/024.log').read_text()
    assert hashlib.sha256((directory/'patch.diff').read_bytes()).hexdigest()==PATCH
    return dict(status='PASS',action_count=len(rows),expected_failures={'008':127,'016':127},
        patch_sha256=PATCH,action_manifest_sha256=hashlib.sha256((directory/'actions.tsv').read_bytes()).hexdigest(),
        guest_action_sum_ns=sum(int(row['duration_ns']) for row in rows))


def file_lru_accounting(out):
    measured=json.loads((out/'host/physical-memory.json').read_text())
    coverage=json.loads((out/'coverage.json').read_text())
    assert coverage['unmapped_pages']==0
    assert measured['guest_file_cache_logical_pages']==coverage['logical_pages']
    assert measured['dax_file_host_physical_pss_mib']==0
    selected_bytes=coverage['independent_unique_host_pages']*4096
    assert selected_bytes==measured['guest_file_cache_host_physical_mib']*2**20
    values={}
    for when in ('before','after'):
        raw=(out/f'exp1-memory-{when}-probe.stdout').read_text()
        values[when]={key:int(value)*1024 for key,value in re.findall(r'^(\w+):\s+(\d+) kB$',raw,re.M)}
        assert {'Cached','Buffers','Shmem'}<=values[when].keys()
    result=dict(status='PARTIAL',unit='bytes',
        selected_guest_file_lru_host_bytes=selected_bytes,
        selected_nonresident_mappings=coverage['nonresident_mappings'],
        current_file_cache_physical_bytes=None,
        guest_meminfo_bytes=values,
        guest_cache_and_buffers_delta_bytes=sum(values['after'][key]-values['before'][key]
                                               for key in ('Cached','Buffers')),
        guest_probe_elapsed_ns=int((out/'guest-probe-duration-ns.txt').read_text()),
        host_join_elapsed_ns=coverage['scan_ns'],
        scope='host residency of the guest PFN list selected at one earlier file-LRU scan; single VM',
        reason='Guest page types were not rechecked before/after host mapping reads; file-LRU may contain metadata',
        limitations=['Two host page-table reads agreeing does not prove guest pages remain file cache',
                     'Meminfo changes do not establish that the probe caused reclamation',
                     'Not named task file contents, complete host cache, DAX residency or multi-VM sharing benefit'])
    if (out/'file-type-recheck.json').exists():
        recheck=json.loads((out/'file-type-recheck.json').read_text())
        assert recheck['status']=='PARTIAL' and recheck['current_file_cache_physical_bytes'] is None
        assert recheck['selected_guest_pages']==coverage['logical_pages']
        result['type_recheck']=recheck
        result['reason']='Guest types rechecked around host reads, but non-atomic checks do not exclude PFN reuse or establish task file attribution'
    return result


def run_exp1(aenv, sid, out, *, persistent_sampling=False):
    inputs=out/'exp1-inputs';inputs.mkdir()
    shutil.copytree(WORKLOAD/'actions',inputs/'actions')
    for path,name in [(RUNNER,'runner.sh'),(WORKLOAD/'actions.tsv','actions.tsv'),
                      (ROOT/'scripts/exp1_independent_check.cjs','check.cjs')]:
        shutil.copy2(path,inputs/name)
    if persistent_sampling:
        from persistent_guest_sampling import instrument
        runner=inputs/'runner.sh';runner.write_text(instrument(runner.read_text()))
        shutil.copy2(ROOT/'scripts/guest_memory_collector.py',inputs/'collector.py')
    manifest=[line.split('\t',2) for line in (inputs/'actions.tsv').read_text().splitlines()]
    assert [row[0] for row in manifest]==[f'{i:03d}' for i in range(1,27)]
    for step,digest,_ in manifest:
        assert hashlib.sha256((inputs/'actions'/f'{step}.sh').read_bytes()).hexdigest()==digest
    guest='/workspace/asb-exp1-physical'
    aenv('exp1-upload','upload',sid,str(inputs),'/workspace/asb-exp1-physical')
    hashes=aenv('exp1-input-hashes','exec',sid,'bash','-lc',f'sha256sum {guest}/actions/*.sh').decode()
    assert [line.split()[0] for line in hashes.splitlines()]==[row[1] for row in manifest]
    aenv('exp1-environment','exec',sid,'bash','-lc',
         'git -C /testbed rev-parse HEAD; git -C /testbed status --porcelain; uname -r; node --version')
    before=json.loads(aenv('exp1-independent-before','exec',sid,'bash','-lc',
                         f'node {guest}/check.cjs; rc=$?; test "$rc" -eq 1'))
    assert not before['svg']['javascript_formatted']
    environment=dict(ACTIONS_DIR=guest+'/actions',OUTPUT_DIR='/workspace/asb-exp1-physical/replay',
                     ACTION_TIMEOUT_SECONDS='60',GUEST_MEMORY_SAMPLING='1' if persistent_sampling else '0',DROP_GUEST_CACHES='0',
                     START_STEP='001',END_STEP='026')
    if persistent_sampling:
        environment.update(ASB_GUEST_MEMORY_COLLECTOR=guest+'/collector.py',MEMORY_SAMPLE_INTERVAL_SECONDS='0.1')
    started=time.monotonic_ns()
    aenv('exp1-frozen-replay','exec',sid,'env',*[f'{key}={value}' for key,value in environment.items()],
         'bash',guest+'/runner.sh',timeout=180)
    elapsed=time.monotonic_ns()-started
    aenv('exp1-replay-download','download',sid,environment['OUTPUT_DIR'],str(out))
    result=verify_replay(out/'replay')
    after=json.loads(aenv('exp1-independent-after','exec',sid,'node',guest+'/check.cjs'))
    assert all(row['javascript_formatted'] and row['idempotent'] for row in after.values())
    result.update(controller_runner_ns=elapsed,independent_svg_properties='PASS',official_task_tests='NOT_RUN',
        environment=environment,cwd='/testbed',session_semantics='one bash per frozen action',
        cache_policy='no cache flush; fresh guest; existing host cache',
        scope='physical analysis after full replay and independent check; not a performance comparison')
    (out/'exp1-correctness.json').write_text(json.dumps(result,indent=2)+'\n')
    aenv('exp1-memory-before-probe','exec',sid,'cat','/proc/meminfo')
    return result
