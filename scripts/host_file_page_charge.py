"""Explicit ordinary-file PFN + memcg observation; no live calls on import.

Original PFN v1/default entry points are unchanged. Normal folio references and
before/after checks do not provide an atomic binding; unknowns remain separate.
"""
from collections import Counter
import json
from pathlib import Path
import time

import numpy as np
from host_file_pfn_query import HostFilePfnQuery, probe_program, decode_rows, FOLIO, MASK, PAGE
from host_existing_cache_owner import layout
from snapshot_cache_residency import BatchEntries

VERSION = 'host-file-pfn-charge-v1'
COLUMNS = ('file_page','mincore_bit','return_found','encoded_return','charge_state',
           'raw_cgroup_id','online_ancestor_id','raw_css_flags','read_start_ns','read_end_ns')
STATES = {0:'NOT_APPLICABLE',1:'OBSERVED_NONATOMIC',2:'NO_EXPLICIT_CHARGE_OBSERVED',
          3:'UNSUPPORTED_MEMCG_FLAGS',4:'IDENTITY_OR_STATE_CHANGED',5:'ANCESTOR_UNRESOLVED'}


def charge_program(pid, offsets):
    source = probe_program(pid)
    source = source.replace('@pending[tid]=arg1+(uint64)1;',
                            '@pending[tid]=arg1+(uint64)1; @mapping[tid]=(uint64)$m;')
    source = source.replace('delete(@pending[tid]);', 'delete(@pending[tid]); delete(@mapping[tid]);')
    source = source.replace('clear(@pending);', 'clear(@pending); clear(@mapping);')
    anchor = ' @entries[$i-$c.4]=(@active[tid],$encoded);'
    assert source.count(anchor) == 1
    body = r'''
 $begin=nsecs; $state=(uint64)0; $raw=(uint64)0; $online=(uint64)0; $rawflags=(uint64)0;
 if (($encoded & 0xff80000000000000)==((uint64)1<<63)) {
  $state=4; $order=(uint64)0; $flags=*(uint64*)($v+FOLIO_FLAGS);
  if ($flags & ((uint64)1<<PG_HEAD)) { $order=*(uint64*)($v+FOLIO__FLAGS_1) & 255; }
  $index=*(uint64*)($v+FOLIO_INDEX); $mapping=*(uint64*)($v+FOLIO_MAPPING);
  $data=*(uint64*)($v+FOLIO_MEMCG_DATA); $css=(uint64)0; $rawcss=(uint64)0; $depth=(uint64)0;
  if ($mapping==@mapping[tid] && $index<=$i && $order<=18 && $i-$index<((uint64)1<<$order)) {
   if (!$data) { $state=2; }
   else if ($data & (uint64)7) { $state=3; }
   else {
    $state=5; $css=$data+MEM_CGROUP_CSS; $rawcss=$css;
    $cg=*(uint64*)($css+CGROUP_SUBSYS_STATE_CGROUP);
    $kn=*(uint64*)($cg+CGROUP_KN); $raw=*(uint64*)($kn+KERNFS_NODE_ID);
    $rawflags=(uint64)*(uint32*)($css+CGROUP_SUBSYS_STATE_FLAGS);
    while ($css && $depth<64 && !$online) {
     if (*(uint32*)($css+CGROUP_SUBSYS_STATE_FLAGS) & 2) {
      $acg=*(uint64*)($css+CGROUP_SUBSYS_STATE_CGROUP);
      $akn=*(uint64*)($acg+CGROUP_KN); $online=*(uint64*)($akn+KERNFS_NODE_ID);
     } else { $css=*(uint64*)($css+CGROUP_SUBSYS_STATE_PARENT); }
     $depth=$depth+1;
    }
    if ($raw && $online) {
     $cg2=*(uint64*)($rawcss+CGROUP_SUBSYS_STATE_CGROUP);
     $kn2=*(uint64*)($cg2+CGROUP_KN);
     if ($raw==*(uint64*)($kn2+KERNFS_NODE_ID) &&
         $rawflags==(uint64)*(uint32*)($rawcss+CGROUP_SUBSYS_STATE_FLAGS) &&
         (*(uint32*)($css+CGROUP_SUBSYS_STATE_FLAGS) & 2) &&
         $online==*(uint64*)(*(uint64*)(*(uint64*)($css+CGROUP_SUBSYS_STATE_CGROUP)+CGROUP_KN)+KERNFS_NODE_ID)) {
      $state=1;
     } else { $state=4; }
    }
   }
   if ($data!=*(uint64*)($v+FOLIO_MEMCG_DATA) || $mapping!=*(uint64*)($v+FOLIO_MAPPING) ||
       $index!=*(uint64*)($v+FOLIO_INDEX) || $flags!=*(uint64*)($v+FOLIO_FLAGS) ||
       ($order && $order!=(*(uint64*)($v+FOLIO__FLAGS_1) & 255))) { $state=4; }
  }
 }
 @entries[$i-$c.4]=(@active[tid],$encoded,$state,$raw,$online,$rawflags,$begin,nsecs);
'''
    for name in sorted(offsets, key=len, reverse=True):
        body = body.replace(name, str(offsets[name]))
    return source.replace(anchor, body)


