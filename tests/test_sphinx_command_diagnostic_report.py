"""Synthetic process observations only; no services or existing results."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_sphinx_command_diagnostic import process_summary
from run_sphinx_command_diagnostic import check_trace
from sphinx7590_inputs import sha


class ProcessSummaryTests(unittest.TestCase):
    def test_shell_is_not_misclassified_and_network_states_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);rows=[]
            for elapsed,ticks in [(0,1),(10**9,31)]:
                def process(pid,command,inet):return dict(pid=pid,start_ticks='2',command=command,
                    user_ticks=ticks,system_ticks=0,state='S',wchan='poll',socket_fds=len(inet),
                    inet_socket_states=inet,read_bytes=elapsed//10**9)
                rows.append(dict(elapsed_ns=elapsed,processes=[
                    process(1,'bash -c pip list',[]),process(2,'python /opt/bin/pip list',['tcp:01'])]))
            (root/'processes.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            result=process_summary(root,100)
            self.assertEqual(len(result['processes']),1);p=result['processes'][0]
            self.assertEqual(p['kind'],'pip');self.assertAlmostEqual(p['observed_cpu_seconds'],.3)
            self.assertEqual(p['inet_states'],{'tcp:01':2});self.assertEqual(p['inet_socket_samples'],2)

    def test_missing_old_network_fields_mean_unmeasured_not_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            p=dict(pid=2,start_ticks='1',command='python /opt/bin/pip list',user_ticks=0,
                system_ticks=0,state='R',wchan='0',socket_fds=0,read_bytes=0)
            (root/'processes.jsonl').write_text(json.dumps(dict(elapsed_ns=0,processes=[p]))+'\n')
            value=process_summary(root,100)['processes'][0]
            self.assertIsNone(value['inet_socket_samples']);self.assertIsNone(value['inet_states'])

    def test_output_equality_cannot_hide_changed_frozen_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            raw=Path(temp)/'raw';reference=Path(temp)/'reference';cfg=dict(path=['read_repo','trace_cpp_parser'],input_sha256={})
            for segment in cfg['path']:
                for base in (raw,reference):
                    directory=base/'rollouts/backbone'/segment;directory.mkdir(parents=True)
                    for name in ('stdout','stderr'):(directory/name).mkdir()
                    (directory/'actions.tsv').write_text('step\tsha256\tcommand\n001\ta\ttrue\n')
                    (directory/'patch.diff').write_text('same patch')
                cfg['input_sha256']['segments/'+segment+'/actions.tsv']=sha(reference/'rollouts/backbone'/segment/'actions.tsv')
            with patch('run_sphinx_command_diagnostic.verify_path',return_value=dict(action_count=13)):
                self.assertEqual(check_trace(raw,reference,cfg,{},{}),dict(action_count=13))
                (raw/'rollouts/backbone/trace_cpp_parser/actions.tsv').write_text('changed frozen command')
                with self.assertRaises(AssertionError):check_trace(raw,reference,cfg,{},{})


if __name__=='__main__':unittest.main()
