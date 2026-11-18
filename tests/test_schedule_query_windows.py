"""Saved-shape rows and local temporary files only; no service/device queries."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_schedule_files as m
from host_file_monitor import mincore_file


class QueryWindowTests(unittest.TestCase):
    def row(self,inode,old,new):
        return dict(device=1,inode=inode,status='CHANGED_DURING_QUERY',size=old,resident_bytes=((new+4095)//4096)*4096,
            residency=dict(device=1,inode=inode,size=new,stable=True,resident_bytes=((new+4095)//4096)*4096),
            change_phase='BETWEEN_INVENTORY_AND_QUERY')

    def test_four_changed_upper_rows_have_separate_query_total_without_rewriting_strict(self):
        rows=[self.row(1,868352,872448),self.row(2,7264,7280),self.row(3,1134592,1138688),self.row(4,6976,6992)]
        originals=copy.deepcopy(rows);value=m.query_window_summary(rows,[])
        self.assertEqual(value['changed_before_query_objects'],4)
        self.assertEqual(value['observed_subtotal_bytes'],2027520)
        self.assertEqual(value['complete_bytes'],2027520)
        self.assertEqual(value['status'],'OBSERVED')
        for row,original in zip(rows,originals):
            self.assertEqual({k:v for k,v in row.items() if k!='query_window_status'},original)
        self.assertEqual(sum(r['resident_bytes'] for r in rows if r['status']=='OBSERVED'),0)

    def test_in_query_change_failure_and_unresolved_stay_partial(self):
        row=self.row(1,4096,8192)
        for change in ('unstable','identity','failed','unresolved'):
            rows=[copy.deepcopy(row)];unresolved=[]
            if change=='unstable':rows[0]['residency']['stable']=False
            if change=='identity':rows[0]['residency']['inode']=9
            if change=='failed':rows[0]['status']='QUERY_FAILED'
            if change=='unresolved':unresolved=[{'error':'lost source'}]
            with self.subTest(change=change):
                value=m.query_window_summary(rows,unresolved)
                self.assertEqual(value['status'],'PARTIAL');self.assertIsNone(value['complete_bytes'])
                self.assertEqual(value['observed_subtotal_bytes'],8192 if change=='unresolved' else 0)

    def test_real_fixture_adds_phases_only_in_explicit_layers_and_dedups_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            env=Path(tmp)/'env';env.mkdir();file=env/'upper';file.write_bytes(b'x'*4096)
            (env/'alias').hardlink_to(file)
            with patch.object(m,'pids',return_value=[]),patch.object(m,'permission_evidence',return_value={}):
                old=m.observe_schedule_files(env,{'guests':{}},[],Path('/not-real'))
                def grow(path,**kw):
                    file.write_bytes(b'x'*8192)
                    return mincore_file(path,**kw)
                with patch.object(m,'mincore_file',side_effect=grow):
                    value=m.observe_schedule_files(env,{'guests':{}},[],Path('/not-real'),capture_layers=True)
            self.assertNotIn('query_window_regular',old);self.assertNotIn('stages',old)
            self.assertEqual(value['unique_objects'],1);self.assertEqual(value['status'],'PARTIAL')
            self.assertEqual(value['stable_regular_resident_bytes'],0)
            self.assertEqual(value['query_window_regular']['complete_bytes'],8192)
            self.assertEqual(len(value['files'][0]['aliases']),2)
            self.assertIn('inventory_first_monotonic_ns',value['files'][0])
            self.assertEqual(set(value['stages']),{'inventory','queries','process_recheck'})
            for phase in value['stages'].values():
                self.assertGreaterEqual(phase['end_monotonic_ns'],phase['start_monotonic_ns'])
                self.assertGreaterEqual(phase['cpu_ns'],0)


if __name__=='__main__':unittest.main()
