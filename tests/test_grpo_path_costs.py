"""Synthetic timing records only; no services, task payloads or devices."""
import csv,json
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from analyze_grpo_path_costs import possible_matches,analyze_run,paired_paths


def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))


class PathCostTests(unittest.TestCase):
    def test_forced_global_assignment_is_not_nearest_time(self):
        self.assertEqual(possible_matches({'a':[1,2],'b':[1]}),{'a':[2],'b':[1]})

    def test_ambiguous_assignments_stay_ambiguous(self):
        self.assertEqual(possible_matches({'a':[1,2],'b':[1,2]}),{'a':[1,2],'b':[1,2]})
        with self.assertRaisesRegex(AssertionError,'no complete'):possible_matches({'a':[1],'b':[1]})

    def fixture(self,raw,mode='off'):
        raw.mkdir();paths={'a':['segment'],'b':['segment']}
        write(raw/'result.json',dict(status='PASS',grpo_setup_schedule='create-all-before-setup',
            role_first_segment_monotonic_ns={'a':8,'b':9},paths={r:dict(guest_action_sum_ns=5) for r in paths}))
        write(raw/'effective-config.json',dict(executed_paths=paths))
        (raw/'sandbox-ids.tsv').write_text('role\tsandbox_id\na\taaaa\nb\tbbbb\n')
        events=[]
        for i,start,duration in ((1,10,20),(2,11,21),(3,40,5),(4,42,6)):
            event=dict(index=i,stage=f'{i:04}-controller-call',start_monotonic_ns=start,duration_ns=duration,exit_code=0);events.append(event)
            text=''
            if i>2:
                role='a' if i==3 else 'b';sid='aaaa' if i==3 else 'bbbb'
                text=f'Downloaded directory {sid}:/workspace/artifacts/exp5/{role}/segment to /saved/rollouts/{role}/segment\n'
            (raw/(event['stage']+'.stdout')).write_text(text)
        write(raw/'events.json',events)
        for role in paths:
            p=raw/'rollouts'/role/'segment';p.mkdir(parents=True)
            (p/'steps.tsv').write_text(f'branch\tround\tduration_ns\n{role}\tsegment\t5\n')
            for name in ('guest-memory.tsv','guest-vmstat.tsv'):(p/name).write_text('timestamp_ns\n'+('1\n'*4 if mode=='on' else ''))
        return raw

    def test_download_identity_and_bounds_preserve_exact_path_window(self):
        with tempfile.TemporaryDirectory() as tmp,patch('analyze_grpo_path_costs.ROOT',Path(tmp)):
            raw=self.fixture(Path(tmp)/'raw');v=analyze_run(raw,'off')
            self.assertEqual(v['window_ns'],38);self.assertEqual(v['bounded_exec_assignments'],2)
            self.assertEqual(v['critical_role'],'b')
            for path in v['paths']:
                self.assertEqual((path['exec_min_ns'],path['exec_max_ns']),(20,21))
                self.assertEqual(path['exec_assignment'],'BOUNDED')
                self.assertEqual(path['command_ns']+path['exec_minus_command_min_ns']+path['download_ns']+path['gap_max_ns'],path['path_window_ns'])
            critical=next(x for x in v['paths'] if x['role']=='b')
            self.assertEqual(critical['start_offset_from_original_window_ns'],-1)
            self.assertEqual(critical['path_window_ns']+critical['start_offset_from_original_window_ns'],v['window_ns'])
            p=raw/'0003-controller-call.stdout';p.write_text(p.read_text().replace('aaaa:','bbbb:'))
            with self.assertRaises(AssertionError):analyze_run(raw,'off')

    def test_on_boundary_rows_are_counted_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp,patch('analyze_grpo_path_costs.ROOT',Path(tmp)):
            raw=self.fixture(Path(tmp)/'raw','on');v=analyze_run(raw,'on')
            self.assertEqual((v['boundary_memory_rows'],v['boundary_vmstat_rows']),(8,8))
            (raw/'rollouts/a/segment/guest-vmstat.tsv').write_text('timestamp_ns\n')
            with self.assertRaises(AssertionError):analyze_run(raw,'on')

    def test_paired_differences_keep_both_bounds(self):
        with tempfile.TemporaryDirectory() as tmp,patch('analyze_grpo_path_costs.ROOT',Path(tmp)):
            off=analyze_run(self.fixture(Path(tmp)/'off'),'off')
            on=analyze_run(self.fixture(Path(tmp)/'on','on'),'on')
            data=paired_paths(dict(pairs=[dict(pair=1,runs=[off,on])]))
            self.assertEqual(len(data),2)
            self.assertEqual(data[0]['exec_residual_difference_min_ns'],-1)
            self.assertEqual(data[0]['exec_residual_difference_max_ns'],1)


if __name__=='__main__':unittest.main()
