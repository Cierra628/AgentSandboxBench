"""Check the actual parent merge against the old Python set semantics, on saved inputs."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json,sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
from paused_process_workers import Workers
def check(raw):
    archive=json.loads((raw/'result.json').read_text());roles=sorted(archive['workers'])
    expected=[set(),set()]; real_unique=np.unique; actual=[]
    def observe(v):
        u=real_unique(v);actual.append(u);return u
    for r in roles:
        with np.load(raw/('process-'+r)/'sets.npz',allow_pickle=False) as f:
            for union,k in zip(expected,('four','provider')):union.update(map(int,f[k]))
    dummy=SimpleNamespace(roles=roles,out=raw,stats=archive['workers'],go=archive['go'],
        ps={r:SimpleNamespace(pid=archive['workers'][r]['pid']) for r in roles})
    with patch.object(np,'unique',side_effect=observe):
        merged=Workers.merge(dummy,{r:dict(sandbox_id=archive['query_rows'][r]['sid']) for r in roles})
    assert len(actual)==2 and all(np.array_equal(a,np.array(sorted(e),dtype='<u8')) for a,e in zip(actual,expected))
    counts=[merged['four_unique_pages'],merged['provider_unique_pages']]
    assert counts==list(map(len,expected))==[archive['four_unique_pages'],archive['provider_unique_pages']]
    return counts
if __name__=='__main__': print(check(Path(sys.argv[1])))
