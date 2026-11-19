"""Read the previous paused round; do not synthesize second reads."""
import json
from pathlib import Path
import sys

import numpy as np

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / 'scripts'))
from fast_cache_metadata import decode_array
from provider_slot_cache import PHYSICAL_MASK, account_observed_provider

source = root / '.artifacts/storage-isolated-service/20261119T073032Z-849623/guest-cache'
record = json.loads((source / 'result.json').read_text())
frames = json.loads((source / 'provider-frames.json').read_text())
pairs = list(zip(frames[0]['rows'], frames[1]['rows'], strict=True))
assert all(a['name'] == b['name'] for a, b in pairs)
print('provider slots:',len(pairs),'changed slots by field:',
      {k:sum(a[k] != b[k] for a,b in pairs) for k in ('before','after','segments','pagemap')})
rows = record['rows']
origin = min(r['host_start'] for r in rows.values())
modeled = []
all_pfns = set()
print('branch union guest_rows direct_first_offsets lower_first_offsets direct_changed lower_changed vmm_changed')
for role,r in rows.items():
    folder = source / 'live-cache' / (role + '-pages')
    before,after = [(folder / (s+'.bin')).read_bytes() for s in ('before','after')]
    host,direct,lower = [np.fromfile(folder / (s+'.bin'),dtype='<u8').reshape(-1,4)
                         for s in ('host','direct','lower')]
    _,pfns = account_observed_provider(before,after,host,direct,lower,*frames,
                                       decoded=(decode_array(before),decode_array(after)))
    assert len(pfns) == r['pages']
    all_pfns.update(pfns)
    counts = []
    for key in ('direct_queries','lower_queries'):
        f,l = r[key]
        assert f['requested_pages'] == l['requested_pages']
        counts.append(f['requested_pages'])
    print(role,len(pfns),len(host),*counts,
          *(np.count_nonzero(x[:,2] != x[:,3]) for x in (direct,lower)),
          np.count_nonzero(((host[:,1]^host[:,2]) | (host[:,2]^host[:,3])) & PHYSICAL_MASK))
    stages = r['stages']
    removed = sum(stages[k][1]-stages[k][0] for k in ('direct_last','lower_last'))
    modeled.append(r['host_end']-removed-origin)
assert len(all_pfns) == record['four_unique_pages']
print('batch union:',len(all_pfns),'modeled seconds retaining other stage times:',max(modeled)/1e9)
