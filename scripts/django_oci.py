"""Pinned Django disk OCI input: separate bounded download, native conversion unchanged."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from experiment_disk_guard import writer_lock
from isolated_storage import GIB,available,capacity_budget
from prepare_xarray6992_image import Redirect,remaining_jobs
from xarray6992_offline_image import wrapper

ROOT=Path(__file__).resolve().parents[1]
REPOSITORY='swebench/sweb.eval.x86_64.django_1776_django-16819'
INDEX='sha256:6c46d22aaf692840f16209fe29b1d0c940bed043fce8047eb12169215222f044'
MANIFEST='sha256:63e2e4d9ac42c73b2504606f895d6030d735885e9c0bacc8a2add11c86166771'
IMAGE='docker.io/'+REPOSITORY+'@'+INDEX
PLATFORM_IMAGE='docker.io/'+REPOSITORY+'@'+MANIFEST
LAYER_BYTES=1242117203
INPUT_ALLOWANCE=2*GIB


def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def checked_json(data,digest):
    if 'sha256:'+hashlib.sha256(data).hexdigest()!=digest:raise ValueError('OCI metadata digest mismatch')
    return json.loads(data)


def metadata(index_data,manifest_data):
    index=checked_json(index_data,INDEX);manifest=checked_json(manifest_data,MANIFEST)
    selected=[r for r in index['manifests'] if r.get('platform')==dict(architecture='amd64',os='linux')]
    if len(selected)!=1 or selected[0]['digest']!=MANIFEST or selected[0]['size']!=len(manifest_data):
        raise ValueError('fixed index/platform relationship differs')
    if manifest['schemaVersion']!=2 or len(manifest['layers'])!=10 or sum(r['size'] for r in manifest['layers'])!=LAYER_BYTES:
        raise ValueError('fixed ten-layer image differs')
    blobs=[manifest['config'],*manifest['layers']]
    if any(r['size']<=0 or len(r['digest'])!=71 or not r['digest'].startswith('sha256:') for r in blobs):
        raise ValueError('invalid OCI blob descriptor')
    for r in blobs:bytes.fromhex(r['digest'][7:])
    if sum(r['size'] for r in blobs)+len(index_data)+len(manifest_data)>INPUT_ALLOWANCE:raise ValueError('OCI exceeds input allowance')
    entry=dict(selected[0],annotations={'org.opencontainers.image.ref.name':'latest'})
    return manifest,blobs,dict(schemaVersion=2,manifests=[entry])


def download_budget(free):
    required=capacity_budget(free,32*GIB)['required_available_bytes']+INPUT_ALLOWANCE
    return dict(ready=free>required,available_bytes=free,required_available_bytes=required,
        input_allowance_bytes=INPUT_ALLOWANCE,subsequent_volume_bytes=32*GIB)


def owned(directory):
    directory=Path(directory).resolve()
    if directory.parent!=ROOT/'.artifacts/django-oci':raise ValueError('owned .artifacts/django-oci child required')
    return directory


def blob_valid(path,row):
    if path.is_symlink() or not path.is_file() or path.stat().st_size!=row['size']:return False
    with path.open('rb') as f:return 'sha256:'+hashlib.file_digest(f,'sha256').hexdigest()==row['digest']


def validate(directory):
    directory=owned(directory);state=json.loads((directory/'result.json').read_text())
    if state['status']!='PASS' or state['image']!=IMAGE:raise ValueError('complete fixed Django input required')
    layout=directory/'oci';store=layout/'blobs/sha256'
    manifest,blobs,index=metadata((store/INDEX[7:]).read_bytes(),(store/MANIFEST[7:]).read_bytes())
    if state.get('downloaded_bytes')!=sum(r['size'] for r in blobs):raise ValueError('OCI byte count differs')
    if json.loads((layout/'index.json').read_text())!=index or json.loads((layout/'oci-layout').read_text())!={'imageLayoutVersion':'1.0.0'}:
        raise ValueError('local OCI index/layout differs')
    for row in blobs:
        if not blob_valid(store/row['digest'][7:],row):raise ValueError('OCI blob incomplete or corrupt: '+row['digest'])
    cfg=json.loads((store/manifest['config']['digest'][7:]).read_text())
    if cfg.get('architecture')!='amd64' or cfg.get('os')!='linux':raise ValueError('OCI config platform differs')
    return state,layout



def save_range_checkpoint(directory,blobs):
    """Keep successful write receipts and partial-file identities, not a claim of final digest validity."""
    journal=directory/'download-events.jsonl';active={r['digest']:[] for r in blobs}
    if journal.exists():
        for line in journal.read_text().splitlines():
            event=json.loads(line);digest=event['digest']
            if digest not in active:raise ValueError('range receipt outside fixed OCI input')
            if event['status'] in ('RESTART_BLOB','INVALIDATED_BLOB'):active[digest]=[]
            elif event['status']=='PASS':active[digest].append(event)
    ranges=[];identities={};store=directory/'oci/blobs/sha256'
    for row in blobs:
        partial=store/(row['digest'][7:]+'.part')
        if not active[row['digest']] or not partial.exists():continue
        if partial.is_symlink() or partial.stat().st_size!=row['size']:raise ValueError('partial OCI file differs')
        remaining_jobs([row],active[row['digest']])
        unique={(e['digest'],e['offset']):dict(digest=e['digest'],offset=e['offset'],length=e['length'],status='PASS') for e in active[row['digest']]}
        ranges.extend(unique.values());st=partial.stat()
        identities[row['digest']]=dict(device=st.st_dev,inode=st.st_ino,size=st.st_size,mtime_ns=st.st_mtime_ns)
    checkpoint=dict(image=IMAGE,ranges=ranges,partial_files=identities,reusable_bytes=sum(e['length'] for e in ranges),
        integrity='Successful receive/write receipts only; full OCI layer digest is still required before PASS')
    write(directory/'range-checkpoint.json',checkpoint)
    return checkpoint


def load_range_checkpoint(directory,blobs):
    checkpoint=json.loads((directory/'range-checkpoint.json').read_text())
    if checkpoint['image']!=IMAGE:raise ValueError('range checkpoint image differs')
    expected={row['digest']:row for row in blobs}
    for digest,identity in checkpoint['partial_files'].items():
        if digest not in expected:raise ValueError('range checkpoint blob differs')
        partial=directory/'oci/blobs/sha256'/(digest[7:]+'.part')
        if partial.is_symlink():raise ValueError('partial OCI identity differs')
        st=partial.stat();actual=dict(device=st.st_dev,inode=st.st_ino,size=st.st_size,mtime_ns=st.st_mtime_ns)
        if identity!=actual:raise ValueError('partial OCI identity differs')
    if any(e['digest'] not in checkpoint['partial_files'] for e in checkpoint['ranges']):raise ValueError('range receipt lacks a partial file')
    remaining_jobs(blobs,checkpoint['ranges'])
    keys={(e['digest'],e['offset']) for e in checkpoint['ranges']}
    if len(keys)!=len(checkpoint['ranges']) or checkpoint['reusable_bytes']!=sum(e['length'] for e in checkpoint['ranges']):
        raise ValueError('range checkpoint byte count differs')
    return checkpoint


class DockerHub:
    """Public pull token stays in memory; existing proxy environment is used normally."""
    def __init__(self,deadline):
        self.opener=urllib.request.build_opener(Redirect());self.deadline=deadline
        self.lock=threading.Lock();self.token='';self.token_at=0

    def timeout(self):
        remaining=self.deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('independent download budget expired')
        return min(30,remaining)

    def bearer(self):
        with self.lock:
            if not self.token or time.monotonic()-self.token_at>180:
                query=urllib.parse.urlencode(dict(service='registry.docker.io',scope='repository:'+REPOSITORY+':pull'))
                with self.opener.open('https://auth.docker.io/token?'+query,timeout=self.timeout()) as response:
                    self.token=json.loads(response.read(65536))['token']
                self.token_at=time.monotonic()
            return self.token

    def fetch(self,row,offset,length):
        stage='token'
        try:
            request=urllib.request.Request('https://registry-1.docker.io/v2/'+REPOSITORY+'/blobs/'+row['digest'],
                headers={'Authorization':'Bearer '+self.bearer(),'Range':f'bytes={offset}-{offset+length-1}'})
            stage='response_headers'
            with self.opener.open(request,timeout=self.timeout()) as response:
                stage='response_body'
                if response.status==206:
                    if response.headers['Content-Range']!=f'bytes {offset}-{offset+length-1}/{row["size"]}':raise ValueError('wrong OCI content range')
                elif not (response.status==200 and offset==0 and length==row['size']):raise ValueError('server did not honor OCI range')
                chunks=[];size=0
                while size<=length:
                    self.timeout()
                    chunk=response.read1(min(65536,length+1-size))
                    if not chunk:break
                    chunks.append(chunk);size+=len(chunk)
                if size!=length:raise ValueError('wrong OCI range length')
                return b''.join(chunks)
        except TimeoutError as error:
            error.oci_stage='download_deadline' if time.monotonic()>=self.deadline else stage
            raise
        except urllib.error.HTTPError as error:
            error.oci_stage=stage
            if error.code==401:self.token_at=0;self.token=''
            raise
        except urllib.error.URLError as error:
            error.oci_stage='download_deadline' if time.monotonic()>=self.deadline else stage
            raise


def download_blobs(store,blobs,fetch,record,deadline,*,resume_ranges=()):
    """Default whole-blob recovery; explicitly supplied range receipts may skip prior writes."""
    stop=threading.Event()
    def one(row):
        if stop.is_set():raise TimeoutError('independent download stopped')
        final=store/row['digest'][7:];partial=store/(row['digest'][7:]+'.part')
        if final.exists():
            if not blob_valid(final,row):raise ValueError('completed OCI blob changed')
            record(dict(status='REUSED',digest=row['digest']));return
        completed=[e for e in resume_ranges if e['digest']==row['digest']]
        if completed:
            if partial.is_symlink() or not partial.is_file() or partial.stat().st_size!=row['size']:raise ValueError('partial OCI resume file differs')
            record(dict(status='RESUME_RANGES',digest=row['digest'],bytes=sum(e['length'] for e in completed)))
        else:
            with partial.open('wb') as f:f.truncate(row['size'])
            record(dict(status='RESTART_BLOB',digest=row['digest']))
        for _,offset in remaining_jobs([row],completed):
            length=min(8*2**20,row['size']-offset)
            for attempt in range(1,4):
                if stop.is_set() or time.monotonic()>=deadline:raise TimeoutError('independent download stopped')
                if available(ROOT)<=26*GIB:raise RuntimeError('host disk reserve reached')
                start=time.monotonic_ns()
                try:
                    body=fetch(row,offset,length)
                    if len(body)!=length:raise ValueError('OCI range length differs')
                    with partial.open('r+b') as f:f.seek(offset);assert f.write(body)==length
                    record(dict(status='PASS',digest=row['digest'],offset=offset,length=length,attempt=attempt,duration_ns=time.monotonic_ns()-start))
                    break
                except Exception as error:
                    record(dict(status='FAIL',digest=row['digest'],offset=offset,attempt=attempt,error_type=type(error).__name__,
                        http_status=error.code if isinstance(error,urllib.error.HTTPError) else None,
                        failure_stage=getattr(error,'oci_stage',None),duration_ns=time.monotonic_ns()-start))
                    if attempt==3:
                        stop.set();raise
                    stop.wait(attempt)
        if not blob_valid(partial,row):
            record(dict(status='INVALIDATED_BLOB',digest=row['digest']))
            raise ValueError('downloaded OCI digest differs')
        partial.rename(final);final.chmod(0o444);record(dict(status='VERIFIED',digest=row['digest']))
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(one,r) for r in sorted(blobs,key=lambda r:-r['size'])]
        try:
            for future in as_completed(futures):future.result()
        except BaseException:
            stop.set()
            for f in futures:f.cancel()
            raise


def prepare(args):
    if getattr(args,'resume_ranges',False) and not args.resume:raise ValueError('--resume-ranges requires --resume')
    index_data=args.index.read_bytes();manifest_data=args.manifest.read_bytes()
    manifest,blobs,index=metadata(index_data,manifest_data)
    out=owned(args.output);budget=download_budget(available(ROOT))
    if not budget['ready']:raise RuntimeError('download requires >60 GiB free, including later 32 GiB volume')
    if args.resume:
        prior=json.loads((out/'result.json').read_text())
        if prior['image']!=IMAGE or prior['status'] not in ('FAIL','RUNNING'):raise ValueError('owned incomplete input required for resume')
    else:out.mkdir(parents=True,exist_ok=False)
    store=out/'oci/blobs/sha256';store.mkdir(parents=True,exist_ok=args.resume)
    for digest,data in [(INDEX,index_data),(MANIFEST,manifest_data)]:
        path=store/digest[7:]
        if path.exists() and path.read_bytes()!=data:raise ValueError('saved OCI metadata changed')
        path.write_bytes(data)
    checkpoint=load_range_checkpoint(out,blobs) if getattr(args,'resume_ranges',False) else None
    if args.resume:write(out/('previous-'+str(time.time_ns())+'.json'),prior)
    state=dict(status='RUNNING',image=IMAGE,platform_manifest=MANIFEST,layer_count=10,layer_bytes=LAYER_BYTES,
        download_budget=budget,timeout_seconds=args.download_timeout_seconds,transport='HTTP/1.1; 8 MiB ranges; three workers; three attempts per range',
        recovery='Verified whole blobs reused; incomplete blobs restart; failed input retained')
    start=time.monotonic_ns();state.update(start_monotonic_ns=start,timestamp_ns=time.time_ns())
    if checkpoint:state.update(recovery='Explicit range recovery; final whole-layer digest required',resumed_range_bytes=checkpoint['reusable_bytes'])
    write(out/'result.json',state);deadline=time.monotonic()+args.download_timeout_seconds
    lock=threading.Lock()
    def record(event):
        with lock:
            with (out/'download-events.jsonl').open('a') as f:f.write(json.dumps(dict(monotonic_ns=time.monotonic_ns(),**event))+'\n')
    try:
        client=DockerHub(deadline)
        download_blobs(store,blobs,client.fetch,record,deadline,resume_ranges=checkpoint['ranges'] if checkpoint else ())
        cfg=json.loads((store/manifest['config']['digest'][7:]).read_text())
        if (cfg.get('architecture'),cfg.get('os'))!=('amd64','linux'):raise ValueError('downloaded config platform differs')
        write(out/'oci/index.json',index);write(out/'oci/oci-layout',{'imageLayoutVersion':'1.0.0'})
        state.update(status='PASS',downloaded_bytes=sum(r['size'] for r in blobs),scope='Verified disk OCI input; no sandbox, conversion or forced residency')
    except BaseException as error:
        state.update(status='FAIL',error_type=type(error).__name__)
    finally:
        if state['status']=='FAIL':
            try:state['range_checkpoint_bytes']=save_range_checkpoint(out,blobs)['reusable_bytes']
            except Exception as error:state['range_checkpoint_error_type']=type(error).__name__
        elapsed=time.monotonic_ns()-start
        prior_elapsed=prior.get('cumulative_elapsed_ns') if args.resume else 0
        state.update(elapsed_ns=elapsed,cumulative_elapsed_ns=elapsed+prior_elapsed if prior_elapsed is not None else None,
            available_after_bytes=available(ROOT));write(out/'result.json',state)
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1



def creation_wrapper(saved):
    # The API takes an index, but this backend copies its resolved amd64 manifest.
    # Reject an unexpected index-copy invocation instead of downloading remotely.
    guard='#!/bin/sh\nif [ "$#" -ge 2 ] && [ "$1" = image ] && [ "$2" = copy ]; then\n  for argument in "$@"; do\n    if [ "$argument" = "'+IMAGE+'" ]; then echo "expected resolved Django platform manifest" >&2; exit 2; fi\n  done\nfi\n'
    return guard+wrapper(saved,Path('/asb-django-oci'),PLATFORM_IMAGE)


def install(mount):
    path=mount/'env/deps/regctl/v0.11.5/regctl';saved=path.with_name('regctl.before-django')
    if not path.is_file() or path.is_symlink() or saved.exists():raise ValueError('private original regctl entrypoint required')
    path.rename(saved)
    path.write_text(creation_wrapper(Path('/workspace/env/deps/regctl/v0.11.5/regctl.before-django')))
    path.chmod(0o755)
    return dict(status='INSTALLED',api_image=IMAGE,copy_source=PLATFORM_IMAGE,
        scope='Only copied private entrypoint changed; saved wrapper and existing real binary preserved')


def verify_mount(info,layout):
    rows=[r for r in info['Mounts'] if r['Destination']=='/asb-django-oci']
    if len(rows)!=1 or rows[0]['Type']!='bind' or Path(rows[0]['Source'])!=layout or rows[0]['RW']:
        raise ValueError('Django OCI bind must be the validated read-only layout')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--index',type=Path);p.add_argument('--manifest',type=Path);p.add_argument('--resume',action='store_true')
    p.add_argument('--resume-ranges',action='store_true',help='With --resume only: reuse successful write receipts with unchanged partial files; final layer digest still required')
    p.add_argument('--download-timeout-seconds',type=int,choices=(180,850),default=850)
    p.add_argument('--check-only',action='store_true');p.add_argument('--plan-only',action='store_true');args=p.parse_args()
    if args.resume_ranges and not args.resume:p.error('--resume-ranges requires --resume')
    if args.check_only:validate(args.output);print('status=VALIDATED');return 0
    if not args.index or not args.manifest:p.error('--index and --manifest required for preparation or plan')
    if args.plan_only:
        manifest,blobs,_=metadata(args.index.read_bytes(),args.manifest.read_bytes())
        print(json.dumps(dict(status='PASS_OFFLINE_PLAN',image=IMAGE,layer_count=len(manifest['layers']),blob_bytes=sum(r['size'] for r in blobs),budget=download_budget(available(ROOT))),indent=2));return 0
    os.umask(0o077)
    with writer_lock():return prepare(args)


if __name__=='__main__':raise SystemExit(main())
