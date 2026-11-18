"""Offline capacity-control checks; never write any real proc/sys or device."""
import inspect
import json
import tempfile
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch,mock_open

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import guest_cache_capacity as guest
import live_task_cache
import validate_isolated_agentenv as validator
import run_guest_cache_capacity as runner


class CacheCapacityTests(unittest.TestCase):
    def test_reclaim_only_excess_base_pages(self):
        for excess,expected in [(0,0),(-1,0),(1,1),(4096,1),(4097,2)]:
            with self.subTest(excess=excess),patch.object(Path,'write_text',autospec=True) as write, \
                 patch.object(Path,'read_text',return_value=f'requested_pages {expected}\ninvalidated_entries 0\nscanned_folios 5\nelapsed_ns 10\n'):
                result=guest.reclaim(64*guest.MIB+excess,64*guest.MIB)
                self.assertEqual(result['requested_pages'],expected)
                if expected:write.assert_called_once_with(Path('/proc/asb_cache_reclaim'),str(expected)+'\n')
                else:write.assert_not_called()

    def test_metadata_counts_and_native_counters_have_bytes_units(self):
        header=dict(records=2,shmem_pages=1)
        mem='\n'.join(f'{key}: 3 kB' for key in ['Cached','Shmem','AnonPages','SwapTotal','SwapFree','Dirty','Writeback','Slab'])
        with patch.object(Path,'read_bytes',return_value=b'synthetic'), \
             patch.object(guest,'decode_header',return_value=header),patch.object(Path,'read_text',return_value=mem):
            result=guest.counters()
        self.assertEqual(result['cache_bytes'],8192)
        self.assertEqual(result['shmem_metadata_bytes'],4096)
        self.assertEqual(result['memory']['Cached'],3072)

    def test_kernel_identity_keeps_existing_default(self):
        signature=inspect.signature(live_task_cache.LiveTaskCache)
        self.assertEqual(signature.parameters['expected_kernel'].default,'6.1.175-asb-cache-v3')

    def test_task_daemon_stops_and_restores_owned_damon_on_success_or_failure(self):
        for fail in [False,True]:
            capabilities=dict(kernel=runner.KERNEL,clean_reclaim_interface=True,damon=dict(enabled='Y'))
            with self.subTest(fail=fail),patch.object(guest,'capabilities',return_value=capabilities), \
                 patch.object(Path,'mkdir'),patch.object(Path,'open',mock_open()), \
                 patch.object(Path,'read_text',return_value=''),patch.object(Path,'iterdir',return_value=[]), \
                 patch.object(Path,'exists',side_effect=[False,True]),patch.object(Path,'touch'), \
                 patch.object(Path,'write_text',autospec=True) as write,patch.object(guest.time,'sleep'), \
                 patch.object(guest,'counters',return_value=dict(cache_bytes=64*guest.MIB)), \
                 patch.object(guest,'reclaim',side_effect=RuntimeError('synthetic failure') if fail else None,
                              return_value=dict(requested_pages=0)) as reclaim, \
                 patch.object(guest,'set_damon',side_effect=[dict(enabled='N',kdamond_pid=-1),dict(enabled='Y',kdamond_pid=1)]) as damon:
                self.assertEqual(guest.serve(Path('/workspace/asb-task-capacity'),128),1 if fail else 0)
                self.assertEqual([c.args for c in damon.call_args_list],[('N',),('Y',)])
                reclaim.assert_called_once_with(64*guest.MIB,128*guest.MIB)
                result=json.loads(write.call_args_list[-1].args[1]);self.assertEqual(result['status'],'FAIL' if fail else 'PASS')

    def test_capacity_probe_cannot_join_other_short_workflow(self):
        with patch.object(validator.os,'geteuid',return_value=0),patch.object(validator.os,'posix_fallocate') as allocate:
            with self.assertRaisesRegex(ValueError,'capacity probe is a separate short workflow'):
                validator.main(SimpleNamespace(guest_cache_capacity_probe=True,snapshot_cache_calibration=True))
            allocate.assert_not_called()

    def test_task_target_requires_sampled_pair_and_excludes_initial_policy(self):
        for pair,sampling,policy in [(False,None,None),(True,'off',None),(True,'on','preserve')]:
            with self.subTest(pair=pair,sampling=sampling,policy=policy),patch.object(validator.os,'geteuid',return_value=0), \
                 patch.object(validator.os,'posix_fallocate') as allocate:
                with self.assertRaisesRegex(ValueError,'continuous cache target requires'):
                    validator.main(SimpleNamespace(prettier6604_snapshot_cache_pair=pair,
                        prettier6604_snapshot_cache_sampling=sampling,prettier6604_guest_cache_policy=policy,
                        prettier6604_cache_target_mib=64))
                allocate.assert_not_called()

    def test_slot_capture_starts_before_cold_guest_and_cleanup_runs(self):
        events=[];capture=Mock()
        def factory(*args,**kwargs):events.append('capture');return capture
        def create(*args,**kwargs):events.append('create');return 'owned-sid'
        data=dict(status='PASS',content_checks='PASS',damon_disabled=dict(kdamond_pid=-1),
                  damon_restored=dict(enabled='Y'),capabilities_after=dict(damon=dict(enabled='Y')))
        def command(argv,**kwargs):
            text=(runner.KERNEL if argv[-2:]==['uname','-r'] else
                  json.dumps(data) if 'cat' in argv and str(argv[-1]).endswith('result.json') else '{}')
            return SimpleNamespace(stdout=text.encode(),stderr=b'',check_returncode=lambda:None)
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);parent=root/'mount/controller';parent.mkdir(parents=True)
            args=SimpleNamespace(output=parent/'guest-cache',container='owned',config_home=root,service_url='owned')
            with patch.object(runner,'LiveTaskCache',side_effect=factory),patch.object(runner,'create',side_effect=create), \
                 patch.object(runner.subprocess,'run',side_effect=command):
                self.assertEqual(runner.main(args),0)
            self.assertEqual(events,['capture','create'])
            capture.start.assert_called_once_with('owned-sid');capture.close.assert_called_once()
            self.assertTrue(json.loads((args.output/'result.json').read_text())['owned_guest_deleted'])


if __name__=='__main__':unittest.main()
