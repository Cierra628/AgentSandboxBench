"""Offline guest cache gauges and selected reclaim counters; no live reads."""
GAUGES=('nr_free_pages','nr_file_pages','nr_active_file','nr_inactive_file','nr_shmem','nr_dirty','nr_writeback')
COUNTERS=('pgscan_kswapd','pgscan_direct','pgscan_file','pgsteal_kswapd','pgsteal_direct','pgsteal_file','pgmajfault','pgpgin','pgpgout','workingset_refault_file','workingset_activate_file','workingset_restore_file','allocstall_dma','allocstall_dma32','allocstall_normal','allocstall_movable')
MEMINFO=('MemTotal','MemFree','MemAvailable','Buffers','Cached','AnonPages','Slab','SReclaimable','SUnreclaim','Shmem','Dirty','Writeback','SwapTotal','SwapFree')


def parse_vmstat(text):
    values={}
    for line in text.splitlines():
        key,value=line.split()
        assert key not in values
        if key=='damon_enabled':
            assert value in ('Y','N');values[key]=value
        else:
            assert value.isdecimal();values[key]=int(value)
    assert set(GAUGES+COUNTERS)<=set(values),'required vmstat field missing'
    result=dict(gauges={key:values[key] for key in GAUGES},counters={key:values[key] for key in COUNTERS})
    damon={key[6:]:value for key,value in values.items() if key.startswith('damon_')}
    if damon:
        assert set(damon)=={'enabled','bytes_reclaim_tried_regions','bytes_reclaimed_regions','nr_reclaimed_regions'}
        result['damon']=damon
    return result


def parse_meminfo(text):
    values={}
    for line in text.splitlines():
        fields=line.split();assert len(fields) in (2,3) and fields[0].endswith(':')
        key=fields[0][:-1];assert key not in values and fields[1].isdecimal()
        if key in MEMINFO:assert len(fields)==3 and fields[2]=='kB'
        values[key]=int(fields[1])
    assert set(MEMINFO)<=set(values),'required meminfo field missing'
    return {key:values[key] for key in MEMINFO}


def snapshot(directory,prefix,include_damon=False):
    result=dict(vmstat=parse_vmstat((directory/(prefix+'.vmstat')).read_text()),
                meminfo_kib=parse_meminfo((directory/(prefix+'.meminfo')).read_text()))
    if include_damon:
        values={}
        path=directory/(prefix+'.damon')
        if not path.exists():
            assert 'damon' in result['vmstat'],'required DAMON fields missing'
            result['damon']=result['vmstat'].pop('damon')
            return result
        assert 'damon' not in result['vmstat'],'duplicate DAMON formats'
        for line in path.read_text().splitlines():
            key,value=line.split('=',1);assert key not in values;values[key]=value
        assert set(values)=={'enabled','bytes_reclaim_tried_regions','bytes_reclaimed_regions','nr_reclaimed_regions'}
        assert values['enabled'] in ('Y','N')
        assert all(values[key].isdecimal() for key in values if key!='enabled')
        result['damon']={key:int(value) if key!='enabled' else value for key,value in values.items()}
    return result


def delta(before,after):
    assert before['meminfo_kib']['MemTotal']==after['meminfo_kib']['MemTotal']
    b=before['vmstat'];a=after['vmstat']
    assert all(a['counters'][key]>=b['counters'][key] for key in COUNTERS),'counter decreased'
    return dict(counter_deltas={key:a['counters'][key]-b['counters'][key] for key in COUNTERS},
                gauge_deltas_pages={key:a['gauges'][key]-b['gauges'][key] for key in GAUGES},
                meminfo_deltas_kib={key:after['meminfo_kib'][key]-before['meminfo_kib'][key] for key in MEMINFO})


def collect(directory,include_start=False,include_damon=False):
    before=snapshot(directory,'git-025-before',include_damon);after=snapshot(directory,'git-025-after',include_damon)
    result=dict(before=before,after=after,during_command=delta(before,after),
                scope='guest-wide endpoints; vmstat gauges in pages and meminfo in KiB; no exact file attribution',
                limits='non-atomic snapshots and delayed per-CPU accounting; counters are not durations; zero reclaim does not exclude manual cache invalidation or an evicted page inherited from the template')
    if include_start:
        result['start']=snapshot(directory,'cache-start',include_damon)
        result['before_command']=delta(result['start'],before)
    if include_damon:
        def difference(b,a):
            assert a['enabled']==b['enabled']
            assert all(a[key]>=b[key] for key in a if key!='enabled')
            return {key:a[key]-b[key] for key in a if key!='enabled'}
        result['damon_during_command']=difference(before['damon'],after['damon'])
        if include_start:result['damon_before_command']=difference(result['start']['damon'],before['damon'])
        result['limits']+='; DAMON bytes are region-operation statistics, not verified physical bytes reclaimed'
    return result
