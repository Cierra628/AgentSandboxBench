"""Pinned SWE-PolyBench inputs and task-specific Jest grading, outside replay."""
import argparse
import ast
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import sys
import urllib.request

DATASET_REVISION = 'd56445f9940eae4e9d2974ec66820c2f1d7754e6'
HARNESS_REVISION = '9c836c5d7f3cb991934132b77d29e6941d912a07'
INSTANCE = 'prettier__prettier-14400'
HEAD = '6905b39ec7bc6ed38732e26ceced5b57677e2600'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prepare(destination, *, cached_csv=None, proxy=None, instance=INSTANCE, head=HEAD, cached_parser_inputs=None):
    destination.mkdir(parents=True, exist_ok=False)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(
        {'http': proxy, 'https': proxy}) if proxy else urllib.request.ProxyHandler())
    dataset_url = f'https://huggingface.co/datasets/AmazonScience/SWE-PolyBench/resolve/{DATASET_REVISION}/test.csv'
    data = cached_csv.read_bytes() if cached_csv else opener.open(dataset_url, timeout=45).read()
    csv.field_size_limit(32*1024*1024)
    rows = [r for r in csv.DictReader(io.StringIO(data.decode())) if r['instance_id'] == instance]
    assert len(rows) == 1 and rows[0]['base_commit'] == head
    task = rows[0]
    (destination/'task.json').write_text(json.dumps(task, indent=2)+'\n')
    (destination/'test.patch').write_text(task['test_patch'])
    # Use the upstream parser verbatim; do not substitute a local regex grader.
    package = destination/'harness/poly_bench_evaluation/parsers'
    package.mkdir(parents=True)
    for path in (package.parent/'__init__.py', package/'__init__.py'):
        path.write_text('')
    sources = {}
    cached_manifest = None
    if cached_parser_inputs:
        load(cached_parser_inputs)
        cached_manifest = json.loads((cached_parser_inputs/'manifest.json').read_text())
    for name in ('javascript_parsers.py', 'parser_results.py', 'parser_protocol.py'):
        url = f'https://raw.githubusercontent.com/amazon-science/SWE-PolyBench/{HARNESS_REVISION}/src/poly_bench_evaluation/parsers/{name}'
        if cached_manifest:
            assert cached_manifest['parser_sources'][name]['url'] == url
            content = (cached_parser_inputs/'harness/poly_bench_evaluation/parsers'/name).read_bytes()
        else:
            content = opener.open(url, timeout=30).read()
        (package/name).write_bytes(content)
        sources[name] = dict(url=url, sha256=sha(content))
    manifest = dict(instance_id=instance, base_commit=head, dataset_revision=DATASET_REVISION,
                    dataset_url=dataset_url, dataset_sha256=sha(data), harness_revision=HARNESS_REVISION,
                    parser_sources=sources, test_patch_sha256=sha(task['test_patch'].encode()),
                    task_sha256=sha((destination/'task.json').read_bytes()),
                    scope='official task-specific test command and F2P/P2P; not entire Prettier suite')
    (destination/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


def load(directory):
    manifest = json.loads((directory/'manifest.json').read_text())
    assert manifest['dataset_revision'] == DATASET_REVISION and manifest['harness_revision'] == HARNESS_REVISION
    assert manifest['task_sha256'] == sha((directory/'task.json').read_bytes())
    assert manifest['test_patch_sha256'] == sha((directory/'test.patch').read_bytes())
    for name, row in manifest['parser_sources'].items():
        assert row['sha256'] == sha((directory/'harness/poly_bench_evaluation/parsers'/name).read_bytes())
    return json.loads((directory/'task.json').read_text())


def guest_command(directory, guest_patch, *, guest_output='/workspace', apply_test_patch=True):
    task = load(directory)
    head = task.get('base_commit', HEAD)
    assert re.fullmatch(r'[0-9a-f]{40}', head)
    paths = re.findall(r'^\+\+\+ b/(.+)$', task['test_patch'], re.MULTILINE)
    assert paths and all(p.startswith('tests/') and '..' not in Path(p).parts for p in paths)
    quoted = ' '.join(shlex.quote(p) for p in paths)
    assert guest_output.startswith('/') and re.fullmatch(r'/[A-Za-z0-9_./-]+', guest_output)
    assert '..' not in Path(guest_output).parts
    patch_command = (f'git apply --check {shlex.quote(guest_patch)}\ngit apply {shlex.quote(guest_patch)}'
                     if apply_test_patch else f'git apply --reverse --check {shlex.quote(guest_patch)}')
    program = f'''set -eu
cd /testbed
test "$(git rev-parse HEAD)" = {head}
{patch_command}
sha256sum {quoted} > /workspace/official-tests-before.sha256
set +e
timeout --kill-after=10s 120s bash -lc {shlex.quote(task['test_command'])} > /workspace/official-test.log 2>&1
rc=$?
set -e
sha256sum {quoted} > /workspace/official-tests-after.sha256
if ! cmp -s /workspace/official-tests-before.sha256 /workspace/official-tests-after.sha256; then rc=125; fi
printf 'ASB_OFFICIAL_EXIT=%s\\n' "$rc"
cat /workspace/official-test.log
'''
    return program.replace('/workspace/', guest_output.rstrip('/')+'/')


def grade(directory, output, *, baseline=False):
    task = load(directory)
    first, content = output.split('\n', 1)
    assert first.startswith('ASB_OFFICIAL_EXIT=')
    code = int(first.split('=', 1)[1])
    assert code not in (124, 125, 137), f'official evaluation timed out or changed fixed test files: {code}'
    sys.path.insert(0, str(directory/'harness'))
    from poly_bench_evaluation.parsers.javascript_parsers import JavascriptJestPR
    parsed = JavascriptJestPR(content).parse()
    match = re.search(r'{\s*"numFailedTestSuites"', content)
    assert match, 'official Jest summary missing'
    jest = json.JSONDecoder().raw_decode(content[match.start():])[0]
    assert jest['wasInterrupted'] is False and jest['numPendingTests'] == 0
    if 'numTodoTests' in jest:
        assert jest['numTodoTests'] == 0
    else:
        # Older Jest omits this field. Account for every assertion instead of
        # assuming that a missing counter proves no tests were skipped.
        assertions = [a for suite in jest['testResults'] for a in suite['assertionResults']]
        assert len(assertions) == jest['numTotalTests']
        assert all(a['status'] in ('passed', 'failed') for a in assertions)
    assert jest['numTotalTests'] > 0 and jest['numTotalTests'] == len(parsed['passed_tests']) + len(parsed['failed_tests'])
    assert all(jest['snapshot'][key] == 0 for key in ('added', 'updated', 'filesRemoved'))
    f2p, p2p = (ast.literal_eval(task[key]) for key in ('F2P', 'P2P'))
    assert f2p and set(parsed['passed_tests']).isdisjoint(parsed['failed_tests'])
    required = set(p2p) if baseline else set(f2p + p2p)
    assert required <= set(parsed['passed_tests']), f'missing required passing tests: {required-set(parsed["passed_tests"])}'
    if baseline:
        assert code != 0 and set(f2p) <= set(parsed['failed_tests']), 'unfixed negative control did not fail expected tests'
    else:
        assert code == 0 and not parsed['failed_tests'], 'official command did not fully pass'
        assert jest['numFailedTestSuites'] == jest['numFailedTests'] == 0 and jest['snapshot']['failure'] is False
    return dict(status='PASS', phase='unfixed_negative_control' if baseline else 'replayed_patch',
                exit_code=code, parser_results=parsed, F2P=f2p, P2P=p2p,
                P2P_scope='empty in official dataset' if not p2p else 'all required passed',
                manifest=json.loads((directory/'manifest.json').read_text()),
                scope='official task-specific evaluation; entire Prettier suite NOT_RUN')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('directory', type=Path)
    p.add_argument('--cached-csv', type=Path); p.add_argument('--proxy')
    p.add_argument('--instance', choices=(INSTANCE,'prettier__prettier-6604'), default=INSTANCE)
    p.add_argument('--cached-parser-inputs',type=Path,help='Reuse verified parser sources from existing pinned task inputs')
    p = sub.add_parser('grade'); p.add_argument('directory', type=Path)
    p.add_argument('log', type=Path); p.add_argument('result', type=Path)
    p.add_argument('--baseline', action='store_true')
    args = parser.parse_args()
    if args.command == 'prepare':
        head = HEAD if args.instance == INSTANCE else 'affa24ce764bd14ac087abeef0603012f5c635d6'
        prepare(args.directory, cached_csv=args.cached_csv, proxy=args.proxy, instance=args.instance, head=head, cached_parser_inputs=args.cached_parser_inputs)
    else:
        result = grade(args.directory, args.log.read_text(), baseline=args.baseline)
        args.result.write_text(json.dumps(result, indent=2)+'\n')
    print('status=PASS')


if __name__ == '__main__':
    main()
