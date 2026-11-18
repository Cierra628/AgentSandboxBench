"""Offline relay-rule ownership checks; no real firewall, socket, service or guest."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import external_guest_proxy as relay


class RelayRuleTests(unittest.TestCase):
    def test_owned_relay_copy_accepts_only_selected_private_pools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=self.setup_output(root);up=root/'upstream';(up/'scripts').mkdir(parents=True)
            original="allowed = [ipaddress.ip_network('10.211.0.0/16'), ipaddress.ip_network('10.212.0.0/16'), ipaddress.ip_network('169.254.0.20/30')]\n"
            source=up/'scripts/agentenv_workload_proxy.py';source.write_text(original)
            child=MagicMock(pid=1);child.poll.return_value=None
            sock=MagicMock();sock.__enter__.return_value.getsockname.return_value=('192.0.2.17',34567)
            def opened(argv,**kw):
                (out/'guest-proxy-ready.json').write_text(json.dumps(dict(pid=1,address='192.0.2.17',port=34567)))
                return child
            with patch.dict(relay.os.environ,{'ASB_ISOLATED_UNUSED_NETWORK_POOLS':'on'}),\
                    patch.object(relay.subprocess,'check_output',side_effect=['[]','[{"dst":"10.211.0.1"}]']),\
                    patch.object(relay.subprocess,'run'),patch.object(relay.socket,'socket',return_value=sock),\
                    patch.object(relay.subprocess,'Popen',side_effect=opened):
                proxy=relay.GuestProxy(out,up,proxy_url='http://127.0.0.1:17891')
                proxy.start();proxy.stop()
            copied=(out/'guest-proxy.py').read_text()
            self.assertIn('10.213.0.0/16',copied);self.assertIn('10.214.0.0/16',copied)
            self.assertNotIn('10.211.0.0/16',copied);self.assertEqual(source.read_text(),original)
            self.assertEqual(json.loads((out/'guest-proxy-network.json').read_text())['pools'],['10.213.0.0/16','10.214.0.0/16'])

    def setup_output(self,root):
        out=root/'owned-relay';out.mkdir()
        (out/'guest-proxy-ready.json').write_text(json.dumps(dict(address='192.0.2.17',port=34567,pid=1)))
        return out

    def test_explicit_pool_ignores_foreign_route_and_uses_same_pool_for_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=self.setup_output(Path(tmp));present=False;commands=[]
            def run(argv,**kw):
                nonlocal present
                commands.append(argv)
                if '-C' in argv:return types.SimpleNamespace(returncode=0 if present else 1)
                if '-I' in argv:present=True
                if '-D' in argv:present=False
                return types.SimpleNamespace(returncode=0)
            def query(argv,**kw):
                if argv[0]=='ip':return json.dumps([dict(dst='10.211.0.1',dev='veth-foreign'),dict(dst='10.213.0.1',dev='veth-owned')])
                return '[1:60] -A INPUT -s 10.213.0.1/32 -m comment --comment asb-relay-owned-relay -j ACCEPT\n'
            with patch.object(relay.subprocess,'run',side_effect=run),patch.object(relay.subprocess,'check_output',side_effect=query):
                receipt=relay.allow_guest_relay(out,'guest',source_pool='10.213.0.0/16')
                self.assertEqual(receipt['source'],'10.213.0.1')
                relay.remove_guest_relay_rule(out)
            self.assertFalse(present)
            self.assertTrue(all('10.211.0.1/32' not in c for c in commands))
            self.assertEqual(json.loads((out/'guest-proxy-rule.json').read_text())['status'],'REMOVED')

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
