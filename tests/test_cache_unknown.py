import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_cache_unknown import classify
from kernel_cache_metadata import PRESENT
from snapshot_cache_residency import FILE

class UnknownTests(unittest.TestCase):
    def test_partition_priorities_and_host_resident_anonymous_pages(self):
        left={i:2*i for i in range(1,12)};left[2]=0
        last=dict(left);last[1]=0;last[2]=50;last[3]=60;last[12]=70
        host=[];back=[];offsets={i:i for i in left}
        for i in range(4,12):
            e=PRESENT|100 if i in (4,5,6) else 0
            host.append([i,e,e,e]);back.append([i,i,0,0])
        host[0][3]=PRESENT|101
        host[1][1:]=[PRESENT|FILE|100]*3;back[1][2:]=[PRESENT|101]*2
        back[3][2:]=[PRESENT|102]*2
        offsets[8]=999
        back[5][1]=(1<<64)-1;offsets[9]=(1<<64)-1
        back[6][2:]=[PRESENT|103,PRESENT|104]
        got=classify(left,last,host,back,offsets)
        self.assertEqual(sum(got.values()),12)
        self.assertEqual(got['confirmed'],2)
        self.assertEqual(got['added_or_reused'],2)
        for name in ['metadata_stale','metadata_before_stale','vmm_pte_changed','file_pfn_conflict',
                     'ram_region_changed','anonymous_without_pte','backend_pfn_changed','backend_without_pfn']:
            self.assertEqual(got[name],1,name)

    def test_raw_layer_confirmation_and_changes_partition_without_hiding_pte_conflicts(self):
        left={i:1 for i in range(1,5)}
        host=[[i,0,0,0] for i in left]
        host[3][1:]=[PRESENT|FILE|100]*3
        direct=[[i,i,0,0] for i in left];direct[3][2:]=[PRESENT|101]*2
        lower=[[1,1,PRESENT|110,PRESENT|110],[2,2,0,0],
               [3,3,PRESENT|111,PRESENT|112],[4,4,PRESENT|113,PRESENT|113]]
        got=classify(left,left,host,direct,{i:i for i in left},lower)
        self.assertEqual(sum(got.values()),4)
        for key in ['confirmed','backend_without_pfn','backend_pfn_changed','file_pfn_conflict']:
            self.assertEqual(got[key],1,key)

if __name__=='__main__':unittest.main()
