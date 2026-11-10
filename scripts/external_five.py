"""Strict fixed-input adapter for the five collaborator tasks; no model calls."""
import csv
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

REVISION = 'cbe767b2aa23ceb59875b1cb0a7a6141c749312a'
COUNTS = {'hf-model-inference': 11, 'mozilla__pdf.js-20957': 5,
          'apache__logging-log4j2': 10, 'count-dataset-tokens': 16, 'compile-compcert': 318}
COMPCERT_IMAGE = 'docker.io/alexgshaw/compile-compcert@sha256:f60236a4fe55bbb3c573210fe0fcd91551f4ee97c417453cd4985be074b35411'
BARRIERS = {88: 86, 107: 102, 134: 132, 176: 174, 245: 241, 260: 258, 276: 273, 278: 276, 304: 299}
CWD = {'mozilla__pdf.js-20957': '/testbed', 'apache__logging-log4j2':
       '/data/project/apache__logging-log4j2@204d8251295dfa816b9b28d8d9cfaebab500f415'}
RELAY_PROBES = {
    'hf-model-inference': ('https://huggingface.co/api/models/distilbert-base-uncased-finetuned-sst-2-english/revision/714eb0fa89d2f80546fd',),
    'count-dataset-tokens': ('https://huggingface.co/api/datasets/ryanmarten/OpenThoughts-1k-sample', 'https://pypi.org/simple/datasets/'),
    'mozilla__pdf.js-20957': ('https://registry.npmjs.org/puppeteer', 'https://storage.googleapis.com/chrome-for-testing-public/146.0.7680.76/linux64/chrome-linux64.zip'),
    'compile-compcert': ('https://github.com/ocaml/dune/releases/download/2.8.5/dune-2.8.5.tbz', 'https://compcert.org/'),
    'apache__logging-log4j2': ('https://repo.maven.apache.org/maven2/',),
}


def load(cfg, upstream):
    upstream = Path(upstream).resolve()
    git = ['git', '-c', 'safe.directory='+str(upstream), '-C', str(upstream)]
    task = cfg['task_id']
    if cfg.get('version') != 1 or cfg.get('measurement_profile') != 'single-vmm-endpoint':
        raise ValueError('explicit five-task measured config required')
    if 'hf_http_download' in cfg and (task != 'hf-model-inference' or cfg['hf_http_download'] is not True):
        raise ValueError('hf_http_download is only an explicit opt-in for HF model inference')
    if cfg['revision'] != REVISION or cfg['actions'] != COUNTS[task] or cfg['memory_mib'] != (32768 if task == 'compile-compcert' else 8192):
        raise ValueError('fixed task revision, count or resource budget differs')
    if subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip() != REVISION:
        raise ValueError('upstream HEAD differs')
    primary = json.loads((upstream/'provenance/agentenv-runs.json').read_text())['primary']
    selected = next(r for r in primary if r['task_id'] == task)
    expected = f"results/agentenv/{task}/runs/{selected['run_id']}"
    if cfg['run'] != expected:
        raise ValueError('primary run differs')
    directory = (upstream/expected).resolve()
    if not directory.is_relative_to(upstream):
        raise ValueError('run escapes upstream')
    for name in ('run.json', 'replay-inputs.json', 'steps.csv', 'validation.json', 'oci-origin.json'):
        data = (directory/name).read_bytes()
        pinned = subprocess.check_output([*git, 'show', REVISION+':'+expected+'/'+name])
        if data != pinned or hashlib.sha256(data).hexdigest() != cfg['input_sha256'][name]:
            raise ValueError('fixed input changed: '+name)
    helpers=['clock_ns.c']+(['file_reader.c'] if task=='compile-compcert' else [])
    for name in helpers:
        relative='experiments/container-screening/harness/'+name
        data=(upstream/relative).read_bytes()
        pinned=subprocess.check_output([*git,'show',REVISION+':'+relative])
        if data!=pinned or hashlib.sha256(data).hexdigest()!=cfg['helper_sha256'][name]:
            raise ValueError('fixed helper changed: '+name)
    original = json.loads((directory/'run.json').read_text())
    if original['source_image'] != cfg['image'] or original['memory_mib'] != cfg['memory_mib'] or original['cpus'] != 2:
        raise ValueError('original image/resources differ')
    origin=json.loads((directory/'oci-origin.json').read_text())
    if cfg['image_id'] != origin['verification'].get('source_image_id',origin['source_podman_image_id']):
        raise ValueError('primary OCI source identity differs')
    replay = json.loads((directory/'replay-inputs.json').read_text())
    with (directory/'steps.csv').open() as stream:
        reference = list(csv.DictReader(stream))
    if len(reference) != COUNTS[task] or len(replay['steps']) != len(reference):
        raise ValueError('incomplete trace')
    for i, (step, row) in enumerate(zip(replay['steps'], reference), 1):
        script = replay['files'][f'{i:03}.sh']
        if step['step'] != i or int(row['step']) != i or row['timed_out'] != 'False' or step['tool'] != row['tool']:
            raise ValueError('step sequence differs')
        if hashlib.sha256(script.encode()).hexdigest() != step['script_sha256']:
            raise ValueError('script hash differs')
        for name in re.findall(r'/probe/input/([\w.-]+)', script):
            if name not in replay['files']:
                raise ValueError('missing auxiliary input: '+name)
    for name in replay['files']:
        if Path(name).name != name:
            raise ValueError('unsafe input filename')
    replay['origin'] = origin
    if 'log4j_rebuilt_origin' in cfg:
        replay['origin'] = rebuilt_log4j_origin(cfg, origin)
    replay['validation'] = json.loads((directory/'validation.json').read_text())
    if task == 'compile-compcert':
        barriers = json.loads((upstream/'experiments/agentenv-memory/async-completion-barriers.json').read_text())['wait_before_step']
        if barriers != {str(k): [v] for k, v in BARRIERS.items()}:
            raise ValueError('CompCert completion barriers differ')
    return cfg, replay, reference


