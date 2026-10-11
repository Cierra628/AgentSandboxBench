import sys
from pathlib import Path
import unittest
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from guest_cache_state import GAUGES,COUNTERS,MEMINFO,parse_vmstat,parse_meminfo,delta,collect


class CacheStateTests(unittest.TestCase):
    def fixture(self):
        return dict(vmstat=parse_vmstat(''.join(f'{k} 20\n' for k in GAUGES+COUNTERS)),
                    meminfo_kib=parse_meminfo(''.join(f'{k}: 100 kB\n' for k in MEMINFO)))

    def test_gauge_decrease_is_not_counter_reset(self):
        before=self.fixture();after=self.fixture()
        after['vmstat']['gauges']['nr_file_pages']=15
        after['vmstat']['counters']['workingset_refault_file']=23
        observed=delta(before,after)
        self.assertEqual(observed['gauge_deltas_pages']['nr_file_pages'],-5)
        self.assertEqual(observed['counter_deltas']['workingset_refault_file'],3)

    def test_missing_duplicate_units_and_negative_values_rejected(self):
        v=''.join(f'{k} 20\n' for k in GAUGES+COUNTERS)
        m=''.join(f'{k}: 100 kB\n' for k in MEMINFO)
        for text in (v.replace('pgscan_file 20\n',''),v+'pgscan_file 20\n',v.replace('pgscan_file 20','pgscan_file -1')):
            with self.subTest(text=text),self.assertRaises(AssertionError):parse_vmstat(text)
        for text in (m.replace('Cached: 100 kB\n',''),m+'Cached: 100 kB\n',m.replace('Cached: 100 kB','Cached: 100 MB')):
            with self.subTest(text=text),self.assertRaises(AssertionError):parse_meminfo(text)

    def test_damon_counters_require_all_boundaries_and_stable_enabled_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for prefix,value in (('cache-start',10),('git-025-before',20),('git-025-after',25)):
                (root/(prefix+'.vmstat')).write_text(''.join(f'{k} 20\n' for k in GAUGES+COUNTERS))
                (root/(prefix+'.meminfo')).write_text(''.join(f'{k}: 100 kB\n' for k in MEMINFO))
                (root/(prefix+'.damon')).write_text(f'enabled=Y\nbytes_reclaim_tried_regions={value}\nbytes_reclaimed_regions={value}\nnr_reclaimed_regions=1\n')
            observed=collect(root,include_start=True,include_damon=True)
            self.assertEqual(observed['damon_before_command']['bytes_reclaimed_regions'],10)
            self.assertEqual(observed['damon_during_command']['bytes_reclaimed_regions'],5)
            endpoint=root/'git-025-after.damon';before=endpoint.read_text()
            endpoint.write_text(before.replace('enabled=Y','enabled=N'))
            with self.assertRaises(AssertionError):collect(root,include_start=True,include_damon=True)
            endpoint.write_text(before.replace('bytes_reclaimed_regions=25','bytes_reclaimed_regions=19'))
            with self.assertRaises(AssertionError):collect(root,include_start=True,include_damon=True)
            endpoint.unlink()
            with self.assertRaises(AssertionError):collect(root,include_start=True,include_damon=True)

    def test_embedded_damon_format_rejects_partial_or_duplicate_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for prefix,value in (('git-025-before',20),('git-025-after',25)):
                text=''.join(f'{k} 20\n' for k in GAUGES+COUNTERS)+f'damon_enabled Y\ndamon_bytes_reclaim_tried_regions {value}\ndamon_bytes_reclaimed_regions {value}\ndamon_nr_reclaimed_regions 1\n'
                (root/(prefix+'.vmstat')).write_text(text)
                (root/(prefix+'.meminfo')).write_text(''.join(f'{k}: 100 kB\n' for k in MEMINFO))
            self.assertEqual(collect(root,include_damon=True)['damon_during_command']['bytes_reclaimed_regions'],5)
            endpoint=root/'git-025-after.vmstat';original=endpoint.read_text()
            endpoint.write_text(original.replace('damon_enabled Y\n',''))
            with self.assertRaises(AssertionError):collect(root,include_damon=True)
            endpoint.write_text(original)
            (root/'git-025-after.damon').write_text('enabled=Y\n')
            with self.assertRaises(AssertionError):collect(root,include_damon=True)

    def test_counter_reset_or_changed_memory_budget_rejected(self):
        before=self.fixture();after=self.fixture()
        after['vmstat']['counters']['pgscan_file']=19
        with self.assertRaises(AssertionError):delta(before,after)
        after=self.fixture();after['meminfo_kib']['MemTotal']=99
        with self.assertRaises(AssertionError):delta(before,after)


if __name__=='__main__':unittest.main()
