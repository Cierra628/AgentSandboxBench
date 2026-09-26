"""Regression for fragmented serial readiness output; no VM or service access."""
import unittest
from smoke_trenvx_dax import complete_marker


class SerialMarkerTest(unittest.TestCase):
    def test_waits_for_full_line(self):
        line = b'ASB_READY branch=A hash=123\r\n'
        for end in range(len(line)):
            self.assertFalse(complete_marker(line[:end], b'ASB_READY'))
        self.assertTrue(complete_marker(line, b'ASB_READY'))
        self.assertTrue(complete_marker(b'boot\nASB_READY ready\n', b'ASB_READY'))


if __name__ == '__main__':
    unittest.main()
