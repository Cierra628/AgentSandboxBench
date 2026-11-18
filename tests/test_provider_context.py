"""Shared accounting preparation; pure synthetic records and numpy arrays."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import provider_slot_cache as provider
from fast_cache_metadata import decode_array
from kernel_cache_metadata import PRESENT,SWAPPED
import test_provider_segments as fixtures
from test_kernel_cache_metadata import dump


class ProviderContextTests(unittest.TestCase):
    def data(self):return fixtures.ProviderSegmentTests().data(length=512)

    def array_call(self,args):
        array_args=(*args[:2],*(np.array(rows,dtype='<u8').reshape(-1,4) for rows in args[2:5]),*args[5:])
        return provider.account_observed_provider(*array_args,decoded=tuple(map(decode_array,args[:2])))

    def test_array_and_scalar_paths_match_lifetimes_versions_offsets_and_aliases(self):
        for kind in ('base','cow','swap','missing_guest','changed_guest','vmm_changed',
                'file_conflict','unknown_offset','alias','shmem','unsorted'):
            args=self.data()
            if kind=='cow':args[2][1]=(2,PRESENT|30,PRESENT|30,PRESENT|30)
            if kind=='swap':args[2][1]=(2,SWAPPED|30,SWAPPED|30,SWAPPED|30)
            if kind=='missing_guest':args[1]=dump([(2,4)])
            if kind=='changed_guest':args[1]=dump([(1,6),(2,4)])
            if kind=='vmm_changed':args[2][1]=(2,0,PRESENT|20,0)
            if kind=='file_conflict':args[3][1]=(2,1,PRESENT|21,PRESENT|21)
            if kind=='unknown_offset':args[3][0]=(1,(1<<64)-1,0,0)
            if kind=='alias':args[3][1]=(2,0,PRESENT|20,PRESENT|20)
            if kind=='shmem':args[0]=args[1]=dump([(1,3),(2,5)])
            if kind=='unsorted':args[0]=dump([(2,4),(1,2)])
            expected=provider.account_observed_provider(*args)
            self.assertEqual(self.array_call(args),expected,kind)

    def test_prepare_once_and_do_not_decode_bytes_again_for_live_arrays(self):
        args=self.data()
        with patch.object(provider,'decode',wraps=provider.decode) as decode,\
                patch.object(provider,'provider_context',wraps=provider.provider_context) as prepare:
            provider.account_observed_provider(*args)
            self.assertEqual(decode.call_count,2)
            self.assertEqual(prepare.call_count,1)
        with patch.object(provider,'decode',side_effect=AssertionError('duplicate byte decode')),\
                patch.object(provider,'provider_context',wraps=provider.provider_context) as prepare:
            self.array_call(args)
            self.assertEqual(prepare.call_count,1)

    def test_existing_raw_entry_retains_subset_order_and_validation(self):
        args=self.data()
        for rows in args[2:5]:rows.reverse()
        before=provider.account_observed_provider(*args)
        for rows in args[2:5]:rows.pop()
        provider.account_observed_provider(*args)
        self.assertIsInstance(before[1],set)
        args=self.data();args[0]=dump([(1,2),(1,4)])
        with self.assertRaisesRegex(ValueError,'duplicate'):
            provider.account_observed_provider(*args)

    def test_array_alignment_requirement_does_not_apply_to_old_callers(self):
        args=self.data();args[3].reverse()
        with self.assertRaises(AssertionError):self.array_call(args)

    def test_no_cross_point_cache_and_inputs_are_not_mutated(self):
        args=self.data();saved=copy.deepcopy(args)
        first=self.array_call(args)
        self.assertEqual(args,saved)
        args[1]=dump([(2,4)])
        second=self.array_call(args)
        self.assertNotEqual(first,second)

    def test_old_metadata_without_segments_can_use_shared_arrays(self):
        args=self.data()
        for frame in args[-2:]:
            for row in frame['rows']:row.pop('segments')
        self.assertEqual(self.array_call(args),provider.account_provider(*args))


if __name__=='__main__':unittest.main()
