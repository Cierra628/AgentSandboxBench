"""Synthetic saved records and code copies only; no services, devices or task files."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from report_grpo_create_setup_repeats import CODE,ORDERS,SCHEDULE,summarize,phases


def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))


class NewScheduleRepeatsTests(unittest.TestCase):
    def fixture(self,root):
        pairs=[]
        for number,(order,off,on) in enumerate(zip(ORDERS,(100,200,300),(110,180,360)),1):
            directory=root/f'pair-{number}';directory.mkdir();pairs.append(directory)
            write(directory/'plan.json',dict(grpo_setup_schedule=SCHEDULE,order=order,planned_actions=378,host_sampling=False,pss=False))
            write(directory/'result.json',dict(status='PASS',completed=2,source_returned_to_stopped=True))
            for name in CODE:
                p=directory/'code'/name;p.parent.mkdir(exist_ok=True);p.write_text(name)
            runs=[]
            for mode in order:
                elapsed=on if mode=='on' else off
                raw=directory/mode;raw.mkdir();(raw/'code').mkdir()
                (raw/'code/run_prettier6604_grpo.py').write_text('run_prettier6604_grpo.py')
                (raw/'sandbox-ids.tsv').write_text('sandbox_id\n'+''.join(f'{number}-{mode}-{i}\n' for i in range(7)))
                task=dict(status='PASS',grpo_setup_schedule=SCHEDULE,rollout_start_monotonic_ns=0,
                    all_created_monotonic_ns=20,all_prepared_monotonic_ns=30,first_tool_start_monotonic_ns=31,
                    controller_elapsed_ns=elapsed,controller_end_monotonic_ns=elapsed,rollout_end_monotonic_ns=elapsed-10,
                    created_to_controller_end_ns=elapsed-20,create_to_setup_ns=10)
                write(raw/'result.json',task)
                write(raw/'events.json',[dict(stage='001-controller-call',start_monotonic_ns=32,duration_ns=elapsed-52)])
                sessions=[]
                if mode=='on':
                    write(raw/'events.json',[dict(stage='001-controller-call',start_monotonic_ns=32,duration_ns=elapsed-52),
                        dict(stage='002-monitor-phase',start_monotonic_ns=31,duration_ns=1)])
                    sessions=[dict(samples=3) for _ in range(7)]
                    (raw/'runtime-code').mkdir()
                    for name in ('sandbox_runtime_monitor.py','guest_memory_collector.py'):(raw/'runtime-code'/name).write_text(name)
                    write(raw/'runtime-monitor.json',dict(sessions=[dict(receipt=dict(cpu_user_seconds=.01,cpu_system_seconds=0,elapsed_ns=1_000_000_000)) for _ in range(7)]))
                runs.append(dict(status='PASS',mode=mode,actual_actions=189,grpo_setup_schedule=SCHEDULE,
                    paths={'role':['same']},patches={'role':'same'},official={'role':dict(passed=0,failed=2)},
                    raw=str(raw),storage=str(raw),workflow=str(raw),report=str(raw),sessions=sessions,minimum_free_disk_gib=100,
                    timings_ns=dict(controller=elapsed,created_to_controller_end_ns=elapsed-20,create_to_setup_ns=10,
                        monitor_control_call_sum=1 if mode=='on' else 0)))
            write(directory/'runs.json',runs)
            timings={r['mode']:r['timings_ns'] for r in runs}
            comparison={k:dict(off_ns=timings['off'][k],on_ns=timings['on'][k],
                change_percent=(timings['on'][k]/timings['off'][k]-1)*100 if timings['off'][k] else None) for k in timings['off']}
            write(directory/'comparison.json',dict(grpo_setup_schedule=SCHEDULE,comparison=comparison))
        return pairs

    def test_complete_three_keep_signed_range_and_zero_reference(self):
        with tempfile.TemporaryDirectory() as tmp,patch('report_grpo_create_setup_repeats.ROOT',Path(tmp)):
            result=summarize(self.fixture(Path(tmp)))
            self.assertEqual((result['actual_actions'],result['guest_count'],result['sample_count']),(1134,42,63))
            self.assertEqual(result['additional_actions_after_first_pair'],756)
            self.assertAlmostEqual(result['statistics']['controller']['paired_change_median_percent'],10)
            lo,hi=result['statistics']['controller']['paired_change_range_percent']
            self.assertAlmostEqual(lo,-10);self.assertAlmostEqual(hi,20)
            self.assertIsNone(result['statistics']['monitor_control_call_sum']['paired_change_median_percent'])
            self.assertEqual(result['pairs'][0]['runs'][1]['monitor_control_breakdown']['monitor-phase']['sum_ns'],1)
            self.assertEqual(result['pairs'][1]['order'],['on','off'])

    def test_old_schedule_wrong_order_code_or_patch_rejected(self):
        for case in ('schedule','order','snapshot','collector','patch','comparison','identity'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as tmp,patch('report_grpo_create_setup_repeats.ROOT',Path(tmp)):
                paths=self.fixture(Path(tmp));p=paths[1]
                if case in ('schedule','order'):
                    v=json.loads((p/'plan.json').read_text())
                    v['grpo_setup_schedule' if case=='schedule' else 'order']='per-guest-setup' if case=='schedule' else ['off','on']
                    write(p/'plan.json',v)
                elif case=='snapshot':(p/'code'/CODE[0]).write_text('changed')
                elif case=='collector':(p/'on/runtime-code/guest_memory_collector.py').write_text('changed')
                elif case=='identity':(p/'on/sandbox-ids.tsv').write_text((paths[0]/'on/sandbox-ids.tsv').read_text())
                elif case=='comparison':
                    v=json.loads((p/'comparison.json').read_text());v['comparison']['controller']['on_ns']+=1;write(p/'comparison.json',v)
                else:
                    v=json.loads((p/'runs.json').read_text());v[0]['patches']['role']='changed';write(p/'runs.json',v)
                with self.assertRaises(AssertionError):summarize(paths)

    def test_count_or_duplicates_rejected_before_read(self):
        for paths in ([Path('one')],[Path('same')]*3):
            with self.assertRaises(AssertionError):summarize(paths)

    def test_phase_sum_and_new_window_are_not_redefined(self):
        task=dict(all_created_monotonic_ns=20,all_prepared_monotonic_ns=30,first_tool_start_monotonic_ns=31,
            controller_end_monotonic_ns=100,controller_elapsed_ns=100,rollout_start_monotonic_ns=0,
            rollout_end_monotonic_ns=90,created_to_controller_end_ns=80,create_to_setup_ns=10)
        events=[dict(stage='001-controller-call',start_monotonic_ns=32,duration_ns=48)]
        self.assertEqual(sum(phases(task,events).values()),100)
        with self.assertRaises(AssertionError):phases(dict(task,created_to_controller_end_ns=70),events)


if __name__=='__main__':unittest.main()
