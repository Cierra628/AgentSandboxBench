"""Bounded OCI layer download, with range retries, before any VM creation."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from xarray6992_inputs import ROOT,IMAGE,sha
from isolated_storage import confirmed_window,GIB

MANIFEST=IMAGE.split('@')[1]
REPO=IMAGE.split('@')[0].split('/',1)[1]
SPHINX_IMAGE='ghcr.io/epoch-research/swe-bench.eval.x86_64.sphinx-doc__sphinx-7590@sha256:577ebfcce825e4c9621e066dc1c8375443be94bbc6de30b9368e422b8f123731'
CHUNK=8*2**20


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if urllib.parse.urlsplit(newurl).hostname!=urllib.parse.urlsplit(req.full_url).hostname:
            redirected.remove_header('Authorization')
        return redirected


def descriptors(manifest,image=IMAGE):
    assert manifest['schemaVersion']==2
    rows=[manifest['config'],*manifest['layers']]
    assert image in (IMAGE,SPHINX_IMAGE)
    assert len(manifest['layers'])==13 and sum(x['size'] for x in manifest['layers'])==(935158562 if image==SPHINX_IMAGE else 1646224254)
    for row in rows:
        assert row['digest'].startswith('sha256:') and len(row['digest'])==71 and row['size']>0
        bytes.fromhex(row['digest'][7:])
    return rows


def preparation_end(args):
    if getattr(args,'ram_only_preparation',False):
        if args.window_start_utc or args.window_end_utc:
            raise ValueError('RAM-only preparation must not consume or extend an experiment window')
        return None
    if not args.window_start_utc or not args.window_end_utc:
        raise ValueError('confirmed window required unless RAM-only preparation is explicit')
    confirmed_window(args.window_start_utc,args.window_end_utc)
    return datetime.fromisoformat(args.window_end_utc).timestamp()


def remaining_jobs(blobs,events,complete=()):
    done=set()
    expected={(row['digest'],offset):min(CHUNK,row['size']-offset) for row in blobs for offset in range(0,row['size'],CHUNK)}
    for event in events:
        if event['status']=='PASS':
            key=(event['digest'],event['offset'])
            assert key in expected and event['length']==expected[key],'invalid completed range receipt'
            done.add(key)
    return [(row,offset) for row in sorted(blobs,key=lambda x:-x['size']) for offset in range(0,row['size'],CHUNK)
            if row['digest'] not in complete and (row['digest'],offset) not in done]


def validate(directory,image=IMAGE):
    digest=image.split('@')[1]
    directory=directory.resolve();result=json.loads((directory/'result.json').read_text())
    assert directory.parent==ROOT/'.artifacts/python-image-cache'
    assert result['status']=='PASS' and result['image']==image
    mounted=json.loads(subprocess.check_output(['findmnt','-J','-M',str(directory/'ram'),'-o','SOURCE,FSTYPE,OPTIONS'],timeout=10))['filesystems']
    assert len(mounted)==1 and mounted[0]['fstype']=='tmpfs' and 'ro' in mounted[0]['options'].split(',')
    assert mounted[0]['source']=='asb-xarray-'+directory.name
    layout=directory/'ram/oci';data=(layout/'blobs/sha256'/digest[7:]).read_bytes()
    assert hashlib.sha256(data).hexdigest()==digest[7:]
    manifest=json.loads(data)
    for row in descriptors(manifest,image):
        file=layout/'blobs/sha256'/row['digest'][7:]
        assert file.stat().st_size==row['size'] and sha(file)==row['digest'][7:]
    assert json.loads((layout/'oci-layout').read_text())=={'imageLayoutVersion':'1.0.0'}
    index=json.loads((layout/'index.json').read_text());assert len(index['manifests'])==1
    assert index['manifests'][0]['digest']==digest and index['manifests'][0]['size']==len(data)
    return result,layout


def prepare(args):
    image=getattr(args,'image',IMAGE);digest=image.split('@')[1];repo=image.split('@')[0].split('/',1)[1]
    os.umask(0o077)
    out=args.output.resolve();assert out.parent==ROOT/'.artifacts/python-image-cache'
    resume=getattr(args,'resume_incomplete',False)
    assert (out.exists() if resume else not out.exists()) and os.geteuid()==0
    end=preparation_end(args)
    data=args.manifest.read_bytes();assert hashlib.sha256(data).hexdigest()==digest[7:]
    manifest=json.loads(data);blobs=descriptors(manifest,image)
    mem=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    assert int(mem['MemAvailable'].split()[0])*1024>64*GIB,'RAM cache requires 64 GiB available memory'
    mount=out/'ram';previous=None
    if resume:
        previous=json.loads((out/'result.json').read_text());state=json.loads((out/'resources.json').read_text())
        assert previous['status']=='FAIL' and previous['image']==image
        assert state==dict(mount=str(mount),capacity_bytes=2*GIB,released=False)
        info=json.loads(subprocess.check_output(['findmnt','-J','-M',str(mount),'-o','SOURCE,FSTYPE,OPTIONS'],timeout=10))['filesystems']
        assert len(info)==1 and info[0]['source']=='asb-xarray-'+out.name and info[0]['fstype']=='tmpfs' and 'rw' in info[0]['options'].split(',')
        attempts=out/'preparation-attempts';attempts.mkdir(exist_ok=True)
        with (attempts/(str(time.time_ns())+'.json')).open('x') as file:file.write(json.dumps(previous,indent=2)+'\n')
    else:
        out.mkdir(parents=True);mount.mkdir()
        subprocess.run(['mount','-t','tmpfs','-o','size=2G,nodev,nosuid,noexec','asb-xarray-'+out.name,str(mount)],check=True,timeout=15)
        (out/'resources.json').write_text(json.dumps(dict(mount=str(mount),capacity_bytes=2*GIB,released=False),indent=2)+'\n')
    layout=mount/'oci';store=layout/'blobs/sha256'
    if resume:assert (store/digest[7:]).read_bytes()==data
    else:store.mkdir(parents=True);(store/digest[7:]).write_bytes(data)
    opener=urllib.request.build_opener(Redirect())
    token_lock=threading.Lock();token='';token_time=0;log_lock=threading.Lock()
    events=[json.loads(line) for line in (out/'download-events.jsonl').read_text().splitlines()] if resume else []
    begin=time.monotonic_ns();deadline=time.monotonic()+getattr(args,'download_timeout_seconds',850)
    if end is not None:deadline=min(deadline,time.monotonic()+end-time.time()-30)
    stop=threading.Event()
    result=dict(status='FAIL',image=image,transport='Python urllib HTTP/1.1, 8 MiB ranges, 3 workers',
                source_manifest_sha256=digest[7:],tmpfs_capacity_bytes=2*GIB,volatile_cache=True,
                preparation_mode='bounded RAM only; no VM or fixed disk volume; separate confirmed window required for experiment' if end is None else 'inside confirmed experiment window')
    if previous:result['previous_preparation']=previous
    def record(row):
        with log_lock:
            events.append(row)
            with (out/'download-events.jsonl').open('a') as log:log.write(json.dumps(row)+'\n')
    def bearer():
        nonlocal token,token_time
        with token_lock:
            if not token or time.monotonic()-token_time>200:
                query=urllib.parse.urlencode(dict(scope='repository:'+repo+':pull',service='ghcr.io'))
                with opener.open('https://ghcr.io/token?'+query,timeout=20) as response:
                    token=json.loads(response.read(65536))['token']
                token_time=time.monotonic()
            return token
    def chunk(row,offset):
        length=min(CHUNK,row['size']-offset);finish=offset+length-1
        for attempt in range(1,4):
            if stop.is_set() or time.monotonic()>=deadline:raise TimeoutError('download budget expired')
            start=time.monotonic_ns()
            try:
                req=urllib.request.Request('https://ghcr.io/v2/'+repo+'/blobs/'+row['digest'],
                    headers={'Authorization':'Bearer '+bearer(),'Range':f'bytes={offset}-{finish}'})
                with opener.open(req,timeout=30) as response:
                    if response.status==206:assert response.headers['Content-Range']==f'bytes {offset}-{finish}/{row["size"]}'
                    else:assert response.status==200 and offset==0 and length==row['size']
                    body=response.read(length+1);assert len(body)==length,'range length mismatch'
                fd=os.open(store/(row['digest'][7:]+'.part'),os.O_WRONLY)
                try:assert os.pwrite(fd,body,offset)==length
                finally:os.close(fd)
                record(dict(digest=row['digest'],offset=offset,length=length,attempt=attempt,status='PASS',elapsed_ns=time.monotonic_ns()-start))
                return
            except Exception as error:
                record(dict(digest=row['digest'],offset=offset,attempt=attempt,status='FAIL',error_type=type(error).__name__,
                            http_status=error.code if isinstance(error,urllib.error.HTTPError) else None,elapsed_ns=time.monotonic_ns()-start))
                if attempt==3:raise RuntimeError('range failed: '+row['digest']+' offset '+str(offset)) from None
                stop.wait(attempt)
    try:
        complete=set()
        for row in blobs:
            final=store/row['digest'][7:];partial=store/(row['digest'][7:]+'.part')
            if final.exists():
                assert final.stat().st_size==row['size'] and sha(final)==row['digest'][7:];complete.add(row['digest'])
            elif partial.exists():assert resume and partial.stat().st_size==row['size']
            else:
                with partial.open('xb') as file:file.truncate(row['size'])
        # Prioritize the previously failing large layer; ranges write disjoint offsets.
        jobs=remaining_jobs(blobs,events,complete)
        result['requested_ranges_this_attempt']=len(jobs)
        result['requested_bytes_this_attempt']=sum(min(CHUNK,row['size']-offset) for row,offset in jobs)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures=[pool.submit(chunk,*job) for job in jobs]
            try:
                for future in as_completed(futures):future.result()
            except BaseException:
                stop.set()
                for future in futures:future.cancel()
                raise
        unpacked=0;regular=0
        for row in blobs:
            final=store/row['digest'][7:];partial=store/(row['digest'][7:]+'.part')
            assert sha(final if final.exists() else partial)==row['digest'][7:]
            if not final.exists():partial.rename(final)
            if row in manifest['layers']:
                with tarfile.open(store/row['digest'][7:],mode='r|gz') as archive:
                    for member in archive:
                        if member.isfile():unpacked+=((member.size+4095)//4096)*4096;regular+=1
        (layout/'oci-layout').write_text(json.dumps({'imageLayoutVersion':'1.0.0'})+'\n')
        (layout/'index.json').write_text(json.dumps(dict(schemaVersion=2,manifests=[dict(mediaType=manifest['mediaType'],
            digest=digest,size=len(data),annotations={'org.opencontainers.image.ref.name':'latest'})]),indent=2)+'\n')
        config=json.loads((store/manifest['config']['digest'][7:]).read_text());assert config['architecture']=='amd64' and config['os']=='linux'
        result.update(status='PASS',downloaded_bytes=sum(x['size'] for x in blobs),unpacked_regular_rounded_bytes=unpacked,
                      regular_file_entries=regular,scope='all compressed blob digests verified; tar sizes counted without extraction; native conversion not yet run')
        subprocess.run(['mount','-o','remount,ro',str(mount)],check=True,timeout=15)
    except BaseException as error:
        result.update(error=repr(error),scope='incomplete volatile download cache retained; no VM or conversion started')
    finally:
        result.update(elapsed_ns=time.monotonic_ns()-begin,successful_ranges=sum(e['status']=='PASS' for e in events),
                      failed_range_attempts=sum(e['status']=='FAIL' for e in events))
        result['cumulative_elapsed_ns']=result['elapsed_ns']+(previous.get('cumulative_elapsed_ns',previous['elapsed_ns']) if previous else 0)
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--manifest',type=Path)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--window-start-utc');parser.add_argument('--window-end-utc')
    parser.add_argument('--image',choices=[IMAGE,SPHINX_IMAGE],default=IMAGE)
    parser.add_argument('--ram-only-preparation',action='store_true',help='Explicit bounded RAM download before the shared-disk experiment window; never starts a VM')
    parser.add_argument('--resume-incomplete',action='store_true',help='Resume an owned failed RAM cache; preserve prior attempt and verify every complete blob digest')
    parser.add_argument('--download-timeout-seconds',type=int,choices=[180,850],default=850)
    parser.add_argument('--check-only',action='store_true');parser.add_argument('--release',action='store_true')
    args=parser.parse_args()
    if args.release:
        result,layout=validate(args.output,args.image)
        subprocess.run(['umount',str(layout.parent)],check=True,timeout=20)
        record=json.loads((args.output/'resources.json').read_text());record['released']=True
        (args.output/'resources.json').write_text(json.dumps(record,indent=2)+'\n');print('status=RELEASED')
    elif args.check_only:validate(args.output,args.image);print('status=VALIDATED')
    else:raise SystemExit(prepare(args))
