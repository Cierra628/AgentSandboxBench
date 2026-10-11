import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from platform_tasks import ROOT, configuration, commands, preflight, report


class PlatformTaskTests(unittest.TestCase):
    def config(self, **changes):
        cfg=json.loads((ROOT/'configs/platform-prettier6604.json').read_text())
        cfg.update(changes)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json';path.write_text(json.dumps(cfg))
            return configuration(path)

    def test_unsupported_backend_and_boundary_are_not_silently_ignored(self):
        for changes in ({'backend':'trenvx'}, {'checkpoint_after_step':22}, {'checkpoint_after_step':True},
                        {'rollout':'bpo'}, {'drop_host_caches':True}):
            with self.assertRaises(ValueError): self.config(**changes)

    def test_analysis_cannot_be_mislabeled_as_checkpoint_or_branch_run(self):
        for changes in ({'checkpoint_after_step':24}, {'rollout':'grpo','frozen_export':'example'},
                        {'physical_cache_after_segments':['unknown']}):
            with self.assertRaises(ValueError):
                self.config(physical_cache_after_segments=['read_repo'], **changes) if 'physical_cache_after_segments' not in changes else self.config(**changes)

    def test_frozen_inputs_check_does_not_create_runtime_directories(self):
        cfg=self.config(output_root='.artifacts/platform-task-test-not-created')
        self.assertEqual(preflight(cfg, offline=True)['status'], 'VALIDATED')
        self.assertFalse(Path(cfg['output_root']).exists())

    def test_report_does_not_promote_failed_source_to_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'result.json').write_text(json.dumps({'status':'FAIL'}))
            with self.assertRaisesRegex(ValueError, 'failed/incomplete'): report(root, root/'report')
            self.assertFalse((root/'report').exists())

    def test_sphinx_diagnostic_policy_remains_explicit(self):
        cfg=configuration(ROOT/'configs/platform-sphinx7590.json')
        check, live=commands(cfg,Path('/not-created'))
        self.assertIn('--sphinx-pipeline-diagnostic',check)
        self.assertEqual(live[live.index('--python-sphinx-pipeline-policy-version')+1], '2')
        with self.assertRaises(ValueError): configuration(ROOT/'configs/platform-sphinx7590.json',boundary='29')

    def test_shared_cache_variant_is_explicit_and_routes_root_count(self):
        for count in (1,2,7):
            cfg=self.config(rollout='grpo',frozen_export='.artifacts/prettier6604/grpo-input-audit-20261008T120626Z',shared_cache_measurement=True,root_count=count)
            check,live=commands(cfg,Path('/not-created'))
            self.assertEqual(check[check.index('--root-count')+1],str(count))
            self.assertEqual(live[live.index('--prettier6604-root-count')+1],str(count))
        for changes in ({'root_count':2},{'shared_cache_measurement':True},{'root_count':True,'shared_cache_measurement':True},
                        {'root_count':3,'shared_cache_measurement':True}):
            with self.assertRaises(ValueError):self.config(**changes)

    def test_python_physical_boundaries_route_and_validate_for_each_task(self):
        for task,segment in (('sphinx7590','trace_cpp_parser'),('xarray6992','trace_index_state')):
            cfg=configuration(ROOT/('configs/platform-'+task+'-cache.json'))
            check,live=commands(cfg,Path('/not-created'))
            self.assertEqual(check[check.index('--physical-cache-after')+1],','.join(cfg['physical_cache_after_segments']))
            self.assertEqual(live[live.index('--python-physical-cache-after')+1],','.join(cfg['physical_cache_after_segments']))
            self.assertEqual(preflight(cfg,offline=True)['status'],'VALIDATED')
            for changes in ({'physical_cache_after_segments':['read_typescript']}, {'checkpoint_after_step':29 if task=='sphinx7590' else 22}):
                bad=dict(cfg,**changes)
                with tempfile.TemporaryDirectory() as tmp:
                    path=Path(tmp)/'config.json';path.write_text(json.dumps(bad))
                    with self.assertRaises(ValueError):configuration(path)

    def test_physical_fingerprint_checks_all_python_source_files(self):
        from task_physical_cache import source_fingerprint
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);names=['sphinx/domains/cpp.py','sphinx/util/cfamily.py']
            for name in names:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('original')
            command=source_fingerprint(names).split('; ',1)[1].replace('/testbed/',str(root)+'/')
            before=subprocess.check_output(['bash','-c',command],timeout=5)
            (root/names[1]).write_text('changed')
            after=subprocess.check_output(['bash','-c',command],timeout=5)
            self.assertNotEqual(before,after)
            self.assertIn(names[0].encode(),after);self.assertIn(names[1].encode(),after)

    def test_multiple_physical_points_update_summary_and_keep_previous_point(self):
        from task_physical_cache import TaskPhysicalCache
        with tempfile.TemporaryDirectory() as tmp:
            probe=TaskPhysicalCache.__new__(TaskPhysicalCache)
            probe.directory=Path(tmp);probe.points=[]
            first=probe.directory/'first';first.mkdir()
            second=probe.directory/'second';second.mkdir()
            probe.record_point(first,dict(scope='file-LRU',after_global_step=5))
            original=(first/'point.json').read_bytes()
            probe.record_point(second,dict(scope='file-LRU',after_global_step=24))
            self.assertEqual([p['after_global_step'] for p in json.loads((probe.directory/'summary.json').read_text())['points']], [5,24])
            self.assertEqual((first/'point.json').read_bytes(),original)
            with self.assertRaises(FileExistsError): probe.record_point(first,dict(scope='other'))
            third=probe.directory/'partial';third.mkdir()
            probe.record_point(third,dict(scope='file-LRU',status='PARTIAL',host_unknown_pages=4))
            summary=json.loads((probe.directory/'summary.json').read_text())
            self.assertEqual(summary['status'],'PARTIAL')
            self.assertEqual(summary['points'][-1]['host_unknown_pages'],4)


if __name__ == '__main__': unittest.main()
