"""Offline target-window, excess and signed comparison checks."""
import ast,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_python_cache_baseline import task_samples
from report_python_cache_targets import control_stats,difference


class TargetReportTests(unittest.TestCase):
    def rows(self):
        values=[]
        for t,n in [(0,100),(10,5),(15,5),(20,7),(30,100)]:
            values.append(dict(target_bytes=6*4096,action=dict(requested_pages=max(0,n-6)),before=dict(start_ns=t,end_ns=t+1,cache_bytes=n*4096,metadata=dict(records=n,duplicate_adds=0,missing_removes=0,out_of_range=0)),after=dict(end_ns=t+3,cache_bytes=min(n,6)*4096)))
        return values

    def test_target_filters_whole_after_interval_and_checks_exact_requests(self):
        rows=self.rows();selected=task_samples(rows,dict(end_ns=9),dict(start_ns=22),target_bytes=6*4096)
        self.assertEqual(len(selected),2)
        rows[0]['action']['requested_pages']+=1
        with self.assertRaises(AssertionError):task_samples(rows,dict(end_ns=9),dict(start_ns=29),target_bytes=6*4096)

    def test_strict_excess_keeps_small_and_large_overshoot_without_threshold(self):
        rows=[dict(after=dict(cache_bytes=v)) for v in [4095,4096,4097,8192]]
        stats=control_stats(rows,4096)
        self.assertEqual(stats['above_target_samples'],2);self.assertEqual(stats['max_excess_bytes'],4096)
        self.assertEqual(stats['above_target_sample_fraction'],.5)

    def test_negative_and_positive_differences_both_preserved(self):
        self.assertEqual(difference(100,90)['delta_percent'],-10)
        self.assertEqual(difference(100,120)['delta_percent'],20)

    def test_wrapper_filters_report_and_checks_exact_actual_target(self):
        source=Path('scripts/run-python-cache-targets.sh').read_text();code=source.split("<<'PY'\n",1)[1].split('\nPY',1)[0]
        with tempfile.TemporaryDirectory() as name:
            out=Path(name);run=out/'20261018-run';run.mkdir();storage=out/'storage';raw=storage/'python-backbone';raw.mkdir(parents=True)
            (run/'result.json').write_text(json.dumps(dict(status='PASS',storage_result_dir=str(storage),report_dir='saved-report')))
            (raw/'result.json').write_text(json.dumps(dict(status='PASS',cache_target_mib=65,checks=dict(cache_target_and_damon_restored='PASS'))))
            (out/'sphinx7590-65.log').write_text('status=PASS result_dir='+str(out/'report-run')+'\nstatus=PASS result_dir='+str(run)+'\n')
            with patch.object(sys,'argv',['receipt',str(out),'sphinx7590-65']):exec(compile(code,'<target wrapper>','exec'),{})
            self.assertEqual(len(json.loads((out/'runs.json').read_text())),1)


if __name__=='__main__':unittest.main()
