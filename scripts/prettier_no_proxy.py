"""Explicit pinned task local caches; no registry or proxy fallback."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
FLAG='ASB_LOCAL_NO_PROXY'
PROXY_KEYS=('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')
IMAGE='ghcr.io/timesler/swe-polybench.eval.x86_64.prettier__prettier-6604@sha256:8159d38fcbd6d5f09402d93d3e15cf2a891058ddd835a224f1d6b3357a87cc94'
REPOSITORY=IMAGE.split('@')[0]
DIGEST=IMAGE.split('@')[1]
CONFIG_DIGEST='sha256:973298a5771926f902291e2afbd3e88972c465a18e47b1b45743a4d1434863b2'
MANIFEST=ROOT/'.artifacts/prettier6604/20261008T082850Z/registry-check/registry-manifest.json'
PRETTIER_TASK='prettier__prettier-6604'
SPHINX_TASK='sphinx-doc__sphinx-7590'
SPHINX_IMAGE='ghcr.io/epoch-research/swe-bench.eval.x86_64.sphinx-doc__sphinx-7590@sha256:577ebfcce825e4c9621e066dc1c8375443be94bbc6de30b9368e422b8f123731'
SPHINX_DIGEST=SPHINX_IMAGE.split('@')[1]
SPHINX_MANIFEST=ROOT/'.artifacts/sphinx-backbone/prepare-20261009T041720Z/image-manifest.json'
XARRAY_TASK='pydata__xarray-6992'
XARRAY_IMAGE='ghcr.io/epoch-research/swe-bench.eval.x86_64.pydata__xarray-6992@sha256:c05101ef7105599eca1b7d15279a2928ac5453876cd2b83008deb9165d752655'
XARRAY_DIGEST=XARRAY_IMAGE.split('@')[1]
XARRAY_MANIFEST=ROOT/'.artifacts/python-backbone/20261008T153524Z/registry-manifest.json'


def profile(task_id=None):
    if task_id in (None,PRETTIER_TASK):
        return dict(image=IMAGE,digest=DIGEST,layers=19,manifest=MANIFEST,config_body=True,version_file='regctl')
    if task_id==SPHINX_TASK:
        return dict(image=SPHINX_IMAGE,digest=SPHINX_DIGEST,layers=13,manifest=SPHINX_MANIFEST,config_body=False,version_file='regctl.real')
    if task_id==XARRAY_TASK:
        return dict(image=XARRAY_IMAGE,digest=XARRAY_DIGEST,layers=13,manifest=XARRAY_MANIFEST,config_body=False,version_file='regctl.real')
    raise ValueError('local-no-proxy supports only pinned Prettier, Sphinx and Xarray tasks')


def clean_environment(value):return {k:v for k,v in value.items() if k not in PROXY_KEYS}
def container_environment():return sum((['--env',key+'='] for key in PROXY_KEYS),[])+['--env','NO_PROXY=*','--env','no_proxy=*']


def network_rules(text):
    """Check AgentENV's deny-egress policy, including its explicit DNS exception."""
    chains={name:[] for name in ('FORWARD','AGENTENV-EGRESS','AGENTENV-USER-EGRESS')}
    for line in text.splitlines():
        parts=shlex.split(line)
        if len(parts)>2 and parts[0]=='-A' and parts[1] in chains:chains[parts[1]].append(parts[2:])
    def fields(rule):
        if len(rule)%2 or any(not v.startswith('-') for v in rule[::2]):raise ValueError('unsupported network rule')
        value=dict(zip(rule[::2],rule[1::2]))
        if len(value)*2!=len(rule):raise ValueError('duplicate network rule option')
        return value
    forward={'-i':'tap0','-o':'vpeer','-j':'AGENTENV-EGRESS'}
    if not chains['FORWARD'] or fields(chains['FORWARD'][0])!=forward:
        raise ValueError('guest policy must be first forwarding rule')
    user=chains['AGENTENV-USER-EGRESS']
    if len(user)!=1:raise ValueError('expected one deny-all guest user rule')
    rule=fields(user[0]);rule.setdefault('-d','0.0.0.0/0')
    if rule.get('--reject-with')=='icmp-port-unreachable':rule.pop('--reject-with')
    if rule!={'-i':'tap0','-o':'vpeer','-d':'0.0.0.0/0','-j':'REJECT'}:
        raise ValueError('guest user policy is not deny-all')
    egress=chains['AGENTENV-EGRESS']
    prefix={'-i':'tap0','-o':'vpeer'}
    if not egress or fields(egress[-1])!=dict(prefix,**{'-j':'AGENTENV-USER-EGRESS'}):
        raise ValueError('missing terminal user policy jump')
    dns=[]
    for tokens in egress[:-1]:
        rule=fields(tokens)
        if any(rule.pop(k,None)!=v for k,v in prefix.items()):raise ValueError('unexpected egress interfaces')
        if rule in ({'-m':'conntrack','--ctstate':state,'-j':'ACCEPT'} for state in ('RELATED,ESTABLISHED','ESTABLISHED,RELATED')):continue
        if rule.get('--reject-with')=='icmp-port-unreachable':rule.pop('--reject-with')
        if set(rule)=={'-d','-j'} and rule['-j']=='REJECT':continue
        # The backend selects one DNS server, with 8.8.8.8 as its fallback.
        if (set(rule)=={'-d','-p','-m','--dport','-j'} and rule['-p'] in ('udp','tcp') and
                rule['-m']==rule['-p'] and rule['--dport']=='53' and rule['-j']=='ACCEPT'):
            dest=ipaddress.ip_network(rule['-d'])
            if dest.version==4 and dest.prefixlen==32 and not (dest.network_address.is_loopback or dest.network_address.is_unspecified):
                dns.append((str(dest.network_address),rule['-p']));continue
        raise ValueError('unexpected allowance before guest user policy')
    if len(dns)!=2 or len({ip for ip,protocol in dns})!=1 or {protocol for ip,protocol in dns}!={'udp','tcp'}:
        raise ValueError('expected one DNS address with exactly UDP and TCP port 53 rules')
    address=dns[0][0]
    return dict(status='PASS',policy='AgentENV default deny with DNS exception',
        ipv4_new_egress='REJECT except recorded DNS',rules=chains,
        dns_exception=dict(address=address,port=53,protocols=['tcp','udp'],public=ipaddress.ip_address(address).is_global),
        complete_egress_isolation=False,
        traffic_probe='NOT_SENT',scope='Forwarded guest IPv4 traffic in the owned network namespace')


