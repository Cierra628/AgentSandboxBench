"""Compare guest block-device counters; not pure storage wait or host I/O."""
FIELDS={'reads_completed':3,'sectors_read':5,'read_ms':6,'writes_completed':7,'sectors_written':9,'write_ms':10,'io_ms':12,'weighted_io_ms':13}


def parse(text):
    rows={}
    for line in text.splitlines():
        values=line.split();assert len(values)>=14,line
        major,minor=int(values[0]),int(values[1]);name=values[2]
        assert name not in rows
        row={key:int(values[index]) for key,index in FIELDS.items()}
        assert all(v>=0 for v in row.values())
        rows[name]=dict(major=major,minor=minor,**row)
    assert rows,'no diskstats devices'
    return rows


def delta(before,after):
    assert set(before)==set(after),'device set changed'
    result={}
    for name,old in before.items():
        new=after[name];assert (old['major'],old['minor'])==(new['major'],new['minor'])
        row={key:new[key]-old[key] for key in FIELDS}
        assert all(v>=0 for v in row.values()),'counter decreased'
        result[name]=dict(major=old['major'],minor=old['minor'],**row)
    return result
