"""Offline verification of schema-2 calibration timing and output evidence."""
import csv
import hashlib
import json
from pathlib import Path
import sys


def audit(out):
    result=json.loads((out/'result.json').read_text())
    assert result['status']=='PASS' and result['timing_schema']==2
    assert (out/'cleanup-status.txt').read_text().strip()=='PASS'
    count=0
    for phase in result['phases']:
        raw=Path(phase['raw_dir'])
        assert raw.resolve().is_relative_to(out.resolve())
        with (raw/'calls.tsv').open() as f: rows=list(csv.DictReader(f,delimiter='\t'))
        assert len(rows)==result['phase_iterations']
        assert [int(r['index']) for r in rows]==list(range(1,len(rows)+1))
        for row in rows:
            duration,invocation,archive=(int(row[k]) for k in ('duration_ns','invocation_ns','archive_ns'))
            assert invocation>0 and archive>=0 and duration==invocation+archive
            for kind in ('stdout','stderr'):
                digest=hashlib.sha256((raw/kind/f'{int(row["index"]):03}.log').read_bytes()).hexdigest()
                assert digest==row[kind+'_sha256']
                if kind=='stdout': assert digest==result['expected_stdout_024_sha256']
        for field,column in (('call_duration_sum_ns','duration_ns'),('invocation_sum_ns','invocation_ns'),('archive_sum_ns','archive_ns')):
            assert phase[field]==sum(int(r[column]) for r in rows)
        assert phase['tool_window_ns']>=phase['call_duration_sum_ns']
        count+=len(rows)
    return dict(status='PASS',calls=count,phases=len(result['phases']),
        scope='timing additivity, output hashes, phase sums and recorded cleanup; not overhead acceptance')


if __name__=='__main__':
    out=Path(sys.argv[1]).resolve()
    result=audit(out)
    (out/'timing-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