def rebuilt_log4j_origin(cfg, primary):
    """Explicit documented Log4j rebuild; primary identity stays separately recorded."""
    if cfg['task_id'] != 'apache__logging-log4j2':
        raise ValueError('rebuilt origin is limited to fixed Log4j input')
    spec = cfg['log4j_rebuilt_origin']
    data = Path(spec['path']).read_bytes()
    if hashlib.sha256(data).hexdigest() != spec['sha256']:
        raise ValueError('rebuilt origin receipt changed')
    rebuilt = json.loads(data)
    fixed = primary['rebuilt_local_image']
    actual = rebuilt['rebuilt_local_image']
    for key in ('source_commit', 'containerfile_sha256', 'base_image'):
        if actual[key] != fixed[key]:
            raise ValueError('fixed Log4j rebuild input differs: '+key)
    if rebuilt['task_id'] != cfg['task_id'] or rebuilt['status'] != 'VERIFIED':
        raise ValueError('Log4j rebuild receipt incomplete')
    before, after = primary['verification'], rebuilt['verification']
    if (after['layers'] != before['layers'] or
            after['rootfs_diff_ids'][:-1] != before['rootfs_diff_ids'][:-1] or
            not after['runtime_config_fields_match']):
        raise ValueError('Log4j base filesystem or runtime attestation differs')
    return dict(rebuilt, primary_verification=before,
                rebuild_scope='Documented fixed base/Containerfile/source rebuild; new OCI digest, not byte-identical to primary')


def write_inputs(directory, replay):
    for name, content in replay['files'].items():
        (Path(directory)/name).write_text(content)
    (Path(directory)/'original-validation.sh').write_text(replay['validation']['command'])


