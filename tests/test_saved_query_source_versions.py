"""Historical entry source checks only; stop before analysis or any timing."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import profile_saved_mincore_vectors as vectors
import review_saved_mincore_gap_two as gaps
import review_cache_observation_window as windows

ROOT=Path(__file__).resolve().parents[1]


class SourceChecksComplete(Exception):pass


class SavedQuerySourceTests(unittest.TestCase):
    def check_entry(self,module,stop_name):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);source=root/'source';(source/'code').mkdir(parents=True)
            (source/'result.json').write_text(json.dumps(dict(status='PASS',samples=10)))
            (source/'samples.jsonl').write_text(''.join(json.dumps(dict(point=p))+'\n' for p in range(10)))
            names=('live_task_cache.py','guest_cache_exporter.py','snapshot_cache_residency.py',
                   'provider_slot_cache.py','fast_cache_metadata.py')
            (root/'scripts').mkdir()
            for filename in names:
                current=(ROOT/'scripts'/filename).read_bytes()
                (root/'scripts'/filename).write_bytes(current)
                saved=(ROOT/'tests/fixtures/snapshot_gap_one.py').read_bytes() if filename=='snapshot_cache_residency.py' else current
                (source/'code'/filename).write_bytes(saved)
            partition=root/'partition';partition.mkdir()
            (partition/'summary.json').write_text(json.dumps(dict(status='PASS',source='source',points=[{}]*10)))
            (partition/'guest-identities.local.json').write_text(json.dumps([{}]*10))
            (source/stop_name).write_bytes(b'')
            original=Path.read_bytes
            def read(path):
                if path.name==stop_name:raise SourceChecksComplete()
                return original(path)
            # The expected stop proves old captured code passes source gates;
            # no historical count calculation, performance run or output occurs.
            with patch.object(module,'ROOT',root),patch.object(Path,'read_bytes',read):
                with self.assertRaises(SourceChecksComplete):
                    if module is windows:module.review(source,partition,root/'output')
                    elif module is vectors:module.profile(source,root/'output')
                    else:module.review(source,root/'output.json')
            self.assertFalse((root/'output').exists());self.assertFalse((root/'output.json').exists())

    def test_gap_count_entry_uses_saved_gap_one_not_runtime_gap_two(self):
        self.check_entry(gaps,'snapshot-query-result.json')

    def test_vector_entry_uses_saved_gap_one_without_repeating_timing(self):
        self.check_entry(vectors,'snapshot-backend.json')

    def test_observation_entry_still_accepts_saved_query_version(self):
        self.check_entry(windows,'snapshot-query-result.json')


if __name__=='__main__':unittest.main()
