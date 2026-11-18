"""Pure parser/program tests; do not attach probes, use loop, or start services."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_loop_memcg_diagnostic import parse_contexts,probe_program,launch_command
class ProbeTests(unittest.TestCase):
    def test_io_accounting_is_opt_in_and_only_transient(self):
        default=launch_command(Path('/fixture'),'asblooptest')
        enabled=launch_command(Path('/fixture'),'asblooptest',True)
        self.assertNotIn('--property=IOAccounting=yes',default)
        self.assertNotIn('--io-accounting',default)
        self.assertIn('--property=IOAccounting=yes',enabled)
        self.assertIn('--io-accounting',enabled)
        self.assertEqual(default,[x for x in enabled if x not in ('--property=IOAccounting=yes','--io-accounting')])
    def test_context_parse_and_losses(self):
        text='READY\n@contexts[10, 20]: 32\nDONE\n'
        self.assertEqual(parse_contexts(text,'')[0],dict(blkcg_id=10,memcg_id=20,batches=32))
        for log,error in [(text,'Lost 1 events'),(text.replace('DONE','DEADLINE'),''),('READY\nDONE\n','')]:
            with self.assertRaises(AssertionError):parse_contexts(log,error)
    def test_exact_loop_filter(self):
        text=probe_program(123,dict(LOOP_DEVICE_LO_NUMBER=0,LOOP_CMD_BLKCG_CSS=8,LOOP_CMD_MEMCG_CSS=16,
            CGROUP_SUBSYS_STATE_CGROUP=32,CGROUP_KN=64,KERNFS_NODE_ID=128))
        self.assertIn('==123',text);self.assertNotIn('LOOP_',text)
        self.assertIn('kprobe:loop_process_work',text)
        self.assertNotIn('override',text)
if __name__=='__main__':unittest.main()
