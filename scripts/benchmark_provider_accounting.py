"""Compare saved provider counting CPU and exact results; no live access."""
import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import time
import numpy as np

from fast_cache_metadata import decode_array
from provider_slot_cache import account_observed_provider


def benchmark(source,baseline,output):
    source=source.resolve();baseline=baseline.resolve();output=output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    spec=importlib.util.spec_from_file_location('provider_baseline',baseline)
    previous=importlib.util.module_from_spec(spec);spec.loader.exec_module(previous)
    inputs=[];expected=[]
    for saved in map(json.loads,(source/'samples.jsonl').read_text().splitlines()):
        prefix=source/f"pair-{saved['point']:04}"
        read=lambda suffix:Path(str(prefix)+suffix).read_bytes()
        rows=lambda suffix:np.frombuffer(read(suffix),dtype='<u8').reshape(-1,4)
        raw=json.loads(read('-provider.json'))
        args=(read('-before.bin'),read('-after.bin'),rows('-host.bin'),rows('-backend.bin'),
            rows('-lower.bin'),raw['first'],raw['last'])
        decoded=tuple(map(decode_array,args[:2]))
        reference=previous.account_observed_provider(*args)
        assert all(saved[k]==v for k,v in reference[0].items())
        assert account_observed_provider(*args)==reference
        assert account_observed_provider(*args,decoded=decoded)==reference
        inputs.append((args,decoded));expected.append(reference)
    timings=[]
    # Fixed three rounds with alternating order. Inputs/JSON/array parsing and
    # result checks are outside timing; the live path already has these arrays.
    for round_index in range(3):
        order=('before','after') if round_index%2==0 else ('after','before')
        for mode in order:
            for point,((args,decoded),reference) in enumerate(zip(inputs,expected,strict=True)):
                started=time.thread_time_ns()
                result=(previous.account_observed_provider(*args) if mode=='before' else
                    account_observed_provider(*args,decoded=decoded))
                elapsed=time.thread_time_ns()-started
                assert result==reference,(mode,point)
                timings.append(dict(round=round_index,mode=mode,point=point,cpu_ns=elapsed))
    means={mode:statistics.mean(t['cpu_ns'] for t in timings if t['mode']==mode) for mode in ('before','after')}
    summary=dict(status='PASS',point_count=len(inputs),rounds=3,results_equal=True,physical_pfn_sets_equal=True,
        scope='Provider counting only; excludes live querying, JSON IO and already-paid metadata array decoding.',
        cpu_mean_ns=means,cpu_reduction_fraction=1-means['after']/means['before'],
        round_cpu_sum_ns=[{mode:sum(t['cpu_ns'] for t in timings if t['mode']==mode and t['round']==i)
            for mode in ('before','after')} for i in range(3)],
        timings=timings,live_operations=0,whole_collector_cpu='NOT_REMEASURED')
    output.mkdir(parents=True);(output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path)
    p.add_argument('--baseline',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();result=benchmark(args.source,args.baseline,args.output)
    print(f"status=PASS points={result['point_count']} before_cpu_ms={result['cpu_mean_ns']['before']/1e6:.3f} after_cpu_ms={result['cpu_mean_ns']['after']/1e6:.3f} result_dir={args.output}")
