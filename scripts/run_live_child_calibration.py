"""One opt-in calibration in its own slice; no task matrix, pause or retry."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request

from experiment_memory import GroupSampler,sample_groups
from isolated_storage import available,capacity_budget,GIB
from platform_tasks import ROOT
from prettier_no_proxy import clean_environment
from run_light_monitor_controls import SOURCE,SOURCE_ID
from vmm_child_cgroup_check import accounting_controller_state

VOLUME_GIB=12
KERNEL=ROOT/'.artifacts/agentenv-cache-kernel-20261015-v3/vmlinux.bin'


def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def volume_gib(guests,*,restore_one=False,cache_sessions=False,seven_restored=False):
    if seven_restored:
        if guests!=7 or not cache_sessions or restore_one:raise ValueError('seven restored cache sessions required')
        return 64
    if cache_sessions:
        if guests!=2:raise ValueError("cache sessions require two guests")
        return 24
    assert guests in (1,2,7)
    if restore_one:
        if guests!=7:raise ValueError('restore budget requires seven guests')
        return 40
    return {1:VOLUME_GIB,2:16,7:24}[guests]


def release_empty_slice(parent):
    assert parent.name.startswith('asbvmm') and parent.name.endswith('.slice')
    if not parent.exists():return dict(status='PASS',already_absent=True)
    members={str(p):p.read_text().strip() for p in parent.rglob('cgroup.procs')}
    if not members or any(members.values()):raise RuntimeError('owned slice still has processes; not stopping it')
    subprocess.run(['systemctl','stop',parent.name],check=True,capture_output=True,timeout=20)
    assert not parent.exists(),'owned empty slice remained after stop'
    return dict(status='PASS',unit=parent.name,membership_before=members,slice_directory_exists=False)


def memory_window_checks(samples,endpoint,after_cleanup):
    """Saved records only. Keep the full verdict; separate windows never replace it."""
    from report_experiment_schedule import periodic_closure,checked_peak
    def checks(rows,end):
        closure=periodic_closure(rows)
        file_check=end.get('experiment_memory',{}).get('file_closure')
        return dict(points=len(rows),periodic=closure,endpoint_file_closure=file_check,
            peaks={k:(checked_peak(rows,list(range(len(rows))),k,closure) if
                all(type(r.get('experiment_memory',{}).get('parent_counters',{}).get(k)) is int for r in rows) else
                dict(status='UNAVAILABLE',reason='missing parent counter in saved window',value_bytes=None))
                for k in ('current','file')})
    result=dict(full_collection=checks(samples,after_cleanup),
        interpretation='Separate diagnostic windows; do not replace or discard the full collection/cleanup verdict.')
    if endpoint.get('status')!='OBSERVED':
        result['measurement']=dict(status='UNAVAILABLE',reason=endpoint.get('reason','no saved successful measurement endpoint'))
        result['cleanup']=dict(status='UNAVAILABLE',reason='measurement boundary unavailable; full series retained')
        return result
    snap=endpoint['snapshot'];left=snap['monotonic_ns'];right=left+snap['duration_ns']
    before=[i for i,r in enumerate(samples) if r['monotonic_ns']+r['duration_ns']<=left]
    after=[i for i,r in enumerate(samples) if r['monotonic_ns']>=right]
    result['measurement']=dict(status='RECORDED',scope='collection start, including preparation, through the explicit pre-deletion endpoint',
        original_indices=before,checks=checks([samples[i] for i in before],snap))
    result['cleanup']=dict(status='RECORDED',scope='after the endpoint read, including fixture finish and resource cleanup',
        original_indices=after,checks=checks([samples[i] for i in after],after_cleanup))
    classified=set(before+after)
    result['overlapping_endpoint_indices']=[i for i in range(len(samples)) if i not in classified]
    return result


def memory_acceptance(windows):
    """Apply existing endpoint/periodic/peak rules; do not select replacement points."""
    checks={}
    for name in ('full_collection','measurement'):
        window=windows.get(name,{})
        row=window if name=='full_collection' else window.get('checks',{})
        periodic=row.get('periodic',{});end=row.get('endpoint_file_closure') or {}
        delta=end.get('parent_minus_children_bytes')
        if name=='full_collection':
            checks[name+'_periodic']=(row.get('points',0)>0 and periodic.get('policy')=='segments_v3'
                and periodic.get('status')=='PASS' and periodic.get('data_complete') is True)
        checks[name+'_endpoint_file']=(end.get('status')=='WITHIN_THRESHOLD' and
            end.get('threshold_bytes')==2**20 and type(delta) is int and abs(delta)<=2**20)
        if name=='full_collection':
            for key in ('current','file'):
                checks[name+'_'+key+'_peak']=type(row.get('peaks',{}).get(key,{}).get('value_bytes')) is int
    return dict(status='PASS' if all(checks.values()) else 'FAIL',checks=checks,
        failed_checks=[k for k,v in checks.items() if not v],
        policy='full collection segments_v3 and peaks; both fixed endpoints <=1 MiB; split curves remain diagnostic, not extra denominator-based gates')


def charge_acceptance(receipt,evidence):
    """Consume the completed raw replay, without querying or replaying binaries again."""
    from host_backing_pfn_batch import CHARGE_VERSION
    from host_backing_charge_scope import signature
    checks={}
    try:
        checks['version']=receipt['version']==CHARGE_VERSION
        checks['batch']=receipt['status']=='PASS'
        scope=evidence['charge_scope']
        checks['identity']=(signature(scope['before'])==signature(scope['after'])==
            signature(receipt['charge_scope_before'])==signature(receipt['charge_scope_after']) and
            scope['history']==receipt['charge_identity_history'] and bool(scope['history']))
        attachment=evidence['pfn_attachment']
        checks['replayed_charge']=(attachment['version']==CHARGE_VERSION and
            attachment['page_charge_owner']=='OBSERVED_PER_CHUNK')
        objects=[o for o in evidence['objects'] if o.get('page_charge_attribution')]
        checks['objects']=len(objects)==attachment['available_objects'] and bool(objects)
        checks['query_states']=all(o['page_charge_attribution']['status'] in ('PARTIAL','OBSERVED_PER_CHUNK') and
            bool(o['page_charge_attribution']['chunks']) and
            all(c['unknown_page_observations']==0 for c in o['page_charge_attribution']['chunks']) for o in objects)
        return dict(status='PASS' if all(checks.values()) else 'FAIL',checks=checks,
            identity_status=attachment.get('charge_identity_status'),
            scope='Per-chunk physical page charge, not exclusive usage or a complete sandbox total')
    except (KeyError,TypeError,ValueError) as error:
        return dict(status='FAIL',checks=checks,error=repr(error))


def finalize_full_pfn_acceptance(out,pipeline_returncode,slice_cleanup):
    """Saved receipts only. Retain the volume on PASS too, for explicit cleanup."""
    value=dict(version='private-full-pfn-final-acceptance-v1',status='FAIL',checks={},errors=[],
        volume_policy='retained until explicit cleanup; never delete during final acceptance')
    outer=None
    try:
        read=lambda p:json.loads(p.read_text())
        outer=read(out/'result.json');storage=Path(outer['storage']);raw=storage/'guest-cache'
        stored=read(storage/'result.json');run=read(raw/'result.json');collector=read(raw/'live-cache/result.json')
        binding=read(raw/'live-cache/vmm-child-binding.json')
        receipt=read(raw/'host-backing-full-pfn/result.json');coverage=read(raw/'host-backing-full-pfn/coverage.json')
        launch=read(out/'launch.json');cache=ROOT/'.artifacts/prettier6604'/('child-cache-'+out.name)
        release=read(cache/'resources.json')
        memory=memory_acceptance(outer.get('memory_window_checks',{}));value['memory']=memory
        volume=storage/'volume.ext4';retention=stored.get('fixture_retention',{})
        value['volume_path']=str(volume)
        checks=dict(pipeline=pipeline_returncode==0 and outer['status']==stored['status']==run['status']==collector['status']==binding['status']=='PASS',
            full_pfn=receipt['status']=='PASS',
            file_offsets=coverage['known_page_offsets']==coverage['total_planned_page_offsets'] and
                coverage['unqueried_page_offsets']==coverage['unknown_observed_page_offsets']==0 and coverage['empty_objects_not_revalidated']==0,
            record_budget=sum(p.stat().st_size for p in (raw/'host-backing-full-pfn').rglob('*') if p.is_file())<2**30,
            memory=memory['status']=='PASS',sampler=outer.get('sampling',{}).get('status')=='PASS',
            source_stopped=outer.get('source_returned_to_stopped') is True,
            preparation_released=release.get('released') is True,
            slice_cleanup=slice_cleanup.get('status')=='PASS',
            owned_container_removed=stored.get('cleanup',{}).get('owned_container_removed') is True,
            mounts_and_loop_removed=stored.get('cleanup',{}).get('mount_and_loop_removed') is True,
            volume_retained=retention.get('policy')=='retain_until_explicit_cleanup_after_final_acceptance' and
                retention.get('path')==str(volume) and volume.is_file())
        value['checks']=checks
        if launch.get('host_backing_charge_evidence'):
            checks['page_charge']=False
            evidence=read(raw/'host-backing-full-pfn/evidence.json')
            value['version']='private-page-charge-final-acceptance-v1'
            value['memory']['acceptance_role']='DIAGNOSTIC_ONLY'
            del checks['memory']
            value['page_charge']=charge_acceptance(receipt,evidence)
            checks['page_charge']=value['page_charge']['status']=='PASS'
            checks['measurement_endpoint_recorded']=outer.get('measurement_endpoint',{}).get('status')=='OBSERVED'
        def check_saved_status(name,path,expected):
            checks[name]=False
            try:checks[name]=read(path)['status']==expected
            except (OSError,ValueError,KeyError,TypeError) as error:
                value['errors'].append(f'{name}: {path}: {type(error).__name__}: {error}')
        if launch.get('dynamic_file'):
            check_saved_status('dynamic_file',raw/'dynamic-file-check.json','PASS')
        if launch.get('ram_page_evidence'):
            check_saved_status('ram_evidence',raw/'live-cache/ram-page-analysis.json','OBSERVED')
        if launch.get('vmm_page_evidence'):
            check_saved_status('vmm_evidence',raw/'live-cache/vmm-page-analysis.json','OBSERVED_EVIDENCE')
        value['checks']=checks
        value['status']='PASS' if all(checks.values()) else 'FAIL'
    except (OSError,ValueError,KeyError,TypeError) as error:value['errors'].append(f'{type(error).__name__}: {error}')
    value['failed_checks']=[k for k,v in value['checks'].items() if not v]
    with (out/'final-acceptance.json').open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    if outer is not None:
        outer['pipeline_status']=outer['status'];outer['final_acceptance']=value
        outer['status']=value['status']
        outer['fixture_final_disposition']='RETAINED_FOR_EXPLICIT_CLEANUP' if value.get('checks',{}).get('volume_retained') else 'RETENTION_NOT_CONFIRMED'
        write(out/'result.json',outer)
    return value


def inside(out,unit,guests=1,*,kernel=KERNEL,dynamic_file=False,ram_page_evidence=False,host_backing_evidence=False,vmm_page_evidence=False,io_uring_page_evidence=False,host_backing_pfn_evidence=False,host_backing_full_pfn_evidence=False,host_backing_charge_evidence=False,light_memory=False,restore_one=False,diagnostic_server=None,cache_sessions=False,unified_cache_rounds=False,restored_lower=False,provider_binary=None,seven_restored=False,immediate=False):
    if restored_lower and not cache_sessions:raise ValueError('restored lower requires cache sessions')
    if unified_cache_rounds and not cache_sessions:raise ValueError('unified rounds requires cache sessions')
    if cache_sessions and (guests!=(7 if seven_restored else 2) or light_memory or restore_one or any((dynamic_file,ram_page_evidence,host_backing_evidence,vmm_page_evidence,io_uring_page_evidence,host_backing_pfn_evidence,host_backing_full_pfn_evidence,host_backing_charge_evidence))):raise ValueError('cache sessions is a separate two-guest workflow')
    if restore_one and not (light_memory and guests==7):raise ValueError('restore-one requires seven light guests')
    if guests==7 and not (light_memory or seven_restored):raise ValueError('seven guests require light memory')
    if light_memory and (guests not in (2,7) or any((dynamic_file,ram_page_evidence,vmm_page_evidence,io_uring_page_evidence,host_backing_evidence,host_backing_pfn_evidence,host_backing_full_pfn_evidence,host_backing_charge_evidence))):
        raise ValueError('light memory requires two or seven private guests and no heavy evidence')
    if host_backing_charge_evidence and not host_backing_full_pfn_evidence:
        raise ValueError('page charge evidence requires explicit full-list PFNs')
    if host_backing_full_pfn_evidence and (not host_backing_evidence or guests!=1 or host_backing_pfn_evidence):
        raise ValueError('full-list PFNs require one private guest, backing inventory, and no single-range option')
    if host_backing_pfn_evidence and not (host_backing_evidence and guests==1):
        raise ValueError('host backing PFNs require explicit backing evidence and one private guest')
    if io_uring_page_evidence and not vmm_page_evidence:
        raise ValueError('io_uring evidence requires explicit VMM and RAM evidence')
    if vmm_page_evidence and not ram_page_evidence:
        raise ValueError('VMM mapping evidence requires explicit RAM evidence')
    control=Path('/sys/fs/cgroup')/Path('/proc/self/cgroup').read_text().strip()[3:].lstrip('/')
    parent=control.parent
    assert parent.name==unit+'.slice' and not (parent/'cgroup.procs').read_text().strip()
    assert {'memory','io'}<=set((parent/'cgroup.subtree_control').read_text().split())
    value=dict(status='FAIL',parent=str(parent),controller=str(control),requested_runs=1,
        guest_count=guests,scope='private synthetic calibration on the pinned Prettier #6604 image; no original business replay')
    cache=ROOT/'.artifacts/prettier6604'/('child-cache-'+out.name)
    assert not cache.exists()
    env=clean_environment(dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE,
        ASB_VMM_CHILD_CGROUP_CHECK=str(out),ASB_VMM_CGROUP_PARENT=str(parent),
        ASB_EXPERIMENT_MEMORY_SERIES=str(out/'full-memory/memory.jsonl'),
        ASB_VMM_REQUIRE_IO='on',ASB_ISOLATED_UNUSED_NETWORK_POOLS='on',PYTHONDONTWRITEBYTECODE='1'))
    for key in ('ASB_VMM_ACCOUNTING_ONLY','ASB_LOCAL_NO_PROXY','ASB_LOCAL_NO_PROXY_CHECK'):env.pop(key,None)
    sampler=(GroupSampler(parent,out/'full-memory',diagnostics=True) if host_backing_full_pfn_evidence or cache_sessions else
             GroupSampler(parent,out/'full-memory'));started=False
    def call(label,argv,timeout):
        with (out/(label+'.log')).open('x') as log:
            p=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
        if p.returncode:raise RuntimeError(label+' exited '+str(p.returncode)+'; no retry')
    try:
        value['controller_states_before_preparation']=[accounting_controller_state(p,require_io=True) for p in (parent,control)]
        assert capacity_budget(available(ROOT),volume_gib(guests,restore_one=restore_one,cache_sessions=cache_sessions,seven_restored=seven_restored)*GIB)['ready']
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=15))[0]
        assert source['Id']==SOURCE_ID and not source['State']['Running'] and source['HostConfig']['RestartPolicy']['Name']=='no'
        write(out/'source-before.json',source['State'])
        subprocess.run(['gcc','-O2','-Wall','-Wextra',ROOT/'scripts/vmm_cgroup_launcher.c','-o',out/'vmm-cgroup-launcher'],check=True,timeout=30)
        call('prepare',[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache],300)
        subprocess.run(['docker','start',SOURCE],check=True,capture_output=True,timeout=30);started=True
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            time.sleep(1)
        else:raise TimeoutError('source health timeout')
        call('calibration',[sys.executable,'-B',ROOT/'scripts/validate_isolated_agentenv.py',
            '--guest-cache-child-calibration','--guest-cache-child-count',guests,
            *(['--snapshot-provider-binary',provider_binary,'--snapshot-provider-get-data'] if provider_binary else []),
            *(['--guest-cache-restored-lower'] if restored_lower else []),
            *(['--guest-cache-unified-rounds'] if unified_cache_rounds else []),
            *(['--seven-restored'] if seven_restored else []),*(['--immediate-restores'] if immediate else []),
            *(['--guest-cache-rollout-sessions'] if cache_sessions else []),
            *(['--guest-cache-light-memory'] if light_memory else []),
            *(['--guest-cache-light-restore-one'] if restore_one else []),
            '--diagnostic-guest-kernel',kernel,'--immutable-cache-seed',cache,
            *(['--diagnostic-server-binary',diagnostic_server] if diagnostic_server else []),
            *(['--guest-cache-dynamic-file'] if dynamic_file else []),
            *(['--guest-cache-ram-page-evidence'] if ram_page_evidence else []),
            *(['--guest-cache-vmm-page-evidence'] if vmm_page_evidence else []),
            *(['--guest-cache-io-uring-page-evidence'] if io_uring_page_evidence else []),
            *(['--guest-cache-host-backing-evidence'] if host_backing_evidence else []),
            *(['--guest-cache-host-backing-pfn-evidence'] if host_backing_pfn_evidence else []),
            *(['--guest-cache-host-backing-charge-evidence'] if host_backing_charge_evidence else []),
            *(['--guest-cache-host-backing-full-pfn-evidence','--retain-fixture-for-final-acceptance'] if host_backing_full_pfn_evidence else [])],1100 if seven_restored else 600)
        paths=re.findall(r'status=PASS result_dir=(\S+)',(out/'calibration.log').read_text())
        storage=[Path(p) for p in paths if Path(p).parent==ROOT/'.artifacts/storage-isolated-service']
        assert len(storage)==1
        value['storage']=str(storage[0]);raw=storage[0]/'guest-cache';value['raw']=str(raw)
        result=json.loads((raw/'result.json').read_text());assert result['status']=='PASS'
        if seven_restored:
            if immediate:
                assert 5<=len(result['bindings'])<=7 and 1<=result['rounds']<=60
                assert result['stop_reason'] in ('first_below_10000','60_rounds','120_seconds')
            else:assert len(result['bindings'])==7 and result['rounds']==60
        elif cache_sessions:
            assert len(result['sessions'])==3 and len(result['guests'])==2
        elif guests==1:assert (raw/'live-cache/vmm-child-binding.json').is_file()
        else:
            assert len(result['guests'])==guests
            assert all((raw/'live-cache'/name/'vmm-child-binding.json').is_file() for name in tuple('private-'+chr(ord('a')+i) for i in range(guests)))
        value['status']='PASS'
    except BaseException as error:
        value['error']=repr(error);(out/'failure.txt').write_text(traceback.format_exc())
        if (out/'calibration.log').exists():
            paths=re.findall(r'status=RUNNING result_dir=(\S+)',(out/'calibration.log').read_text())
            if paths:value['storage']=paths[-1]
    finally:
        if started:
            try:
                call('source-stop',['docker','stop','-t','30',SOURCE],60)
                state=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State}}',SOURCE],timeout=15))
                write(out/'source-after.json',state);assert not state['Running'];value['source_returned_to_stopped']=True
            except Exception as error:value.update(status='FAIL',source_cleanup_error=repr(error))
        if (cache/'resources.json').exists():
            try:call('cache-release',[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,'--release'],90)
            except Exception as error:value.update(status='FAIL',cache_cleanup_error=repr(error))
        try:
            if guests in (2,7) or dynamic_file or ram_page_evidence or host_backing_evidence:
                value['cleanup_boundary_monotonic_ns']=time.monotonic_ns()
                value['fixed_cleanup_tail_seconds']=1.2
                time.sleep(1.2)
            value['after_cleanup']=(sample_groups(parent,diagnostics=True) if host_backing_full_pfn_evidence else sample_groups(parent))
            value['sampling']=sampler.stop()
            if host_backing_full_pfn_evidence:
                storage=Path(value['storage']) if value.get('storage') else None
                endpoint_path=storage/'guest-cache/measurement-endpoint.json' if storage else None
                endpoint=(json.loads(endpoint_path.read_text()) if endpoint_path and endpoint_path.is_file() else
                    dict(status='UNAVAILABLE',reason='no saved successful pre-deletion endpoint; no cleanup fallback'))
                value['measurement_endpoint']=endpoint
                rows=[json.loads(line) for line in (out/'full-memory/memory.jsonl').read_text().splitlines()]
                value['memory_window_checks']=memory_window_checks(rows,endpoint,value['after_cleanup'])
            for group in parent.glob('vmm-*'):
                assert not (group/'cgroup.procs').read_text().strip(),'owned VMM still alive'
                group.rmdir()
        except Exception as error:value.update(status='FAIL',cleanup_error=repr(error))
        value['available_after_bytes']=available(ROOT);write(out/'result.json',value)
    return 0 if value['status']=='PASS' else 1


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--inside',action='store_true');p.add_argument('--unit')
    p.add_argument('--guests',type=int,choices=(1,2,7),default=1)
    p.add_argument('--dynamic-file',action='store_true',help='Private guests: write, evict and reread with current-file queries')
    p.add_argument('--ram-page-evidence',action='store_true',help='Opt in to saving already-read whole-RAM pagemap triples; existing 1 GiB/session budget unchanged')
    p.add_argument('--vmm-page-evidence',action='store_true',help='Private calibration only: full maps/smaps and non-RAM pagemap triples; requires --ram-page-evidence; default off')
    p.add_argument('--io-uring-page-evidence',action='store_true',help='Explicit VMM/RAM evidence only: restricted io_uring mixed mappings; default off')
    p.add_argument('--host-backing-evidence',action='store_true',help='Opt in to endpoint backing objects and references after RAM collectors stop; default off')
    p.add_argument('--host-backing-pfn-evidence',action='store_true',help='One private guest only: fixed first lower, first <=256 pages; default off')
    p.add_argument('--host-backing-full-pfn-evidence',action='store_true',help='One private guest only: all bound ordinary file ranges; default off')
    p.add_argument('--kernel',type=Path,default=KERNEL,help='Explicit local diagnostic kernel; ordinary default unchanged')
    p.add_argument('--host-backing-charge-evidence',action='store_true',help='Explicit page ownership acceptance; counter closure diagnostic only')
    p.add_argument('--light-memory',action='store_true',help='Two or seven private VMMs; parent and PSS rounds only, default off')
    p.add_argument('--provider-binary',type=Path,help='Opt-in owned GET_DATA provider for restored cache session; default off')
    p.add_argument('--restored-lower',action='store_true',help='Explicit restored session lower-layer cache only')
    p.add_argument('--unified-cache-rounds',action='store_true',help='Explicit shared clock for cache-session calibration')
    p.add_argument('--immediate-restores',action='store_true')
    p.add_argument('--seven-restored',action='store_true',help='One snapshot, seven restored page readers only')
    p.add_argument('--cache-sessions',action='store_true',help='One source and one restore; explicit page-cache lifecycle calibration')
    p.add_argument('--restore-one',action='store_true',help='Seven light guests only: save/delete/restore one instance')
    p.add_argument('--diagnostic-server',type=Path,help='Explicit private save/restore only: patched server binary')
    args=p.parse_args();out=args.output.resolve()
    if args.immediate_restores and not args.seven_restored:raise ValueError("immediate restores requires seven restored branches")
    if args.seven_restored and not (args.guests==7 and args.cache_sessions and args.restored_lower and args.provider_binary and args.diagnostic_server and args.unified_cache_rounds):
        raise ValueError('seven-restored requires seven guests, owned restore stack and shared clock')
    if args.provider_binary:
        if not (args.cache_sessions and args.restored_lower):raise ValueError('provider binary requires restored lower cache sessions')
        args.provider_binary=args.provider_binary.resolve()
        if not args.provider_binary.is_relative_to(ROOT/'.artifacts') or not args.provider_binary.is_file() or not os.access(args.provider_binary,os.X_OK):raise ValueError('provider binary must be a local executable under artifacts')
    if args.restored_lower and not args.cache_sessions:raise ValueError('restored lower requires cache sessions')
    if args.unified_cache_rounds and not args.cache_sessions:raise ValueError('unified rounds requires cache sessions')
    if args.diagnostic_server:
        if not (args.restore_one or args.cache_sessions):raise ValueError('diagnostic server requires restore-one or cache-sessions')
        args.diagnostic_server=args.diagnostic_server.resolve()
        if not args.diagnostic_server.is_relative_to(ROOT/'.artifacts') or not args.diagnostic_server.is_file() or not os.access(args.diagnostic_server,os.X_OK):
            raise ValueError('diagnostic server must be an executable file under project artifacts')
    if args.cache_sessions and (args.guests!=(7 if args.seven_restored else 2) or not args.diagnostic_server or args.light_memory or args.restore_one or any((args.dynamic_file,args.ram_page_evidence,args.host_backing_evidence,args.vmm_page_evidence,args.io_uring_page_evidence,args.host_backing_pfn_evidence,args.host_backing_full_pfn_evidence,args.host_backing_charge_evidence))):raise ValueError('cache sessions requires two guests, private server, no other probes')
    if args.restore_one and not (args.light_memory and args.guests==7):raise ValueError('restore-one requires seven light guests')
    if args.guests==7 and not (args.light_memory or args.seven_restored):raise ValueError('seven guests require light memory')
    if args.light_memory and (args.guests not in (2,7) or any((args.dynamic_file,args.ram_page_evidence,args.vmm_page_evidence,args.io_uring_page_evidence,args.host_backing_evidence,args.host_backing_pfn_evidence,args.host_backing_full_pfn_evidence,args.host_backing_charge_evidence))):
        raise ValueError('light memory requires two or seven private guests and no heavy evidence')
    if args.host_backing_charge_evidence and not args.host_backing_full_pfn_evidence:
        raise ValueError('page charge evidence requires explicit full-list PFNs')
    if args.host_backing_full_pfn_evidence and (not args.host_backing_evidence or args.guests!=1 or args.host_backing_pfn_evidence):
        raise ValueError('full-list PFNs require one private guest, backing inventory, and no single-range option')
    if args.host_backing_pfn_evidence and not (args.host_backing_evidence and args.guests==1):
        raise ValueError('host backing PFNs require explicit backing evidence and one private guest')
    if args.io_uring_page_evidence and not args.vmm_page_evidence:
        raise ValueError('io_uring evidence requires explicit VMM and RAM evidence')
    if args.vmm_page_evidence and not args.ram_page_evidence:
        raise ValueError('VMM mapping evidence requires explicit RAM evidence')
    assert out.is_relative_to(ROOT/'.artifacts')
    kernel=args.kernel.resolve();assert kernel.is_relative_to(ROOT/'.artifacts') and kernel.is_file()
    if args.inside:return inside(out,args.unit,args.guests,kernel=kernel,diagnostic_server=args.diagnostic_server,cache_sessions=args.cache_sessions,unified_cache_rounds=args.unified_cache_rounds,restored_lower=args.restored_lower,provider_binary=args.provider_binary,seven_restored=args.seven_restored,immediate=args.immediate_restores,dynamic_file=args.dynamic_file,ram_page_evidence=args.ram_page_evidence,host_backing_evidence=args.host_backing_evidence,**(dict(vmm_page_evidence=True) if args.vmm_page_evidence else {}),**(dict(io_uring_page_evidence=True) if args.io_uring_page_evidence else {}),**(dict(host_backing_pfn_evidence=True) if args.host_backing_pfn_evidence else {}),**(dict(host_backing_full_pfn_evidence=True) if args.host_backing_full_pfn_evidence else {}),**(dict(host_backing_charge_evidence=True) if args.host_backing_charge_evidence else {}),**(dict(light_memory=True) if args.light_memory else {}),**(dict(restore_one=True) if args.restore_one else {}))
    assert not out.exists()
    budget=capacity_budget(available(ROOT),volume_gib(args.guests,restore_one=args.restore_one,cache_sessions=args.cache_sessions,seven_restored=args.seven_restored)*GIB);assert budget['ready'],budget
    out.mkdir();(out/'code').mkdir()
    for name in ('run_live_child_calibration.py','run_guest_cache_child.py','guest_cache_child_fixture.py',
                 'validate_isolated_agentenv.py','vmm_cgroup_launcher.c','vmm_child_cgroup_check.py',
                 'task_physical_cache.py','live_task_cache.py','agentenv_whole_cache.py','experiment_memory.py'):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if args.cache_sessions:
        for name in ('provider_slot_cache.py','snapshot_lower_owner.py','snapshot_layer_metadata.py','cache_round_clock.py','run_rollout_cache_calibration.py','rollout_cache_sessions.py','light_prettier_rollout.py','run_guest_light_memory.py','light_memory_round.py','guest_cache_exporter.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if args.light_memory:
        for name in ('run_guest_light_memory.py','light_memory_round.py','host_sandbox_monitor.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if args.guests==2:
        for name in ('run_guest_cache_two_children.py','report_two_child_cache.py','report_live_child_calibration.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if args.dynamic_file:
        for name in ('guest_file_cache_query.py','file_cache_metadata.py','guest_cache_exporter.py',
                     'report_dynamic_file_cache.py','report_dynamic_file_calibration.py','report_dynamic_two_child_cache.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if args.ram_page_evidence:
        shutil.copy2(ROOT/'scripts/ram_page_evidence.py',out/'code/ram_page_evidence.py')
    if args.vmm_page_evidence:
        for name in ('vmm_page_evidence.py','host_special_metadata.py','host_schedule_files.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if args.host_backing_evidence:
        from host_backing_evidence import CODE_FILES
        for name in CODE_FILES:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if args.host_backing_pfn_evidence:
        from host_backing_pfn_live import CODE_FILES
        for name in CODE_FILES:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if args.host_backing_full_pfn_evidence:
        from host_backing_pfn_batch import CODE_FILES
        for name in CODE_FILES:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        shutil.copy2(ROOT/'scripts/report_experiment_schedule.py',out/'code/report_experiment_schedule.py')
    unit='asbvmm'+datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%Sz').lower()
    command=['systemd-run','--quiet','--wait','--pipe','--collect','--unit='+unit+'control','--slice='+unit+'.slice',
        '--property=WorkingDirectory='+str(ROOT),'--property=MemoryAccounting=yes','--property=IOAccounting=yes',
        sys.executable,'-B',str(Path(__file__).resolve()),'--output',str(out),'--inside','--unit',unit,'--guests',str(args.guests),
        *(['--diagnostic-server',str(args.diagnostic_server)] if args.diagnostic_server else []),
        *(['--provider-binary',str(args.provider_binary)] if args.provider_binary else []),
        *(['--restored-lower'] if args.restored_lower else []),
        *(['--unified-cache-rounds'] if args.unified_cache_rounds else []),
        *(['--seven-restored'] if args.seven_restored else []),*(['--immediate-restores'] if args.immediate_restores else []),
        *(['--cache-sessions'] if args.cache_sessions else []),
        '--kernel',str(kernel),*(['--restore-one'] if args.restore_one else []),*(['--light-memory'] if args.light_memory else []),*(['--dynamic-file'] if args.dynamic_file else []),
        *(['--ram-page-evidence'] if args.ram_page_evidence else []),
        *(['--vmm-page-evidence'] if args.vmm_page_evidence else []),
        *(['--io-uring-page-evidence'] if args.io_uring_page_evidence else []),
        *(['--host-backing-evidence'] if args.host_backing_evidence else []),
        *(['--host-backing-pfn-evidence'] if args.host_backing_pfn_evidence else []),
        *(['--host-backing-full-pfn-evidence'] if args.host_backing_full_pfn_evidence else []),
        *(['--host-backing-charge-evidence'] if args.host_backing_charge_evidence else [])]
    write(out/'launch.json',dict(command=command,budget=budget,kernel=str(kernel),unit=unit,guest_count=args.guests,
        provider_binary=str(args.provider_binary) if args.provider_binary else None,restored_lower=args.restored_lower,unified_cache_rounds=args.unified_cache_rounds,cache_sessions=args.cache_sessions,restore_one=args.restore_one,light_memory=args.light_memory,dynamic_file=args.dynamic_file,ram_page_evidence=args.ram_page_evidence,host_backing_evidence=args.host_backing_evidence,vmm_page_evidence=args.vmm_page_evidence,
        io_uring_page_evidence=args.io_uring_page_evidence,host_backing_pfn_evidence=args.host_backing_pfn_evidence,host_backing_full_pfn_evidence=args.host_backing_full_pfn_evidence,
        host_backing_charge_evidence=args.host_backing_charge_evidence,
        full_pfn_cgroup_diagnostics=args.host_backing_full_pfn_evidence,
        retain_fixture_for_final_acceptance=args.host_backing_full_pfn_evidence,
        fixed_cleanup_tail_seconds=1.2 if args.guests in (2,7) or args.dynamic_file or args.ram_page_evidence or args.host_backing_evidence else 0))
    with (out/'launch.log').open('x') as log:
        done=subprocess.run(command,env=clean_environment(os.environ),stdout=log,stderr=subprocess.STDOUT,timeout=1450 if args.seven_restored else 1100)
    if args.host_backing_full_pfn_evidence:
        try:cleanup=release_empty_slice(Path('/sys/fs/cgroup')/(unit+'.slice'))
        except Exception as error:cleanup=dict(status='FAIL',error=repr(error))
        write(out/'slice-cleanup.json',cleanup)
        acceptance=finalize_full_pfn_acceptance(out,done.returncode,cleanup)
        status=acceptance['status'];returncode=0 if status=='PASS' else 1
    else:
        if args.cache_sessions or args.light_memory or args.dynamic_file or args.ram_page_evidence or args.host_backing_evidence:
            write(out/'slice-cleanup.json',release_empty_slice(Path('/sys/fs/cgroup')/(unit+'.slice')))
        status='PASS' if done.returncode==0 else 'FAIL';returncode=done.returncode
    print('status='+status+' result_dir='+str(out),flush=True)
    return returncode


if __name__=='__main__':raise SystemExit(main())
