"""One prepared Django image, four host-only creation controls; no guest commands."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import threading
import traceback
import uuid

from host_file_monitor import HostFileMonitor,counters,measurement_group,pids

ROOT=Path(__file__).resolve().parents[1]
ORDER=('off','on','on','off')
DJANGO='django__django-16819'
COPY_MARKER='source image is standard OCI; starting background image copy'


def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def creation_phases(text,sid,event,*,prepared,daemon_text=""):
    lines=re.sub(r'\x1b\[[0-9;]*m','',text).splitlines()
    copies=[n for n,line in enumerate(lines,1) if COPY_MARKER in line]
    if prepared and copies:raise ValueError('converted layer missing again during measured creation')
    needles=dict(resolved='image resolved to overlaybd config',creating='creating sandbox timeout=',launched='sandbox launch completed')
    markers={};numbers={}
    for key,needle in needles.items():
        matches=[(n,line) for n,line in enumerate(lines,1) if needle in line and (key=='resolved' or 'sandbox_id='+sid in line)]
        if key=='resolved' and prepared and not matches:continue
        if len(matches)!=1:raise ValueError('creation phase log missing or duplicated: '+key)
        n,line=matches[0];numbers[key]=n;stamp=datetime.fromisoformat(line.split()[0].replace('Z','+00:00'))
        markers[key]=int(stamp.timestamp())*10**9+stamp.microsecond*1000
    a=event['timestamp_ns'];b=event['end_timestamp_ns']
    if not a<=markers['creating']<markers['launched']<=b:raise ValueError('client/server phase order differs')
    if 'resolved' in markers:
        if not a<=markers['resolved']<=markers['creating']:raise ValueError('client/server resolution order differs')
        spans=dict(before_resolved=markers['resolved']-a,resolved_to_creating=markers['creating']-markers['resolved'],
            creating_to_launched=markers['launched']-markers['creating'],launch_to_client_return=b-markers['launched'])
        resolution='OBSERVED'
    else:
        # A successful cached launch need not emit the resolver's conversion log.
        # Bind it to the fixed platform image using the daemon's ready event.
        from django_oci import MANIFEST
        key='/workspace/env/image-cache/configs/'+MANIFEST.replace(':','-')+'-image.json'
        ready=[]
        for line in re.sub(r'\x1b\[[0-9;]*m','',daemon_text).splitlines():
            if 'sandbox reported envd-ready; releasing held downloads' not in line or 'device_key="'+key+'"' not in line:continue
            stamp=datetime.fromisoformat(line.split()[0].replace('Z','+00:00'))
            timestamp=int(stamp.timestamp())*10**9+stamp.microsecond*1000
            if markers['creating']<=timestamp<=markers['launched']:ready.append(timestamp)
        if len(ready)!=1:raise ValueError('cached creation requires one matching image-ready event')
        spans=dict(before_creating=markers['creating']-a,creating_to_launched=markers['launched']-markers['creating'],
            launch_to_client_return=b-markers['launched'])
        resolution='ABSENT_CACHE_IMAGE_CONFIRMED'
    assert sum(spans.values())==b-a
    return dict(status='PASS',converted_layer_copy_started=bool(copies),resolution_log=resolution,server_lines=numbers,phases_wall_ns=spans,
        client_wall_minus_monotonic_duration_ns=(b-a)-event['duration_ns'],
        scope=('Prepared-image measurement: no new missing-layer copy; image lookup may still access a registry' if prepared else 'Preparation may copy/convert the image before measured creations'))



class PreparationTrace:
    """Only the unmeasured preparation: owned process CPU/I/O state, every five seconds."""
    def __init__(self,path,group):
        self.path,self.group=path,group
        self.stop_event=threading.Event();self.error=None
        self.thread=threading.Thread(target=self.collect,name='image-preparation-trace',daemon=True)
        self.thread.start()

    def collect(self):
        try:
            with self.path.open('x',buffering=1) as stream:
                while True:
                    processes=[]
                    for pid in pids(self.group):
                        root=Path('/proc')/str(pid)
                        try:
                            fields=(root/'stat').read_text().rsplit(')',1)[1].split()
                            io=dict(line.split(':',1) for line in (root/'io').read_text().splitlines())
                            processes.append(dict(pid=pid,comm=(root/'comm').read_text().strip(),state=fields[0],
                                start_ticks=int(fields[19]),utime_ticks=int(fields[11]),stime_ticks=int(fields[12]),
                                wchan=(root/'wchan').read_text().strip(),
                                read_bytes=int(io['read_bytes']),write_bytes=int(io['write_bytes'])))
                        except (FileNotFoundError,ProcessLookupError):pass
                    stream.write(json.dumps(dict(monotonic_ns=time.monotonic_ns(),timestamp_ns=time.time_ns(),processes=processes))+'\n')
                    if self.stop_event.wait(5):break
        except Exception as error:self.error=repr(error)

    def stop(self):
        self.stop_event.set();self.thread.join(timeout=10)
        if self.thread.is_alive():raise TimeoutError('preparation trace did not stop')
        if self.error:raise RuntimeError('preparation trace: '+self.error)


def run(args):
    cfg=json.loads(args.config.read_text())
    if cfg['task_id']!=DJANGO or cfg['memory_mib']!=8192:raise ValueError('fixed Django 8 GiB image required')
    group,_=measurement_group()
    out=args.output;out.mkdir(parents=True,exist_ok=False)
    state=dict(status='FAIL',actions=0,task_id=DJANGO,image=cfg['image'],cpu=2,memory_mib=8192,
        order=list(ORDER),runs=[],guests=[],guest_sampling='NOT_REQUESTED',preparation=None)
    events=[];sid=None;collector=None;preparation_trace=None;known_deleted=[]
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(args.config_home))
    def call(label,argv,timeout=90):
        index=len(events);start=time.monotonic_ns();wall=time.time_ns();code=None
        try:
            with (out/(f'{index:03}-'+label+'.stderr')).open('x') as error:
                result=subprocess.run(list(map(str,argv)),env=env,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=error,timeout=timeout)
            code=result.returncode;(out/(f'{index:03}-'+label+'.stdout')).write_text(result.stdout)
        finally:
            end=time.monotonic_ns();end_wall=time.time_ns()
            event=dict(stage=label,start_monotonic_ns=start,end_monotonic_ns=end,duration_ns=end-start,
                timestamp_ns=wall,end_timestamp_ns=end_wall,exit_code=code)
            events.append(event);write(out/'events.json',events)
        if code:raise RuntimeError(label+': exit '+str(code))
        return result.stdout.strip(),event
    def cli(label,*argv):return call(label,[ROOT/'runtime/bin/aenv',*argv])[0]
    log=args.mount/'env/logs/server.log'
    try:
        assert json.loads(cli('initial-guests','list','--output','json'))==[]
        for index,mode in enumerate(('prepare',*ORDER)):
            label='preparation' if index==0 else f'run-{index}-{mode}'
            # Only the owned filesystem; outside the timed creation and periodic collector.
            call(label+'-sync',['sync','-f',args.mount],60)
            baseline=counters(group)
            before_text=log.read_text();offset=len(before_text)
            management=dict(start_ns=None,stop_ns=None)
            if mode=='on':
                management_start=time.monotonic_ns()
                collector=HostFileMonitor(out/label/'host',group,include_pss=False)
                management['start_ns']=time.monotonic_ns()-management_start
            if index==0:preparation_trace=PreparationTrace(out/'preparation-processes.jsonl',group)
            try:
                text,event=call(label+'-create',[sys.executable,'-B',ROOT/'scripts/agentenv_cold_start.py',
                    '--config-home',args.config_home,'--service-url',args.service_url,'--image',cfg['image'],
                    '--memory-mib','8192','--allow-8gib'],610)
                sid=str(uuid.UUID(text));state['guests'].append(sid)
                if preparation_trace:
                    active_trace=preparation_trace;preparation_trace=None;active_trace.stop()
                # Capture creation cost before deletion, on exactly the same scope in both modes.
                after=counters(group)
                if collector:
                    management_start=time.monotonic_ns();active=collector;collector=None
                    receipt=active.stop();management['stop_ns']=time.monotonic_ns()-management_start
                    if receipt['status']!='PASS':raise RuntimeError('host collector failed')
                else:receipt=dict(status='NOT_REQUESTED',samples=None,thread_cpu_ns=None,counter_read_total_ns=None)
                complete=log.read_text()
                assert complete.startswith(before_text),'service log replaced during creation'
                segment=complete[offset:];(out/(label+'-server.log')).write_text(segment)
                row=dict(index=index,mode=mode,guest_id=sid,creation=event,baseline=baseline,after_creation=after,
                    collector=receipt,collector_management_ns=management)
                write(out/(label+'-observation.json'),row)
                daemon_text=''
                if index>0 and 'image resolved to overlaybd config' not in segment:
                    daemon_text=(args.mount/'env/logs/ublk-daemon.log').read_text()
                    (out/(label+'-ublk.log')).write_text(daemon_text)
                row['log_phases']=creation_phases(segment,sid,event,prepared=index>0,daemon_text=daemon_text)
                if index:state['runs'].append(row)
                else:state['preparation']=row
                write(out/'result.json',state)
                cli(label+'-delete','delete',sid);known_deleted.append(sid);sid=None
                assert json.loads(cli(label+'-remaining','list','--output','json'))==[]
                print('status=PASS stage='+label+' result_dir='+str(out),flush=True)
            finally:
                if preparation_trace:
                    active_trace=preparation_trace;preparation_trace=None;active_trace.stop()
                if collector:
                    receipt=collector.stop();collector=None
                    write(out/(label+'-collector-finally.json'),receipt)
        assert len(set(state['guests']))==5 and len(state['runs'])==4
        state['status']='PASS'
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        cleanup={}
        if sid:
            try:cli('failure-delete','delete',sid);known_deleted.append(sid)
            except Exception as error:cleanup['guest_error']=repr(error);state['status']='FAIL'
        try:
            cleanup['service_sandbox_list_empty']=not json.loads(cli('final-guests','list','--output','json'))
            cleanup['known_guests_deleted']=set(known_deleted)==set(state['guests'])
            if not all(cleanup.values()):state['status']='FAIL'
        except Exception as error:cleanup['list_error']=repr(error);state['status']='FAIL'
        state['cleanup']=cleanup;write(out/'result.json',state)
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('config','output','config-home','mount'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--service-url',required=True)
    raise SystemExit(run(p.parse_args()))
