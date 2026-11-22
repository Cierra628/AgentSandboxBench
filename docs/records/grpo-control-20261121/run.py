"""Three matched, cold GRPO controls. Stop before the next launch on any failure."""
import csv, hashlib, json, os, re, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'scripts'))
from platform_tasks import configuration, commands
from run_prettier_sampling_pair import inspect, read, write, SOURCE, SOURCE_ID
from isolated_storage import GIB, available, capacity_budget
from prettier_no_proxy import clean_environment
from run_live_child_calibration import release_empty_slice

ART = ROOT/'.artifacts'
MEASURED = ART/'storage-isolated-service/20261121T150333Z-1693930'
REF = ART/'storage-isolated-service/20261107T084731Z-3799106/prettier6604'
SERVER = ART/'light-restore-pid-build-20261117T083141Z/server'
KERNEL = ART/'agentenv-cache-kernel-20261015-v3/vmlinux.bin'
FLAGS = ['--schedule-smoke-metrics','--schedule-sampling-control']

def verify(storage):
    inspect(storage,'off')
    raw=storage/'prettier6604'
    task=read(raw/'result.json')
    outer=read(storage/'result.json')
    old=read(MEASURED/'result.json')
    assert outer['diagnostic_guest_kernel']['sha256']==old['diagnostic_guest_kernel']['sha256']
    assert not any((raw/p).exists() for p in ['action-points','light-memory.jsonl','live-cache'])
    paths=read(REF/'frozen-input/rollouts.json')
    events=read(raw/'events.json')
    rows=[]
    for role,path in paths.items():
        patch=Path('rollouts')/role/path[-1]/'patch.diff'
        data=(raw/patch).read_bytes()
        assert data==(REF/patch).read_bytes(), role+' patch mismatch'
        def official(directory):
            result=read(directory/f'official-result-{role}.json')
            result['summary'].pop('startTime',None)
            return result
        assert official(raw)==official(REF),role+' official mismatch'
        def event(name):
            found=[e for e in events if e['stage'].split('-',1)[1]==name+'-'+role]
            assert len(found)==1
            return found[0]
        begin=event('cold')['start_monotonic_ns']
        last=event('official')
        rows.append(dict(role=role,cold_start_ns=begin,official_end_ns=last['start_monotonic_ns']+last['duration_ns'],
                         sha256=hashlib.sha256(data).hexdigest(),patch_equal=True,official_equal=True,passed=0,failed=2))
    return dict(storage=storage.name,roles=rows,start_ns=task['rollout_start_monotonic_ns'],end_ns=task['rollout_end_monotonic_ns'])

def one(out,unit):
    parent=Path('/sys/fs/cgroup')/(unit+'.slice')
    assert parent.name in Path('/proc/self/cgroup').read_text()
    env=clean_environment(dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE,ASB_VMM_CHILD_CGROUP_CHECK=str(out),
        ASB_VMM_CGROUP_PARENT=str(parent),ASB_VMM_ACCOUNTING_ONLY='on',ASB_VMM_REQUIRE_IO='on',
        ASB_ISOLATED_UNUSED_NETWORK_POOLS='on',PYTHONDONTWRITEBYTECODE='1'))
    for key in ('ASB_GRPO_COLD_ACTIONS','ASB_EXPERIMENT_MEMORY_SERIES','ASB_LOCAL_NO_PROXY','ASB_LOCAL_NO_PROXY_CHECK'):
        env.pop(key,None)
    def call(argv,label,timeout):
        with (out/(label+'.log')).open('x') as log:
            subprocess.run(argv,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    cache=ART/'prettier6604'/('control-cache-'+out.name)
    assert not cache.exists()
    check,live=commands(configuration(ROOT/'configs/platform-prettier6604-grpo-off.json'),cache)
    budget=capacity_budget(available(ROOT),32*GIB)
    write(out/'budget.json',budget);assert budget['ready']
    source=read_docker()
    assert source['Id']==SOURCE_ID and not source['State']['Running'] and source['HostConfig']['RestartPolicy']['Name']=='no'
    call(check+FLAGS,'offline',60)
    call(['gcc','-O2','-Wall','-Wextra','scripts/vmm_cgroup_launcher.c','-o',out/'vmm-cgroup-launcher'],'compile',30)
    started=False
    try:
        prep=[sys.executable,'-B','scripts/prepare_prettier6604_cache.py',cache]
        call(prep,'prepare',300)
        call(['docker','start',SOURCE],'start',30);started=True
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            time.sleep(1)
        else:raise RuntimeError('source health timeout')
        assert capacity_budget(available(ROOT),16*GIB)['ready']
        assert int(re.search(r'MemAvailable:\s*(\d+)',Path('/proc/meminfo').read_text())[1])*1024>64*GIB
        call(live+FLAGS+['--diagnostic-server-binary',str(SERVER),'--diagnostic-guest-kernel',str(KERNEL)],'live',1100)
        [storage]=[Path(s) for s in re.findall(r'status=PASS result_dir=(\S+)',(out/'live.log').read_text())
                   if Path(s).parent==ART/'storage-isolated-service']
        write(out/'verified.json',verify(storage))
    finally:
        if started:
            call(['docker','stop','-t','30',SOURCE],'source-stop',60)
            assert not read_docker()['State']['Running']
        if (cache/'resources.json').exists():
            call(prep+['--release'],'release',90)
            assert read(cache/'resources.json')['released']
        for g in parent.glob('vmm-*'):
            assert not (g/'cgroup.procs').read_text().strip()
            g.rmdir()

def read_docker():
    return json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=15))[0]

if __name__=='__main__':
    if len(sys.argv)==3:
        one(Path(sys.argv[1]),sys.argv[2])
    else:
        stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
        for i in range(1,4):
            unit='asbvmmcontrol'+stamp.lower()+str(i)
            out=ART/f'grpo-control-{stamp}-{i}';out.mkdir()
            print(out,flush=True)
            try:
                subprocess.run(['systemd-run','--quiet','--wait','--pipe','--collect','--unit='+unit+'control',
                    '--slice='+unit+'.slice','--property=WorkingDirectory='+str(ROOT),'--property=MemoryAccounting=yes',
                    '--property=IOAccounting=yes',sys.executable,'-B',__file__,str(out),unit],
                    check=True,env=clean_environment(os.environ),timeout=1550)
            finally:
                write(out/'slice-cleanup.json',release_empty_slice(Path('/sys/fs/cgroup')/(unit+'.slice')))
