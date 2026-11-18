"""Owned guest relay using the pinned upstream implementation and a temporary address."""
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit


def upstream_port(environment):
    value=urlsplit(environment.get('HTTP_PROXY') or environment.get('http_proxy',''))
    if (value.scheme!='http' or value.hostname!='127.0.0.1' or not value.port
            or value.username or value.password or value.path not in ('','/') or value.query or value.fragment):
        raise ValueError('Cython relay requires the existing unauthenticated loopback HTTP proxy')
    return value.port


class GuestProxy:
    def __init__(self,output,upstream,proxy_url=None):
        self.output=Path(output);self.upstream=Path(upstream);self.process=None;self.address=None
        self.proxy_url=proxy_url

    def start(self):
        port=upstream_port(dict(HTTP_PROXY=self.proxy_url) if self.proxy_url else os.environ)
        rows=json.loads(subprocess.check_output(['ip','-j','-4','addr','show'],text=True,timeout=10))
        routes=json.loads(subprocess.check_output(['ip','-j','-4','route','show','table','all'],text=True,timeout=10))
        used=[ipaddress.ip_network(a['local']+'/'+str(a['prefixlen']),strict=False) for row in rows for a in row['addr_info'] if a['family']=='inet']
        used += [ipaddress.ip_network(r['dst'],strict=False) for r in routes if r.get('dst') not in (None,'default','0.0.0.0/0')]
        choices=[ipaddress.ip_address(v) for v in ('192.0.2.17','198.51.100.17','203.0.113.17')]
        address=next((v for v in choices if not any(v in network for network in used)),None)
        if address is None:raise ValueError('no unused documented guest relay address')
        subprocess.run(['ip','addr','add',str(address)+'/32','dev','lo'],check=True,timeout=10,capture_output=True)
        self.address=str(address)
        (self.output/'guest-proxy-address.json').write_text(json.dumps(dict(address=self.address,prefix=32,interface='lo',status='ADDED'))+'\n')
        with socket.socket() as sock:
            sock.bind((str(address),0));listen_port=sock.getsockname()[1]
        script=self.output/'guest-proxy.py'
        shutil.copy2(self.upstream/'scripts/agentenv_workload_proxy.py',script)
        if os.environ.get('ASB_ISOLATED_UNUSED_NETWORK_POOLS')=='on':
            # Adapt only the owned copy, retaining the original relay implementation.
            from isolated_storage import rootfs_network_pools
            pools=rootfs_network_pools(json.dumps(routes))
            text=script.read_text()
            for old,new in zip(('10.211.0.0/16','10.212.0.0/16'),pools):
                literal="ipaddress.ip_network('"+old+"')"
                if text.count(literal)!=1:raise ValueError('upstream relay source-pool declaration changed')
                text=text.replace(literal,"ipaddress.ip_network('"+new+"')")
            script.write_text(text)
            (self.output/'guest-proxy-network.json').write_text(json.dumps(dict(pools=pools,scope='Owned relay copy only; original upstream unchanged'))+'\n')
        ready=self.output/'guest-proxy-ready.json'
        with (self.output/'guest-proxy.log').open('x') as log:
            self.process=subprocess.Popen([sys.executable,'-B',str(script),'--address',str(address),
                '--port',str(listen_port),'--upstream-port',str(port),'--ready',str(ready)],stdout=log,stderr=log)
        deadline=time.monotonic()+5
        while not ready.exists():
            if self.process.poll() is not None:raise RuntimeError('owned guest relay failed to start')
            if time.monotonic()>deadline:raise TimeoutError('owned guest relay readiness')
            time.sleep(.05)
        receipt=json.loads(ready.read_text())
        assert receipt['pid']==self.process.pid and receipt['address']==str(address) and receipt['port']==listen_port
        return f'http://{address}:{listen_port}'

    def check(self):
        if self.process is None or self.process.poll() is not None:raise RuntimeError('owned guest relay is not running')

    def stop(self):
        try:
            remove_guest_relay_rule(self.output)
        finally:
            self._stop_process_and_address()

    def _stop_process_and_address(self):
        try:
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill();self.process.wait(timeout=5)
                self.process=None
        finally:
            if self.address is not None:
                subprocess.run(['ip','addr','del',self.address+'/32','dev','lo'],check=True,timeout=10,capture_output=True)
                (self.output/'guest-proxy-address.json').write_text(json.dumps(dict(address=self.address,prefix=32,interface='lo',status='REMOVED'))+'\n')
                self.address=None


def relay_rule(output, receipt):
    """Rebuild only the rule belonging to this relay; never execute saved arbitrary arguments."""
    output=Path(output);ready=json.loads((output/'guest-proxy-ready.json').read_text())
    if receipt.get('backend')!='nft':raise ValueError('relay rule must use the private service nft backend')
    source=ipaddress.IPv4Address(receipt['source'])
    source_pool=receipt.get('source_pool','10.211.0.0/16')
    if source_pool not in ('10.211.0.0/16','10.213.0.0/16','10.215.0.0/16'):
        raise ValueError('unknown private experiment pool')
    if source not in ipaddress.ip_network(source_pool):
        raise ValueError('relay source outside the private experiment pool')
    interface=receipt['interface']
    if not re.fullmatch(r'veth-[A-Za-z0-9_-]{1,10}',interface):raise ValueError('unexpected guest route interface')
    if receipt['destination']!=ready['address'] or receipt['port']!=ready['port']:
        raise ValueError('rule does not belong to this relay')
    return ['-i',interface,'-s',str(source)+'/32','-d',ready['address']+'/32',
        '-p','tcp','-m','tcp','--dport',str(ready['port']),'-m','comment','--comment',
        'asb-relay-'+output.name,'-j','ACCEPT']


