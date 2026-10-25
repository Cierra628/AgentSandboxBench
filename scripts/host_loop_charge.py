"""One-image metadata tracing: actual folio memcg, lifecycle, and rstat completion."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
from collections import Counter


def layout():
    types=json.loads(subprocess.check_output(['bpftool','-j','btf','dump','file',
        '/sys/kernel/btf/vmlinux','format','raw'],timeout=25))['types']
    by_id={row['id']:row for row in types}
    def struct(name):return next(t for t in types if t.get('kind')=='STRUCT' and t.get('name')==name)
    def member(t,name):
        for m in t.get('members',[]):
            if m['name']==name:return m['bits_offset']//8
            if m['name']=='(anon)':
                child=by_id[m['type_id']]
                if child.get('kind') in ('STRUCT','UNION'):
                    value=member(child,name)
                    if value is not None:return m['bits_offset']//8+value
        return None
    fields={'folio':['mapping','index','memcg_data','flags','_flags_1'],
            'address_space':['host'],'inode':['i_ino','i_sb','i_rdev'],'super_block':['s_dev'],
            'mem_cgroup':['css'],'cgroup_subsys_state':['cgroup','parent'],'cgroup':['kn'],'kernfs_node':['id']}
    result={s+'_'+f:member(struct(s),f) for s,names in fields.items() for f in names}
    assert all(v is not None for v in result.values())
    flags=next(t for t in types if t.get('kind')=='ENUM' and t.get('name')=='pageflags')
    result['pg_head']=next(v['val'] for v in flags['values'] if v['name']=='PG_head')
    assert os.uname().release.startswith('6.19.9') # memcg flag mask below follows this host source.
    result['memcg_flags_mask']=7
    return result


def program(info,groups,offsets):
    dev=(os.major(info.st_dev)<<20)|os.minor(info.st_dev)
    predicate=' || '.join('args->id == '+str(g) for g in groups)
    cg_predicate=' || '.join('$gid == '+str(g) for g in groups)
    parts=['BEGIN { printf("READY\\n"); }']
    for operation,number,event in [('ADD',1,'add'),('DEL',2,'delete')]:
        text=r'''
rawtracepoint:mm_filemap_EVENT_to_page_cache {
 $f=(uint64)arg0;
 $mapping=*(uint64 *)($f+FOLIO_MAPPING);
 $inode=*(uint64 *)($mapping+ADDRESS_SPACE_HOST);
 if (*(uint64 *)($inode+INODE_I_INO)==TARGET_INODE) {
  $sb=*(uint64 *)($inode+INODE_I_SB);
  if (*(uint32 *)($sb+SUPER_BLOCK_S_DEV)==TARGET_DEVICE) {
   $index=*(uint64 *)($f+FOLIO_INDEX);
   $flags=*(uint64 *)($f+FOLIO_FLAGS);
   $order=0;
   if ($flags & (1 << PG_HEAD)) { $order=*(uint64 *)($f+FOLIO__FLAGS_1) & 255; }
   $data=*(uint64 *)($f+FOLIO_MEMCG_DATA); $owner=0;
   if ($data && !($data & MEMCG_FLAGS_MASK)) {
    $cg=*(uint64 *)(($data & ~MEMCG_FLAGS_MASK)+MEM_CGROUP_CSS+CGROUP_SUBSYS_STATE_CGROUP);
    $kn=*(uint64 *)($cg+CGROUP_KN); $owner=*(uint64 *)($kn+KERNFS_NODE_ID);
   }
   @events[EVENT_NUMBER]=count();
   printf("OP %llu %llu %llu %llu %llu %llu %llu %llu %s\n",
          nsecs,tid,$index,$f,$order,$owner,$data & MEMCG_FLAGS_MASK,cgroup,comm);
  }
 }
}
'''.replace('mm_filemap_EVENT_to_page_cache','mm_filemap_'+event+('_to_page_cache' if event=='add' else '_from_page_cache'))
        text=text.replace('EVENT_NUMBER',str(number)).replace('OP ',operation+' ')
        parts.append(text)
    parts.append(r'''
tracepoint:memcg:memcg_flush_stats /FLUSH_PREDICATE/ {
 @events[3]=count();
 printf("FLUSH %llu %llu %llu %lld %u %u\n",nsecs,tid,args->id,args->stats_updates,args->force,args->needs_flush);
}
kprobe:css_rstat_flush {
 $cg=*(uint64 *)((uint64)arg0+CGROUP_SUBSYS_STATE_CGROUP);
 $kn=*(uint64 *)($cg+CGROUP_KN); $gid=*(uint64 *)($kn+KERNFS_NODE_ID);
 if (CG_PREDICATE) { @flushing[tid]=$gid; }
}
kretprobe:css_rstat_flush /@flushing[tid]/ {
 @events[4]=count();
 printf("FLUSH_DONE %llu %llu %llu\n",nsecs,tid,@flushing[tid]);delete(@flushing[tid]);
}
interval:s:45 { exit(); }
END { print(@events);clear(@events);clear(@flushing);printf("DONE\n"); }
'''.replace('FLUSH_PREDICATE',predicate).replace('CG_PREDICATE',cg_predicate))
    text='\n'.join(parts)
    values={**{k.upper():v for k,v in offsets.items()},'TARGET_INODE':info.st_ino,'TARGET_DEVICE':dev}
    for key in sorted(values,key=len,reverse=True):text=text.replace(key,str(values[key]))
    return text


class Capture:
    lifetime_seconds=55
    def __init__(self,out,image,inner,outer,leaf,offsets):
        self.out=out;self.unit=outer.name.removesuffix('.slice')+'charge'
        self.slice=self.unit+'.slice';self.path=out/'charge-events.log'
        self.groups={str(p):p.stat().st_ino for p in (Path('/sys/fs/cgroup'),outer,inner,leaf)}
        self.process=None;self.stdout=None;self.stderr=None
        (out/'charge.bt').write_text(program(image.stat(),list(self.groups.values()),offsets))
        (out/'charge-groups.json').write_text(json.dumps(self.groups,indent=2)+'\n')

    def start(self):
        self.stdout=self.path.open('x');self.stderr=(self.out/'charge.stderr').open('x')
        argv=['systemd-run','--quiet','--wait','--pipe','--collect','--unit='+self.unit,'--slice='+self.slice,
              '--property=MemoryAccounting=yes','--property=RuntimeMaxSec='+str(self.lifetime_seconds),'--property=KillSignal=SIGINT',
              'bpftrace','-q','-B','line',str(self.out/'charge.bt')]
        self.process=subprocess.Popen(argv,stdout=self.stdout,stderr=self.stderr,
            env=dict(os.environ,BPFTRACE_PERF_RB_PAGES='64'))
        deadline=time.monotonic()+25
        while 'READY\n' not in self.path.read_text():
            if self.process.poll() is not None:raise RuntimeError('charge tracer failed before READY')
            if time.monotonic()>deadline:raise TimeoutError('charge tracer READY timeout')
            time.sleep(.05)
        # Prove the tracer is outside the measurement tree from birth.
        pid=int(subprocess.check_output(['systemctl','show',self.unit+'.service','--property=MainPID','--value'],text=True,timeout=10))
        membership=(Path('/proc')/str(pid)/'cgroup').read_text()
        assert '/'+self.slice+'/' in membership
        self.ready_ns=time.monotonic_ns()

    def validate(self,text,error):
        return validate_events(text,error)

    def stop(self):
        try:
            if self.process is not None and self.process.poll() is None:
                subprocess.run(['systemctl','kill','--signal=SIGINT',self.unit+'.service'],check=True,timeout=10)
                try:self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    subprocess.run(['systemctl','stop',self.unit+'.service'],check=True,timeout=15)
                    self.process.wait(timeout=5)
            if self.process is not None:assert self.process.returncode==0,'charge tracer exit failure'
        finally:
            subprocess.run(['systemctl','stop',self.unit+'.service'],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
            subprocess.run(['systemctl','stop',self.slice],check=True,timeout=20)
            if self.stdout:self.stdout.close()
            if self.stderr:self.stderr.close()
        rows,counts=self.validate(self.path.read_text(),(self.out/'charge.stderr').read_text())
        return dict(status='PASS',ready_ns=self.ready_ns,events=len(rows),event_counts=dict(counts),
                    tracer_slice=self.slice,scope='Only target image lifecycle; flush attempts/completions for root and owned groups; tracer outside measurement group')


def validate_events(text,error):
    assert 'READY\n' in text and 'DONE\n' in text
    assert not re.search(r'lost\s+[1-9]|ERROR',text+error,re.I),error
    kinds={'ADD':1,'DEL':2,'FLUSH':3,'FLUSH_DONE':4}
    rows=[line.split() for line in text.splitlines() if line.split() and line.split()[0] in kinds]
    totals={int(k):int(v) for k,v in re.findall(r'@events\[(\d+)\]:\s*(\d+)',text)}
    counts=Counter(kinds[row[0]] for row in rows)
    assert dict(counts)==totals,(dict(counts),totals)
    return rows,counts


def analyze(out,snapshots,groups):
    events=[]
    for line in (out/'charge-events.log').read_text().splitlines():
        p=line.split()
        if p and p[0] in ('ADD','DEL'):
            events.append(dict(op=p[0],time=int(p[1]),tid=int(p[2]),index=int(p[3]),folio=int(p[4]),
                order=int(p[5]),owner=int(p[6]),flags=int(p[7]),actor_group=int(p[8]),comm=p[9]))
    events.sort(key=lambda e:e['time']);table={};cursor=0;result={}
    for name,point in snapshots.items():
        while cursor<len(events) and events[cursor]['time']<point['start_monotonic_ns']:
            e=events[cursor];cursor+=1
            assert 0<=e['order']<=15
            for i in range(e['index'],e['index']+(1<<e['order'])):
                if e['op']=='ADD':
                    assert i not in table and not e['flags'] and e['owner']>0,e
                    table[i]=e
                else:
                    assert i in table and table[i]['folio']==e['folio'],e
                    del table[i]
        changed=any(point['start_monotonic_ns']<=e['time']<=point['end_monotonic_ns'] for e in events)
        if not point['files']:continue
        indices=point['files'][0]['resident_indices']
        assert len(indices)*4096==point['resident_bytes']
        if changed:
            result[name]=dict(status='CHANGED_DURING_QUERY');continue
        assert all(i in table for i in indices),'resident page missing lifecycle identity'
        owners=Counter(table[i]['owner'] for i in indices)
        actors=Counter(table[i]['comm'] for i in indices)
        result[name]=dict(status='STABLE',resident_pages=len(indices),owner_pages=dict(owners),actor_pages=dict(actors),
            measurement_owned_pages=sum(n for owner,n in owners.items() if owner in groups),
            outside_measurement_pages=sum(n for owner,n in owners.items() if owner not in groups))
    return result
