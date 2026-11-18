"""Prove adjacent saved buffer-prefix continuity; never reads live data or payloads."""
import argparse
from collections import Counter
import json
from pathlib import Path

MAGIC=0x31544f4c53425341


def stable_read(frame):
    row=frame['before']
    return (row if row==frame['after'] and len(row)==8 and row[0]==MAGIC
        and row[1]%2==0 and row[6]==3 and 0<row[5]<=row[3] else None)


def compare(left,right):
    """Requires one unchanged slot owner, GET_DATA, prefix-only fills, no u64 wrap.

    Each successful read costs four generation increments; every other mutation
    costs additional increments. Two stable complete-read endpoints cannot hide
    an in-flight fill. Range endpoints below are byte offsets, end exclusive.
    """
    a,b=stable_read(left),stable_read(right)
    if a is None or b is None:return dict(kind='unstable_or_invalid')
    if a[2:4]!=b[2:4]:return dict(kind='allocation_changed')
    generation,reads=b[1]-a[1],b[7]-a[7]
    result=dict(generation_delta=generation,read_delta=reads)
    if generation<0 or reads<0:return dict(result,kind='counter_backwards')
    if generation==0 and reads==0:
        if a!=b:return dict(result,kind='inconsistent_record')
        return dict(result,kind='unchanged',preserved_slot_range=[0,a[5]],
            source_image_range=[a[4],a[4]+a[5]])
    if generation!=4*reads or reads==0:return dict(result,kind='other_mutations')
    if reads>1:return dict(result,kind='multiple_reads')
    result.update(kind='one_read',previous_length=a[5],next_length=b[5])
    if b[5]<a[5]:
        result.update(preserved_slot_range=[b[5],a[5]],
            source_image_range=[a[4]+b[5],a[4]+a[5]],tail_bytes=a[5]-b[5])
    return result


def report(source,output):
    source=source.resolve();output=output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    identity=json.loads((source/'provider/identity.json').read_text())
    slots={s['name']:s for s in identity['slots']}
    assert all(s['negotiated_flags']&4 and s['content_validity']=='before_overwrite' for s in slots.values())
    observations=[]
    for saved in map(json.loads,(source/'samples.jsonl').read_text().splitlines()):
        point=saved['point'];raw=json.loads((source/f'pair-{point:04}-provider.json').read_text())
        for phase in ('first','last'):
            rows={r['name']:r for r in raw[phase]['rows']}
            assert rows.keys()==slots.keys()
            observations.append((dict(point=point,phase=phase),rows))
    transitions=[];counts={'within_point':Counter(),'between_points':Counter()}
    for (start,left),(end,right) in zip(observations,observations[1:]):
        scope='within_point' if start['point']==end['point'] else 'between_points'
        for name in sorted(slots,key=lambda n:slots[n]['slot']):
            result=compare(left[name],right[name]);counts[scope][result['kind']]+=1
            transitions.append(dict(start=start,end=end,queue=slots[name]['queue'],
                slot=slots[name]['slot'],scope=scope,**result))
    tails=[t for t in transitions if 'tail_bytes' in t]
    summary=dict(status='PASS',point_count=len(observations)//2,slot_count=len(slots),
        observation_pairs_per_slot=len(observations),transition_count=len(transitions),
        counts={k:dict(v) for k,v in counts.items()},tail_proof_count=len(tails),
        tail_byte_min=min((t['tail_bytes'] for t in tails),default=0),
        tail_byte_max=max((t['tail_bytes'] for t in tails),default=0),tail_proofs=tails,
        scope='Adjacent endpoint content continuity, not physical residency or current guest cache attribution.',
        assumptions=['Same saved process, device, queue, slot and allocation lifetime; GET_DATA negotiated.',
            'Tested single-writer protocol and prefix-only payload writes; counters do not wrap.',
            'Only directly adjacent observations are used; no propagation of older segment histories.'],
        original_physical_union='UNCHANGED')
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (output/'transitions.json').write_text(json.dumps(transitions,indent=2)+'\n')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=report(args.source,args.output)
    print(f"status=PASS transitions={result['transition_count']} tail_proofs={result['tail_proof_count']} result_dir={args.output}")
