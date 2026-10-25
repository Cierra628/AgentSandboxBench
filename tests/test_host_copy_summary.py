"""Saved tiny JSON fixtures only; no processes, cache queries, or services."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from report_host_copy_summary import collect_pair,summarize,markdown


def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
def read(path):return json.loads(path.read_text())


class CopySummaryTests(unittest.TestCase):
    def fixture(self,root,schedule,*,zero=False):
        groups={}
        for mode,count,users in [('shared',19,7),('private',133,1)]:
            storage=schedule+'/'+mode;raw=root/storage/'prettier6604';points={};obs=dict(status='OBSERVED',schedule=schedule)
            for phase in ('P0','P1'):
                rows=[]
                for i in range(count):
                    n=0 if zero else 1+(int(i==0)*(1 if mode=='shared' else 2) if phase=='P1' else 0)
                    rows.append(dict(relative=f'layer-{i%19}',identity=dict(device=1,inode=i),users=[str(u) for u in range(users)],residency=dict(resident_bytes=n*4096,resident_pages=n,stable=True)))
                total=sum(r['residency']['resident_bytes'] for r in rows);points[phase]=total
                obs[phase]=dict(resident_bytes=total,elapsed_ns=10,objects=rows)
            if schedule=='bpo':obs['generated_layers']=dict(P0=dict(status='NOT_CREATED',resident_bytes=None),P1=dict(elapsed_ns=2,resident_bytes=8192,objects=[dict(step=step,identity=dict(size=4096),users=list(range(n)),residency=dict(resident_bytes=4096)) for step,n in [(11,7),(24,4)]]))
            write(raw/'result.json',dict(status='PASS',actual_action_count=189 if schedule=='grpo' else 84));write(raw/'host-full-rootfs.json',obs)
            groups[mode]=dict(storage=storage,P0_resident_bytes=points['P0'],P1_resident_bytes=points['P1'])
        s,p=groups['shared'],groups['private'];d0=p['P0_resident_bytes']-s['P0_resident_bytes'];d1=p['P1_resident_bytes']-s['P1_resident_bytes']
        value=dict(status='PASS_SAVED_RESULT_AUDIT' if schedule=='grpo' else 'PASS_BPO_COPY_PAIR_AUDIT',schedule=schedule,pair='saved-'+schedule,actual_actions=378 if schedule=='grpo' else 168,groups=groups,comparison=dict(preparation_difference_bytes=d0,endpoint_difference_bytes=d1,difference_change_bytes=d1-d0,endpoint_reduction_fraction=d1/p['P1_resident_bytes'] if p['P1_resident_bytes'] else None))
        path=root/(schedule+'.json');write(path,value);return path

    def test_same_definitions_and_generated_cache_is_not_added_to_main_savings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);g=self.fixture(root,'grpo');b=self.fixture(root,'bpo');value=summarize(g,b,root=root)
            self.assertEqual(value['total_accepted_actions'],546);self.assertEqual(value['new_live_actions'],0)
            for row in value['schedules']:
                self.assertEqual(row['P1_difference_bytes'],115*4096);self.assertEqual(row['runtime_difference_change_bytes'],4096)
                self.assertEqual(row['page_size_bytes'],4096);self.assertIsNone(row['standard_deviation'])
            self.assertIsNone(value['schedules'][0]['groups']['private']['generated_layers'])
            self.assertEqual(value['schedules'][1]['groups']['private']['generated_layers']['layers'][0]['resident_bytes'],4096)
            self.assertEqual(value['schedules'][1]['groups']['private']['total_query_ns'],22)
            self.assertIn('GRPO',markdown(value));self.assertIn('BPO',markdown(value))

    def test_changed_raw_values_duplicate_objects_and_unknown_residency_are_rejected(self):
        for case in ['number','duplicate','unknown']:
            with self.subTest(case=case),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);path=self.fixture(root,'grpo');raw=root/'grpo/private/prettier6604/host-full-rootfs.json';v=read(raw)
                if case=='number':v['P1']['resident_bytes']+=4096
                elif case=='duplicate':v['P1']['objects'][1]['identity']=v['P1']['objects'][0]['identity']
                else:v['P1']['resident_bytes']=None
                write(raw,v)
                with self.assertRaises(AssertionError):collect_pair(path,root=root)

    def test_zero_measured_denominator_is_undefined_not_a_zero_savings_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);g=self.fixture(root,'grpo',zero=True);b=self.fixture(root,'bpo',zero=True)
            value=summarize(g,b,root=root)
            self.assertTrue(all(row['reduction_fraction'] is None for row in value['schedules']))
            self.assertIn('未定义',markdown(value))

    def test_bundled_example_cli_needs_no_private_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'report'
            result=subprocess.run([sys.executable,'-B',ROOT/'scripts/report_host_copy_summary.py','--example','--output',out],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            value=read(out/'summary.json');self.assertEqual(value['total_accepted_actions'],546)
            self.assertEqual([r['P1_difference_bytes'] for r in value['schedules']],[26516766720,26514739200])
            self.assertEqual([r['runtime_difference_change_bytes'] for r in value['schedules']],[4349952,2322432])
            for row in value['schedules']:
                self.assertEqual(row['accepted_pairs'],1);self.assertIsNone(row['standard_deviation'])
            conflict=subprocess.run([sys.executable,'-B',ROOT/'scripts/report_host_copy_summary.py','--example','--grpo','/unused','--output',Path(tmp)/'unused'],capture_output=True,text=True,timeout=10)
            self.assertNotEqual(conflict.returncode,0);self.assertFalse((Path(tmp)/'unused').exists())


if __name__=='__main__':unittest.main()