def check_network(container_id,before,call):
    from probe_storage_boundary import identities
    owned=[v for v in identities() if v not in before and container_id in v['cgroup']]
    if len(owned)!=1:raise ValueError('expected exactly one owned new Firecracker')
    vm=owned[0];ns=Path('/proc')/str(vm['pid'])/'ns/net'
    identity=os.readlink(ns)
    if identity==os.readlink('/proc/self/ns/net'):raise ValueError('guest VMM is in host network namespace')
    text=call('local-no-proxy-network-rules',['nsenter','-t',str(vm['pid']),'-n','--','iptables-nft-save','-t','filter'],15)
    result=network_rules(text)
    if vm not in identities() or os.readlink(ns)!=identity:raise ValueError('guest identity changed during network query')
    return dict(result,vmm=vm,network_namespace=identity)


def require_mode(cfg,persistent):
    from host_persistent_input import local_grpo_config
    profile(cfg['task_id'])
    sampled=cfg.get('light_monitor') and cfg.get('monitor_mode','continuous')=='continuous'
    off=(cfg.get('host_file_monitor') and
        cfg.get('monitor_mode')=='off' and not cfg.get('light_monitor'))
    if not (persistent and cfg.get('host_current_only') and
            (sampled or off) and (cfg['rollout']=='single' or local_grpo_config(cfg)) and cfg['checkpoint_after_step'] is None and
            not cfg.get('damon_observe') and not cfg.get('damon_disable')):
        raise ValueError('local-no-proxy requires a persistent complete light current-only task or sampling-off control')
    if cfg['task_id']==SPHINX_TASK and cfg.get('replay_policy')!='sphinx-pipeline-v2':
        raise ValueError('local Sphinx requires the existing sphinx-pipeline-v2 policy')
    if cfg['task_id']==XARRAY_TASK and cfg.get('replay_policy')!='xarray-approved-sigpipe':
        raise ValueError('local Xarray requires the existing xarray-approved-sigpipe policy')


def metadata(directory,*,task_id=None):
    selected=profile(task_id)
    directory=Path(directory);raw=(directory/'manifest.json').read_bytes()
    # These small immutable registry responses must match the actual image pins.
    if 'sha256:'+hashlib.sha256(raw).hexdigest()!=selected['digest']:raise ValueError('fixed local manifest differs')
    manifest=json.loads(raw)
    if len(manifest['layers'])!=selected['layers']:raise ValueError('fixed local layer count differs')
    if not selected['config_body']:return manifest
    config=(directory/'config.json').read_bytes()
    if len(config)!=manifest['config']['size'] or 'sha256:'+hashlib.sha256(config).hexdigest()!=manifest['config']['digest']:
        raise ValueError('fixed local image config differs')
    cfg=json.loads(config)
    if cfg['architecture']!='amd64' or cfg['os']!='linux':raise ValueError('local image platform differs')
    return manifest


def prepare_metadata(directory,*,task_id=None):
    # Read only KB-scale public image metadata, never Docker layer bodies.
    directory=Path(directory)
    selected=profile(task_id)
    if not selected['config_body']:
        directory.mkdir();shutil.copy2(selected['manifest'],directory/'manifest.json')
        metadata(directory,task_id=task_id);return
    root=Path(subprocess.check_output(['docker','info','--format','{{.DockerRootDir}}'],text=True,timeout=10).strip())
    source=root/'image/overlay2/imagedb/content/sha256'/CONFIG_DIGEST[7:]
    directory.mkdir()
    shutil.copy2(MANIFEST,directory/'manifest.json');shutil.copy2(source,directory/'config.json')
    metadata(directory)


