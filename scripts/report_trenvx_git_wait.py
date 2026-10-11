"""Offline recount of native Git timers and observed waits; excludes failed identity inference."""
import argparse
import collections
import csv
from decimal import Decimal
import json
from pathlib import Path

from audit_exp1_metrics import audit_run


def summarize(raw):
    result=json.loads((raw/'result.json').read_text())
    assert result['status']=='PASS' and all(v=='PASS' for v in result['checks'].values())
    checked=audit_run(dict(backend='trenvx',mode='full',raw=str(raw)))
    assert checked['final_checks']['status']=='PASS' and checked['cleanup']['status']=='PASS'
    official=json.loads((raw/'official-task-result.json').read_text())
    assert official['status']=='PASS' and official['parser_results']['num_tests_passed']==4
    commands={};waits={}
    for prefix,tags in (('baseline',('add','write_tree','reset','rev_parse','done')),
                        ('final',('add','diff','reset','status','done'))):
        table=[s.split('\t') for s in (raw/f'replay/{prefix}-commands.tsv').read_text().splitlines()]
        assert [r[0] for r in table]==list(tags)
        times=[int(Decimal(r[1])*10**9) for r in table]
        assert all(a<b for a,b in zip(times,times[1:]))
        commands[prefix]={}
        for tag,start,end in zip(tags,times,times[1:]):
            base=raw/f'replay/{prefix}-{tag}-time.tsv'
            values=[float(v) for v in base.read_text().split()]
            assert len(values)==3 and all(v>=0 for v in values) and values[0]*1e9<=end-start+2_000_000
            events=[json.loads(s) for s in Path(str(base)+'.events.jsonl').read_text().splitlines()]
            exits=[r for r in events if r['event']=='exit'];assert len(exits)==1 and exits[0]['code']==0
            commands[prefix][tag]=dict(real_seconds=values[0],user_seconds=values[1],system_seconds=values[2],
                boundary_ns=end-start,native_exit_seconds=exits[0]['t_abs'])
            if tag=='reset':
                rows=[json.loads(s) for s in Path(str(base)+'.process.jsonl').read_text().splitlines()]
                assert rows[-1]['status']=='STOPPED'
                states=collections.Counter();poll=0
                for row in rows[:-1]:
                    for process in row['observations']:
                        if process['comm']!='git':continue
                        for thread in process['threads']:
                            states[str(thread['wchan'])]+=1
                            fields=thread['syscall'].split() if isinstance(thread['syscall'],str) else []
                            if len(fields)>=4 and fields[0]=='7' and int(fields[3],0)==5000:poll+=1
                assert poll>0
                writes=[e for e in events if e['event']=='region_leave' and e.get('label')=='do_write_index']
                assert len(writes)==1
                waits[prefix]=dict(sample_count=len(rows)-1,git_thread_states=dict(states),poll_5000ms_samples=poll,
                    index_write_region_seconds=writes[0]['t_rel'],descriptor_target='NOT_MEASURED',
                    probe_scan_total_ns=sum(r['scan_ns'] for r in rows[:-1]))
    return dict(status='PASS',run_id=raw.name,third_acceptance='PARTIAL',commands=commands,waits=waits,
        timing_ns=checked['timing_ns'],official_task_assertions=4,
        conclusion='Both resets spend about ten seconds in poll; DNS identity hypothesis remains unverified.',
        scope='One diagnostic replay; native events and process reads are intrusive; not monitoring overhead acceptance.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=summarize(args.raw.resolve(strict=True))
    with args.output.open('x') as stream:json.dump(report,stream,indent=2);stream.write('\n')
    print(f'status=PASS result={args.output}')
