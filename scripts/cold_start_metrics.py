"""Two owned-service metric snapshots, outside the seven cold request intervals."""
import json
import math
import re
import subprocess
import time
from urllib.parse import urlsplit
import urllib.request

from agentenv_cold_start import NoRedirect
from host_private_files import write
from host_sandbox_monitor import identity

STAGE='agentenv_sandbox_stage_duration_seconds'
IMAGE='agentenv_image_resolve_stage_duration_seconds'
HTTP='agentenv_http_request_duration_seconds'
CACHE='agentenv_image_resolve_cache_total'
INFLIGHT='agentenv_sandbox_stage_inflight'
STAGES=('resolve_rootfs','resolve_attached_drives','create_sandbox')
LABEL=re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*("(?:[^"\\]|\\.)*")\s*(?:,\s*|$)')
SAMPLE=re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)(?:\s+\d+)?$')
NAMES={name+suffix for name in (STAGE,IMAGE,HTTP) for suffix in ('_sum','_count')}|{CACHE,INFLIGHT}


def parse(text):
    """Read only the needed scalar series; ignore unrelated metrics and buckets."""
    result={}
    for line in text.splitlines():
        if not line or line.startswith('#'):continue
        if re.split(r'[\s{]',line,1)[0] not in NAMES:continue
        match=SAMPLE.fullmatch(line)
        if not match:raise ValueError('malformed selected metric')
        labels={};tail=match[2] or ''
        while tail:
            item=LABEL.match(tail)
            if not item or item[1] in labels:raise ValueError('malformed or duplicate metric label')
            labels[item[1]]=json.loads(item[2]);tail=tail[item.end():]
        value=float(match[3]);key=(match[1],tuple(sorted(labels.items())))
        if not math.isfinite(value) or value<0 or key in result:raise ValueError('invalid or duplicate metric sample')
        result[key]=value
    return result


def server_identity(container):
    state=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State}}',container],timeout=10))
    if not state['Running']:raise ValueError('owned service is not running')
    return dict(**identity(state['Pid']),started_at=state['StartedAt'])


def capture(service_url,container,out,label):
    started=time.monotonic_ns();receipt=dict(status='FAIL',label=label,start_monotonic_ns=started)
    try:
        url=urlsplit(service_url)
        if url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port or url.path or url.query or url.fragment or url.username or url.password:
            raise ValueError('owned loopback service URL required')
        before=server_identity(container)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        receipt['get_start_monotonic_ns']=time.monotonic_ns()
        with opener.open(service_url+'/metrics',timeout=3) as response:
            if response.status!=200:raise ValueError('metrics response must be HTTP 200')
            body=response.read()
        receipt['get_end_monotonic_ns']=time.monotonic_ns()
        receipt['get_duration_ns']=receipt['get_end_monotonic_ns']-receipt['get_start_monotonic_ns']
        target=out/('server-metrics-'+label+'.prom');target.write_bytes(body)
        receipt.update(raw_file=target.name,response_bytes=len(body))
        if before!=server_identity(container):raise ValueError('owned service changed during metric read')
        receipt.update(status='PASS',server_identity=before)
    except Exception as error:receipt['error']=type(error).__name__+': '+str(error)
    receipt.update(end_monotonic_ns=time.monotonic_ns())
    receipt['duration_ns']=receipt['end_monotonic_ns']-started
    write(out/('server-metrics-'+label+'.json'),receipt)
    return receipt


