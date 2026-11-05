"""Identical complete Prettier paths on one fresh service, with two file queries."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback

from host_density import ROOT,request,evict,live_objects,independent_extents
from host_private_files import read,write,query_objects
from host_file_monitor import measurement_group,counters
from host_sandbox_monitor import Binding
from agentenv_cold_start import create


def run(args):
    value=request(args.request);out=args.output.resolve();out.mkdir()
    n=value['count'];group,_=measurement_group();guests={};children=[]
    env=dict(os.environ,XDG_CONFIG_HOME=str(args.config_home));state=dict(status='FAIL',request=value,creation_batches=[],guests={},children=[])
    start=time.monotonic_ns()
    c4=value['task']=='c4'
    mount=out.parent.parent
    preparation=None if c4 else read(mount/'env/rootfs-copy-control/preparation.json')
    binding=Binding(args.container,out)
    def cli(*argv,timeout=90):return subprocess.check_output([str(ROOT/'runtime/bin/aenv'),*map(str,argv)],env=env,text=True,timeout=timeout).strip()
    def save():write(out/'result.json',state)
    try:
        assert json.loads(cli('list','--output','json'))==[]
        if c4:
            from c4_density_input import converted_layers,prepare_volume
            from host_density import prepare
            begin=time.monotonic_ns()
            # Native resolver converts the local OCI input without creating any guest.
            conversion_volume=prepare_volume(args.config_home,args.service_url)
            write(out/'conversion-volume.json',conversion_volume)
            cli('volume','delete',conversion_volume['volume_id'])
            assert json.loads(cli('list','--output','json'))==[]
            def local_call(label,argv,timeout):
                result=subprocess.run(list(map(str,argv)),env=env,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,check=True)
                (out/(label+'.stderr')).write_text(result.stderr);return result.stdout
            preparation=prepare(mount,value,local_call,layers=converted_layers(mount))
            local_call('sync-C4-targets',['sync','-f',mount],300)
            state['input_conversion']=dict(status='PASS',start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns(),guests_created=0)
        # The native lifecycle synced this exact filesystem before service creation.
        state['before_guest_counters']=counters(group)
        state['copy_independence']=independent_extents(preparation['objects'])
        if 'pressure_context' in value:
            from host_pressure import before_guests
            state['pressure_before_guests']=before_guests(value,out,group)
        state['eviction'],state['P0']=evict(preparation['objects'],group);save()
        if c4:
            from external_workload import load
            cfg,_,_=load(args.external_config,args.upstream)
        else:
            from prettier6604_inputs import validate
            cfg,_,_=validate()
        def allocate(index):
            begin=time.monotonic_ns()
            sid=create(args.config_home,args.service_url,cfg['image'],memory_mib=8192,allow_8gib=True,local_c4=True) if c4 else create(args.config_home,args.service_url,cfg['source_image'],local_no_proxy=True)
            return f'guest-{index:02}',sid,dict(start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns(),sandbox_id=sid)
        batch_start=time.monotonic_ns()
        # No numeric API/admission cap in the fixed creation path; the requested maximum is ten.
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures=[pool.submit(allocate,i) for i in range(n)]
            errors=[]
            for future in futures:
                try:
                    role,sid,record=future.result();guests[role]=sid;state['guests'][role]=record;save()
                except Exception as error:errors.append(repr(error))
        state['creation_batches'].append(dict(index=1,count=n,start_monotonic_ns=batch_start,end_monotonic_ns=time.monotonic_ns(),gap_from_previous_ns=None))
        if errors:raise RuntimeError('density creation failed: '+repr(errors))
        assert len(set(guests.values()))==n
        info=json.loads(subprocess.check_output(['docker','inspect',args.container],text=True,timeout=10))[0]
        assert not any(m['Destination'].startswith('/workspace/env/image-cache/commits') or m['Destination'].startswith('/workspace/env/rootfs-copy-control') for m in info['Mounts'])
        state['individual_target_bind_mounts']='ABSENT'
        for role,sid in guests.items():
            target=out.parent/('density-'+role)
            context=out/(role+'.json');write(context,dict(name=role,task=value['task'],sandbox_id=sid,container=args.container,output=str(target)))
            command=[] if c4 else [sys.executable,'-B',str(ROOT/'scripts/run_prettier6604.py'),'--official-task',str(args.official_task),
                '--container',args.container,'--config-home',str(args.config_home),'--service-url',args.service_url,
                '--output',str(target),'--light-monitor','--expected-official-audit',str(args.expected_official_audit),'--density-context',str(context)]
            if c4:
                command=[sys.executable,'-B',str(ROOT/'scripts/run_external_workload.py'),'--config',str(args.external_config),
                    '--upstream',str(args.upstream),'--container',args.container,'--config-home',str(args.config_home),
                    '--service-url',args.service_url,'--output',str(target),'--density-context',str(context)]
            log=(out/(role+'.log')).open('x')
            p=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            children.append((role,p,log,target));state['children'].append(dict(role=role,command=command));save()
        deadline=time.monotonic()+360
        while not all((out/(role+'-tools.json')).exists() for role in guests):
            for role,p,_,target in children:
                if p.poll() is not None:raise RuntimeError('density child exited before P1: '+role+' '+str(read(target/'result.json') if (target/'result.json').exists() else p.returncode))
            if time.monotonic()>deadline:raise TimeoutError('density original tools timeout')
            time.sleep(.05)
        state['paths_finished_monotonic_ns']=max(read(out/(r+'-tools.json'))['end_monotonic_ns'] for r in guests)
        objects,state['identity']=live_objects(preparation,guests,binding)
        state['P1']=query_objects(objects,group,'P1-all-original-paths-and-downloads-finished')
        from host_rootfs_layers import validate_rootfs_inventory
        validate_rootfs_inventory(state['identity']['inventory'])
        assert all(binding.resolve(sid)==state['identity']['bindings'][role] for role,sid in guests.items())
        save();(out/'paths.release').write_text('continue\n')
        results=[];deadline=time.monotonic()+240
        for role,p,log,target in children:
            code=p.wait(timeout=max(.1,deadline-time.monotonic()));log.close()
            child=read(target/'result.json')
            if c4:
                assert code==0 and child['status']=='PASS' and child['actions']==36 and child['roundtrip_validation']=='PASS'
                assert child['strict_exit_match'] and child['syntax_validation']=='PASS'
                assert child['cleanup']['sandbox_deletion']=='DELEGATED_TO_DENSITY_COORDINATOR'
                results.append(dict(role=role,result=child,raw=target.name));continue
            official=read(target/'official-task-result.json')
            assert code==0 and child['status']=='PASS' and child['action_count']==29 and child['local_no_proxy']=='PASS'
            assert child['checks']['frozen_order_hashes_exit_codes_patch_and_key_outputs']=='PASS' and child['checks']['independent_properties']=='PASS'
            assert child['checks']['official_outcome_matches_full_replay']=='PASS'
            assert official['parser_results']['num_tests_passed']==0 and official['parser_results']['num_tests_failed']==2
            results.append(dict(role=role,result=child,official=official,raw=target.name))
        state.update(status='PASS',actions=n*(36 if c4 else 29),results=results)
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if not (out/'paths.release').exists():(out/'paths.release').write_text('abort\n')
        cleanup={}
        for role,p,log,_ in children:
            if p.poll() is None:
                try:p.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid,signal.SIGTERM)
                    try:p.wait(timeout=20)
                    except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=10)
            log.close()
        state['cleanup']=cleanup
        remaining=json.loads(cli('list','--output','json'))
        for role,sid in guests.items():
            if any(sid in str(r) for r in remaining):
                begin=time.monotonic_ns()
                try:
                    cli('delete',sid)
                    cleanup[role]=dict(status='PASS',sandbox_id=sid,start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns())
                except Exception as error:cleanup[role]=dict(status='FAIL',sandbox_id=sid,error=repr(error));state['status']='FAIL'
                if c4:save()
        cleanup['service_sandbox_list_empty']=json.loads(cli('list','--output','json'))==[]
        if not cleanup['service_sandbox_list_empty']:state['status']='FAIL'
        state.update(cleanup=cleanup,controller_elapsed_ns=time.monotonic_ns()-start);save()
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('request','output','config-home'):parser.add_argument('--'+name,type=Path,required=True)
    for name in ('official-task','expected-official-audit','external-config','upstream'):parser.add_argument('--'+name,type=Path)
    parser.add_argument('--container',required=True);parser.add_argument('--service-url',required=True)
    raise SystemExit(run(parser.parse_args()))
