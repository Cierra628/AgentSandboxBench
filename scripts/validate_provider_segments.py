"""Check the offline segment model against saved one-read transitions only."""
import argparse
import json
from pathlib import Path

from provider_segment_model import SegmentModel,UNIT,layout,stable_sources


def sources(table):
    snapshot=table.snapshot()
    return stable_sources(snapshot,snapshot,snapshot,snapshot)


def ranges(offsets):
    result=[]
    for index,offset in enumerate(offsets):
        if result and ((offset is None and result[-1]['source_offset'] is None)
                or offset is not None and result[-1]['source_offset'] is not None
                and offset==result[-1]['source_offset']+(index*UNIT-result[-1]['start'])):
            result[-1]['end']=(index+1)*UNIT
        else:result.append(dict(start=index*UNIT,end=(index+1)*UNIT,source_offset=offset))
    return result


def validate(source,output):
    source=source.resolve();output=output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    identity=json.loads((source/'provider/identity.json').read_text())
    slots={s['name']:s for s in identity['slots']}
    assert all(s['negotiated_flags']&4 and s['content_validity']=='before_overwrite' for s in slots.values())
    observations=[]
    for saved in map(json.loads,(source/'samples.jsonl').read_text().splitlines()):
        point=saved['point'];raw=json.loads((source/f'pair-{point:04}-provider.json').read_text())
        for phase in ('first','last'):
            rows={r['name']:r for r in raw[phase]['rows']};assert rows.keys()==slots.keys()
            observations.append((dict(point=point,phase=phase),rows))
    checked=[];tails=[];multiple=0
    for (start,left),(end,right) in zip(observations,observations[1:]):
        for name in sorted(slots,key=lambda n:slots[n]['slot']):
            a=left[name]['before'];b=right[name]['before']
            assert a==left[name]['after'] and b==right[name]['after']
            assert a[1]%2==b[1]%2==0 and a[6]==b[6]==3 and a[2:4]==b[2:4]
            dr=b[7]-a[7];dg=b[1]-a[1]
            assert dr>=0 and dg==4*dr
            if dr>1:multiple+=1
            if dr!=1:continue
            table=SegmentModel(a[3]);table.begin_read(a[4],a[5]);table.complete_read(a[5])
            previous=sources(table)
            table.begin_read(b[4],b[5])
            assert sources(table)==[None]*(b[5]//UNIT)+previous[b[5]//UNIT:]
            table.complete_read(b[5]);actual=sources(table)
            expected=[b[4]+i*UNIT if i*UNIT<b[5] else
                a[4]+i*UNIT if i*UNIT<a[5] else None for i in range(a[3]//UNIT)]
            assert actual==expected
            item=dict(start=start,end=end,slot=slots[name]['slot'],queue=slots[name]['queue'])
            checked.append(dict(item,expected_ranges=ranges(expected)))
            if b[5]<a[5]:tails.append(dict(item,preserved_slot_range=[b[5],a[5]],
                source_image_range=[a[4]+b[5],a[4]+a[5]]))
    example=SegmentModel(256*1024)
    events=[dict(offset=o,length=n) for o,n in ((0,128*1024),(256*1024,64*1024),(512*1024,12*1024))]
    for event in events:
        example.begin_read(event['offset'],event['length']);example.complete_read(event['length'])
    summary=dict(status='PASS',scope='Offline update rules, not a runtime or physical-residency measurement.',
        saved_point_count=len(observations)//2,slot_count=len(slots),
        saved_single_read_transitions_checked=len(checked),saved_tail_proofs_checked=len(tails),
        multiple_read_transitions_not_reconstructed=multiple,
        metadata_layout=layout(256*1024,16),
        synthetic_example=dict(events=events,final_ranges=ranges(sources(example))),
        tail_proofs=tails,original_physical_union='UNCHANGED')
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (output/'saved-transition-checks.json').write_text(json.dumps(checked,indent=2)+'\n')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=validate(args.source,args.output)
    print(f"status=PASS saved_single_reads={result['saved_single_read_transitions_checked']} tail_proofs={result['saved_tail_proofs_checked']} result_dir={args.output}")
