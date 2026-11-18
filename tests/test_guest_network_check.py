"""Execute the real guest check with mocked HTTP; forbid real socket connections."""
import contextlib
import io
from pathlib import Path
import socket
import ssl
import sys
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from external_guest_proxy import NETWORK_CHECK

URLS = ('https://github.com/SPOCKnots/pyknotid', 'https://pypi.org/simple/pip/')
HEADER = "source /probe/input/guest-env.sh && python3 - <<'PYNET'\n"
assert NETWORK_CHECK.startswith(HEADER) and NETWORK_CHECK.endswith('\nPYNET')
CHECK = compile(NETWORK_CHECK[len(HEADER):-len('\nPYNET')], '<guest-network-check>', 'exec')


def response(status):
    value = MagicMock()
    value.__enter__.return_value.status = status
    return value


class GuestNetworkCheckTests(unittest.TestCase):
    def assert_requests(self, opened, urls):
        self.assertEqual(opened.call_count, len(urls))
        for call, url in zip(opened.call_args_list, urls):
            self.assertEqual(len(call.args), 1)
            self.assertEqual(call.args[0].full_url, url)
            self.assertEqual(call.args[0].get_method(), 'HEAD')
            self.assertEqual(call.kwargs, {'timeout': 20})

    def check_failure(self, side_effect, expected, attempted):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('urllib.request.urlopen', side_effect=side_effect) as opened, \
                patch.object(socket.socket, 'connect', side_effect=AssertionError('real network forbidden')) as connect, \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(type(expected)) as caught:
                exec(CHECK, {'__name__': '__main__'})
        self.assertIs(caught.exception, expected)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stderr.getvalue(), 'guest-network-failed url='+attempted[-1]+'\n')
        self.assert_requests(opened, attempted)
        connect.assert_not_called()

    def test_first_url_failure_logs_url_and_preserves_exception_without_retry(self):
        error = URLError(ssl.SSLEOFError('UNEXPECTED_EOF_WHILE_READING'))
        self.check_failure([error], error, URLS[:1])

    def test_second_url_failure_logs_only_failed_url_without_retry(self):
        error = URLError(ssl.SSLEOFError('UNEXPECTED_EOF_WHILE_READING'))
        self.check_failure([response(200), error], error, URLS)

    def test_both_urls_success_keep_order_timeout_and_original_stdout(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('urllib.request.urlopen', side_effect=[response(200), response(399)]) as opened, \
                patch.object(socket.socket, 'connect', side_effect=AssertionError('real network forbidden')) as connect, \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(CHECK, {'__name__': '__main__'})
        self.assert_requests(opened, URLS)
        self.assertEqual(stdout.getvalue(), 'guest-network-ready\n')
        self.assertEqual(stderr.getvalue(), '')
        connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
