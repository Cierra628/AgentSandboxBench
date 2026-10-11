"""Offline v1 acceptance audit; raw evidence stays local, no new experiments."""
import argparse
import json
from pathlib import Path
import csv
from audit_exp1_metrics import audit_run, sha
from exp1_official_task import grade, HEAD

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'f1cc23ac4f5ac1cff87eb7f8c54f8176c1a47cec37fa4cb6907b9dd8ad199af2'
STDOUT25 = '9a64ed707c224734715f7af4fea2d84f0b51d128e5c8d05535fc9de7d5b6162f'


def audit(config):
    bundles = {}
    for backend, name in config['bundles'].items():
        path = ROOT/name
        manifest = json.loads((path/'reproduction-manifest.json').read_text())
        assert manifest['historical_raw_copied'] is False and manifest['credentials_copied'] is False
        assert (path/'runtime').is_symlink()
        for file, digest in manifest['source_sha256'].items():
            assert sha(path/file) == digest, f'copied source changed: {file}'
        for file, digest in manifest['dependency_sha256'].items():
            actual = path/'IncrementalDAX_moti'/file
            if file.endswith('/systems/agentenv/run.sh') and backend == 'agentenv':
                assert sha(actual) == manifest['effective_host_runner_sha256']
            else:
                assert sha(actual) == digest, f'copied dependency changed: {file}'
        bundles[backend] = dict(directory=name, manifest_sha256=sha(path/'reproduction-manifest.json'),
                                senior_commit=manifest['senior_commit'],
                                historical_raw_copied=False, credentials_copied=False,
                                external_runtime=manifest['runtime_link'])
    negative_path = ROOT/config['negative_control']
    negative = json.loads((negative_path/'result.json').read_text())
    assert negative['status'] == negative['cleanup'] == 'PASS'
    task = ROOT/config['bundles']['agentenv']/'inputs/official-task'
    assert grade(task, (negative_path/'official-tests.stdout').read_text(), baseline=True) == negative['evaluation']
    rows = []
    evidence = {}
    for item in config['runs']:
        row = audit_run(item)
        raw = ROOT/item['raw']
        own_audit = ROOT/item['audit'] if item['backend'] == 'agentenv' else raw
        assert row['final_patch_sha256'] == PATCH
        assert sha(raw/'replay/stdout/025.log') == STDOUT25
        assert (raw/'replay/original-head.txt').read_text().strip() == HEAD
        assert row['guest_memory']['sample_count'] == 0, 'periodic/boundary guest scan enabled in v1 host-only run'
        assert row['host_memory']['sample_count'] >= 2
        if item['backend'] == 'agentenv':
            cfg = json.loads((own_audit/'effective-config.json').read_text())
            assert cfg['host_monitor_light'] and cfg['sample_interval_seconds'] == 1
            assert not cfg['guest_sampling'] and cfg['damon_control_mode'] == 'damon_disabled'
            directories = list(raw.glob('frequency-*')) if item['mode'].startswith('checkpoint') else [raw/'fixed-frequency']
            assert len(directories) == (2 if item['mode'].startswith('checkpoint') else 1)
            for path in directories:
                freq = json.loads((path/'result.json').read_text())
                assert freq['status'] == freq['restoration']['status'] == 'PASS'
                assert [x['cpu'] for x in freq['binding']] == [2,4]
            for path in raw.glob('damon-*.json'):
                assert json.loads(path.read_text())['status'] == 'PASS'
            if item['mode'] == 'full':
                checked = json.loads((raw/'independent.json').read_text())
                assert set(checked) == {'svg','nested_svg','html_control'}
                assert all(x['javascript_formatted'] and x['idempotent'] for x in checked.values())
                row['final_checks']['independent_svg'] = 'PASS: offline verified runner artifact'
        else:
            cfg = json.loads((raw/'config.json').read_text())
            assert cfg['host_sample_interval_seconds'] == 1 and not cfg['diagnostic_metrics']
            assert cfg['record_phases']
        if (own_audit/'official-task-result.json').exists():
            log = (own_audit/'official-task-tests.stdout').read_text() if item['backend'] == 'agentenv' else json.loads((raw/'official-task-tests.json').read_text())['stdout']
            result = json.loads((own_audit/'official-task-result.json').read_text())
            assert grade(ROOT/config['bundles'][item['backend']]/'inputs/official-task', log) == result
        elif item['mode'] != 'full':
            raise AssertionError('official task evaluation missing from restore run')
        key = item['backend']+':'+item['mode']
        rows.append(row)
        evidence[key] = {str(p.relative_to(ROOT)):sha(p) for p in [
            raw/'replay/actions.tsv', raw/'replay/steps.tsv', raw/'replay/patch.diff',
            raw/'replay/stdout/025.log', raw/'host/memory-samples.tsv', own_audit/'result.json']}
    assert len(rows) == 4 and {x['backend'] for x in rows} == {'agentenv','trenvx'}
    return dict(status='PASS', scope='v1 single-task functional acceptance on this prepared host',
                bundles=bundles, negative_control=negative['evaluation'], runs=rows, evidence_sha256=evidence,
                monitoring_acceptance='reuse separate five-group host-only results; not re-estimated from closeout runs',
                limitations=['existing service, runtime, credentials, compiled binaries and local template are external dependencies',
                             'checkpoint functional runs add process probes or phase timestamps; lifecycle overhead NOT_VALIDATED',
                             'TrEnv-X restores file state only; arbitrary process continuity NOT_SUPPORTED',
                             'entire Prettier suite, fresh-machine deployment and multi-VM memory benefit NOT_VALIDATED'])


def figures(result, directory):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    figure, axes = plt.subplots(2,2,figsize=(12,7),constrained_layout=True)
    for axis, row in zip(axes.flat,result['runs']):
        with (Path(row['raw_dir'])/'host/memory-samples.tsv').open() as f:
            samples=list(csv.DictReader(f,delimiter='\t'))
        start=int(samples[0]['timestamp_ns'])
        times=[(int(x['timestamp_ns'])-start)/1e9 for x in samples]
        for name in ('current','file','anon'):
            axis.plot(times,[int(x['cgroup_'+name+'_bytes'])/2**20 for x in samples],label=name)
        axis.set_title(row['backend']+' '+row['mode']); axis.set_xlabel('Elapsed time (s)');axis.set_ylabel('Host cgroup (MiB)');axis.legend()
    figure.suptitle('Observed host memory; backend scopes differ; guest/PSS not measured')
    for suffix in ('png','svg'):figure.savefig(directory/('host-memory.'+suffix))
    plt.close(figure)
    figure, axes=plt.subplots(1,2,figsize=(12,5),constrained_layout=True)
    for axis,row in zip(axes,[r for r in result['runs'] if r['mode'].startswith('checkpoint')]):
        phases=row['phase_breakdown']
        keys=['create_to_first_success','controller_runner_calls','checkpoint','restore_to_first_success','parent_delete']
        assert all(phases[k]['status']=='MEASURED' for k in keys)
        axis.barh(keys,[phases[k]['duration_ns']/1e6 for k in keys])
        axis.set_title(row['backend']+' '+row['mode']);axis.set_xlabel('Controller duration (ms)')
    figure.suptitle('Separate measured phases; not summed to estimate end-to-end time')
    for suffix in ('png','svg'):figure.savefig(directory/('phase-breakdown.'+suffix))
    plt.close(figure)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--figures',action='store_true')
    args=parser.parse_args()
    result=audit(json.loads(args.config.read_text()))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    if args.figures:figures(result,args.output.parent)
    print(f'status=PASS result_dir={args.output.parent}')


if __name__=='__main__':main()
