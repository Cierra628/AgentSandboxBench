"""Pure metadata fixtures only: no probes, devices or services are started."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from host_loop_charge import analyze,program,validate_events


def snapshot(start,indices):
    return dict(start_monotonic_ns=start,end_monotonic_ns=start+1,
        files=[dict(resident_indices=indices)],resident_bytes=len(indices)*4096)


class ChargeTests(unittest.TestCase):
    def test_program_reads_actual_memcg_and_filters_exact_image(self):
        names=['folio_mapping','address_space_host','inode_i_ino','inode_i_sb','super_block_s_dev',
            'folio_index','folio_flags','folio__flags_1','folio_memcg_data','mem_cgroup_css',
            'cgroup_subsys_state_cgroup','cgroup_kn','kernfs_node_id','pg_head','memcg_flags_mask']
        text=program(SimpleNamespace(st_dev=os.makedev(8,1),st_ino=12345),[1,22],dict.fromkeys(names,0))
        self.assertIn('==12345',text);self.assertIn('==8388609',text)
        self.assertIn('rawtracepoint:mm_filemap_delete_from_page_cache',text)
        self.assertIn('kretprobe:css_rstat_flush',text)
        self.assertNotIn('$owner=cgroup',text)

    def test_record_counts_and_loss_are_not_silent(self):
        text='READY\nADD 1 2 3 4 0 5 0 6 actor\n@events[1]: 1\nDONE\n'
        self.assertEqual(validate_events(text,'')[1][1],1)
        for broken,error in [(text.replace(': 1',': 2'),''),(text,'Lost 1 events'),(text.replace('DONE\n',''),'')]:
            with self.assertRaises(AssertionError):validate_events(broken,error)

    def test_owner_is_not_actor_and_lifecycle_reuse_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            (out/'charge-events.log').write_text('ADD 1 1 0 100 1 10 0 20 writer\nADD 2 2 2 200 0 20 0 10 worker\nDEL 5 2 2 200 0 20 0 10 worker\nADD 6 2 2 300 0 10 0 20 newer\n')
            result=analyze(out,{'before':snapshot(3,[0,1,2]),'after':snapshot(7,[0,1,2])},{10})
            self.assertEqual(result['before']['measurement_owned_pages'],2)
            self.assertEqual(result['before']['outside_measurement_pages'],1)
            self.assertEqual(result['after']['measurement_owned_pages'],3)
            self.assertEqual(analyze(out,{'changing':snapshot(5,[0,1,2])},{10})['changing']['status'],'CHANGED_DURING_QUERY')
            with self.assertRaises(AssertionError):analyze(out,{'missing':snapshot(3,[3])},{10})
            (out/'charge-events.log').write_text('DEL 1 2 0 200 0 20 0 10 worker\n')
            with self.assertRaises(AssertionError):analyze(out,{'missing_add':snapshot(3,[0])},{10})


if __name__=='__main__':unittest.main()