def runner(task):
    cwd = shlex.quote(CWD.get(task, '/app'))
    lines = ['#!/bin/bash', 'set +e', 'umask 077', 'mkdir -p /probe/work /probe/input/logs',
             'if test -f /probe/input/guest-env.sh; then source /probe/input/guest-env.sh || exit 94; fi',
             'cd '+cwd+' || exit 93',
             "printf 'step\\tstart_monotonic_ns\\tend_monotonic_ns\\tduration_ns\\texit_code\\n' > /probe/input/tools.tsv"]
    for i in range(1, COUNTS[task]+1):
        if task == 'compile-compcert' and i in BARRIERS:
            bg = BARRIERS[i]
            lines += ['ASB_WAIT_START=$(/probe/input/clock-ns)',
                      f'while ! test -f /probe/work/async-{bg}.done; do',
                      '  test $(($(/probe/input/clock-ns)-ASB_WAIT_START)) -lt 14400000000000 || exit 95',
                      '  sleep .2', 'done',
                      f"printf '%s\\t%s\\t%s\\t%s\\t%s\\n' {bg} {i} \"$ASB_WAIT_START\" \"$(/probe/input/clock-ns)\" \"$(cat /probe/work/async-{bg}.done)\" >> /probe/input/async-waits.tsv"]
        execute = f'source /probe/input/{i:03}.sh' if task == 'apache__logging-log4j2' else f'(cd {cwd} && bash /probe/input/{i:03}.sh)'
        lines += ['ASB_BEGIN=$(/probe/input/clock-ns) || exit 94',
                  execute+f' > /probe/input/logs/{i:03}.stdout 2> /probe/input/logs/{i:03}.stderr',
                  'ASB_CODE=$?', 'ASB_END=$(/probe/input/clock-ns) || exit 94',
                  f"printf '%s\\t%s\\t%s\\t%s\\t%s\\n' {i} \"$ASB_BEGIN\" \"$ASB_END\" \"$((ASB_END-ASB_BEGIN))\" \"$ASB_CODE\" >> /probe/input/tools.tsv"]
    lines += ['cp -a /probe/work /probe/input/background-evidence || exit 96']
    return '\n'.join(lines)+'\n'


def validate_outputs(task, downloaded):
    downloaded = Path(downloaded)
    def output(step):
        return ''.join((downloaded/'logs'/f'{step:03}.{suffix}').read_text(errors='replace') for suffix in ('stdout', 'stderr'))
    if task == 'mozilla__pdf.js-20957':
        for step in (3, 5):
            if not re.search(r'1,?127 specs, 0 failures, 56 pending', output(step)):
                raise ValueError('PDF.js CLI test evidence missing at step '+str(step))
        return dict(cli_runs=2, specs_each=1127, failures_each=0, pending_each=56)
    if task == 'apache__logging-log4j2':
        if 'BUILD SUCCESS' not in output(10):
            raise ValueError('Maven BUILD SUCCESS missing')
        return dict(compilation='PASS', tests='SKIPPED_BY_ORIGINAL_COMMAND', checker='REPORTED_SEPARATELY')
    if task == 'count-dataset-tokens':
        if not re.search(r'(?m)^79586\s*$', output(16)):
            raise ValueError('original answer missing')
        if not re.search(r'science rows:\s*26\b',output(15)):
            raise ValueError('original 26-row count evidence missing')
        return dict(answer=79586, scope='Original 26-row input only; not a full large dataset')
    if task == 'compile-compcert':
        with (downloaded/'async-waits.tsv').open() as stream:
            waits=list(csv.reader(stream,delimiter='\t'))
        if [(int(r[1]),int(r[0])) for r in waits] != list(BARRIERS.items()) or any(int(r[3])<int(r[2]) for r in waits):
            raise ValueError('CompCert completion waits incomplete')
        return dict(background_waits=waits,compiler_smoke='ORIGINAL_CHECKER')
    return dict(sentiment_positive_negative='ORIGINAL_CHECKER',backend='CPU')


def runtime_window(task):
    from datetime import datetime, timezone, timedelta
    from external_workload import budgets
    if task not in COUNTS:raise ValueError('five-task window only')
    start=datetime.now(timezone.utc);duration=budgets(task)['native']-300
    return dict(status='STANDING_USER_AUTHORIZATION',start_utc=start.isoformat(),
                end_utc=(start+timedelta(seconds=duration)).isoformat(),duration_seconds=duration,
                scope='Fixed single-task runtime budget, no renewal or exclusive shared-disk claim')


def runtime_image(cfg, origin):
    # Request the exact platform manifest recorded by primary, including HF's
    # platform selection beneath its original multi-platform index.
    repo=cfg['image'].split('@')[0]
    if ':' in repo.rsplit('/',1)[-1]:repo=repo.rsplit(':',1)[0]
    return repo+'@'+origin['verification']['manifest_digest']
