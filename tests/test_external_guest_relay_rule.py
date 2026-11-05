"""Offline relay-rule ownership checks; no real firewall, socket, service or guest."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import external_guest_proxy as relay


class RelayRuleTests(unittest.TestCase):
    def setup_output(self,root):
        out=root/'owned-relay';out.mkdir()
        (out/'guest-proxy-ready.json').write_text(json.dumps(dict(address='192.0.2.17',port=34567,pid=1)))
        return out

    def test_exact_rule_counters_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=self.setup_output(Path(tmp));commands=[];present=False
            def run(argv,**kw):
                nonlocal present
                commands.append(argv)
                if '-C' in argv:return types.SimpleNamespace(returncode=0 if present else 1)
                if '-I' in argv:present=True
                if '-D' in argv:present=False
                return types.SimpleNamespace(returncode=0)
            def query(argv,**kw):
                if argv[0]=='ip':return json.dumps([dict(dst='10.211.0.1',dev='veth-abcdef12')])
                self.assertEqual(argv[0],'iptables-nft-save')
                return '[2:120] -A INPUT -s 10.211.0.1/32 -m comment --comment asb-relay-owned-relay -j ACCEPT\n[4:240] -A INPUT -s 10.211.0.0/16 -j REJECT\n-A INPUT -s 10.99.0.0/16 -j DROP\n'
            with patch.object(relay.subprocess,'run',side_effect=run),patch.object(relay.subprocess,'check_output',side_effect=query):
                state=relay.allow_guest_relay(out,'owned-guest')
                self.assertEqual(state['source'],'10.211.0.1')
                relay.capture_relay_counters(out,'before-head');relay.capture_relay_counters(out,'after-head')
                relay.remove_guest_relay_rule(out);relay.remove_guest_relay_rule(out)
            self.assertFalse(present)
            add=next(c for c in commands if '-I' in c);delete=next(c for c in commands if '-D' in c)
            self.assertEqual(add[6:],delete[5:]);self.assertIn('10.211.0.1/32',add);self.assertIn('192.0.2.17/32',add)
            self.assertIn('veth-abcdef12',add);self.assertIn('34567',add);self.assertNotIn('10.211.0.0/16',add)
            self.assertEqual(json.loads((out/'guest-proxy-rule.json').read_text())['status'],'REMOVED')
            self.assertEqual(len(json.loads((out/'guest-proxy-counters-before-head.json').read_text())['rules']),2)
            self.assertFalse(any('-Z' in c or '-F' in c for c in commands))
            self.assertTrue(all(c[0]=='iptables-nft' for c in commands))
            self.assertEqual(json.loads((out/'guest-proxy-rule.json').read_text())['backend'],'nft')

    def test_ambiguous_add_failure_still_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=self.setup_output(Path(tmp));present=False
            def run(argv,**kw):
                nonlocal present
                if '-C' in argv:return types.SimpleNamespace(returncode=0 if present else 1)
                if '-I' in argv:
                    present=True;raise subprocess.TimeoutExpired(argv,10)
                if '-D' in argv:present=False
                return types.SimpleNamespace(returncode=0)
            with patch.object(relay.subprocess,'run',side_effect=run),patch.object(relay.subprocess,'check_output',side_effect=[json.dumps([dict(dst='10.211.0.2/32',dev='veth-abcd')]),'']):
                with self.assertRaises(subprocess.TimeoutExpired):relay.allow_guest_relay(out,'guest')
                self.assertEqual(json.loads((out/'guest-proxy-rule.json').read_text())['status'],'PLANNED')
                relay.remove_guest_relay_rule(out)
            self.assertFalse(present)

    def test_existing_rule_not_adopted_or_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=self.setup_output(Path(tmp))
            with patch.object(relay.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as change,patch.object(relay.subprocess,'check_output',return_value=json.dumps([dict(dst='10.211.0.1',dev='veth-abcd')])):
                with self.assertRaises(ValueError):relay.allow_guest_relay(out,'guest')
                relay.remove_guest_relay_rule(out)
            self.assertEqual(change.call_count,1);self.assertFalse((out/'guest-proxy-rule.json').exists())

    def test_unknown_or_multiple_routes_and_foreign_receipt_rejected(self):
        for routes in ([],[dict(dst='10.211.0.0/16',dev='veth-abcd')],
                       [dict(dst='10.211.0.1',dev='veth-abcd'),dict(dst='10.211.0.2',dev='veth-abce')]):
            with tempfile.TemporaryDirectory() as tmp:
                out=self.setup_output(Path(tmp))
                with patch.object(relay.subprocess,'run') as change,patch.object(relay.subprocess,'check_output',return_value=json.dumps(routes)):
                    with self.assertRaises(ValueError):relay.allow_guest_relay(out,'guest')
                change.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp:
            out=self.setup_output(Path(tmp))
            receipt=dict(backend='nft',source='10.211.0.1',interface='veth-abcd',destination='198.51.100.17',port=34567,status='ADDED')
            (out/'guest-proxy-rule.json').write_text(json.dumps(receipt))
            with patch.object(relay.subprocess,'run') as change:
                with self.assertRaises(ValueError):relay.remove_guest_relay_rule(out)
            change.assert_not_called()

    def test_rule_cleanup_failure_still_stops_process_and_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            proxy=relay.GuestProxy(Path(tmp),Path(tmp))
            with patch.object(relay,'remove_guest_relay_rule',side_effect=RuntimeError('mock delete failure')),patch.object(proxy,'_stop_process_and_address') as stop:
                with self.assertRaises(RuntimeError):proxy.stop()
            stop.assert_called_once()


if __name__=='__main__':unittest.main()
