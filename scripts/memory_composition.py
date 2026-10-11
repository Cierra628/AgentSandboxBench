"""Guest logical composition; never adds guest counters to host physical memory."""

FIELDS = ('MemTotal', 'MemFree', 'MemAvailable', 'Buffers', 'Cached', 'Shmem',
          'AnonPages', 'Slab', 'KernelStack', 'PageTables', 'Percpu')


def composition(values):
    missing = [key for key in FIELDS if key not in values]
    result = dict(status='PARTIAL' if missing else 'PASS', missing_fields=missing,
                  unit='bytes', scope='guest logical counters at one sample; not host physical residency',
                  used_definition='MemTotal - MemFree', percentage_denominator='same-sample nonfree memory',
                  slab_rule='Slab includes SReclaimable and SUnreclaim; do not add them again')
    if missing:
        return dict(result, nonfree_bytes=None, components_bytes=None, percentages=None)
    if any(not isinstance(values[key], int) or values[key] < 0 for key in FIELDS):
        raise ValueError('invalid guest counter')
    used = values['MemTotal'] - values['MemFree']
    cache = values['Cached'] + values['Buffers'] - values['Shmem']
    components = dict(file_cache_and_buffers=cache, anonymous=values['AnonPages'],
        slab=values['Slab'], shared_memory=values['Shmem'], kernel_stack=values['KernelStack'],
        page_tables=values['PageTables'], percpu=values['Percpu'])
    residual = used - sum(components.values())
    components['unclassified_residual'] = residual
    result.update(nonfree_bytes=used, used_estimate_bytes=values['MemTotal']-values['MemAvailable'],
                  components_bytes=components,
                  percentages={key:100*value/used for key,value in components.items()} if used > 0 else None,
                  reclaimable_slab_bytes=values.get('SReclaimable'),
                  unreclaimable_slab_bytes=values.get('SUnreclaim'))
    if used < 0 or cache < 0 or residual < 0 or values['MemAvailable'] > values['MemTotal']:
        result.update(status='PARTIAL', reason='counters do not form a nonnegative composition; no normalization')
    return result


def series(samples):
    rows = [dict(timestamp_ns=row['timestamp_ns'], phase=row.get('phase'), step=row.get('step'),
                 **composition(row['meminfo_bytes'])) for row in samples]
    valid = [row for row in rows if row['status']=='PASS']
    peak = max(valid, key=lambda row:row['nonfree_bytes']) if valid else None
    return dict(status='PASS' if rows and len(valid)==len(rows) else 'PARTIAL' if rows else 'NOT_MEASURED',
                sample_count=len(rows), valid_sample_count=len(valid), samples=rows,
                observed_peak_nonfree_bytes=peak['nonfree_bytes'] if peak else None,
                composition_at_observed_peak=peak,
                peak_scope='sample maximum, not instantaneous peak; incomplete samples excluded and counted')