def compare(before_text,after_text):
    before,after=map(parse,(before_text,after_text))
    # Existing series may not disappear or decrease in one live server process.
    for key,value in before.items():
        if key not in after:raise ValueError('metric series disappeared')
        if key[0]!=INFLIGHT and after[key]<value:raise ValueError('metric counter decreased')
    def selected(key,name,labels):return key[0]==name and all(dict(key[1]).get(k)==v for k,v in labels.items())
    def delta(name,labels):
        keys=[k for k in after if selected(k,name,labels)]
        if not keys:raise ValueError('required final metric missing: '+name)
        return sum(after[k]-before.get(k,0) for k in keys)
    def histogram(name,labels):
        count=delta(name+'_count',labels);seconds=delta(name+'_sum',labels)
        if count!=int(count):raise ValueError('noninteger metric count')
        return dict(count=int(count),seconds=seconds)
    def errors(name,base,success):
        rows={}
        for key,value in after.items():
            if selected(key,name+'_count',base) and dict(key[1]).get('status')!=success:
                status=dict(key[1]).get('status');rows[status]=rows.get(status,0)+value-before.get(key,0)
        return rows
    stages={};failures=[]
    for stage in STAGES:
        labels=dict(operation='create_cold',stage=stage)
        value=histogram(STAGE,dict(**labels,status='ok'));value['other_status_counts']=errors(STAGE,labels,'ok')
        if value['count']!=7 or any(value['other_status_counts'].values()):failures.append('incomplete stage '+stage)
        if any(v!=0 for k,v in before.items() if selected(k,INFLIGHT,labels)):
            failures.append('baseline stage already inflight '+stage)
        gauges=[v for k,v in after.items() if selected(k,INFLIGHT,labels)]
        if len(gauges)!=1 or gauges[0]!=0:failures.append('stage still inflight or missing '+stage)
        stages[stage]=value
    labels=dict(method='POST',route='/sandboxes-cold',route_source='control_plane')
    http=histogram(HTTP,dict(**labels,status='2xx'));http['other_status_counts']=errors(HTTP,labels,'2xx')
    if http['count']!=7 or any(http['other_status_counts'].values()):failures.append('incomplete cold HTTP requests')
    images={}
    for stage in ('manifest_fetch','config_fetch','layer_convert'):
        if any(selected(k,IMAGE+'_count',dict(stage=stage)) for k in after):
            value=histogram(IMAGE,dict(stage=stage,status='ok'))
            value['other_status_counts']=errors(IMAGE,dict(stage=stage),'ok');images[stage]=value
        else:images[stage]=dict(status='NO_RECORDED_EVENT')
    manifest=images['manifest_fetch']
    if manifest.get('count')!=7 or any(manifest.get('other_status_counts',{}).values()):failures.append('image manifest scope/count differs from seven rootfs requests')
    for value in images.values():
        if any(value.get('other_status_counts',{}).values()):failures.append('image stage error/cancellation')
    cache={}
    for outcome in ('hit','miss'):
        keys=[k for k in after if selected(k,CACHE,dict(result=outcome))]
        cache[outcome]=sum(after[k]-before.get(k,0) for k in keys)
    if cache['hit']+cache['miss']!=7:failures.append('image cache scope/count differs from seven rootfs requests')
    if failures:raise ValueError('; '.join(failures))
    r,a,c=(stages[s]['seconds'] for s in STAGES);f=manifest['seconds']
    return dict(status='PASS',stages=stages,http=http,image_stages=images,image_cache=cache,
        http_other_processing_seconds=http['seconds']-r-a-c,rootfs_other_than_manifest_seconds=r-f,
        scope='Seven-request cumulative seconds, not batch wall time; nested image stages are not added to rootfs; aggregate labels cannot identify one slow request')


def evaluate(out,before,after):
    if before['status']!='PASS' or after['status']!='PASS':raise ValueError('metric snapshot missing/failed')
    if before['server_identity']!=after['server_identity']:raise ValueError('owned service process changed between snapshots')
    result=compare((out/before['raw_file']).read_text(),(out/after['raw_file']).read_text())
    result['snapshots']=[before,after]
    result['get_duration_sum_ns']=sum(s['get_duration_ns'] for s in (before,after))
    result['snapshot_duration_sum_ns']=sum(s['duration_ns'] for s in (before,after))
    return result


def audit(out,value):
    saved=value['server_metrics'];before,after=saved['snapshots']
    result=evaluate(out,before,after)
    if result!=saved:raise ValueError('saved metric summary differs from raw responses')
    if not (before['end_monotonic_ns']<=value['creation_begin_monotonic_ns']<=value['cold_begin_monotonic_ns']
            and after['start_monotonic_ns']>=value['all_creation_and_sampler_setup_returned_ns']>=value['cold_end_monotonic_ns']
            and after['end_monotonic_ns']<=value['postcheck_begin_monotonic_ns']):
        raise ValueError('metric snapshot overlaps original creation interval')
    return result