def rule_present(rule):
    result=subprocess.run(['iptables-nft','-w','5','-C','INPUT',*rule],capture_output=True,timeout=10)
    if result.returncode not in (0,1):raise RuntimeError('cannot check owned relay INPUT rule')
    return result.returncode==0


def allow_guest_relay(output, sandbox, *, source_pool='10.211.0.0/16'):
    """Install the approved single-guest exception; the outer relay owns its cleanup."""
    output=Path(output);path=output/'guest-proxy-rule.json'
    if path.exists():raise ValueError('relay rule receipt already exists')
    if source_pool not in ('10.211.0.0/16','10.213.0.0/16','10.215.0.0/16'):
        raise ValueError('unknown private experiment pool')
    rows=json.loads(subprocess.check_output(['ip','-j','-4','route','show'],text=True,timeout=10))
    candidates=[r for r in rows if r.get('dst') not in (None,'default')
        and ipaddress.ip_network(r['dst'],strict=False).subnet_of(ipaddress.ip_network(source_pool))]
    if len(candidates)!=1 or ipaddress.ip_network(candidates[0]['dst']).prefixlen!=32:
        raise ValueError('expected exactly one actual guest route in the private experiment pool')
    ready=json.loads((output/'guest-proxy-ready.json').read_text())
    receipt=dict(status='PLANNED',backend='nft',sandbox_id=sandbox,source_pool=source_pool,source=str(ipaddress.ip_network(candidates[0]['dst']).network_address),
        interface=candidates[0]['dev'],destination=ready['address'],port=ready['port'])
    rule=relay_rule(output,receipt)
    if rule_present(rule):raise ValueError('refusing to adopt an existing relay rule')
    # Save ownership before mutation so the outer finally can recover an interrupted add.
    path.write_text(json.dumps(receipt,indent=2)+'\n')
    subprocess.run(['iptables-nft','-w','5','-I','INPUT','1',*rule],check=True,capture_output=True,timeout=10)
    if not rule_present(rule):raise RuntimeError('relay rule insertion not effective')
    receipt['status']='ADDED';path.write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def capture_relay_counters(output, stage):
    output=Path(output);receipt=json.loads((output/'guest-proxy-rule.json').read_text())
    text=subprocess.check_output(['iptables-nft-save','-c','-t','filter'],text=True,timeout=10)
    network=ipaddress.ip_network(receipt.get('source_pool','10.211.0.0/16'))
    prefix='.'.join(str(network.network_address).split('.')[:2])+'.'
    lines=[line for line in text.splitlines() if '-A INPUT ' in line and
        ('asb-relay-'+output.name in line or re.search(r'-s '+re.escape(prefix),line))]
    own=[line for line in lines if 'asb-relay-'+output.name in line]
    packets=int(re.match(r'\[(\d+):',own[0]).group(1)) if len(own)==1 else None
    snapshot=dict(monotonic_ns=time.monotonic_ns(),backend='nft',source=receipt['source'],rules=lines,allowed_packets=packets)
    (output/('guest-proxy-counters-'+stage+'.json')).write_text(json.dumps(snapshot,indent=2)+'\n')
    return snapshot


def remove_guest_relay_rule(output):
    output=Path(output);path=output/'guest-proxy-rule.json'
    if not path.exists():return
    receipt=json.loads(path.read_text())
    if receipt['status']=='REMOVED':return
    if receipt['status'] not in ('PLANNED','ADDED'):raise ValueError('unknown relay rule ownership')
    rule=relay_rule(output,receipt)
    capture_relay_counters(output,'cleanup')
    if rule_present(rule):
        subprocess.run(['iptables-nft','-w','5','-D','INPUT',*rule],check=True,capture_output=True,timeout=10)
    if rule_present(rule):raise RuntimeError('owned relay INPUT rule remains')
    receipt['status']='REMOVED';path.write_text(json.dumps(receipt,indent=2)+'\n')


def environment_script(proxy):
    value=urlsplit(proxy)
    if value.scheme!='http' or not value.hostname or value.username or value.password or not value.port:
        raise ValueError('owned relay URL required')
    address=ipaddress.ip_address(value.hostname)
    if not address.is_private or address.is_loopback:raise ValueError('guest relay cannot use loopback or a public address')
    url=f'http://{address}:{value.port}'
    return '\n'.join([*[f'export {key}={url}' for key in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy')],
        'export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost',
        'export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.version GIT_CONFIG_VALUE_0=HTTP/1.1',
        'export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false',
        'export PAGER=cat MANPAGER=cat LESS=-R PIP_PROGRESS_BAR=off TQDM_DISABLE=1',''])


def direct_environment_script():
    """Same original task environment, with explicit empty proxy variables."""
    return '\n'.join(['export HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= http_proxy= https_proxy= all_proxy=',
        'export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost',
        'export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.version GIT_CONFIG_VALUE_0=HTTP/1.1',
        'export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false',
        'export PAGER=cat MANPAGER=cat LESS=-R PIP_PROGRESS_BAR=off TQDM_DISABLE=1',''])


NETWORK_CHECK='''source /probe/input/guest-env.sh && python3 - <<'PYNET'
import sys
import urllib.request
for url in ('https://github.com/SPOCKnots/pyknotid', 'https://pypi.org/simple/pip/'):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=20) as response:
            assert 200 <= response.status < 400
    except Exception:
        print('guest-network-failed url='+url, file=sys.stderr, flush=True)
        raise
print('guest-network-ready')
PYNET'''
