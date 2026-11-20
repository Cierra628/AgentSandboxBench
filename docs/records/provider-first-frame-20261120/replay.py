"""Replay archived rows through the unmodified ProviderCache.snapshot code object.
Only the two metadata reads and one pagemap read per slot are supplied from RAM.
Run as an ordinary user with read access to the two historical guest-cache dirs.
"""
import argparse
import csv
import gc
import json
import multiprocessing as mp
import os
from pathlib import Path
import pickle
import resource
import struct
import subprocess
import sys
import tempfile
from types import FunctionType, SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / 'scripts'))
from provider_slot_cache import PAGE, ROW, SEGMENT, ProviderCache


def pack(directory, old):
    path = directory / ('provider-frames.json' if old else 'process-restored-1/frames.json')
    frames = json.loads(path.read_bytes())
    if old:
        frames = frames['restored-1']
    identity = directory/'shared-device'/('provider' if old else 'provider-restored-1')/'identity.json'
    slots = {s['name']: s for s in json.loads(identity.read_bytes())['slots']}
    assert len(slots) == 16 and len(frames) == 2
    assert len({s['dev_id'] for s in slots.values()}) == 1
    assert {(s['queue'], s['slot']) for s in slots.values()} == {(0, i) for i in range(16)}
    inputs = []
    for frame in frames:
        assert len(frame['rows']) == len(slots) and {r['name'] for r in frame['rows']} == slots.keys()
        reads, spec = [], []
        for i,r in enumerate(frame['rows']):
            s = slots[r['name']]
            assert r['first_address'] == r['before'][2] // PAGE * PAGE
            def metadata(side):
                assert len(r['segments'][side]) == s['segments']['count']
                return ROW.pack(*r[side])+b''.join(SEGMENT.pack(*v) for v in r['segments'][side])
            reads += [(i,0,metadata('before')),
                      (16,r['first_address']//PAGE*8,struct.pack(f"<{len(r['pagemap'])}Q",*r['pagemap'])),
                      (i,0,metadata('after'))]
            spec.append((r['name'],s['segments']))
        assert sum(len(buf) for fd,_,buf in reads if fd!=16)==frame['metadata_bytes_read']
        inputs.append((spec,reads))
    return inputs,[f['rows'] for f in frames]


class FD(int):
    def fileno(self): return int(self)


def run(inputs, order):
    # No measurements discarded as warm-up. Keep both publications of each pair.
    proc = Path('/proc/self')
    obj = SimpleNamespace(proc=proc,start=(proc/'stat').read_text().rsplit(')',1)[1].split()[19],
                          pm=FD(16),_check_owner=lambda: None)
    def pread(fd,n,offset):
        expected_fd,expected_offset,buf = next(stream)
        assert (fd,offset,n)==(expected_fd,expected_offset,len(buf))
        return buf
    original = ProviderCache.snapshot
    replay = FunctionType(original.__code__,dict(original.__globals__,os=SimpleNamespace(pread=pread)))
    plans=[([(Path(name),FD(i),segments) for i,(name,segments) in enumerate(spec)],reads)
           for spec,reads in inputs]
    stats=[]
    for pair in range(12):
        held=[]
        for index in order:
            obj.slots, reads = plans[index]
            stream=iter(reads)
            gc0=[g['collections'] for g in gc.get_stats()]
            ru0=resource.getrusage(resource.RUSAGE_SELF)
            frame=replay(obj)
            ru1=resource.getrusage(resource.RUSAGE_SELF)
            assert next(stream,None) is None
            held.append(frame['rows'])
            cost=frame['stage_costs']['record_build']
            assert cost['calls']==16
            stats.append(dict(call=len(stats)+1,frame=index+1,wall_ns=cost['elapsed_ns'],cpu_ns=cost['cpu_ns'],
                              gc_delta=','.join(str(g['collections']-b) for b,g in zip(gc0,gc.get_stats())),
                              minor_faults=ru1.ru_minflt-ru0.ru_minflt,major_faults=ru1.ru_majflt-ru0.ru_majflt))
    # Last pair checked against archive by the parent, outside every timed call.
    return stats,held


def instance(inputs,order,conn):
    conn.send(run(inputs,order))
    conn.close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('old',type=Path);p.add_argument('new',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args()
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
    with tempfile.TemporaryDirectory(prefix='provider-first-') as scratch, a.output.open('x') as output:
        writer=None
        for name,path in [('serial',a.old),('process',a.new)]:
            inputs,expected=pack(path,name=='serial')
            data=Path(scratch)/'prepared';data.write_bytes(pickle.dumps(inputs))
            for trial in range(8):
                first=trial%2
                # Fresh launcher and fork for each trial; no archived JSON heap inherited.
                raw=subprocess.check_output([sys.executable,'-B',__file__,'--instance',str(data),str(first)],
                                            env=env,timeout=30)
                stats,rows=json.loads(raw)
                assert rows==[expected[first],expected[1-first]]
                for record in stats:
                    row=dict(batch=name,trial=trial+1,**record)
                    if writer is None:
                        writer=csv.DictWriter(output,fieldnames=list(row),lineterminator='\n')
                        writer.writeheader()
                    writer.writerow(row)


if __name__=='__main__':
    if sys.argv[1:2]==['--instance']:
        # Only read a payload created by this program in its private temporary dir.
        import numpy  # present in the original fork parent
        ctx=mp.get_context('fork')
        receive,send=ctx.Pipe(False)
        first=int(sys.argv[3])
        worker=ctx.Process(target=instance,args=(pickle.loads(Path(sys.argv[2]).read_bytes()),[first,1-first],send))
        worker.start();send.close()
        if not receive.poll(20):raise TimeoutError('offline worker')
        result=receive.recv();worker.join(5);assert worker.exitcode==0
        print(json.dumps(result));receive.close()
    else:
        main()
