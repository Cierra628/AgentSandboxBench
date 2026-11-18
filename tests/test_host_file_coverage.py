"""Pure saved-data checks; no devices, service commands or real workloads."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_host_file_coverage import inventory_summary, loop_summary


def point(resident, file):
    return dict(files=[dict(device=1, inode=2, stable=True, resident_bytes=resident)],
                resident_bytes=resident, inner_before={'file':file},
                inner_after={'file':file}, outer_after={'file':file})


class CoverageTests(unittest.TestCase):
    def test_initial_unknown_is_preserved_when_new_writes_agree(self):
        trial=dict(status='PASS', io_accounting_requested='yes', snapshots=dict(
            baseline=point(325*4096, 33*4096),
            immediate=point(33093*4096, 32805*4096),
            settled=point(33093*4096, 32805*4096)))
        result=loop_summary(trial)
        self.assertEqual(result['phases']['settled']['signed_gap_bytes'], 288*4096)
        self.assertEqual(result['signed_gap_change_bytes'], -4*4096)
        self.assertIn('UNRESOLVED', result['initialization_classification'])
        self.assertEqual(result['payload_accounting_classification'],'NO_POSITIVE_INCREMENTAL_SHORTFALL')

    def test_new_write_shortfall_is_separate_from_initial_unknown(self):
        trial=dict(status='PASS', io_accounting_requested='no', snapshots=dict(
            baseline=point(325*4096,33*4096),
            immediate=point(33093*4096,16417*4096),
            settled=point(33093*4096,16417*4096)))
        result=loop_summary(trial)
        self.assertEqual(result['signed_gap_change_bytes'],64*2**20)
        self.assertEqual(result['payload_accounting_classification'],'POSITIVE_INCREMENTAL_SHORTFALL')

    def test_inventory_distinguishes_omitted_path_sizes_from_residency(self):
        roots=['/seed/commits','/volume/env/image-cache','/volume.ext4',
               '/ram/volume.ext4','/volume/env/snapshot-store','/volume/env/snapshot-local-cache']
        row=dict(path='/ram/volume.ext4', device=2, inode=7, stable=True,
                 resident_bytes=4096, resident_pages=1)
        inventory=dict(status='OBSERVED', errors=[], roots=roots, files=[row],
            page_size=4096, resident_bytes=4096, cgroup_before=dict(file=8192, shmem=4096),
            cgroup_after=dict(file=8192, shmem=4096), minimum_unaccounted_or_timing_bytes=0)
        writable=[dict(path='/workspace/env/logs/server.log',size=1)]*2
        manifest={'deps':{'binary':dict(type='file',size=123)}}
        result=inventory_summary(inventory,writable,manifest)
        self.assertEqual(result['file_minus_inventory_bytes'],4096)
        self.assertEqual(result['omitted_paths_observed_size_bytes'],1)
        self.assertEqual(result['omitted_paths_page_rounded_size_bytes'],4096)
        self.assertEqual(len(result['omitted_writable_paths']),1)
        self.assertEqual(result['accounting_validation'],'AGGREGATES_DO_NOT_RESOLVE_OWNERSHIP')
        duplicated=copy.deepcopy(inventory);duplicated['files'].append(row)
        with self.assertRaises(AssertionError):inventory_summary(duplicated,writable,manifest)
        unstable=copy.deepcopy(inventory);unstable['files'][0]['stable']=False
        with self.assertRaises(AssertionError):inventory_summary(unstable,writable,manifest)

    def test_expanded_restore_audit_classifies_deps_logs_and_keeps_old_layout(self):
        roots=['/seed/commits','/volume/env/image-cache','/volume.ext4',
               '/ram/volume.ext4','/volume/env/snapshot-store','/volume/env/snapshot-local-cache',
               '/seed/deps','/volume/env/deps','/volume/env/logs',
               '/volume/env/persisted-sandboxes','/volume/env/template-builds']
        files=[dict(path=path+'/resident',device=1,inode=index,stable=True,resident_bytes=4096,resident_pages=1)
               for index,path in enumerate(roots[6:])]
        inventory=dict(status='OBSERVED',errors=[],roots=roots,files=files,page_size=4096,
            resident_bytes=5*4096,cgroup_before=dict(file=5*4096,shmem=0),
            cgroup_after=dict(file=5*4096,shmem=0),minimum_unaccounted_or_timing_bytes=0)
        writable=[dict(path='/workspace/env/logs/resident',size=4096)]
        result=inventory_summary(inventory,writable,{'deps':{'binary':dict(type='file',size=4096)}})
        self.assertEqual(result['omitted_writable_paths'],[])
        self.assertEqual(result['omitted_deps_manifest']['omitted_trees'],[])
        for label in ('input-deps','private-deps','private-logs','sandbox-records','template-builds'):
            self.assertEqual(result['categories'][label]['resident_bytes'],4096)


if __name__ == '__main__':
    unittest.main()
