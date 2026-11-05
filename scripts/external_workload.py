"""Pinned Django/C4/Cython replay; no model, freezing or guest file traversal."""
import csv
import hashlib
import json
import re
from pathlib import Path

C4='reshard-c4-data'
C4_WRITES={4,6,7,8,9,10,17,19,23}
CYTHON='build-cython-ext'
CYTHON_READS={3,4,6,7,8,9,12,13,14,15,23,29,30,39,40,42,51,56,66,71}
CYTHON_WRITES={31,53}
CYTHON_VALIDATION='''cd /tmp && python3 - <<'PYCHECK'
import subprocess
print("source_repo_commit:", subprocess.check_output(["git","-C","/app/pyknotid","rev-parse","HEAD"],text=True).strip())
from pyknotid import cinvariants
from pyknotid.spacecurves import chelpers,ccomplexity
assert all(str(x.__file__).endswith('.so') for x in [cinvariants,chelpers,ccomplexity])
print('compiled extensions import successfully')
PYCHECK'''


def budgets(task_id):
    if task_id==CYTHON:return dict(replay=900,controller=1800,native=2100,workflow=2400)
    return dict(replay=240,controller=720,native=1050,workflow=1200)


def load(config,upstream):
    cfg=json.loads(Path(config).read_text());upstream=Path(upstream).resolve()
    count={'django__django-16819':28,C4:36,CYTHON:83}.get(cfg['task_id'])
    if not (count and cfg['version']==1 and cfg['memory_mib']==8192 and cfg['actions']==count):
        raise ValueError('only pinned Django 28-call, C4 36-call and Cython 83-call tasks are supported')
    run=(upstream/cfg['run']).resolve()
    if not run.is_relative_to(upstream):raise ValueError('published run outside repository')
    replay=json.loads((run/'replay-inputs.json').read_text())
    steps=replay['steps']
    with (run/'steps.csv').open() as stream:reference=list(csv.DictReader(stream))
    if len(steps)!=cfg['actions'] or len(reference)!=len(steps):raise ValueError('incomplete published trace')
    for i,(step,expected) in enumerate(zip(steps,reference),1):
        tool='write' if cfg['task_id']==C4 and i in C4_WRITES else 'bash'
        if cfg['task_id']==CYTHON:tool='read' if i in CYTHON_READS else 'write' if i in CYTHON_WRITES else 'bash'
        assert step['step']==i and int(expected['step'])==i and step['tool']==tool
        assert expected['timed_out']=='False'
        text=replay['files'][f'{i:03}.sh']
        # Published command metadata and normalized executable scripts differ by a newline.
        assert hashlib.sha256(text.encode()).hexdigest()==step['script_sha256'],'published script integrity mismatch'
        if tool in ('read','write'):
            name=f'{i:03}-read.py' if tool=='read' else f'{i:03}.payload'
            if name not in replay['files'] or '/probe/input/'+name not in text:
                raise ValueError('missing tool input or original reference: '+name)
    return cfg,replay,reference


def write_inputs(directory,replay,task_id):
    for step in replay['steps']:
        names=[f"{step['step']:03}.sh"]
        if task_id in (C4,CYTHON) and step['tool']=='write':names.append(f"{step['step']:03}.payload")
        if task_id==CYTHON and step['tool']=='read':names.append(f"{step['step']:03}-read.py")
        for name in names:(Path(directory)/name).write_text(replay['files'][name])


def validate_c4_outputs(logs):
    for step,markers in {33:['CONSTRAINT VIOLATIONS: NONE','FINAL VERDICT: BYTE-EXACT ROUNDTRIP ON c4_sample'],
                         34:['9898 files, structure + content']}.items():
        content=(Path(logs)/f'{step:03}.stdout').read_text()
        for marker in markers:
            if marker not in content:raise ValueError('C4 output evidence missing at step '+str(step)+': '+marker)


def validate_cython_outputs(logs):
    logs=Path(logs);text=(logs/'082.stdout').read_text()
    source='=== tests from /app/pyknotid (source-tree context) ==='
    installed='=== tests from /tmp (site-packages context) ==='
    if source not in text or installed not in text:raise ValueError('Cython test contexts missing')
    first,second=text.split(source,1)[1].split(installed,1)
    for section in (first,second):
        if re.findall(r'\b(\d+) passed\b',section)!=['18'] or re.search(r'\b[1-9]\d* (?:failed|errors?)\b|Traceback \(most recent call last\)|(?m:^ERROR\b|^FAILED\b)',section):
            raise ValueError('Cython needs 18 passed in each original test context, without errors')
    if 'SNIPPET OK - alexander_polynomial(-1) =' not in (logs/'076.stdout').read_text():raise ValueError('Cython NumPy snippet evidence missing')
    if not re.search(r'(?m)^final check: numpy 2\.3\.0\s*$',(logs/'083.stdout').read_text()):raise ValueError('Cython final NumPy version differs')


def validate_timings(rows):
    previous=0
    for index,row in enumerate(rows,1):
        start=int(row['start_monotonic_ns']);end=int(row['end_monotonic_ns'])
        if int(row['step'])!=index or not (0<start<=end and start>=previous) or int(row['duration_ns'])!=end-start:
            raise ValueError('invalid tool timing at step '+str(index))
        previous=end


def guest_runner(count,*,task_id='django__django-16819'):
    setup='cd /testbed || exit 93\nexport GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/testbed'
    root='/workspace/asb-external-tools'
    execute='source "$ASB_EXTERNAL_ROOT/$ASB_EXTERNAL_FILE.sh"'
    if task_id in (C4,CYTHON):
        setup='cd /app || exit 93\numask 077'
        root='/probe/input'
        execute='(cd /app && /bin/bash "$ASB_EXTERNAL_ROOT/$ASB_EXTERNAL_FILE.sh")'
        if task_id==CYTHON:setup+='\nsource /probe/input/guest-env.sh || exit 94'
    return '''#!/bin/bash
set +e
'''+setup+'''
ASB_EXTERNAL_ROOT='''+root+'''
mkdir -p "$ASB_EXTERNAL_ROOT/logs"
printf 'step\tstart_monotonic_ns\tend_monotonic_ns\tduration_ns\texit_code\n' > "$ASB_EXTERNAL_ROOT/tools.tsv"
for ASB_EXTERNAL_I in $(seq 1 '''+str(count)+'''); do
  printf -v ASB_EXTERNAL_FILE '%03d' "$ASB_EXTERNAL_I"
  ASB_EXTERNAL_BEGIN=$("$ASB_EXTERNAL_ROOT/clock-ns") || exit 94
  '''+execute+''' > "$ASB_EXTERNAL_ROOT/logs/$ASB_EXTERNAL_FILE.stdout" 2> "$ASB_EXTERNAL_ROOT/logs/$ASB_EXTERNAL_FILE.stderr"
  ASB_EXTERNAL_CODE=$?
  ASB_EXTERNAL_END=$("$ASB_EXTERNAL_ROOT/clock-ns") || exit 94
  printf '%s\t%s\t%s\t%s\t%s\n' "$ASB_EXTERNAL_I" "$ASB_EXTERNAL_BEGIN" "$ASB_EXTERNAL_END" "$((ASB_EXTERNAL_END-ASB_EXTERNAL_BEGIN))" "$ASB_EXTERNAL_CODE" >> "$ASB_EXTERNAL_ROOT/tools.tsv"
done
'''
