"""Usage: python3 summarize-three.py .artifacts/variance-three-XXXXXXXX"""
import csv
import json
import re
import sys
from pathlib import Path
from statistics import median

here=Path(__file__).resolve().parent
logs=Path(sys.argv[1])
space=(logs/'space.txt').read_text()
read=lambda p:json.loads(p.read_bytes())
branches=[]; batches=[]
for i in range(1,4):
    launch=Path(re.search(r'exit=0 result_dir=(.*)',(logs/f'{i}.log').read_text())[1])
    storage=Path(re.search(r'status=PASS result_dir=(.*?/storage-isolated-service/\S+)',(launch/'launch.log').read_text())[1])
    series=read(storage/'guest-cache/result.json')
    receipt=read(storage/'result.json'); data=read(storage/'guest-cache/try-1/result.json')
    a,=series['attempts']
    assert a['file_exists']==0 and a['cleanup']=='PASS' and a['exit']==0
    assert series['status']==data['status']==receipt['status']=='PASS' and data['valid']==7
    assert receipt['cleanup']['successful_fixture_removed'] and receipt['cleanup']['mount_and_loop_removed']
    assert series['snapshot']==data['snapshot'] and not (storage/'volume.ext4').exists()
    source=series['save_point'];rows=[]
    for role,v in sorted(data['rows'].items()):
        q=data['query_rows'][role]
        assert v['status']=='OBSERVED' and v['restore_end']<v['content_verified']<=v['ready']<=v['first_end']<=v['pause_end']<=q['start']
        before,after=v['pre_verify'],v['post_verify']
        assert before['guest_end_ns']<=after['guest_start_ns']<=after['guest_end_ns']<=v['first_publication']['guest_start_ns']
        rec=dict(run=i,role=role,snapshot=series['snapshot'],sandbox_id=q['sid'])
        for k,p in [('source',source),('restored',before),('verified',after)]:
            rec.update({k+'_'+key:p[key] for key in ['records','generation','shmem_pages','stale_records','guest_start_ns','guest_end_ns']})
        rec.update(to_restore=before['records']-source['records'],during_verify=after['records']-before['records'],
                   post_to_export_ms=(v['first_publication']['guest_start_ns']-after['guest_end_ns'])/1e6,
                   to_export=q['guest_before']-after['records'],first_export=q['guest_before'],confirmed=q['pages'])
        assert (q['pages']<30000)==(role in a['below_30000'])
        rows.append(rec)
    branches.extend(rows)
    rec=dict(run=i,storage=storage.name,source_id=series['source'],snapshot=series['snapshot'],
             getty=series['pre_save_serial']['getty_slice'],snapd=series['pre_save_serial']['snap_daemon'],
             space_before=int(re.search(rf'^{i} before\s+(\d+)',space,re.M)[1]),space_after=int(re.search(rf'^{i} after\s+(\d+)',space,re.M)[1]),
             source=source['records'],union=data['four_unique_pages'],provider=data['provider_unique_pages'],total_ms=(data['merge_end']-data['go'])/1e6)
    for k in ['restored_records','verified_records','to_restore','during_verify','to_export','first_export','confirmed']:
        vs=[r[k] for r in rows]; rec.update({k+'_min':min(vs),k+'_median':median(vs),k+'_max':max(vs)})
    batches.append(rec)

assert len({r['source_id'] for r in batches})==len({r['snapshot'] for r in batches})==3 and len({r['sandbox_id'] for r in branches})==21
for name,rs in [('three-branches',branches),('three-runs',batches)]:
    with (here/(name+'.csv')).open('w') as f:
        writer=csv.DictWriter(f,rs[0],lineterminator='\n');writer.writeheader();writer.writerows(rs)
for r in batches: print(r)
for k in ['source','restored_records_median','verified_records_median','first_export_median','confirmed_median','union']:
    vs=[r[k] for r in batches]; print(k,vs,max(vs)-min(vs),round((max(vs)-min(vs))/median(vs)*100,3))
