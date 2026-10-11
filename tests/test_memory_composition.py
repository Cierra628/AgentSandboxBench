import sys
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from memory_composition import composition, series


class CompositionTests(unittest.TestCase):
    def values(self):
        return dict(MemTotal=1000,MemFree=400,MemAvailable=450,Buffers=50,Cached=200,
            Shmem=20,AnonPages=100,Slab=80,KernelStack=10,PageTables=20,Percpu=5,
            SReclaimable=60,SUnreclaim=20)

    def test_slab_and_shared_memory_are_not_counted_twice(self):
        result=composition(self.values())
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['components_bytes']['file_cache_and_buffers'],230)
        self.assertEqual(result['components_bytes']['unclassified_residual'],135)
        self.assertEqual(sum(result['components_bytes'].values()),600)
        self.assertAlmostEqual(sum(result['percentages'].values()),100)

    def test_missing_is_partial_not_zero(self):
        values=self.values();del values['MemFree']
        result=composition(values)
        self.assertEqual(result['status'],'PARTIAL')
        self.assertIsNone(result['components_bytes'])
        self.assertEqual(result['missing_fields'],['MemFree'])

    def test_inconsistent_sample_is_preserved(self):
        values=self.values();values['AnonPages']=900
        result=composition(values)
        self.assertEqual(result['status'],'PARTIAL')
        self.assertLess(result['components_bytes']['unclassified_residual'],0)

    def test_peak_composition_comes_from_same_sample(self):
        first=self.values();second=self.values()
        second.update(MemFree=100,Cached=100)
        result=series([dict(timestamp_ns=1,meminfo_bytes=first),dict(timestamp_ns=2,meminfo_bytes=second)])
        self.assertEqual(result['observed_peak_nonfree_bytes'],900)
        self.assertEqual(result['composition_at_observed_peak']['components_bytes']['file_cache_and_buffers'],130)

    def test_all_partial_samples_cannot_pass(self):
        result=series([dict(timestamp_ns=1,meminfo_bytes={})])
        self.assertEqual(result['status'],'PARTIAL')
        self.assertIsNone(result['observed_peak_nonfree_bytes'])
        self.assertEqual(series([])['status'],'NOT_MEASURED')

    def test_header_only_tsv_does_not_relabel_boundary_samples_as_periodic(self):
        from report_memory_metrics import report
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'replay').mkdir()
            (root/'replay/guest-memory.tsv').write_text('timestamp_ns\tphase\tstep\tMemTotal_kib\n')
            audited=dict(raw_dir=str(root),guest_memory=dict(sampling_mode='controller_boundary',
                boundary_observations=[dict(timestamp_ns=1,label='restored',meminfo_bytes=self.values())]))
            with patch('report_memory_metrics.audit_run',return_value=audited):
                result=report({})
            self.assertEqual(result['guest_composition']['sampling_mode'],'controller_boundary')
            self.assertEqual(result['guest_composition']['sample_count'],1)
            self.assertNotIn('time_scope',result['guest_composition'])
