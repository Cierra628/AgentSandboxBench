"""Opt-in one-file host PFN receipt while its referenced VMM remains alive."""
import os
from pathlib import Path
import time
import traceback

from host_file_pfn_query import file_identity,observe as observe_query,PAGE

VERSION='host-backing-single-pfn-v1'
PAGES=256
CODE_FILES=('host_backing_pfn_live.py','host_backing_pfns.py','host_file_pfn_query.py',
    'snapshot_cache_residency.py','kernel_cache_metadata.py','vmm_page_evidence.py')


def host_boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def observe_one(raw,out,recheck):
    """Fixed first lower, first <=1 MiB. Never warm, try another file or retry."""
    import json
    out=Path(out);receipt=out/'host-backing-single-pfn.json'
    if receipt.exists() or (out/'host-backing-pfn-query').exists():
        raise FileExistsError('single-file PFN evidence already exists')
    value=dict(version=VERSION,status='FAIL',query_invocations=0,process_id=os.getpid(),
        start_monotonic_ns=time.monotonic_ns(),host_boot_id=host_boot_id(),
        query_result='host-backing-pfn-query/result.json',file=None,identity=None,
        scope='first active rootfs lower, first <=256 pages; references are not page ownership')
    stage='reference_before'
    try:
        if len(raw['guests'])!=1:raise ValueError('single-file joint query requires exactly one private guest')
        value['bindings_before']=recheck()
        if any(vm['boot_id']!=value['host_boot_id'] for vm in value['bindings_before'].values()):
            raise ValueError('VMM host boot differs from query host')
        role=next(iter(raw['guests']));root=raw['rootfs_inventory']['guests'][role]
        lower=root['lowers'][0]
        if root['status']!='IDENTIFIED' or lower['status']!='IDENTIFIED' or lower['index']!=0:
            raise ValueError('first active lower is not identified; no fallback')
        key=['regular',lower['device'],lower['inode']]
        matches=[r for r in raw['expanded_files']['files'] if [
            'regular',r['device'],r['inode']]==key and r['status']=='OBSERVED'
            and role in r.get('users',[]) and 'active_snapshot_rootfs_lower' in r['categories']]
        if len(matches)!=1:raise ValueError('selected lower lacks a unique bound ordinary-file object')
        path=Path(lower['path']);identity=file_identity(path.stat(follow_symlinks=False))
        if any(identity[k]!=lower[k] for k in ('device','inode','size','mtime_ns')):
            raise ValueError('first lower identity changed before query')
        if identity['size']<=0:raise ValueError('first lower is empty; no fallback')
        value.update(file=str(path),identity=identity,object_key=key,lower_index=0,
            first_page=0,pages=min(PAGES,(identity['size']+PAGE-1)//PAGE),
            references=[dict(role=role,sandbox_id=raw['guests'][role])])
        stage='query';value['query_invocations']=1
        query=observe_query(path,(identity['device'],identity['inode']),0,value['pages'],out/'host-backing-pfn-query')
        value['query_status']=query['status']
        if query['status']!='OBSERVED_NONATOMIC':raise RuntimeError('single-file query '+query['status']+'; no retry')
        stage='reference_after';value['bindings_after']=recheck()
        value['identity_after']=file_identity(path.stat(follow_symlinks=False))
        if value['bindings_after']!=value['bindings_before'] or value['identity_after']!=identity or query['identity']!=identity:
            raise ValueError('file or sandbox references changed during PFN query')
        if host_boot_id()!=value['host_boot_id']:raise ValueError('host boot changed during query')
        value['status']='PASS'
    except Exception as error:
        value.update(error=repr(error),failed_stage=stage)
        with (out/'host-backing-single-pfn-failure.txt').open('x') as f:f.write(traceback.format_exc())
    value['end_monotonic_ns']=time.monotonic_ns()
    with receipt.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    return value
