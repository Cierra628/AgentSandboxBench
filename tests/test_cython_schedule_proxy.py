"""Synthetic VMM/network evidence only; no network namespace or firewall mutation."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import cython_schedule_proxy as m


class CythonProxyTests(unittest.TestCase):
    def test_seven_sources_bound_to_their_namespaces_not_route_order(self):
        routes=[dict(dst=f'10.213.0.{i}/32',dev=f'veth-{i}') for i in range(1,8)]
        for i in range(1,8):
            nat=f'-A POSTROUTING -s 172.16.0.2/32 -o vpeer -j SNAT --to-source 10.213.0.{i}\n-A POSTROUTING -s 10.214.0.2/32 -o vpeer -j SNAT --to-source 10.213.0.{i}\n'
            self.assertEqual(m.select_source(nat,list(reversed(routes)),'10.213.0.0/16'),(f'10.213.0.{i}',f'veth-{i}'))
        for nat in ('', '-A POSTROUTING -o vpeer -j SNAT --to-source 10.211.0.9',nat+'-A POSTROUTING -o vpeer -j SNAT --to-source 10.213.0.99'):
            with self.assertRaises(ValueError):m.select_source(nat,routes,'10.213.0.0/16')
        with self.assertRaises(ValueError):m.select_source('-A POSTROUTING -o vpeer -j SNAT --to-source 10.213.0.1',[dict(dst='10.213.0.0/16',dev='veth-1')],'10.213.0.0/16')

    def test_rule_ownership_written_before_insert_and_exact_cleanup_after_failure(self):
        sid='11111111-1111-1111-1111-111111111111'
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);(out/'guest-proxy-ready.json').write_text(json.dumps(dict(address='192.0.2.17',port=19001)))
            binding=Mock();vm=dict(pid=123,start_ticks='9',boot_id='boot');binding.resolve.return_value=vm;binding.owned.return_value=True
            def check_output(args,**kwargs):
                if args[0]=='nsenter':
                    self.assertIn('--net=/proc/self/fd/77',args);self.assertEqual(kwargs['pass_fds'],(77,))
                    return '-A POSTROUTING -o vpeer -j SNAT --to-source 10.213.0.3\n'
                return json.dumps([dict(dst='10.213.0.3/32',dev='veth-31')])
            def insert(args,**kwargs):
                receipt=json.loads((out/'guests'/sid/'guest-proxy-rule.json').read_text());self.assertEqual(receipt['status'],'PLANNED')
                self.assertIn('10.213.0.3/32',args);self.assertIn('192.0.2.17/32',args);self.assertIn('asb-relay-'+sid,args)
                raise RuntimeError('insert interrupted')
            with patch.object(m,'identity',return_value=vm),patch.object(m.os,'open',return_value=77),patch.object(m.os,'close'),patch.object(m.os,'fstat',return_value=Mock(st_ino=-1)),patch.object(m.subprocess,'check_output',side_effect=check_output),patch.object(m.subprocess,'run',side_effect=insert),patch.object(m,'rule_present',return_value=False):
                with self.assertRaisesRegex(RuntimeError,'interrupted'):m.allow(out,sid,binding,source_pool='10.213.0.0/16')
            with patch.object(m,'remove_guest_relay_rule') as remove:m.cleanup_rules(out);remove.assert_called_once_with(out/'guests'/sid)

    def test_relay_pool_selection_is_explicit_and_environment_is_restored(self):
        with patch.dict(m.os.environ,{'ASB_ISOLATED_UNUSED_NETWORK_POOLS':'original'}):
            def start(proxy):
                self.assertEqual(m.os.environ['ASB_ISOLATED_UNUSED_NETWORK_POOLS'],'on')
                return 'http://192.0.2.17:19000'
            with patch.object(m.GuestProxy,'start',start):
                self.assertEqual(m.ScheduleProxy('/unused','/unused').start(),'http://192.0.2.17:19000')
            self.assertEqual(m.os.environ['ASB_ISOLATED_UNUSED_NETWORK_POOLS'],'original')

    def test_cleanup_continues_all_owned_rules_then_stops_helper_even_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            for i in range(7):
                directory=out/'guests'/str(i);directory.mkdir(parents=True);(directory/'guest-proxy-rule.json').write_text('{}')
            with patch.object(m,'remove_guest_relay_rule',side_effect=[RuntimeError('first'),*[None]*6]) as remove,patch.object(m.GuestProxy,'stop') as stop:
                proxy=m.ScheduleProxy(out,Path('/unused'))
                with self.assertRaisesRegex(RuntimeError,'cleanup failed'):proxy.stop()
                self.assertEqual(remove.call_count,7);stop.assert_called_once()


if __name__=='__main__':unittest.main()
