"""Offline Sphinx grading, profile and frozen-sampler checks; no services."""
import json
import csv
import copy
import hashlib
import tempfile
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from sphinx7590_inputs import ROOT,WORKLOAD,FILES,BACKGROUND_FILES,grade_xml,verify_final_state,verify_image_background,check_pipeline_exit,failed_find_action
from xarray6992_persistent_sampling import instrument
from xarray6992_offline_image import budget,wrapper
from prepare_xarray6992_image import SPHINX_IMAGE,descriptors,preparation_end,remaining_jobs,CHUNK
from isolated_storage import GIB
from validate_isolated_agentenv import main
from sphinx7590_boundary import PREFIX,NEXT,verify as verify_boundary,configuration as boundary_configuration
from run_sphinx_command_diagnostic import check_trace


class SphinxTests(unittest.TestCase):
    def test_find_diagnostic_records_failure_without_changing_formal_policy(self):
        cfg=json.loads((ROOT/'configs/sphinx7590-backbone.json').read_text())
        samples=json.loads((WORKLOAD/'agent-sampling.json').read_text())['segments']
        with tempfile.TemporaryDirectory() as temp:
            raw=Path(temp)
            for segment in cfg['path'][:2]:
                directory=raw/'rollouts/backbone'/segment;directory.mkdir(parents=True)
                manifest=(WORKLOAD/'segments'/segment/'actions.tsv').read_bytes()
                (directory/'actions.tsv').write_bytes(manifest)
                frozen=raw/'frozen-input/segments'/segment;frozen.mkdir(parents=True)
                (frozen/'actions.tsv').write_bytes(manifest)
                for kind in ('stdout','stderr'):
                    (directory/kind).mkdir()
                    for i in range(len(cfg['expected_exit_codes'][segment])):(directory/kind/f'{i+1:03}.log').write_bytes(b'')
                (directory/'patch.diff').write_bytes(b'')
                (directory/'guest-memory.tsv').write_text('sample\n')
            with (raw/'rollouts/backbone/read_repo/actions.tsv').open() as stream:first=next(csv.DictReader(stream,delimiter='\t'))
            pinned=dict(command=first['sha256'],stdout=hashlib.sha256(b'').hexdigest(),stderr=hashlib.sha256(b'').hexdigest(),prefix_patch=hashlib.sha256(b'').hexdigest())
            def steps(find_exit,other_exit=None):
                for segment in cfg['path'][:2]:
                    codes=list(cfg['expected_exit_codes'][segment])
                    if segment=='trace_cpp_parser':
                        codes[4]=find_exit
                        if other_exit is not None:codes[3]=other_exit
                    (raw/'rollouts/backbone'/segment/'steps.tsv').write_text('step\texit_code\tbranch\tround\tduration_ns\n'+''.join(f'{i+1:03}\t{code}\tbackbone\t{segment}\t1\n' for i,code in enumerate(codes)))
            with patch('sphinx7590_inputs.PIPELINE_REFERENCE',pinned):
                for code in (0,1):
                    steps(code)
                    checked=check_trace(raw,raw,cfg,samples,{s:s for s in cfg['path']},True)
                    self.assertEqual(checked['strict_prefix_status'],'FAIL' if code else 'PASS')
                    if code:
                        with self.assertRaises(AssertionError):check_trace(raw,raw,cfg,samples,{s:s for s in cfg['path']})
                for code in (2,141):
                    steps(code)
                    with self.assertRaises(AssertionError):check_trace(raw,raw,cfg,samples,{s:s for s in cfg['path']},True)
                steps(1,other_exit=99)
                with self.assertRaises(AssertionError):check_trace(raw,raw,cfg,samples,{s:s for s in cfg['path']},True)
            self.assertEqual(next(s for s in samples if s['role']=='backbone' and s['segment']=='trace_cpp_parser')['actions'][4]['exit_code'],0)

    def test_failed_find_selection_preserves_failure_and_requires_frozen_manifest(self):
        cfg=json.loads((ROOT/'configs/sphinx7590-backbone.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            raw=Path(temp);directory=raw/'rollouts/backbone/trace_cpp_parser';directory.mkdir(parents=True)
            self.assertIsNone(failed_find_action(raw,cfg))
            (directory/'actions.tsv').write_bytes((WORKLOAD/'segments/trace_cpp_parser/actions.tsv').read_bytes())
            for code in (0,1,2,141):
                (directory/'steps.tsv').write_text(f'step\texit_code\n005\t{code}\n')
                row=failed_find_action(raw,cfg)
                self.assertEqual(row is not None,code==1)
                if row:self.assertTrue(row['command'].endswith('2>/dev/null | head'))
            (directory/'steps.tsv').write_text('step\texit_code\n005\t1\n')
            (directory/'actions.tsv').write_text('modified')
            with self.assertRaises(AssertionError):failed_find_action(raw,cfg)

    def test_version_two_allows_only_pinned_second_pipeline(self):
        data=('command',b'stdout',b'',b'patch')
        pinned={k:hashlib.sha256(v.encode() if isinstance(v,str) else v).hexdigest()
            for k,v in zip(('command','stdout','stderr','prefix_patch'),data)}
        with patch('sphinx7590_inputs.SECOND_PIPELINE_REFERENCE',pinned):
            with self.assertRaises(AssertionError):check_pipeline_exit('core_patch','001',141,0,*data)
            self.assertIsNone(check_pipeline_exit('core_patch','001',141,141,*data,policy_version=2))
            self.assertEqual(check_pipeline_exit('core_patch','001',141,0,*data,policy_version=2)['actual'],0)
            for index in range(4):
                changed=list(data);changed[index]+='changed' if index==0 else b'changed'
                with self.assertRaises(AssertionError):check_pipeline_exit('core_patch','001',141,0,*changed,policy_version=2)
            for segment,step,expected,actual in [('core_patch','002',141,0),('core_verify','001',0,141),('core_patch','001',141,1),('core_patch','001',0,0)]:
                with self.assertRaises(AssertionError):check_pipeline_exit(segment,step,expected,actual,*data,policy_version=2)
            with self.assertRaises(AssertionError):check_pipeline_exit('core_patch','001',141,0,*data,policy_version=3)

    def test_pipeline_policy_requires_exact_first_action_and_all_outputs(self):
        data=('command',b'stdout',b'',b'patch')
        pinned={k:hashlib.sha256(v.encode() if isinstance(v,str) else v).hexdigest()
            for k,v in zip(('command','stdout','stderr','prefix_patch'),data)}
        with patch('sphinx7590_inputs.PIPELINE_REFERENCE',pinned):
            self.assertIsNone(check_pipeline_exit('read_repo','001',141,141,*data))
            self.assertEqual(check_pipeline_exit('read_repo','001',141,0,*data)['actual'],0)
            for expected,actual in ((141,1),(141,2),(0,0),(0,141)):
                with self.assertRaises(AssertionError):check_pipeline_exit('read_repo','001',expected,actual,*data)
            for index in range(4):
                changed=list(data);changed[index]+='changed' if index==0 else b'changed'
                with self.assertRaises(AssertionError):check_pipeline_exit('read_repo','001',141,0,*changed)
            for segment,step in (('read_repo','002'),('core_patch','001'),('backbone_finish','001')):
                with self.assertRaises(AssertionError):check_pipeline_exit(segment,step,141,0,*data)
                self.assertIsNone(check_pipeline_exit(segment,step,1,1,*data))

    def test_boundary_reference_supports_original_file_open_styles(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            for name in FILES:
                p=base/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('')
            cfg=dict(base_file_sha256={name:hashlib.sha256(b'').hexdigest() for name in FILES})
            codes=["text=open('sphinx/domains/cpp.py').read()\nwith open('sphinx/domains/cpp.py','w') as f:\n    f.write(text+'1')",
                   "open('sphinx/domains/cpp.py','w').write(open('sphinx/domains/cpp.py').read()+'2')",
                   "with open('sphinx/util/cfamily.py','r') as f:\n    text=f.read()\nf=open('sphinx/util/cfamily.py','w')\nf.write(text+'3')"]
            actions=[{},{}]+[dict(command="python3 - <<'EOF'\n"+code+'\nEOF') for code in codes]
            samples=base/'samples.json'
            samples.write_text(json.dumps(dict(segments=[dict(role='backbone',segment='core_verify',actions=actions)])))
            result=boundary_configuration(cfg,base,samples,{name:'a'*64 for name in BACKGROUND_FILES})
            self.assertEqual(result['checkpoint_file_sha256'],dict(zip(FILES,[hashlib.sha256(b'12').hexdigest(),hashlib.sha256(b'3').hexdigest()])))
    def test_step29_restore_checks_progress_source_and_next_segment(self):
        cfg=json.loads((ROOT/'configs/sphinx7590-backbone.json').read_text())
        cfg['checkpoint_file_sha256']={name:'a'*64 for name in FILES}
        cfg['checkpoint_background_sha256']={name:'b'*64 for name in BACKGROUND_FILES}
        state=dict(base_commit=cfg['base_commit'],next_segment=NEXT,next_segment_absent=True,
                   completed_steps=[dict(segment=s,step=f'{i:03}',exit_code=c) for s in PREFIX for i,c in enumerate(cfg['expected_exit_codes'][s],1)],
                   files_sha256={'/testbed/'+name:digest for name,digest in {**cfg['checkpoint_file_sha256'],**cfg['checkpoint_background_sha256']}.items()})
        for s in PREFIX:state['files_sha256']['/workspace/artifacts/exp5/backbone/'+s+'/actions.tsv']=cfg['input_sha256']['segments/'+s+'/actions.tsv']
        self.assertEqual(verify_boundary(state,state,cfg)['continuation_global_step'],30)
        for kind in ('wrong-exit','reordered','incomplete','next-started','source-change','background-change','manifest-change'):
            bad=copy.deepcopy(state)
            if kind=='wrong-exit':bad['completed_steps'][0]['exit_code']=0
            elif kind=='reordered':bad['completed_steps'][1:3]=reversed(bad['completed_steps'][1:3])
            elif kind=='incomplete':bad['completed_steps'].pop()
            elif kind=='next-started':bad['next_segment_absent']=False
            elif kind=='source-change':bad['files_sha256']['/testbed/sphinx/domains/cpp.py']='c'*64
            elif kind=='background-change':bad['files_sha256']['/testbed/setup.py']='c'*64
            else:bad['files_sha256']['/workspace/artifacts/exp5/backbone/core_verify/actions.tsv']='c'*64
            with self.assertRaises(AssertionError):verify_boundary(bad,bad,cfg)
            with self.assertRaises(AssertionError):verify_boundary(state,bad,cfg)
    def test_resume_reuses_completed_ranges_but_rejects_invalid_receipts(self):
        row=dict(digest='sha256:'+'a'*64,size=CHUNK+7)
        passed=dict(status='PASS',digest=row['digest'],offset=0,length=CHUNK)
        self.assertEqual(remaining_jobs([row],[passed]),[(row,CHUNK)])
        self.assertEqual(remaining_jobs([row],[dict(passed,status='FAIL')]),[(row,0),(row,CHUNK)])
        self.assertEqual(remaining_jobs([row],[passed],{row['digest']}),[])
        for bad in (dict(passed,length=3),dict(passed,offset=1),dict(passed,digest='other')):
            with self.assertRaises(AssertionError):remaining_jobs([row],[bad])
    def test_ram_preparation_does_not_renew_experiment_window(self):
        args=SimpleNamespace(ram_only_preparation=True,window_start_utc=None,window_end_utc=None)
        self.assertIsNone(preparation_end(args))
        args.window_start_utc='2026-10-09T00:00:00+00:00'
        with self.assertRaises(ValueError):preparation_end(args)
        args.window_start_utc=None;args.ram_only_preparation=False
        with self.assertRaises(ValueError):preparation_end(args)
        args.window_start_utc='2026-10-09T00:00:00+00:00';args.window_end_utc='2026-10-09T00:20:00+00:00'
        with patch('prepare_xarray6992_image.confirmed_window') as confirmed:
            self.assertIsInstance(preparation_end(args),float)
            confirmed.assert_called_once_with(args.window_start_utc,args.window_end_utc)
    def xml(self,children=None,drop=False,duplicate=False):
        cases=['<testcase file="tests/test_domain_cpp.py" classname="test_domain_cpp" name="case'+str(i)+'">'+(children or {}).get(i,'')+'</testcase>' for i in range(25)]
        if drop:cases.pop()
        if duplicate:cases.append(cases[0])
        return '<testsuite>'+''.join(cases)+'</testsuite>'
    def grade(self,xml,code):
        required=['tests/test_domain_cpp.py::case'+str(i) for i in range(25)]
        return grade_xml(xml,required,required[:1],required[1:],code)
    def test_complete_selected_results_and_real_failure(self):
        self.assertEqual(self.grade(self.xml(),0)['status'],'PASS')
        result=self.grade(self.xml({0:'<failure/>'}),1)
        self.assertEqual((result['F2P_passed'],result['P2P_passed'],result['failed']),(0,24,1))
    def test_missing_duplicate_skipped_and_infrastructure_are_not_pass(self):
        self.assertEqual(self.grade(self.xml({0:'<skipped/>'}),0)['status'],'FAIL')
        for xml,code in [(self.xml(drop=True),0),(self.xml(duplicate=True),0),(self.xml({0:'<error/>'}),1),
                         (self.xml(),124),(self.xml(),1),(self.xml().replace('tests/test_domain_cpp.py','other.py'),0)]:
            with self.assertRaises(AssertionError):self.grade(xml,code)
    def test_sphinx_manifest_and_task_specific_budget(self):
        manifest=json.loads((ROOT/'configs/sphinx7590-image-manifest.json').read_text())
        self.assertEqual(len(descriptors(manifest,SPHINX_IMAGE)),14)
        with self.assertRaises(AssertionError):descriptors(manifest)
        result=dict(downloaded_bytes=GIB,unpacked_regular_rounded_bytes=3*GIB)
        with patch('xarray6992_offline_image.validate',return_value=(result,None)),patch('xarray6992_offline_image.allocated',return_value=6*GIB):
            self.assertEqual(budget(None,None,22*GIB,SPHINX_IMAGE)['estimated_peak_bytes'],int(18.5*GIB))
            with self.assertRaises(AssertionError):budget(None,None,18*GIB,SPHINX_IMAGE)
            with self.assertRaises(AssertionError):budget(None,None,20*GIB,SPHINX_IMAGE)
        measured=dict(downloaded_bytes=935166685,unpacked_regular_rounded_bytes=2033913856)
        with patch('xarray6992_offline_image.validate',return_value=(measured,None)),patch('xarray6992_offline_image.allocated',return_value=5834698752):
            self.assertGreater(budget(None,None,18*GIB,SPHINX_IMAGE)['unallocated_margin_bytes'],2*GIB)
    def test_sphinx_does_not_inherit_xarray_diagnostic_exception(self):
        for options in (dict(python_diagnostic_continuation=True),dict(python_checkpoint=True),dict(python_readonly_replay=True)):
            args=SimpleNamespace(python_task_profile='sphinx7590',python_backbone_inputs=Path('/not-read'),exp1_checkpoint=False,**options)
            with patch('validate_isolated_agentenv.os.geteuid',return_value=0),self.assertRaisesRegex(ValueError,'strict native frozen replay'):main(args)
    def test_single_sampler_retains_original_frozen_commands(self):
        text=(WORKLOAD/'guest-runner.sh').read_text();modified=instrument(text)
        self.assertIn('timeout --signal=TERM --kill-after=10s',modified)
        self.assertEqual(text.split('  (cd /testbed && timeout',1)[1].split('  status=$?',1)[0],modified.split('  (cd /testbed && timeout',1)[1].split('  status=$?',1)[0])
        script=wrapper(Path('/readonly/regctl.real'),Path('/readonly/oci'),SPHINX_IMAGE)
        self.assertIn(SPHINX_IMAGE,script)
        self.assertNotIn('pydata__xarray-6992',script)

    def test_pinned_image_background_is_unchanged_and_not_task_output(self):
        background=dict(files={name:'a'*64 for name in BACKGROUND_FILES},patch=(ROOT/'configs/sphinx7590-image-background.patch').read_text())
        source=dict(git_status=' M setup.py\n M tox.ini\n',image_background=background)
        final=dict(files={name:'b'*64 for name in FILES},changed=list(FILES)+list(BACKGROUND_FILES),image_background=copy.deepcopy(background))
        cfg=dict(final_file_sha256=final['files'])
        self.assertEqual(verify_final_state(source,final,cfg)['status'],'PASS')
        for name in ('file-change','patch-change','extra-path','task-change'):
            changed=copy.deepcopy(final)
            if name=='file-change':changed['image_background']['files']['setup.py']='c'*64
            elif name=='patch-change':changed['image_background']['patch']+='unexpected edit\n'
            elif name=='extra-path':changed['changed'].append('other.py')
            else:changed['files']['sphinx/domains/cpp.py']='d'*64
            with self.assertRaises(AssertionError):verify_final_state(source,changed,cfg)
        unknown=copy.deepcopy(background);unknown['patch']+='unexpected edit\n'
        with self.assertRaises(AssertionError):verify_image_background(unknown)


if __name__=='__main__':unittest.main()
