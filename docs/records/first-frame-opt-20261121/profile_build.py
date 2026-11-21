"""Diagnostic batch only: split the original two independent list constructions."""
import gc
import inspect
from pathlib import Path
import resource
import textwrap
import time
import json
from provider_slot_cache import ProviderCache


def install(*, pread=None):
    original = ProviderCache.snapshot
    source = textwrap.dedent(inspect.getsource(original))
    for side, data in [('before','left'), ('after','right')]:
        expr=f'list(SEGMENT.iter_unpack({data}[ROW.size:]))'
        assert source.count(expr)==1
        source=source.replace(expr,f"timed('{side}', lambda: {expr})")
    ns=dict(original.__globals__)
    if pread is not None:  # offline archive supplies the independent read replies
        from types import SimpleNamespace
        ns['os']=SimpleNamespace(pread=pread)
    def measured(self):
        events=[]; parts=[]; active=None
        def event(kind,info):
            events.append((time.monotonic_ns(),kind,info['generation'],active))
        def timed(name,fn):
            nonlocal active
            active=name
            u=resource.getrusage(resource.RUSAGE_SELF); start=time.monotonic_ns()
            result=fn()
            end=time.monotonic_ns();v=resource.getrusage(resource.RUSAGE_SELF)
            parts.append((name,end-start,v.ru_minflt-u.ru_minflt,v.ru_majflt-u.ru_majflt))
            active=None
            return result
        ns['timed']=timed
        gc.callbacks.append(event)
        try: frame=ns['snapshot'](self)
        finally: gc.callbacks.remove(event)
        with (self.directory/'profile.jsonl').open('a') as f:
            print(json.dumps(dict(parts=parts,gc=events,build=frame['stage_costs']['record_build'])),file=f)
        return frame
    exec(source,ns)
    if pread is None:
        ProviderCache.snapshot=measured
    return measured
