import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from agentenv_cold_start import create, NoRedirect


class ColdStartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home/'aenv').mkdir()
        (self.home/'aenv/credentials').write_text('url="http://127.0.0.1:34567"\napi_key="fixture-key"\n')
        self.url = 'http://127.0.0.1:34567'
        self.sid = '0dc6b752-644f-4d44-bc76-ecafc27ebec2'

    def response(self, sandbox_id=None):
        response = MagicMock()
        response.status = 201
        response.read.return_value = json.dumps(dict(sandboxID=sandbox_id or self.sid,envdAccessToken='never-output')).encode()
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response
        return opener

    def test_http_timeout_is_separate_from_sandbox_lifetime(self):
        opener = self.response()
        with patch('agentenv_cold_start.urllib.request.build_opener', return_value=opener) as build:
            self.assertEqual(create(self.home,self.url,'fixed@sha256:digest'),self.sid)
        request = opener.open.call_args.args[0]
        self.assertEqual(opener.open.call_args.kwargs['timeout'],600)
        self.assertEqual(json.loads(request.data),dict(image='fixed@sha256:digest',timeout=1800,cpuCount=2,memoryMB=4096,secure=True))
        self.assertEqual(request.full_url,self.url+'/sandboxes-cold')
        self.assertEqual(request.get_header('X-api-key'),'fixture-key')
        self.assertEqual(build.call_args.args[0].proxies,{})

    def test_wrong_service_rejected_before_network(self):
        with patch('agentenv_cold_start.urllib.request.build_opener') as build:
            with self.assertRaises(ValueError):create(self.home,'http://127.0.0.1:18000','image')
            build.assert_not_called()

    def test_smoke_memory_is_explicit_and_default_remains_4096(self):
        opener=self.response()
        with patch('agentenv_cold_start.urllib.request.build_opener',return_value=opener):
            create(self.home,self.url,'fixed@sha256:digest',memory_mib=512)
        self.assertEqual(json.loads(opener.open.call_args.args[0].data)['memoryMB'],512)
        for memory in (0,511,4097):
            with self.assertRaises(ValueError):create(self.home,self.url,'image',memory_mib=memory)

    def test_remote_or_unbounded_request_rejected(self):
        for url in ('http://example.com:1234','http://127.0.0.1:34567/x','http://secret@127.0.0.1:34567'):
            with self.assertRaises(ValueError):create(self.home,url,'image')
        for timeout in (0,601):
            with self.assertRaises(ValueError):create(self.home,self.url,'image',request_timeout=timeout)

    def test_invalid_id_cannot_be_pass(self):
        with patch('agentenv_cold_start.urllib.request.build_opener',return_value=self.response('invalid')):
            with self.assertRaises(ValueError):create(self.home,self.url,'image')

    def test_auth_header_cannot_follow_redirect(self):
        req=Request(self.url,headers={'X-API-Key':'fixture-key'})
        with self.assertRaises(RuntimeError):NoRedirect().redirect_request(req,None,302,'redirect',{},'http://other.example')


if __name__ == '__main__':unittest.main()
