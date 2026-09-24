"""Small ownership/configuration wrapper around the senior repository's Exp1."""
import csv,datetime,hashlib,json,os,pathlib,subprocess,sys,tomllib
root=pathlib.Path(__file__).resolve().parent.parent
cfg=json.loads(pathlib.Path(sys.argv[1]).read_text())
repo=(root/cfg['repository']).resolve()
server=root/cfg['server_result']
output=(root/cfg['output_root']).resolve()
run_id=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid())
audit=output/'audit'/run_id
os.umask(0o077);audit.mkdir(parents=True)
raw=output/'raw'/run_id
checks={};rc=1

def capture(args):
 return subprocess.run(args,capture_output=True,text=True,check=True,timeout=15).stdout
try:
 cid=(server/'container-id.txt').read_text().strip()
 info=json.loads(capture(['docker','inspect',cid]))[0]
 assert info['State']['Running'] and info['Config']['Labels'].get('agentenv.experiment')=='initial-smoke'
 cred=tomllib.loads((root/'runtime/config/aenv/credentials').read_text())
 assert cred['url']=='http://'+(server/'address.txt').read_text().strip()
 workload=repo/'motivation/experiments/exp1-single-app-smoke/workloads/prettier-14400'
 rows=[x.split('\t',2) for x in (workload/'actions.tsv').read_text().splitlines()]
 assert [r[0] for r in rows]==[f'{i:03}' for i in range(1,27)]
 for step,digest,_ in rows:assert hashlib.sha256((workload/'actions'/f'{step}.sh').read_bytes()).hexdigest()==digest
 checks['input_hashes']='PASS'
 (audit/'effective-config.json').write_text(json.dumps(dict(cfg,container=cid,server_image_id=info['Image'],run_id=run_id,cwd='/testbed',session_semantics='one bash per frozen action',cache_policy='preserve; no host/guest drop_caches',measurement='diagnostic; overhead not validated',vmm_scope='largest RSS Firecracker within selected service cgroup; includes pool'),indent=2)+'\n')
 for name,args in {'repository-version':['git','-C',str(repo),'rev-parse','HEAD'],'submodules':['git','-C',str(repo),'submodule','status'],'local-changes':['git','-C',str(repo),'diff'],'server-config':['docker','exec',cid,'cat','/workspace/config/default.toml']}.items():
  (audit/(name+'.txt')).write_text(capture(args))
 env=dict(os.environ,PATH=str(root/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(root/'runtime/config'),AGENTENV_CONTAINER=cid,OUTPUT_ROOT=str(output),RUN_ID=run_id,DROP_GUEST_CACHES=str(cfg['drop_guest_caches']),SANDBOX_CPU=str(cfg['sandbox_cpu']),SANDBOX_MEMORY_MIB=str(cfg['sandbox_memory_mib']),MEMORY_SAMPLE_INTERVAL_SECONDS=str(cfg['sample_interval_seconds']),ACTION_TIMEOUT_SECONDS=str(cfg['action_timeout_seconds']))
 runner=repo/'motivation/experiments/exp1-single-app-smoke/systems/agentenv/run.sh'
 print(f'status=RUNNING run_id={run_id} result_dir={raw}',flush=True)
 with (audit/'controller.log').open('w') as log:
  p=subprocess.Popen(['bash',str(runner)],env=env,cwd=repo,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  try: rc=p.wait(timeout=600)
  except subprocess.TimeoutExpired:
   import signal
   os.killpg(p.pid,signal.SIGTERM)
   try:p.wait(timeout=70)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
   rc=124
 checks['runner']='PASS' if rc==0 else 'FAIL'
 if rc==0:
  with (raw/'replay/steps.tsv').open() as f:steps=list(csv.DictReader(f,delimiter='\t'))
  assert [x['step'] for x in steps]==[x[0] for x in rows]
  failures={x['step']:int(x['exit_code']) for x in steps if int(x['exit_code'])}
  assert failures=={'008':127,'016':127},failures
  assert (raw/'oracle-exit-code.txt').read_text().strip()=='0'
  patch=raw/'replay/patch.diff';assert patch.stat().st_size>0
  (audit/'correctness.json').write_text(json.dumps({'action_order':'PASS','expected_failures':failures,'oracle':'PASS','patch_sha256':hashlib.sha256(patch.read_bytes()).hexdigest(),'patch_reference_match':'NOT_VERIFIED; historical raw absent','independent_task_tests':'NOT_RUN'},indent=2)+'\n')
  with (raw/'host/memory-samples.tsv').open() as f:host=list(csv.DictReader(f,delimiter='\t'))
  assert host, 'no host samples'
  checks['replay_correctness']='PASS'
  checks['host_sampling']='PASS'
  listing=json.loads(subprocess.run([str(root/'runtime/bin/aenv'),'list','--output','json'],env=env,capture_output=True,text=True,timeout=15,check=True).stdout)
  sid=(raw/'sandbox-id.txt').read_text().strip()
  assert not any(x['sandboxID']==sid for x in listing)
  checks['cleanup']='PASS'
except Exception:
 import traceback
 (audit/'failure.txt').write_text(traceback.format_exc());rc=1
finally:
 (audit/'checks.json').write_text(json.dumps(checks,indent=2)+'\n')
 (audit/'result.json').write_text(json.dumps({'rc':rc,'raw_dir':str(raw),'status':'PASS' if rc==0 else 'FAIL'},indent=2)+'\n')
 print(f'status={"PASS" if rc==0 else "FAIL"} run_id={run_id} result_dir={raw} audit_dir={audit}',flush=True)
sys.exit(rc)
