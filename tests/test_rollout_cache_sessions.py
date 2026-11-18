"""Only temporary files and fake collectors; never invokes Docker/proc/BPF."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from rollout_cache_sessions import RolloutCacheSessions
from live_task_cache import LiveTaskCache
from light_prettier_rollout import LightRollout


class Fake:
    instances = []
    def __init__(self, out, container, cli, **options):
        self.options = options
        self.directory = out/options['session_name']
        self.directory.mkdir()
        self.capture = options.get('capture', object())
        self.closed = False
        self.instances.append(self)
    def start(self, sid):
        if getattr(self, 'missing_ram', False):
            raise ValueError('missing actual KVM RAM layout')
        self.sid = sid
    def close(self):
        self.closed = True
        if getattr(self, 'stop_error', False):
            raise RuntimeError('collector did not stop')


def binding(pid=1):
    return dict(pid=pid, start_ticks=str(pid), sandbox_id='sandbox-'+str(pid),
                boot_id='host', actual_cgroup='/owned/vmm-'+str(pid))


class SessionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        Fake.instances = []
        self.sessions = RolloutCacheSessions(self.root, 'unused', None, factory=Fake)
    def tearDown(self):
        self.temp.cleanup()
    def test_provider_opt_in_only_restored_and_requires_lower(self):
        root=self.root/'provider';root.mkdir()
        with self.assertRaises(ValueError):RolloutCacheSessions(root,'unused',None,factory=Fake,restored_provider=True)
        s=RolloutCacheSessions(root,'unused',None,factory=Fake,restored_lower=True,restored_provider=True)
        s.prepare('source',restored=False)
        self.assertNotIn('provider_cache',Fake.instances[-1].options)
        s.prepare('restored',restored=True)
        self.assertTrue(Fake.instances[-1].options['provider_cache'])
        self.assertTrue(Fake.instances[-1].options['lower_cache'])

    def test_opt_in_shares_clock_across_roles_and_source_restart(self):
        root=self.root/'shared';root.mkdir()
        s=RolloutCacheSessions(root,'unused',None,factory=Fake,unified_rounds=True)
        s.prepare('source',restored=False);s.register('source',binding())
        clock=Fake.instances[-1].options['round_clock']
        s.saving('source');s.saved('source',binding())
        self.assertIs(Fake.instances[-1].options['round_clock'],clock)
        s.prepare('child',restored=True)
        self.assertIs(Fake.instances[-1].options['round_clock'],clock)

    def test_lower_only_enabled_on_restored_role(self):
        root=self.root/'lower';root.mkdir()
        s=RolloutCacheSessions(root,'unused',None,factory=Fake,restored_lower=True)
        s.prepare('backbone',restored=False)
        self.assertNotIn('lower_cache',Fake.instances[-1].options)
        s.prepare('branch',restored=True)
        self.assertTrue(Fake.instances[-1].options['lower_cache'])

    def test_save_continue_and_new_branch(self):
        s = self.sessions
        s.prepare('backbone', restored=False);s.register('backbone', binding())
        first = Fake.instances[0]
        s.saving('backbone');self.assertTrue(first.closed)
        s.saved('backbone', binding())
        second = Fake.instances[1]
        self.assertIs(first.capture, second.capture)
        self.assertNotEqual(first.options['session_name'], second.options['session_name'])
        s.prepare('early_1', restored=True);s.register('early_1', binding(2))
        self.assertEqual(second.options['session'], 'parent')
        self.assertEqual(Fake.instances[2].options['session'], 'restored')
        self.assertIsNot(first.capture, Fake.instances[2].capture)
        s.stop()
        events = json.loads(s.path.read_text())['events']
        names = [e['event'] for e in events]
        self.assertLess(names.index('GAP_BEGIN'), names.index('GAP_END'))
        self.assertEqual(names[-1], 'FINISHED')
    def test_missing_prepare_and_old_identity_rejected(self):
        with self.assertRaisesRegex(ValueError, 'prepared'):self.sessions.register('x', binding())
        self.sessions.prepare('x', restored=False);self.sessions.register('x', binding())
        self.sessions.saving('x')
        with self.assertRaisesRegex(ValueError, 'identity changed'):self.sessions.saved('x', binding(9))
        with self.assertRaisesRegex(RuntimeError, 'no resumed'):self.sessions.stop()
    def test_missing_ram_is_not_fallback(self):
        self.sessions.prepare('x', restored=True)
        Fake.instances[-1].missing_ram = True
        with self.assertRaisesRegex(ValueError, 'KVM'):self.sessions.register('x', binding())
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):self.sessions.stop()
        self.assertTrue(Fake.instances[-1].closed)
    def test_stop_failure_prevents_save(self):
        self.sessions.prepare('x', restored=False);self.sessions.register('x', binding())
        Fake.instances[-1].stop_error = True
        with self.assertRaisesRegex(RuntimeError, 'did not stop'):self.sessions.saving('x')
        with self.assertRaises(ValueError):self.sessions.saved('x', binding())
    def test_duplicate_identity_rejected(self):
        self.sessions.prepare('x', restored=False);self.sessions.register('x', binding())
        self.sessions.prepare('y', restored=True)
        with self.assertRaisesRegex(ValueError, 'duplicate'):self.sessions.register('y', binding())
    def test_old_session_semantics_select_restore_backend(self):
        # No real constructor: test the existing backend selection independently of role labels.
        from unittest.mock import MagicMock
        for kind, expected in [('parent', 0), ('restored', 1), ('child-a', 1)]:
            obj = LiveTaskCache.__new__(LiveTaskCache)
            obj.session=kind;obj.session_name='late_1-g1';obj.batch_lookup=False
            obj.proof={'slots':[]};obj.directory=self.root;obj.lower_cache=False
            with patch('snapshot_cache_residency.SnapshotCache', MagicMock()) as query:
                obj._start_snapshot_queries(self.root)
                self.assertEqual(query.call_count, expected)
    def test_actual_ram_binding_must_match_rollout_identity(self):
        obj=LiveTaskCache.__new__(LiveTaskCache)
        obj.expected_binding=binding();obj.directory=self.root
        obj.vm=dict(pid=1,start_time_ticks=1,sandbox_id='sandbox-1',cgroup='/owned/vmm-1')
        path=self.root/'vmm-child-binding.json'
        path.write_text(json.dumps({'vmm_binding':{'boot_id':'host'}}))
        obj._check_rollout_binding()
        obj.vm['pid']=99
        with self.assertRaisesRegex(ValueError,'RAM binding'):obj._check_rollout_binding()
        obj.vm['pid']=1
        path.write_text(json.dumps({'vmm_binding':{'boot_id':'different-host'}}))
        with self.assertRaisesRegex(ValueError,'boot identity'):obj._check_rollout_binding()

    def test_light_hooks_stop_sampler_on_cache_failure(self):
        from unittest.mock import Mock
        obj=LightRollout.__new__(LightRollout)
        obj.page_cache=Mock();obj.sampler=Mock();obj.by_role={'x':binding()}
        obj.before_create('x',restored=True)
        obj.page_cache.prepare.assert_called_once_with('x',restored=True)
        obj.saving('x');obj.saved('x')
        obj.page_cache.stop.side_effect=RuntimeError('failure')
        with self.assertRaises(RuntimeError):obj.stop()
        obj.sampler.stop.assert_called_once()

if __name__ == '__main__':unittest.main()
