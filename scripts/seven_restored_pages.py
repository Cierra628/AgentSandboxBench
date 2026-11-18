"""Opt-in synthetic measurement: seven restores, one device, two page tracers."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import ctypes
import errno
import json
import os
from pathlib import Path
import re
import threading
import time
import traceback
from live_task_cache import LiveTaskCache
from light_prettier_rollout import LightRollout
from measure_file_physical import parse_maps
from provider_slot_cache import ProviderCache, account_observed_provider
from snapshot_cache_residency import SnapshotCache, SourceTracer
from snapshot_layer_metadata import owned_memory_layer
from snapshot_lower_owner import service_openers, select_config
from task_physical_cache import TaskPhysicalCache
from prettier_no_proxy import IMAGE

ROLES=tuple(f'restored-{i}' for i in range(1,8))
PERIOD=10**9
CONTENT="from pathlib import Path; import os; p=Path('/workspace/cache-session-file'); data=b'A'*(16*2**20); "
WRITE="f=p.open('wb'); f.write(data); f.flush(); os.fsync(f.fileno()); f.close(); assert p.read_bytes()==data"


def save(path,value):
    path.write_text(json.dumps(value,indent=2)+'\n')


def mapped_device(vm):
    proc=Path('/proc')/str(vm['pid'])
    raw=(proc/'maps').read_text()
    devices={line.split()[-1] for line in raw.splitlines() if re.fullmatch(r'/dev/ublkb\d+',line.split()[-1])}
    if len(devices)!=1:raise ValueError(f'non-unique mapped snapshot device: {devices}')
    path=devices.pop()
    st=(proc/'root'/path.lstrip('/')).stat()
    matches=[r for line,r in zip(raw.splitlines(),parse_maps(raw)) if line.split()[-1]==path]
    if not all((r['device'],r['inode'],r['permissions'])==(st.st_dev,st.st_ino,'rw-p') for r in matches):
        raise ValueError('actual VMM mapping differs from the device')
    return path,(st.st_dev,st.st_ino,st.st_rdev),matches


class Reader(LiveTaskCache):
    def _start_snapshot_queries(self,proc):
        self.backend=SnapshotCache(proc,self.proof['slots'],self.directory,dispatch_counts=True)
        receipt=self.binding_row
        paths={r['path'] for r in self.backend.regions if r['path'].startswith('/dev/ublkb')}
        if self.backend.identity!=tuple(receipt['device_identity']) or paths!={receipt['device_path']}:
            raise ValueError('RAM-slot device differs from saved sandbox binding')
        d=self.directory/'lower-query';d.mkdir()
        self.lower_query=SnapshotCache(proc,[],d,file_path=self.lower_layer.path,dispatch_counts=True)
        if self.lower_query.identity[:2]!=(receipt['lower_device'],receipt['lower_inode']):
            raise ValueError('lower file identity changed')


class DeviceRound:
    """Both shared observations stay inside every contributing guest publication pair."""
    def __init__(self,provider,roles,deadline=None):
        self.deadline=deadline
        self.provider=provider;self.active=set(roles);self.cv=threading.Condition()
        self.arrived=[set(),set()];self.frames=[None,None];self.failed=None;self.matches={}
    def drop(self,role):
        with self.cv:self.active.discard(role);self.cv.notify_all()
    def frame(self,role,i):
        with self.cv:
            self.arrived[i].add(role);self.cv.notify_all()
            timeout=45 if self.deadline is None else min(45,max(0,(self.deadline-time.monotonic_ns())/PERIOD))
            if not self.cv.wait_for(lambda:self.failed or self.active<=self.arrived[i],timeout):
                self.failed='shared provider barrier timeout';self.cv.notify_all()
            if self.failed:raise RuntimeError(self.failed)
            if self.frames[i] is None:
                try:self.frames[i]=self.provider.snapshot()
                except Exception as e:
                    self.failed=repr(e);self.cv.notify_all();raise
            return self.frames[i]
    def match(self,role,b,a,h,d,l,left,right):
        current=set()
        _,four=account_observed_provider(b,a,h,d,l,*self.frames,decoded=(left,right),current_pfns=current)
        self.matches[role]=(current,four)
        return len(four)


def dispatch(q):
    words=[]
    for key in ('nent','nexit'):
        v=ctypes.c_uint64();tid=ctypes.c_uint64(threading.get_native_id())
        rc=q.lib.bpf_map_lookup_elem(q.service.fds[key],ctypes.byref(tid),ctypes.byref(v))
        if rc and ctypes.get_errno()!=errno.ENOENT:raise RuntimeError('dispatch evidence unreadable')
        words.append(v.value)
    return words


def prime_tracers(out,resources):
    # Both attachment and map discovery are independent of guest RAM. Leave all banks free.
    for source in ('backend','lower'):
        directory=out/('pre-attached-'+source);directory.mkdir()
        key=os.getpid(),source
        lib=ctypes.CDLL('libbpf.so.1',use_errno=True)
        with SourceTracer.lock:
            if key in SourceTracer.sources:raise RuntimeError('source tracer already active')
            service=SourceTracer(key,directory,lib,dispatch_counts=True)
            SourceTracer.sources[key]=service
        def release_empty(s=service):
            with SourceTracer.lock:
                if SourceTracer.sources.get(s.key) is s and not s.leased:
                    del SourceTracer.sources[s.key];s.stop()
        resources.callback(release_empty)


def restore_parallel(out,cli,snapshot,ids,light,errors,staged):
    gate=threading.Barrier(len(ROLES))
    timeline={}
    def branch(r):
        gate.wait(timeout=10)
        start=time.monotonic_ns()
        try:
            ids[r]=cli('restore-'+r,'start',snapshot,'--detach','--timeout','1800',timeout=150)
        finally:
            timeline[r]=dict(start_ns=start,return_ns=time.monotonic_ns())
        # Each branch can do its own work as soon as its restore returns.
        cli('contents-'+r,'exec',ids[r],'python3','-c',CONTENT+'assert p.read_bytes()==data; '+WRITE)
        staged[r]._start_guest_exporter(ids[r])
        light.register(r,ids[r])
    with ThreadPoolExecutor(7) as pool:
        fs={r:pool.submit(branch,r) for r in ROLES}
        for r,f in fs.items():
            try:f.result()
            except Exception as e:errors[r]=repr(e)
    save(out/'parallel-restores.json',timeline)
    return max(t['return_ns'] for t in timeline.values())


def stop_reason(ordinal,pages,end,deadline):
    if len(pages)<5: return 'configuration_failure'
    if ordinal==0 and min(pages)<10000:return 'first_below_10000'
    if end>=deadline:return '120_seconds'
    if ordinal==59:return '60_rounds'


def measure(out,readers,provider,procs,result,*,restore_end=None):
    clock=time.monotonic_ns;origin=clock();previous=-1
    adaptive=restore_end is not None
    deadline=restore_end+120*PERIOD if adaptive else None
    last_end=None
    with ExitStack() as archive, (out/'page-rounds.jsonl').open('x',buffering=1) as log,ThreadPoolExecutor(7) as pool:
        for ordinal in range(60):
            # Do not queue overlapping rounds or re-label skipped one-second ticks.
            if adaptive:
                planned=clock();tick=ordinal
                if planned>=deadline:
                    result['stop_reason']='120_seconds';break
            else:
                tick=max(previous+1,(clock()-origin+PERIOD-1)//PERIOD);planned=origin+tick*PERIOD
                time.sleep(max(0,planned-clock())/PERIOD)
            group=DeviceRound(provider,[c.session_name for c in readers.values() if not c.error],deadline)
            def worker(role,c):
                if c.error:return dict(status='UNKNOWN',error=c.error)
                c.device_round=group
                start=clock();cpu=time.thread_time_ns();queries=(c.backend,c.lower_query)
                counts=[dispatch(q) for q in queries]
                try:
                    c._run(one_point=True,deadline_ns=deadline,scheduled_ticket=dict(round=tick,origin_ns=origin,scheduled_ns=planned,period_ns=None if adaptive else PERIOD,dispatch_ns=start))
                    if c.error:raise RuntimeError(c.error)
                    t=c.samples[-1]['shared_round']
                    row=dict(read_start_ns=t['read_start_ns'],read_end_ns=t['read_end_ns'],status='OBSERVED',pages=c.samples[-1]['four_source_union_pages'],point=c.samples[-1]['point'])
                except Exception as e:
                    group.drop(c.session_name);row=dict(status='UNKNOWN',error=repr(e))
                counts=[[b-a for a,b in zip(old,dispatch(q))] for old,q in zip(counts,queries)]
                return dict(row,start_ns=start,end_ns=clock(),thread_cpu_ns=time.thread_time_ns()-cpu,dispatch=counts)
            def cpus():return [time.clock_gettime_ns(((~p.pid)<<3)|2) for p in procs]
            load=os.getloadavg();cp0=cpus();start=clock()
            fs={r:pool.submit(worker,r,c) for r,c in readers.items()}
            rows={r:f.result(timeout=120) for r,f in fs.items()}
            for r,e in result.get('branch_errors',{}).items():rows.setdefault(r,dict(status='UNKNOWN',error=e))
            valid=[r for r,v in rows.items() if v['status']=='OBSERVED']
            provider_union=set();four_union=set()
            for r in valid:
                a,b=group.matches[readers[r].session_name];provider_union.update(a);four_union.update(b)
            end=clock();cp1=cpus()
            offsets=[v.get('read_start_ns',v['start_ns']) for v in rows.values() if 'start_ns' in v]
            counts=[v['dispatch'] for v in rows.values() if 'dispatch' in v]
            enters=sum(x[0] for pair in counts for x in pair);exits=sum(x[1] for pair in counts for x in pair)
            row=dict(ordinal=ordinal,tick=tick,planned_ns=planned,start_ns=start,end_ns=end,wall_ns=end-start,
                skipped_ticks=tick-previous-1 if ordinal else 0,host_load=load,readers=rows,valid=len(valid),
                max_start_skew_ns=max(offsets)-min(offsets) if offsets else None,
                thread_cpu_ns=sum(v.get('thread_cpu_ns',0) for v in rows.values()),
                tracer_cpu_ns={p.pid:b-a for p,a,b in zip(procs,cp0,cp1)},probe_enters=enters,probe_exits=exits,
                provider_current_unique_pages=len(provider_union) if valid else None,
                four_unique_pages=len(four_union) if valid else None,atomic_snapshot=False)
            if adaptive:
                row.update(period_ns=None,previous_end_gap_ns=None if last_end is None else start-last_end)
                row.pop('skipped_ticks');last_end=end
            log.write(json.dumps(row)+'\n')
            if adaptive:archive.callback(save,out/f'device-round-{ordinal:03}.json',group.frames)
            else:save(out/f'device-round-{ordinal:03}.json',group.frames)
            previous=tick;result['rounds']=ordinal+1
            if adaptive and end>=deadline:
                row['complete_before_deadline']=False
                save(out/'deadline.json',dict(ordinal=ordinal,deadline_ns=deadline,end_ns=end,
                    valid_before_deadline=[r for r in valid if rows[r]['read_end_ns']<=deadline]))
                result['stop_reason']='120_seconds';break
            if group.failed:raise RuntimeError('shared provider failed: '+group.failed)
            if len(valid)<5:raise ValueError('configuration failure: fewer than five valid branches')
            if any(p.poll() is not None for p in procs):raise RuntimeError('shared tracer exited')
            if adaptive:
                reason=stop_reason(ordinal,[rows[r]['pages'] for r in valid],end,deadline)
                if reason:result['stop_reason']=reason;break


def run(args,out,cli,events):
    from run_rollout_cache_calibration import create_source
    adaptive=getattr(args,'immediate_restores',False)
    errors={};restore_end=None
    ids={};rows={};collectors={};stage='create-source'
    result=dict(status='FAIL',image=IMAGE,rounds=0,bindings=rows,guests=ids,scope='one snapshot; seven restores; device-level provider; non-atomic read intervals')
    try:
        source=create_source(args.config_home,args.service_url);result['source']=source
        cli('source-ready','exec',source,'true')
        from host_sandbox_monitor import Binding,identity as host_identity
        original=Binding(args.container,out).resolve(source);result['source_vmm']=original
        cli('write-source','exec',source,'python3','-c',CONTENT+'p.parent.mkdir(exist_ok=True); '+WRITE)
        snapshot='cache-session-'+out.parents[2].name
        stage='save-stop-source';cli('save','snapshot','create',source,'--name',snapshot,timeout=150)
        cli('stop-source','delete',source,timeout=90);result['snapshot']=snapshot
        if Path('/proc',str(original['pid'])).exists() and host_identity(original['pid'])['start_ticks']==original['start_ticks']:
            raise RuntimeError('source VMM survived deletion')
        with ExitStack() as resources:
            light=LightRollout(args.container,out,ROLES);resources.callback(light.stop)
            capture=TaskPhysicalCache(out,args.container,cli,directory=out/'kvm-capture');resources.callback(capture.close)
            if adaptive:
                stage='pre-attach-tracers';prime_tracers(out,resources)
                result['branch_errors']=errors;stage='parallel-restores'
                staged={r:Reader(out,args.container,cli,session='restored',session_name=r+'-pages',
                                 capture=capture,lower_cache=True) for r in ROLES}
                restore_end=restore_parallel(out,cli,snapshot,ids,light,errors,staged)
                result['all_restore_calls_returned_ns']=restore_end
                if source in ids.values() or len(set(ids.values()))!=len(ids):raise ValueError('duplicate restore ID')
                if len(light.by_role)<5:raise ValueError('configuration failure: fewer than five ready restores')
            else:
                for r in ROLES:
                    stage='restore-'+r
                    sid=cli('restore-'+r,'start',snapshot,'--detach','--timeout','1800',timeout=150)
                    if sid in [source,*ids.values()]:raise ValueError('duplicate restore ID')
                    ids[r]=sid;light.register(r,sid)
                    cli('contents-'+r,'exec',sid,'python3','-c',CONTENT+'assert p.read_bytes()==data; '+WRITE)
            capture.ready()
            shared_dir=out/'shared-device';shared_dir.mkdir()
            anchor=None;layer=None;provider=None
            order=list(light.by_role)
            if adaptive:
                env=out.parent.parent/'env';log=(env/'logs/server.log').read_text()
                configs=list((env/'snapshot-local-cache/runtime').glob('*/memory/image.json'))
                creators=[]
                for r in order:
                    try:
                        path,_,_=mapped_device(light.by_role[r])
                        select_config(log,ids[r],path,configs,env)
                    except ValueError:continue
                    creators.append(r)
                if not creators:raise ValueError('no restore has the daemon creation receipt')
                order=creators+[r for r in order if r not in creators]
            for r in order:
                sid=ids[r];stage='bind-device-'+r
                vm=light.by_role[r];proc=Path('/proc')/str(vm['pid'])
                try:path,identity,maps=mapped_device(vm)
                except Exception as e:
                    if not adaptive:raise
                    errors[r]=repr(e);continue
                if anchor is None:
                    # First restore has the real service creation receipt. Pin the actual device,
                    # not a snapshot name or a device number that can be recycled.
                    pinned=resources.enter_context((proc/'root'/path.lstrip('/')).open('rb',buffering=0))
                    st=os.fstat(pinned.fileno());anchor=(st.st_dev,st.st_ino,st.st_rdev)
                    layer=owned_memory_layer(out,proc,shared_dir/'lower-metadata',binding=light.binding,vm=vm,device_path=path)
                    provider=ProviderCache(out,proc,shared_dir/'provider',binding=light.binding,vm=vm,device_path=path)
                    resources.callback(provider.close,final_snapshot=False)
                    assert provider.segmented
                try:
                    if identity!=anchor:raise ValueError('branch does not map pinned shared device')
                    owners=service_openers(light.binding,vm,layer.path);provider._check_owner()
                    if {v['pid'] for v in owners}!={provider.identity['host_pid']}:raise ValueError('daemon identity conflict')
                    devs={s['dev_id'] for s in provider.identity['slots']}
                    if devs!={int(path.removeprefix('/dev/ublkb'))}:raise ValueError('slot device conflict')
                    rows[r]=dict(sandbox_id=sid,vmm_pid=vm['pid'],device_path=path,device_identity=identity,maps=maps,
                        image_config=provider.identity['image_config'],provider_dev_id=next(iter(devs)),lower_device=layer.stat.st_dev,
                        lower_inode=layer.stat.st_ino,daemon_pid=provider.identity['host_pid'],snapshot=snapshot)
                    save(out/'branch-bindings.json',rows)
                except Exception as e:
                    if not adaptive:raise
                    errors[r]=repr(e)
            def close_reader(c):
                try:c.close()
                except Exception as e:
                    result.setdefault('collector_close_errors',{})[c.session_name]=repr(e)
                    if not c.error and result['rounds']>=(1 if adaptive else 2):raise
            def prepare(r):
                sid=ids[r]
                c=staged[r] if adaptive else Reader(out,args.container,cli,session='restored',session_name=r+'-pages',capture=capture,
                         expected_binding=light.by_role[r],lower_cache=True)
                if adaptive:c.interval=None;c.expected_binding=light.by_role[r]
                c.binding_row=rows[r];c.lower_layer=layer;collectors[r]=c
                # On a hard setup error don't run later queries; close owned live handles only.
                try:c.start(sid,coordinated=True)
                except BaseException:
                    collectors.pop(r,None)
                    if c.lower_query:c.lower_query.close()
                    if c.backend:c.backend.close()
                    raise
                return c
            stage='prepare-page-readers'
            if adaptive:
                with ThreadPoolExecutor(7) as pool:
                    fs={r:pool.submit(prepare,r) for r in rows}
                    for r,f in fs.items():
                        try:c=f.result()
                        except Exception as e:errors[r]=repr(e)
                        else:resources.callback(close_reader,c)
                if len(collectors)<5:raise ValueError('configuration failure: fewer than five registered page readers')
            else:
                for r in rows:resources.callback(close_reader,prepare(r))
            stage='ready-seven-two-tracers'
            procs=[]
            for attr in ('backend','lower_query'):
                qs=[getattr(c,attr) for c in collectors.values()];p=qs[0].tracer
                if any(q.tracer is not p for q in qs) or len({q.bank for q in qs})!=len(collectors) or p.poll() is not None:
                    raise RuntimeError('seven registered banks on one live source tracer required')
                procs.append(p)
            assert len({p.pid for p in procs})==2
            save(out/'ready.json',dict(roles=ROLES,ids=ids,tracers=[p.pid for p in procs],source_stopped=True))
            stage='continuous-page-rounds';measure(out,collectors,provider,procs,result,restore_end=restore_end)
            stage='close-collectors'
        for r,sid in ids.items():cli('delete-'+r,'delete',sid,timeout=90)
        cli('delete-snapshot','template','delete',snapshot,timeout=60)
        result['status']='PASS'
    except BaseException as e:
        result.update(failed_stage=stage,error=repr(e));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        save(out/'events.json',events);save(out/'result.json',result)
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return int(result['status']!='PASS')
