"""Saved dictionaries only: no proc, services, sandbox or device access."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ram_page_evidence import manifest
from report_vmm_mapping_gaps import audit_vmm, geometry, union_bytes


class VmmMappingGapTests(unittest.TestCase):
    def fixture(self):
        vm=dict(pid=123,start_time_ticks=9,cgroup='/owned.slice/vmm-a',sandbox_id='sid-a',executable='/fake/vmm')
        meta=manifest('sid-a','guest-boot',vm,[dict(guest=0,host=0x100000,size=0x4000)])
        region=dict(start=0x200000,end=0x201000,offset=0,device=1,inode=20,
                    pid=123,start_ticks='9',permissions='rw-s',mapping_path='/memfd:test (deleted)')
        alias=dict(process=123,region=region)
        raw=dict(guests={'a':'sid-a'},bindings={'a':dict(pid=123,start_ticks='9')},expanded_files=dict(
            process_identities={'123':dict(start_ticks='9',cgroup='0::/owned.slice/vmm-a\n')},
            files=[dict(device=1,inode=20,aliases=[alias])],excluded_entries=[dict(
                path='/proc/123/map_files/300000-301000',reason='REFERENCED_NONREGULAR_OBJECT',mode=384)]))
        evidence=dict(objects=[dict(object_key=['regular',1,20],kind='shmem_or_hugetlb',
                                    query=dict(status='OBSERVED',bytes=4096))])
        return raw,evidence,meta

    def test_geometry_splits_partially_overlapping_ram(self):
        s=[dict(host=0x100000,size=0x4000)]
        r=geometry(0x103000,0x105000,s)
        self.assertEqual(r['captured_ram_overlap_virtual_bytes'],4096)
        self.assertEqual(r['outside_captured_ram_virtual_bytes'],4096)
        self.assertIsNone(r['resident_physical_bytes'])

    def test_empty_and_overlapping_offset_union_is_virtual_only(self):
        self.assertEqual(union_bytes([]),0)
        self.assertEqual(union_bytes([(0,4096),(0,4096),(2048,8192)]),8192)
        for intervals in ([(-1,4096)],[(4096,4096)]):
            with self.assertRaises(ValueError):union_bytes(intervals)

    def test_saved_exclusion_is_retained_without_name_inference(self):
        raw,e,meta=self.fixture();v=audit_vmm(raw,e,'a',meta)
        self.assertEqual(v['saved_file_mapping_count'],1)
        self.assertEqual(v['saved_excluded_mapping_count'],1)
        excluded=next(r for r in v['mappings'] if r['source_kind']=='excluded_nonregular_mapping')
        self.assertEqual(excluded['backing_kind'],'UNKNOWN')
        self.assertIsNone(excluded['region']);self.assertIsNone(v['complete_non_ram_host_physical_bytes'])
        self.assertEqual(v['complete_maps_status'],'NOT_SAVED')

    def test_two_maps_of_same_offset_do_not_duplicate_cache(self):
        raw,e,meta=self.fixture();a=copy.deepcopy(raw['expanded_files']['files'][0]['aliases'][0])
        a['region'].update(start=0x202000,end=0x203000)
        raw['expanded_files']['files'][0]['aliases'].append(a)
        v=audit_vmm(raw,e,'a',meta);o=v['linked_objects'][0]
        self.assertEqual(o['mappings'],2);self.assertEqual(o['mapped_virtual_bytes'],8192)
        self.assertEqual(o['mapped_file_offset_union_bytes'],4096)
        self.assertEqual(o['whole_object_cache_query']['bytes'],4096)
        self.assertIsNone(o['mapped_resident_physical_bytes'])

    def test_duplicate_alias_keeps_one_interval_and_source_pointers(self):
        raw,e,meta=self.fixture();f=raw['expanded_files']['files'][0]
        f['aliases'].append(copy.deepcopy(f['aliases'][0]))
        v=audit_vmm(raw,e,'a',meta)
        self.assertEqual(v['saved_file_mapping_count'],1)
        mapped=next(r for r in v['mappings'] if r['source_kind']=='file_mapping')
        self.assertEqual(len(mapped['sources']),2)

    def test_unknown_backing_not_filled_from_virtual_length(self):
        raw,e,meta=self.fixture();e['objects'][0]['query']=dict(status='UNKNOWN',bytes=None)
        v=audit_vmm(raw,e,'a',meta)
        self.assertIsNone(v['linked_objects'][0]['whole_object_cache_query']['bytes'])
        self.assertIsNone(v['linked_objects'][0]['mapped_resident_physical_bytes'])

    def test_identity_conflicts_fail(self):
        for mode in ('pid','ticks','cgroup','sandbox','alias_inode','alias_ticks'):
            raw,e,meta=self.fixture()
            if mode=='pid':raw['bindings']['a']['pid']=124
            elif mode=='ticks':raw['bindings']['a']['start_ticks']='10'
            elif mode=='cgroup':raw['expanded_files']['process_identities']['123']['cgroup']='0::/other\n'
            elif mode=='sandbox':raw['guests']['a']='other'
            elif mode=='alias_inode':raw['expanded_files']['files'][0]['aliases'][0]['region']['inode']=21
            else:raw['expanded_files']['files'][0]['aliases'][0]['region']['start_ticks']='10'
            with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,'identity conflict'):
                audit_vmm(raw,e,'a',meta)

    def test_conflicting_alias_and_overlapping_ranges_rejected(self):
        for start,end in ((0x200000,0x201000),(0x200000,0x202000)):
            raw,e,meta=self.fixture();a=copy.deepcopy(raw['expanded_files']['files'][0]['aliases'][0])
            a['region'].update(start=start,end=end,offset=4096)
            raw['expanded_files']['files'][0]['aliases'].append(a)
            with self.assertRaises(ValueError):audit_vmm(raw,e,'a',meta)

    def test_other_pid_exclusion_not_attributed_to_guest(self):
        raw,e,meta=self.fixture();raw['expanded_files']['excluded_entries'][0]['path']='/proc/456/map_files/300000-301000'
        v=audit_vmm(raw,e,'a',meta)
        self.assertEqual(v['saved_excluded_mapping_count'],0)


if __name__=='__main__':unittest.main()