def check_cache(root,*,task_id=None):
    selected=profile(task_id)
    root=Path(root);stem='sha256-'+selected['digest'][7:]+'-image'
    image=root/'image-cache/configs'/(stem+'.json');saved=root/'image-cache/configs'/(stem+'.metadata.json')
    saved_value=json.loads(saved.read_text());value=json.loads(image.read_text())
    if not isinstance(saved_value,dict):raise ValueError('local image metadata object required')
    if value.get('repoBlobUrl') or len(value['lowers'])!=selected['layers']:raise ValueError('complete local lower stack required')
    paths=[]
    for lower in value['lowers']:
        if any(lower.get(k) for k in ('targetFile','gzipIndex','targetDigest')):raise ValueError('auxiliary remote lower unsupported in local mode')
        name=lower.get('file') or lower.get('dir')
        if not name or '..' in Path(name).parts or not Path(name).is_relative_to('/workspace/env/image-cache/commits'):raise ValueError('lower outside verified local commits')
        path=root/Path(name).relative_to('/workspace/env')
        if not lower.get('file'):
            path=next((path/n for n in ('overlaybd.commit','overlaybd.sealed') if (path/n).is_file()),path/'overlaybd.commit')
        if path.is_symlink() or not path.is_file() or path.stat().st_size==0:raise ValueError('missing local lower: '+str(path))
        if task_id in (SPHINX_TASK,XARRAY_TASK) and 'size' in lower and path.stat().st_size!=lower['size']:raise ValueError('local lower size differs')
        paths.append(str(path.relative_to(root)))
    tools=root/'deps/tools/0.1.1/image.json'
    if not tools.is_file():raise ValueError('missing local tools config')
    if len(set(paths))!=selected['layers']:raise ValueError('distinct local lowers required')
    return dict(status='PASS',image=selected['image'],lower_count=selected['layers'],local_files=paths,remote_fallback=False)


def wrapper(directory,config_digest,*,task_id=None):
    directory=Path(directory)
    selected=profile(task_id)
    body=('#!/bin/sh\nset -eu\n'
        'if [ "$#" -eq 1 ] && [ "$1" = version ]; then\n'
        '  echo version >> /workspace/env/logs/local-no-proxy-regctl.log\n  exec cat '+shlex.quote(str(directory/'version.txt'))+'\nfi\n'
        'if [ "$#" -eq 5 ] && [ "$1" = manifest ] && [ "$2" = get ] && [ "$3" = --format ] && [ "$4" = raw-body ] && [ "$5" = '+shlex.quote(selected['image'])+' ]; then\n'
        '  echo manifest >> /workspace/env/logs/local-no-proxy-regctl.log\n  exec cat '+shlex.quote(str(directory/'manifest.json'))+'\nfi\n')
    if selected['config_body']:
        body+=('if [ "$#" -eq 4 ] && [ "$1" = blob ] && [ "$2" = get ] && [ "$3" = '+shlex.quote(REPOSITORY)+' ] && [ "$4" = '+shlex.quote(config_digest)+' ]; then\n'
            '  echo config >> /workspace/env/logs/local-no-proxy-regctl.log\n  exec cat '+shlex.quote(str(directory/'config.json'))+'\nfi\n')
    return body+'echo blocked >> /workspace/env/logs/local-no-proxy-regctl.log\necho "local-no-proxy: registry command has no approved local input; remote fallback disabled" >&2\nexit 78\n'


def dependency_version(path):
    # The fixed restored input contains the original executable, not a wrapper.
    with Path(path).open('rb') as stream:
        if stream.read(4)!=b'\x7fELF':raise ValueError('fixed local regctl ELF binary required')
    value=subprocess.check_output([str(path),'version'],env=clean_environment(os.environ),text=True,timeout=10)
    if 'v0.11.5' not in re.split(r'''[\s:=,"']+''',value):raise ValueError('local regctl version differs')
    return value


def install(seed,mount,*,task_id=None,cache_root=None):
    selected=profile(task_id)
    seed=Path(seed);mount=Path(mount);manifest=metadata(seed/'local-no-proxy-metadata',task_id=task_id)
    result=check_cache(Path(cache_root) if cache_root is not None else seed/'input/env',task_id=task_id)
    path=mount/'env/deps/regctl/v0.11.5/regctl';saved=path.with_name('regctl.before-local-no-proxy')
    if not path.is_file() or path.is_symlink() or saved.exists():raise ValueError('private regctl entrypoint required')
    version_path=path.with_name(selected['version_file'])
    if version_path.is_symlink() or not version_path.is_file():raise ValueError('missing original local regctl binary')
    version=dependency_version(version_path)
    target=mount/'env/local-no-proxy';shutil.copytree(seed/'local-no-proxy-metadata',target)
    (target/'version.txt').write_text(version)
    path.rename(saved);path.write_text(wrapper(Path('/workspace/env/local-no-proxy'),manifest['config']['digest'],task_id=task_id));path.chmod(0o755)
    return dict(result,registry_metadata='fixed manifest and config only' if selected['config_body'] else 'fixed manifest only; cached metadata required',dependency_version='v0.11.5; local original ELF output',
        guest_internet=False,guest_internet_scope='API allowInternetAccess request only; DNS exception remains')
