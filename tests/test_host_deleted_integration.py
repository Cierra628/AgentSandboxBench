"""Synthetic proc files and mocked child only; no live target or cache queries."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from host_mapped_backing import mapped_backing_inventory


class DeletedIntegrationTests(unittest.TestCase):
    def test_optional_query_keeps_original_metadata_and_default_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc=Path(tmp);root=proc/'10';root.mkdir();(root/'fd').mkdir()
            (root/'stat').write_text('10 (fixture) '+' '.join(['S']+['0']*18+['123']))
            header='1000-2000 rw-s 0 00:01 20 /memfd:fixture (deleted)'
            (root/'maps').write_text(header+'\n')
            (root/'smaps').write_text(header+'\nPss: 4 kB\nVmFlags: rd wr sh\n')
            observed=dict(status='OBSERVED',objects=[dict(device=1,inode=20)],whole_backing_cache_bytes=8192)
            with patch('host_mapped_backing.pids',return_value=[10]),\
                 patch('host_deleted_backing.query_deleted_backings_bounded',return_value=observed) as query:
                default=mapped_backing_inventory(proc,[],proc=proc);query.assert_not_called()
                self.assertNotIn('deleted_backing_cache',default)
                enabled=mapped_backing_inventory(proc,[],proc=proc,query_deleted_cache=True)
                self.assertEqual(enabled['deleted_backing_cache'],observed)
                self.assertEqual(query.call_args.args[0][0]['pid'],10)
                self.assertEqual(query.call_args.args[0][0]['start_ticks'],'123')
                for k in ('status','coverage','special_metadata_status','unresolved','additional_global_resident_bytes'):
                    self.assertEqual(default[k],enabled[k])
                self.assertEqual(enabled['special_metadata_status'],'INCOMPLETE')
                query.return_value=dict(status='TIMEOUT',objects=[],whole_backing_cache_bytes=None)
                timed=mapped_backing_inventory(proc,[],proc=proc,query_deleted_cache=True)
                self.assertEqual(timed['deleted_backing_cache']['status'],'TIMEOUT')
                self.assertEqual(timed['status'],'OBSERVED')
                self.assertEqual(timed['coverage'],'INCOMPLETE')


if __name__=='__main__':unittest.main()
