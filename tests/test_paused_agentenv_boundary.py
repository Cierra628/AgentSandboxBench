import http.server
import json
from pathlib import Path
import socketserver
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from paused_agentenv_boundary import PausedBoundary
from trenvx_whole_cache import RECORD


class UnixServer(socketserver.UnixStreamServer):
    pass


class PausedBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.proc=self.root/'proc';(self.proc/'root/owned').mkdir(parents=True)
        self.directory=self.root/'result';self.directory.mkdir()
        self.vm=dict(sandbox_id='owned');self.proof=dict(slots=[dict(guest=0,host=0,size=4096)])
        (self.directory/'marker.json').write_text(json.dumps(dict(sandbox_id='owned',heartbeat_offset=128,pfn=0,text='marker')))
        (self.proc/'cmdline').write_bytes(b'firecracker\0--api-sock\0/owned/firecracker.socket\0')
        self.state='Running'
        parent=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):
                assert self.path in ('/','/api/v1/vm.info')
                value=json.dumps(dict(state=parent.state)).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(value)));self.end_headers();self.wfile.write(value)
            def do_PATCH(self):
                assert self.path=='/vm'
                value=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                assert value['state'] in ('Paused','Resumed')
                parent.state='Paused' if value['state']=='Paused' else 'Running'
                self.send_response(204);self.end_headers()
            def do_PUT(self):
                assert self.path in ('/api/v1/vm.pause','/api/v1/vm.resume')
                parent.state='Paused' if self.path.endswith('pause') else 'Running'
                self.send_response(204);self.end_headers()
        self.server=UnixServer(str(self.proc/'root/owned/firecracker.socket'),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start()
        self.mock=patch('paused_agentenv_boundary.identity',return_value=self.proc);self.mock.start()
        self.boundary=PausedBoundary(self.directory,self.vm,self.proof,'/owned')
        self.count=1
        def heartbeat():
            if self.state=='Running':self.count+=1
            return self.count
        self.boundary.heartbeat=heartbeat
    def tearDown(self):
        self.mock.stop();self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def test_unix_api_pause_and_original_heartbeat_resume(self):
        self.boundary.pause();self.assertEqual(self.state,'Paused')
        self.assertEqual(*self.boundary.receipt['paused_counter'])
        self.boundary.resume();self.assertEqual(self.state,'Running')
        a,b=self.boundary.receipt['resumed_counter'];self.assertGreater(b,a)

    def cloud_boundary(self):
        (self.proc/'root/tmp').mkdir()
        (self.proc/'root/owned/firecracker.socket').rename(self.proc/'root/tmp/vmm-owned.socket')
        (self.proc/'cmdline').write_bytes(b'cloud-hypervisor\0--api-socket\0/tmp/vmm-owned.socket\0')
        vm=dict(self.vm,api_socket='/tmp/vmm-owned.socket')
        boundary=PausedBoundary(self.directory,vm,self.proof,'/tmp',backend='trenvx')
        boundary.heartbeat=self.boundary.heartbeat
        return boundary

    def test_cloud_api_pause_and_resume_original_guest(self):
        boundary=self.cloud_boundary();boundary.pause();boundary.resume()
        self.assertEqual(self.state,'Running')
        self.assertEqual(boundary.receipt['original_vmm_identity'],'PASS')

    def test_cloud_nonresident_pte_uses_verified_snapshot_backing(self):
        boundary=self.cloud_boundary()
        (self.directory/'ram-pages.bin').write_bytes(RECORD.pack(0,0,0))
        (self.proc/'pagemap').write_bytes(struct.pack('<Q',0))
        item=dict(index=0,role='vm-memory-snapshot',regions=[dict(start=0,end=4096,offset=0,permissions='rw-p')])
        (self.directory/'host-scan.json').write_text(json.dumps(dict(backing_files=[item])))
        (self.directory/'backing-0-before.bin').write_bytes(b'\1')
        (self.directory/'backing-0-after.bin').write_bytes(b'\1')
        entry=(1<<63)|(1<<61)|100
        (self.directory/'backing-0-pages.bin').write_bytes(RECORD.pack(0,entry,entry))
        boundary.pause();boundary.snapshot_recheck();boundary.resume()
        self.assertEqual(boundary.receipt['measurement_status'],'PASS')
        self.assertEqual(boundary.receipt['frozen_guest_snapshot_fallback_pages'],1)
        self.assertEqual(boundary.receipt['frozen_guest_selected_physical_bytes'],4096)

    def test_cloud_changed_snapshot_backing_remains_unknown(self):
        boundary=self.cloud_boundary()
        (self.directory/'ram-pages.bin').write_bytes(RECORD.pack(0,0,0))
        (self.proc/'pagemap').write_bytes(struct.pack('<Q',0))
        item=dict(index=0,role='vm-memory-snapshot',regions=[dict(start=0,end=4096,offset=0,permissions='rw-p')])
        (self.directory/'host-scan.json').write_text(json.dumps(dict(backing_files=[item])))
        (self.directory/'backing-0-before.bin').write_bytes(b'\1')
        (self.directory/'backing-0-after.bin').write_bytes(b'\1')
        entry=(1<<63)|(1<<61)|100
        (self.directory/'backing-0-pages.bin').write_bytes(RECORD.pack(0,entry,entry+1))
        boundary.pause();boundary.snapshot_recheck();boundary.resume()
        self.assertEqual(boundary.receipt['measurement_status'],'PARTIAL')
        self.assertEqual(boundary.receipt['frozen_guest_selected_unknown_pages'],1)
        self.assertEqual(boundary.receipt['frozen_guest_selected_physical_bytes'],0)

    def test_socket_outside_attested_workdir_rejected(self):
        with self.assertRaises(AssertionError):PausedBoundary(self.directory,self.vm,self.proof,'/other')

    def sample(self,first,last,current):
        (self.directory/'before-ram.bin').write_bytes(RECORD.pack(0,first,last))
        (self.proc/'pagemap').write_bytes(struct.pack('<Q',current))
        self.boundary.pause()
        self.boundary.snapshot_recheck()
        self.boundary.resume()

    def test_three_reads_must_match_for_stable_count(self):
        self.sample((1<<63)|100,(1<<63)|101,(1<<63)|100)
        self.assertEqual(self.boundary.receipt['measurement_status'],'PARTIAL')
        self.assertEqual(self.boundary.receipt['frozen_guest_selected_unknown_pages'],1)
        self.assertEqual(self.boundary.receipt['frozen_guest_selected_physical_bytes'],0)

    def test_stable_selected_set_counts_once(self):
        self.sample((1<<63)|100,(1<<63)|100,(1<<63)|100)
        self.assertEqual(self.boundary.receipt['measurement_status'],'PASS')
        self.assertEqual(self.boundary.receipt['frozen_guest_selected_physical_bytes'],4096)

    def offline_inputs(self):
        self.sample((1<<63)|100,(1<<63)|100,(1<<63)|100)
        self.boundary.save(True)
        (self.directory/'before.json').write_text(json.dumps(dict(ram_ranges_pfns=[[0,1]])))
        (self.directory/'before-flags.bin').write_bytes(struct.pack('<Q',1<<5))
        (self.directory/'ram-attestation.json').write_text(json.dumps(self.proof))

    def test_offline_recount_matches_live_selected_bytes(self):
        from report_paused_cache_boundaries import frozen
        self.offline_inputs()
        result=frozen(self.directory,'agentenv')
        self.assertEqual(result['frozen_guest_selected_physical_bytes'],4096)
        self.assertEqual(result['offline_recount'],'PASS')

    def test_offline_recount_rejects_tampered_physical_total(self):
        from report_paused_cache_boundaries import frozen
        self.offline_inputs()
        path=self.directory/'paused-boundary.json';value=json.loads(path.read_text())
        value['frozen_guest_selected_physical_bytes']=8192;path.write_text(json.dumps(value))
        with self.assertRaises(AssertionError):frozen(self.directory,'agentenv')

    def test_paused_heartbeat_must_not_advance(self):
        self.boundary.heartbeat=lambda: __import__('time').monotonic_ns()
        try:
            with self.assertRaisesRegex(AssertionError,'while paused'):self.boundary.pause()
        finally:self.boundary.resume()


if __name__=='__main__':unittest.main()
