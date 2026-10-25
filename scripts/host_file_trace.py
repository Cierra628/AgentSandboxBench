"""Opt-in short-task file-cache lifecycle trace, outside the measured cgroup."""
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

from host_loop_charge import Capture, layout


def kernel_device(device):
    return (os.major(device)<<20)|os.minor(device)


def program(image, volume_device, measurement_id, offsets):
    parts=['BEGIN { printf("READY\\nAGGREGATE_FORMAT 2\\n"); }']
    for operation,event,number in [('ADD','add_to',1),('DEL','delete_from',2)]:
        body=r'''
rawtracepoint:mm_filemap_EVENT_page_cache {
 $f=(uint64)arg0;
 $mapping=*(uint64 *)($f+FOLIO_MAPPING);
 $inode=*(uint64 *)($mapping+ADDRESS_SPACE_HOST);
 $sb=*(uint64 *)($inode+INODE_I_SB);
 $dev=*(uint32 *)($sb+SUPER_BLOCK_S_DEV);
 $ino=*(uint64 *)($inode+INODE_I_INO);
 $rdev=*(uint32 *)($inode+INODE_I_RDEV);
 $data=*(uint64 *)($f+FOLIO_MEMCG_DATA);$owner=0;$owned=0;
 if ($data && !($data & MEMCG_FLAGS_MASK)) {
  $css=($data & ~MEMCG_FLAGS_MASK)+MEM_CGROUP_CSS;
  $cg=*(uint64 *)($css+CGROUP_SUBSYS_STATE_CGROUP);
  $kn=*(uint64 *)($cg+CGROUP_KN);$owner=*(uint64 *)($kn+KERNFS_NODE_ID);
  unroll(6) {
   if ($css) {
    $cg=*(uint64 *)($css+CGROUP_SUBSYS_STATE_CGROUP);
    $kn=*(uint64 *)($cg+CGROUP_KN);$gid=*(uint64 *)($kn+KERNFS_NODE_ID);
    if ($gid==MEASUREMENT_ID) {$owned=1;}
    $css=*(uint64 *)($css+CGROUP_SUBSYS_STATE_PARENT);
   }
  }
 }
 $backing=$dev==IMAGE_DEVICE && $ino==IMAGE_INODE;
 // The restored device does not exist at READY. Retain block-device events
 // from every owner, then select the actual VMM device from saved metadata.
 if ($owned || $dev==VOLUME_DEVICE || $backing || $rdev!=0) {
  $index=*(uint64 *)($f+FOLIO_INDEX);$flags=*(uint64 *)($f+FOLIO_FLAGS);$order=0;
  if ($flags & (1 << PG_HEAD)) {$order=*(uint64 *)($f+FOLIO__FLAGS_1) & 255;}
  $cpu=cpu;
  @events[$cpu,EVENT_NUMBER]=count();
  $bin=nsecs/100000000;
  @bytes[$cpu,$bin,$dev,$ino,$rdev,$owner,EVENT_NUMBER]=sum(4096 << $order);
  @records[$cpu,$bin,$dev,$ino,$rdev,$owner,EVENT_NUMBER]=count();
  STACK
 }
}
'''.replace('EVENT_NUMBER',str(number)).replace('EVENT',event).replace('OP ',operation+' ')
        body=body.replace('STACK','if ($backing) {@backing_stacks[$cpu,kstack(12)]=count();}' if operation=='ADD' else '')
        parts.append(body)
    parts.append(r'''
tracepoint:memcg:memcg_flush_stats /args->id==ROOT_ID || args->id==MEASUREMENT_ID/ {
 $cpu=cpu;@events[$cpu,3]=count();
 printf("FLUSH %llu %llu %llu %lld %u %u %u\n",nsecs,tid,args->id,args->stats_updates,args->force,args->needs_flush,$cpu);
}
kprobe:css_rstat_flush {
 $cg=*(uint64 *)((uint64)arg0+CGROUP_SUBSYS_STATE_CGROUP);
 $kn=*(uint64 *)($cg+CGROUP_KN);$gid=*(uint64 *)($kn+KERNFS_NODE_ID);
 if ($gid==ROOT_ID || $gid==MEASUREMENT_ID) {@flushing[tid]=$gid;}
}
kretprobe:css_rstat_flush /@flushing[tid]/ {
 $cpu=cpu;@events[$cpu,4]=count();
 printf("FLUSH_DONE %llu %llu %llu %u\n",nsecs,tid,@flushing[tid],$cpu);delete(@flushing[tid]);
}
interval:s:180 {exit();}
END {print(@events);print(@bytes);print(@records);print(@backing_stacks);clear(@events);clear(@bytes);clear(@records);clear(@backing_stacks);clear(@flushing);printf("DONE\n");}
''')
    text='\n'.join(parts)
    values={**{k.upper():v for k,v in offsets.items()},'IMAGE_INODE':image.st_ino,
            'IMAGE_DEVICE':kernel_device(image.st_dev),'VOLUME_DEVICE':kernel_device(volume_device),
            'MEASUREMENT_ID':measurement_id,'ROOT_ID':Path('/sys/fs/cgroup').stat().st_ino}
    for key in sorted(values,key=len,reverse=True):text=text.replace(key,str(values[key]))
    return text


