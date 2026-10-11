from pathlib import Path
import json
import ctypes
import io
import os
import subprocess
import socket
import struct
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from guest_git_wait import instrument, poll_descriptor, FINAL_BLOCK, identity_probe, audit_identity, download_identity_probe
from guest_runner_phases import instrument_baseline, BASELINE_BLOCK


class GitWaitTests(unittest.TestCase):
    def fixture(self,base,reset_failure=False):
        repo=base/'repo';repo.mkdir();bin_dir=base/'bin';bin_dir.mkdir()
        env=dict(os.environ,GIT_CONFIG_GLOBAL='/dev/null',GIT_CONFIG_SYSTEM='/dev/null',EMAIL='fixture@localhost')
        for argv in (['init','-q'],['-c','user.name=fixture','-c','user.email=fixture@localhost','commit','--allow-empty','-qm','fixture']):
            subprocess.run(['/usr/bin/git','-C',str(repo)]+argv,env=env,check=True,capture_output=True)
        wrapper=f'''#!{sys.executable}
import os,sys
if {reset_failure!r} and sys.argv[-2:]==['reset','--mixed']:
    print('reset failure',file=sys.stderr);sys.exit(7)
if sys.argv[-2:]==['var','GIT_COMMITTER_IDENT'] and not any('user.email=' in a for a in sys.argv):
    print('fatal: unable to auto-detect email address',file=sys.stderr);sys.exit(128)
os.execv('/usr/bin/git',['git',*sys.argv[1:]])
'''
        (bin_dir/'git').write_text(wrapper);(bin_dir/'git').chmod(0o755)
        subprocess.run(['gcc','-O2',str(ROOT/'scripts/command_clock.c'),'-o',str(base/'command-clock')],check=True)
        env['PATH']=str(bin_dir)+':'+env['PATH']
        return repo,env

    def test_identity_rejection_is_preserved_as_an_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);repo,env=self.fixture(base)
            with patch.dict(os.environ,env,clear=True):identity_probe(base,repo)
            result=audit_identity(base/'identity-probe')
            self.assertEqual(result['records'][4]['exit_code'],128)
            self.assertEqual(result['records'][4]['command_result'],'IDENTITY_UNAVAILABLE')
            self.assertEqual(len(result['records']),6)

    def test_unexpected_reset_exit_retains_partial_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);repo,env=self.fixture(base,reset_failure=True)
            with patch.dict(os.environ,env,clear=True),self.assertRaises(AssertionError):identity_probe(base,repo)
            result=json.loads((base/'identity-probe/result.json').read_text())
            self.assertEqual(result['status'],'FAIL')
            self.assertEqual(result['records'][0]['exit_code'],7)
            self.assertIn('reset failure',(base/'identity-probe/0-default.stderr').read_text())

    def test_poll_descriptor_identifies_owned_udp_socket(self):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.connect(('127.0.0.1',53))  # no packet is sent
            buffer=ctypes.create_string_buffer(struct.pack('ihh',sock.fileno(),1,0))
            observed=poll_descriptor(Path('/proc/self'),f'7 {ctypes.addressof(buffer):#x} 0x1 0x1388')
            self.assertEqual(observed['fd'],sock.fileno())
            self.assertEqual(observed['timeout_ms'],5000)
            self.assertEqual(observed['remote_port'],53)
            self.assertEqual(observed['protocol'],'udp')
            self.assertIsNone(poll_descriptor(Path('/proc/self'),'running'))

    def test_failure_and_stderr_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            source='set -euo pipefail\nout="$1"\ngit() { echo failed >&2; return 7; }\n'+BASELINE_BLOCK+'\n'+FINAL_BLOCK+'\n'
            runner=instrument(instrument_baseline(source))
            p=subprocess.run(['bash','-c',runner,'probe',str(out)],capture_output=True,text=True,
                env=dict(os.environ,ASB_GIT_WAIT_HELPER=str(ROOT/'scripts/guest_git_wait.py')),timeout=5)
            self.assertEqual((p.returncode,p.stdout,p.stderr),(7,'','failed\n'))
            self.assertFalse((out/'baseline-write_tree-time.tsv').exists())

    def test_reset_probe_and_final_timers(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            source='''set -euo pipefail
out="$1"
git() {
  if [[ $3 == reset ]]; then sleep .25; fi
  if [[ $3 == write-tree ]]; then echo tree; fi
}
'''+BASELINE_BLOCK+'\n'+FINAL_BLOCK+'\nprintf done\n'
            p=subprocess.run(['bash','-c',instrument(instrument_baseline(source)),'probe',str(out)],
                capture_output=True,text=True,timeout=5,
                env=dict(os.environ,ASB_GIT_WAIT_HELPER=str(ROOT/'scripts/guest_git_wait.py')))
            self.assertEqual((p.returncode,p.stdout,p.stderr),(0,'done',''))
            for prefix in ('baseline','final'):
                rows=[json.loads(s) for s in (out/f'{prefix}-reset-time.tsv.process.jsonl').read_text().splitlines()]
                self.assertEqual(rows[-1]['status'],'STOPPED')
                self.assertTrue(any(r.get('observations') for r in rows[:-1]))
            self.assertEqual([s.split('\t')[0] for s in (out/'final-commands.tsv').read_text().splitlines()],['add','diff','reset','status','done'])


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_failure_downloads_partial_records(self):
        payload=io.BytesIO()
        with tarfile.open(fileobj=payload,mode='w:gz') as archive:
            data=b'{"status":"FAIL"}\n';info=tarfile.TarInfo('result.json');info.size=len(data);archive.addfile(info,io.BytesIO(data))
        class Sandbox:
            async def download_file(self,*args,**kwargs):return payload.getvalue()
        failure=AssertionError('probe failed')
        async def tool(sb,label,*args,**kwargs):
            if label=='identity-probe':raise failure
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            with self.assertRaises(AssertionError) as caught:await download_identity_probe(Sandbox(),out,tool)
            self.assertIs(caught.exception,failure)
            self.assertEqual(json.loads((out/'identity-download.json').read_text())['status'],'PASS')
            self.assertEqual(json.loads((out/'identity-probe/result.json').read_text())['status'],'FAIL')

    async def test_download_failure_reports_both_errors(self):
        class Sandbox:
            async def download_file(self,*args,**kwargs):raise OSError('download failed')
        async def tool(sb,label,*args,**kwargs):
            if label=='identity-probe':raise AssertionError('probe failed')
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            with self.assertRaises(ExceptionGroup) as caught:await download_identity_probe(Sandbox(),out,tool)
            self.assertEqual(len(caught.exception.exceptions),2)
            self.assertEqual(json.loads((out/'identity-download.json').read_text())['status'],'FAIL')


if __name__=='__main__':unittest.main()