def decode_charge_rows(rows):
    if rows.ndim != 2 or rows.shape[1] != len(COLUMNS):
        raise ValueError('invalid charge row layout')
    result = decode_rows(rows[:,:4])
    states = Counter(); owners = {}; unknown = 0
    for row in rows:
        offset,bit,found,code,state,raw,online,flags,start,end = map(int,row)
        if not found:
            if any(row[3:]): raise ValueError('missing return carries charge fields')
            states['MISSING_KERNEL_RETURN'] += 1; unknown += 1; continue
        if state not in STATES or start <= 0 or end < start:
            raise ValueError('invalid charge state/time')
        folio = code & ~MASK == FOLIO
        if not folio:
            if state or raw or online or flags: raise ValueError('non-folio carries charge')
            states['NOT_APPLICABLE'] += 1; continue
        if state == 0: raise ValueError('folio without explicit charge state')
        states[STATES[state]] += 1
        if state == 1:
            if not raw or not online or (flags & 2 and raw != online):
                raise ValueError('invalid observed owner identity')
            key = (raw,online,bool(flags & 2))
            owners.setdefault(key,set()).add(code & MASK)
        else:
            unknown += 1
    # A PFN observed with conflicting owners is not assigned to either owner.
    assignments = {}
    for key,pfns in owners.items():
        for pfn in pfns: assignments.setdefault(pfn,set()).add(key)
    conflicts = {pfn for pfn,keys in assignments.items() if len(keys)>1}
    owner_rows = [dict(raw_cgroup_id=key[0],online_ancestor_id=key[1],raw_online=key[2],
                       observed_unique_pages=len(pfns-conflicts),observed_bytes=len(pfns-conflicts)*PAGE)
                  for key,pfns in sorted(owners.items())]
    charge = dict(status='PARTIAL' if unknown or conflicts else 'OBSERVED_NONATOMIC',
                  state_counts=dict(states),unknown_page_observations=unknown,
                  conflicting_pfn_count=len(conflicts),owners=owner_rows,
                  atomic=False,exclusive_usage='NOT_MEASURED',
                  scope='Same-return charge observation per chunk; not a simultaneous total or historical backfill')
    result['charge'] = charge
    if charge['status']=='PARTIAL': result['status']='PARTIAL'
    return result


class HostFileChargeQuery(HostFilePfnQuery):
    version = VERSION
    row_bytes = len(COLUMNS)*8

    def make_program(self):
        offsets = layout()  # Explicit live attach only; BTF lookup, never on import/replay.
        (self.directory/'charge-layout.json').write_text(json.dumps(dict(offsets=offsets,columns=COLUMNS,
            version=VERSION,source_build_match=self.kernel['source_build_match']),indent=2)+'\n')
        return charge_program(self.pid,offsets)

    def make_reader(self,info):
        return BatchEntries(self.lib,self.mapfds['entries'],info,value_words=8)

    def make_rows(self,first,pages,vector,entries):
        return np.array([[first+i,int(vector[i])&1,int(i in entries),*entries.get(i,(0,)*7)]
                         for i in range(pages)],dtype='<u8')

    def summarize_rows(self,rows):
        return decode_charge_rows(rows)


class GroupIdentityHistory:
    """Preserve observed group IDs after disappearance; never carry forward counters."""
    def __init__(self,boot_id):
        self.boot_id=boot_id; self.records=[]

    def observe(self,path,role,*,now_ns=None,stat_func=None):
        path=Path(path); now=time.monotonic_ns() if now_ns is None else now_ns
        current=next((r for r in reversed(self.records) if r['path']==str(path) and r['first_absent_ns'] is None),None)
        try:
            st=(stat_func or Path.stat)(path)
        except FileNotFoundError:
            if current:current['first_absent_ns']=now
            return None
        if current and (current['inode'],current['device'])!=(st.st_ino,st.st_dev):
            current['first_absent_ns']=now; current['end_reason']='PATH_ID_REPLACED';current=None
        if not current:
            current=dict(host_boot_id=self.boot_id,path=str(path),role=role,
                         id_kind='cgroupfs_st_ino',inode=st.st_ino,device=st.st_dev,
                         first_seen_ns=now,last_seen_ns=now,first_absent_ns=None,
                         parent_path=str(path.parent),end_reason=None)
            self.records.append(current)
        current['last_seen_ns']=now
        return current

    def save(self,path):
        Path(path).write_text(json.dumps(dict(version='cgroup-identity-history-v1',records=self.records,
            scope='Observation bounds, not exact birth/death; no counters carried forward'),indent=2)+'\n')
