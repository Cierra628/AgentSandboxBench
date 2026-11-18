"""Offline only: mocked creates and temporary files, no services or devices."""
import asyncio
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from cold_start_sampling import ROLES,create_batch,options
from run_startup_sampling_controls import summarize,utc_ns


class StartupSamplingTests(unittest.TestCase):
    def args(self,mode):
        return SimpleNamespace(startup_sampling=mode,light_monitor=mode=='on',
            prettier6604_monitor_mode='continuous' if mode=='on' else 'off',
            prettier6604_task='task',prettier6604_export='export',immutable_cache_seed='seed',
            prettier6604_schedule='grpo',prettier6604_grpo_capacity_gib=16)

    def test_only_new_mode_has_new_requirements(self):
        self.assertIsNone(options(SimpleNamespace()))
        for mode in ('off','on'):self.assertEqual(options(self.args(mode)),mode)

    def test_mixed_or_inconsistent_modes_rejected_before_allocation(self):
        for name,value in [('light_monitor',False),('prettier6604_schedule','bpo'),
            ('prettier6604_grpo_capacity_gib',32),('host_file_monitor',True),('damon_disable',True),
            ('prettier6604_role','early_1'),('online_task_config','task')]:
            with self.subTest(name=name):
                args=self.args('on');setattr(args,name,value)
                with self.assertRaises(ValueError):options(args)

    def test_parallel_creates_start_sampler_before_other_creates_return(self):
        gate=threading.Barrier(7,timeout=5);sample_started=threading.Event();live={}
        def create(role):
            gate.wait()
            if role!=ROLES[0]:self.assertTrue(sample_started.wait(5))
            return f'00000000-0000-0000-0000-{ROLES.index(role):012d}'
        def monitor(sid,role,phase):
            self.assertEqual(phase,'startup-only');self.assertEqual(live[role],sid)
            if role==ROLES[0]:sample_started.set()
        result=asyncio.run(create_batch(create,monitor,live))
        self.assertEqual(len(result),7);self.assertTrue(sample_started.is_set())

    def test_off_has_no_sampler(self):
        live={}
        asyncio.run(create_batch(lambda role:f'00000000-0000-0000-0000-{ROLES.index(role):012d}',None,live))
        self.assertEqual(set(live),set(ROLES))

    def test_partial_failure_waits_for_other_creates_and_tracks_created_guests(self):
        live={};gate=threading.Barrier(7,timeout=5)
        def create(role):
            gate.wait()
            if role==ROLES[0]:raise RuntimeError('create failed')
            return f'00000000-0000-0000-0000-{ROLES.index(role):012d}'
        with self.assertRaisesRegex(RuntimeError,'create failed'):asyncio.run(create_batch(create,None,live))
        self.assertEqual(len(live),6)

    def test_sampler_failure_keeps_guest_for_cleanup(self):
        live={}
        def fail(*args):raise RuntimeError('collector failed')
        with self.assertRaisesRegex(RuntimeError,'collector failed'):
            asyncio.run(create_batch(lambda role:f'00000000-0000-0000-0000-{ROLES.index(role):012d}',fail,live))
        self.assertEqual(len(live),7)

    def runs(self):
        runs=[]
        for pair,order in enumerate([('off','on'),('on','off'),('off','on')],1):
            for mode in order:runs.append(dict(pair=pair,mode=mode,status='PASS',actual_actions=0,created_guests=7,
                cold_span_ns=100 if mode=='off' else {1:110,2:90,3:120}[pair]))
        return runs

    def test_signed_pair_summary_and_fixed_scope(self):
        value=summarize(self.runs())
        self.assertEqual([r['difference_ns'] for r in value['pairs']],[10,-10,20])
        self.assertAlmostEqual(value['change_percent']['median'],10)
        self.assertEqual(value['created_guests'],42);self.assertEqual(value['actual_actions'],0)

    def test_summary_rejects_changed_order_or_task_actions(self):
        for field,value in [('mode','on'),('actual_actions',1),('created_guests',6)]:
            runs=self.runs();runs[0][field]=value
            with self.assertRaises(ValueError):summarize(runs)
        with self.assertRaises(ValueError):summarize(self.runs()[:-1])

    def test_server_timestamp_preserves_nanoseconds(self):
        self.assertEqual(utc_ns('1970-01-01T00:00:00.123456789Z info'),123456789)
        self.assertEqual(utc_ns('1970-01-01T00:00:00.1Z info'),100000000)
        with self.assertRaises(ValueError):utc_ns('missing')

    def test_validator_keeps_old_dispatch_and_skips_task_work_only_in_new_mode(self):
        code=(ROOT/'scripts/validate_isolated_agentenv.py').read_text()
        self.assertIn('if prettier6604 and not online_task and not startup_sampling:',code)
        start=code.index('elif startup_sampling:');end=code.index('elif prettier6604:',start)
        self.assertIn('cold_start_sampling.py',code[start:end]);self.assertNotIn('run_prettier6604_grpo.py',code[start:end])
        self.assertIn("if startup_sampling:\n            receipt['default_runtime_report']=dict(status='NOT_REQUESTED'",code)


if __name__=='__main__':unittest.main()
