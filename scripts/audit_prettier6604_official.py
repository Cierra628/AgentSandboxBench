"""Audit saved official test output without running tools or changing raw data."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from exp1_official_task import load, grade


def inspect(task_dir, content):
    task=load(task_dir)
    assert task['instance_id']=='prettier__prettier-6604'
    assert content.startswith('ASB_OFFICIAL_EXIT=')
    code=int(content.split('\n',1)[0].split('=',1)[1])
    sys.path.insert(0,str(task_dir/'harness'))
    from poly_bench_evaluation.parsers.javascript_parsers import JavascriptJestPR
    parsed=JavascriptJestPR(content.split('\n',1)[1]).parse()
    match=re.search(r'{\s*"numFailedTestSuites"',content);assert match
    summary=json.JSONDecoder().raw_decode(content[match.start():])[0]
    try:
        result=grade(task_dir,content)
    except AssertionError as error:
        result=dict(status='FAIL',reason=str(error),exit_code=code,parser_results=parsed,
                    F2P=task['F2P'],P2P=task['P2P'])
    result.update(summary={k:v for k,v in summary.items() if k!='testResults'})
    return result


def signature(result):
    j=result['summary']
    return dict(status=result['status'],exit_code=result['exit_code'],
                passed=sorted(result['parser_results']['passed_tests']),
                failed=sorted(result['parser_results']['failed_tests']),
                summary={k:j[k] for k in ('numTotalTests','numFailedTests','numFailedTestSuites','numPendingTests','wasInterrupted')},
                snapshot={k:j['snapshot'][k] for k in ('added','updated','filesRemoved','failure','unmatched','matched')})


def reference(path):
    result=json.loads(path.read_text());source=Path(result['source'])
    assert hashlib.sha256(source.read_bytes()).hexdigest()==result['source_sha256']
    assert result['status']=='FAIL' and result['exit_code']==1
    assert result['parser_results']['num_tests_failed']==2 and result['parser_results']['num_tests_passed']==0
    return result


def audit(task_dir, log, output):
    assert not output.resolve().is_relative_to(log.parent.resolve()), 'output must be outside raw'
    result=inspect(task_dir,log.read_text())
    result.update(
                  source=str(log),source_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),
                  scope='offline strict official test audit; no task rerun')
    with output.open('x') as stream:stream.write(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task_dir',type=Path);parser.add_argument('log',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();result=audit(args.task_dir,args.log,args.output)
    print('status='+result['status']+' result='+str(args.output))
    raise SystemExit(0 if result['status']=='PASS' else 1)