class FileCapture(Capture):
    lifetime_seconds=210

    def __init__(self,out,image,mount,group):
        self.out=Path(out);self.out.mkdir(exist_ok=False)
        self.unit='asbfiletrace'+self.out.parent.name.lower().replace('-','')
        self.slice=self.unit+'.slice';self.path=self.out/'charge-events.log'
        self.process=self.stdout=self.stderr=None
        self.groups={str(p):p.stat().st_ino for p in [Path('/sys/fs/cgroup'),group]}
        self.targets=dict(image=dict(path=str(image),device=kernel_device(image.stat().st_dev),inode=image.stat().st_ino),
                          volume_device=kernel_device(mount.stat().st_dev),measurement_id=group.stat().st_ino,
                          block_device_scope='ALL_BLOCK_DEVICES',aggregation_format=2,
                          initial_block_devices=[kernel_device(os.makedev(*map(int,p.name.split(':'))))
                                                 for p in Path('/sys/dev/block').iterdir()],
                          initial_inventory_ns=time.monotonic_ns())
        (self.out/'targets.json').write_text(json.dumps(self.targets,indent=2)+'\n')
        offsets=layout()
        (self.out/'charge.bt').write_text(program(image.stat(),mount.stat().st_dev,group.stat().st_ino,offsets))
        (self.out/'executed-host-file-trace.py').write_text(Path(__file__).read_text())

    def validate(self,text,error):
        self.rows,counts=validate_aggregates(text,error)
        return self.rows,counts

    def stop(self,matched=None):
        result=super().stop()
        result['scope']='Per-CPU in-kernel 100 ms bins for measurement owners, private-volume files, explicit backing image and all block-device cache owners. Validate each CPU before summing. Select target devices by actual VMM rdev, never add unrelated devices. Owner 0 is unknown, not root. Initial pages before READY remain unknown. Tracer outside measurement tree.'
        result['folio_event_count']=sum(result['event_counts'].get(k,0) for k in (1,2))
        result['aggregation_format']=2
        result['aggregate_cpu_entries']=result.pop('events')
        result['aggregate_file_bins']=len({tuple(r[k] for k in ('bin','device','inode','rdev','owner','op')) for r in self.rows})
        result['coverage_status']='NOT_CHECKED'
        if matched is not None:
            result['device_coverage']=matched_device_coverage(self.rows,json.loads(Path(matched).read_text()),self.targets,self.ready_ns)
            result['coverage_status']='MATCHES_QUERY_BOUNDS' if all(r['status']=='MATCHES_QUERY_BOUNDS' for r in result['device_coverage']) else 'INCOMPLETE'
        (self.out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        return result


def events(path):
    rows=[]
    for line in Path(path).read_text().splitlines():
        p=line.split(maxsplit=12)
        if p and p[0] in ('ADD','DEL'):
            assert len(p)==13,p
            row=dict(zip(('time','tid','device','inode','rdev','index','folio','order','owner','flags','actor_group'),map(int,p[1:12])))
            assert 0<=row['order']<=15 and row['owner']>0 and row['flags']==0,row
            row.update(op=p[0],comm=p[12],bytes=(1<<row['order'])*4096)
            rows.append(row)
    return sorted(rows,key=lambda r:r['time'])


def summarize(rows,start,end):
    counts={}
    for row in rows:
        if not start<=row['time']<=end:continue
        key=(row['device'],row['inode'],row['rdev'],row['owner'],row['comm'])
        item=counts.setdefault(key,dict(device=key[0],inode=key[1],rdev=key[2],owner=key[3],actor=key[4],add_bytes=0,del_bytes=0,events=0))
        item['add_bytes' if row['op']=='ADD' else 'del_bytes']+=row['bytes'];item['events']+=1
    for item in counts.values():item['net_bytes']=item['add_bytes']-item['del_bytes']
    return sorted(counts.values(),key=lambda r:abs(r['net_bytes']),reverse=True)


def validate_aggregates(text,error):
    """Read old maps, or validate each CPU before summing new event totals."""
    assert 'READY\n' in text and 'DONE\n' in text
    assert not re.search(r'lost\s+[1-9]|ERROR',text+error,re.I),error
    formats=re.findall(r'^AGGREGATE_FORMAT (\d+)$',text,re.M)
    assert formats in ([],['2']),formats
    per_cpu=bool(formats)
    def mapping(name):
        pairs=re.findall(r'@'+name+r'\[([^\]]+)\]:\s*(\d+)',text)
        result={tuple(map(int,key.split(','))):int(value) for key,value in pairs}
        assert len(result)==len(pairs),'duplicate '+name+' keys'
        return result
    capacity,records=mapping('bytes'),mapping('records')
    assert capacity.keys()==records.keys(),'byte/count map coverage differs'
    totals=mapping('events')
    assert all(len(k)==(2 if per_cpu else 1) and k[-1] in (1,2,3,4) and (not per_cpu or k[0]>=0) for k in totals)
    counts=Counter();cpu_counts=Counter();rows=[]
    for key,size in capacity.items():
        assert len(key)==(7 if per_cpu else 6),key
        bin_number,device,inode,rdev,owner,op=key[1:] if per_cpu else key
        assert owner>=0 and op in (1,2) and size%4096==0
        assert records[key]>0 and size>=records[key]*4096
        counts[op]+=records[key]
        row=dict(bin=bin_number,device=device,inode=inode,rdev=rdev,owner=owner,
                 op='ADD' if op==1 else 'DEL',bytes=size,events=records[key])
        if per_cpu:
            assert key[0]>=0,key
            row['cpu']=key[0];cpu_counts[key[0],op]+=records[key]
        rows.append(row)
    for line in text.splitlines():
        op=3 if line.startswith('FLUSH ') else 4 if line.startswith('FLUSH_DONE ') else None
        if op is not None:
            counts[op]+=1
            if per_cpu:
                fields=line.split();assert len(fields)==(8 if op==3 else 5),line
                number=int(fields[-1]);assert number>=0
                cpu_counts[number,op]+=1
    if per_cpu:assert dict(cpu_counts)==totals,(dict(cpu_counts),totals)
    else:assert dict(counts)=={k[0]:v for k,v in totals.items()},(dict(counts),totals)
    return sorted(rows,key=lambda r:(r['bin'],r['device'],r['inode'])),counts


def device_coverage(rows,sample,*,initial_bytes=None):
    """Compare endpoint mincore with ADD-DEL; bound bins touching the query.

    initial_bytes must come from an independent baseline (zero for a device
    absent before READY and created afterward), never from the first ADD.
    Matching bounds is a quantity check, not guest page-type attribution.
    """
    target=kernel_device(sample['rdev'])
    selected=[r for r in rows if r['rdev']==target]
    start,end=sample['start_monotonic_ns'],sample['end_monotonic_ns']
    assert start<=end and sample['resident_bytes']>=0
    bin_ns=100000000
    before=[r for r in selected if (r['bin']+1)*bin_ns<=start]
    boundary=[r for r in selected if r['bin']*bin_ns<=end and (r['bin']+1)*bin_ns>start]
    net=sum(r['bytes']*(1 if r['op']=='ADD' else -1) for r in before)
    lower=net-sum(r['bytes'] for r in boundary if r['op']=='DEL')
    upper=net+sum(r['bytes'] for r in boundary if r['op']=='ADD')
    unknown=[r for r in before+boundary if r['owner']==0]
    identities={(r['device'],r['inode']) for r in selected}
    result=dict(rdev=target,resident_bytes=sample['resident_bytes'],initial_bytes=initial_bytes,
        query_start_ns=start,query_end_ns=end,event_delta_lower_bytes=lower,event_delta_upper_bytes=upper,
        unknown_owner_add_bytes=sum(r['bytes'] for r in unknown if r['op']=='ADD'),
        unknown_owner_del_bytes=sum(r['bytes'] for r in unknown if r['op']=='DEL'),
        ownership_status='HAS_UNKNOWN_EVENTS' if unknown else 'RECORDED_OWNERS_KNOWN',
        meaning='Event deltas exclude initial cache. Boundary bins are bounds, not interpolated events; unknown-owner bytes are event totals, not current residency.')
    if not sample['stable']:result['status']='QUERY_UNSTABLE'
    elif not selected:result['status']='MISSING_TARGET_EVENTS'
    elif len(identities)!=1:result['status']='IDENTITY_AMBIGUOUS'
    elif initial_bytes is None:result['status']='INITIAL_STATE_UNKNOWN'
    else:
        lower,upper=initial_bytes+lower,initial_bytes+upper
        result.update(query_lower_bytes=lower,query_upper_bytes=upper,
            unexplained_resident_bytes=max(0,sample['resident_bytes']-upper),
            event_excess_bytes=max(0,lower-sample['resident_bytes']))
        matches=lower<=sample['resident_bytes']<=upper
        result['status']='COVERAGE_GAP' if not matches else 'OWNER_UNKNOWN' if unknown else 'MATCHES_QUERY_BOUNDS'
    return result


def matched_device_coverage(rows,matched,targets,ready_ns):
    """Bind saved VMM/device identities and establish the initial-zero condition."""
    assert matched['status']=='PASS' and matched['groups']
    checks=[]
    for phase in matched['groups']:
        devices=phase['residency']['backends']
        assert len(devices)==phase['sandboxes']
        assert len({(r['device'],r['inode'],r['rdev']) for r in devices})==1
        samples=[r for r in phase['residency']['files'] if r['kind']=='memory-device']
        assert phase['residency']['status']=='OBSERVED' and len(samples)==1
        sample=samples[0]
        assert all(sample[k]==devices[0][k] for k in ('device','inode','rdev'))
        # A missing old-version inventory is unknown, not an empty inventory.
        new_device=(targets.get('block_device_scope')=='ALL_BLOCK_DEVICES'
            and 'initial_block_devices' in targets
            and kernel_device(sample['rdev']) not in targets['initial_block_devices']
            and targets['initial_inventory_ns']<=ready_ns<min(g['restore_begin_ns'] for g in phase['guests']))
        check=device_coverage(rows,sample,initial_bytes=0 if new_device else None)
        check.update(sandboxes=phase['sandboxes'],initial_basis='ABSENT_BEFORE_READY_RESTORED_AFTER_READY' if new_device else 'UNKNOWN')
        checks.append(check)
    return checks
