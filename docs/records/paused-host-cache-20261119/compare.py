"""Recalculate offset differences from saved little-endian, one-bit-per-page maps."""
import itertools
import json
from pathlib import Path


def compare(rows, directory):
    bits = []
    for r in rows:
        data = (directory/r['bitmap']).read_bytes()
        assert len(data) == (r['pages']+7)//8 and r['pages'] == rows[0]['pages']
        v = int.from_bytes(data,'little')
        assert v.bit_length() <= r['pages'] and v.bit_count() == r['resident']
        bits.append(v)
    def series(pairs):
        changes = []
        all_out = all_in = 0
        for i,j in pairs:
            left = bits[i] & ~bits[j]
            entered = bits[j] & ~bits[i]
            changes.append([rows[i]['second'],rows[j]['second'],left.bit_count(),entered.bit_count()])
            all_out |= left
            all_in |= entered
        return dict(rows=changes, identical=sum(x[2:]==[0,0] for x in changes),
                    lost=sum(x[2] for x in changes), gained=sum(x[3] for x in changes),
                    distinct_lost=all_out.bit_count(), distinct_gained=all_in.bit_count())
    return dict(first=series((0,i) for i in range(1,len(rows))),
                previous=series((i-1,i) for i in range(1,len(rows))),
                equal_count_different_bits=[[rows[i]['second'],rows[j]['second']]
                  for i,j in itertools.combinations(range(len(rows)),2)
                  if rows[i]['resident']==rows[j]['resident'] and bits[i]!=bits[j]])


if __name__ == '__main__':
    directory = Path(__file__).resolve().parent
    print(json.dumps(compare(json.loads((directory/'samples.json').read_text()),directory),indent=2))
