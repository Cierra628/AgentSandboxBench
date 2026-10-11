"""Pause one attested private VMM in place; prove heartbeat and RAM stability."""
import http.client
import json
import os
from pathlib import Path
import socket
import stat
import struct
import time

from measure_file_physical import identity, ram_address, PFN_MASK, PAGE
from trenvx_whole_cache import dump, records, same_physical_mapping, RECORD


class PausedBoundary:
    def __init__(self, directory, vm, proof, workdir, backend="agentenv"):
        self.directory, self.vm, self.proof = directory, vm, proof
        self.marker = json.loads((directory/'marker.json').read_text())
        assert self.marker['sandbox_id'] == vm['sandbox_id']
        assert self.marker['heartbeat_offset'] == 128
        proc = identity(vm, Path('/proc'), Path('/sys/fs/cgroup'))
        args = (proc/'cmdline').read_bytes().split(b'\0')
        assert backend in ('agentenv','trenvx')
        self.backend = backend
        flag = b'--api-sock' if backend=='agentenv' else b'--api-socket'
        target = args[args.index(flag)+1].decode()
        expected = workdir+'/firecracker.socket' if backend=='agentenv' else '/tmp/vmm-'+vm['sandbox_id']+'.socket'
        assert target == expected
        if backend=='trenvx':assert vm['api_socket']==expected
        self.socket = proc/'root'/target.lstrip('/')
        assert stat.S_ISSOCK(self.socket.stat().st_mode)
        self.paused = False
        self.started = None
        self.receipt = dict(status='FAIL', backend=backend, scope='same attested VMM, in-place pause; not persistent snapshot or rebuild')

    def api(self, method, target, body=None):
        identity(self.vm, Path('/proc'), Path('/sys/fs/cgroup'))
        connection = http.client.HTTPConnection('localhost', timeout=5)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect(str(self.socket)); connection.sock = sock
            connection.request(method, target, body=json.dumps(body) if body is not None else None,
                               headers={'Content-Type':'application/json'})
            response = connection.getresponse(); data = response.read()
            assert response.status == (200 if method=='GET' else 204), (method, target, response.status, data)
            return json.loads(data) if data else None
        finally:
            connection.close(); sock.close()

    def state(self):
        return self.api('GET','/' if self.backend=='agentenv' else '/api/v1/vm.info')['state']

    def set_state(self, paused):
        if self.backend=='agentenv':self.api('PATCH','/vm',dict(state='Paused' if paused else 'Resumed'))
        else:self.api('PUT','/api/v1/vm.pause' if paused else '/api/v1/vm.resume')

    def heartbeat(self):
        proc = identity(self.vm, Path('/proc'), Path('/sys/fs/cgroup'))
        address = ram_address(self.marker['pfn'], self.proof['slots'])
        with (proc/'mem').open('rb', buffering=0) as stream:
            assert os.pread(stream.fileno(),len(self.marker['text']),address)==self.marker['text'].encode()
            return struct.unpack('<Q',os.pread(stream.fileno(),8,address+self.marker['heartbeat_offset']))[0]

    def pause(self):
        assert self.state()=='Running'
        first = self.heartbeat(); time.sleep(.1); second = self.heartbeat()
        assert second>first,'guest heartbeat did not advance before pause'
        self.started = time.monotonic_ns(); self.paused = True
        self.set_state(True)
        assert self.state()=='Paused'
        a = self.heartbeat(); time.sleep(.2); b = self.heartbeat()
        assert a==b,'guest heartbeat advanced while paused'
        self.receipt.update(pause_interface_and_verification_ns=time.monotonic_ns()-self.started,
                            before_running_counter=[first,second], paused_counter=[a,b])

    def resume(self):
        if not self.paused:return
        start = time.monotonic_ns()
        if self.state()=='Paused':
            self.set_state(False)
        assert self.state()=='Running'
        a = self.heartbeat(); deadline=time.monotonic()+3
        while self.heartbeat()<=a:
            assert time.monotonic()<deadline,'guest heartbeat did not resume'
            time.sleep(.02)
        b = self.heartbeat(); self.paused=False
        self.receipt.update(resume_interface_and_heartbeat_ns=time.monotonic_ns()-start,
                            paused_interval_ns=time.monotonic_ns()-self.started,
                            resumed_counter=[a,b], original_vmm_identity='PASS', heartbeat_continuity='PASS')

    def snapshot_recheck(self):
        assert self.paused and self.state()=='Paused'
        rows = records(self.directory/('before-ram.bin' if self.backend=='agentenv' else 'ram-pages.bin'))
        proc = identity(self.vm,Path('/proc'),Path('/sys/fs/cgroup'))
        from measure_file_physical import read_entry
        with (proc/'pagemap').open('rb',buffering=0) as stream:
            new = [(pfn,first,read_entry(stream.fileno(),ram_address(pfn,self.proof['slots'])))
                   for pfn,first,_ in rows]
        (self.directory/'frozen-ram-recheck.bin').write_bytes(b''.join(RECORD.pack(*row) for row in new))
        original={pfn:last for pfn,_,last in rows}
        changed=sum(not same_physical_mapping(first,last) or not same_physical_mapping(first,original[pfn])
                    for pfn,first,last in new)
        unknown=0;fallback=0;pfns=set()
        missing=[pfn for pfn,first,last in new if not first&(1<<63) and same_physical_mapping(first,last)
                 and same_physical_mapping(first,original[pfn]) and not first&(1<<62)]
        backing={};regions=[]
        if missing and self.backend=='trenvx':
            scan=json.loads((self.directory/'host-scan.json').read_text())
            for item in scan['backing_files']:
                if item.get('role')!='vm-memory-snapshot':continue
                index=item['index'];before=(self.directory/f'backing-{index}-before.bin').read_bytes()
                after=(self.directory/f'backing-{index}-after.bin').read_bytes()
                stable={offset:first&PFN_MASK for offset,first,last in records(self.directory/f'backing-{index}-pages.bin')
                        if before[offset] and after[offset] and same_physical_mapping(first,last)}
                backing[index]=(stable,{i for i,(a,b) in enumerate(zip(before,after)) if not a and not b})
                regions.extend((index,region) for region in item['regions'])
        for pfn,first,last in new:
            if not same_physical_mapping(first,last) or not same_physical_mapping(first,original[pfn]) or first&(1<<62):
                unknown+=1;continue
            if first&(1<<63):
                assert first&PFN_MASK,'hidden resident PFN';pfns.add(first&PFN_MASK);continue
            if self.backend!='trenvx':unknown+=1;continue
            address=ram_address(pfn,self.proof['slots'])
            matches=[(index,(address-region['start']+region['offset'])//PAGE) for index,region in regions
                     if region['start']<=address<region['end'] and region['permissions']=='rw-p']
            assert len(matches)==1,'missing one owned private RAM snapshot mapping'
            index,offset=matches[0];stable,cold=backing[index]
            if offset in stable:pfns.add(stable[offset]);fallback+=1
            elif offset not in cold:unknown+=1
        assert self.heartbeat()==self.receipt['paused_counter'][0]
        self.receipt.update(frozen_host_mapping_changed_pages=changed,
            measurement_status='PASS' if changed==unknown==0 else 'PARTIAL',
            frozen_guest_snapshot_fallback_pages=fallback,
            frozen_guest_selected_unknown_pages=unknown,frozen_guest_selected_physical_bytes=len(pfns)*PAGE,
            frozen_scan_scope='same pre-pause guest file-LRU selection; host PTE rechecked while paused',
            limitation='guest flags were exported before pause; no new guest classification while paused; not atomic inode attribution')

    def save(self, successful):
        self.receipt['status']='PASS' if successful else 'FAIL'
        dump(self.directory/'paused-boundary.json',self.receipt)
