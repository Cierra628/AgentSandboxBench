import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import model_tool_agent as agent
import tool_trajectory as trace
from platform_tasks import configuration, commands, ROOT


class OnlineAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.guest=self.root/'guest';self.guest.mkdir()
        self.cfg=self.root/'config.json'
        value=json.loads((ROOT/'configs/online-agent-glm.json').read_text())
        value['max_model_calls']=3;value['max_tool_calls']=2
        self.cfg.write_text(json.dumps(value))
        self.trajectory=self.root/'captured'
        trace.begin(self.trajectory,{'session_semantics':'fresh bash per tool call; filesystem persists',
            'tracked_files':[str(self.guest/'state')]},{str(self.guest/'state'):None})
        self.executions=0

    def cli(self,label,*args,**kw):
        self.executions+=1
        return subprocess.check_output([sys.executable,str(ROOT/'scripts/guest_tool_capture.py'),args[-1]],text=True)

    def reply(self,command=None):
        message={'role':'assistant','content':'done'}
        if command is not None:message.update(content=None,tool_calls=[{'id':'call-1','type':'function',
            'function':{'name':'run_shell','arguments':json.dumps({'command':command})}}])
        return {'choices':[{'message':message,'finish_reason':'tool_calls' if command is not None else 'stop'}],
                'usage':{'total_tokens':10}}

    def run_agent(self,completion):
        with patch.object(agent,'CWD',str(self.guest)):
            return agent.run(self.cfg,self.root/'online',self.trajectory,self.cli,'guest',{'PATH':'/usr/bin:/bin'},lambda _:None,
                             completion=completion,key='test-only-secret')

    def test_feedback_capture_and_frozen_replay_use_actual_execution(self):
        requests=[]
        def complete(cfg,key,payload,timeout):
            requests.append(json.loads(json.dumps(payload)))
            if len(requests)==1:return self.reply("printf 'alpha\\n' > state; cat state")
            feedback=json.loads(payload['messages'][-1]['content'])
            self.assertEqual(feedback,{'exit_code':0,'stdout':'alpha\n','stderr':''})
            return self.reply()
        result=self.run_agent(complete)
        self.assertEqual(result['status'],'PASS');self.assertEqual(len(result['tool_calls']),1)
        trace.freeze(self.trajectory,{str(self.guest/'state'):trace.sha(self.guest/'state')})
        (self.guest/'state').unlink()
        self.assertEqual(trace.replay(self.trajectory,self.root/'replayed',self.cli,'other')['status'],'PASS')
        for p in (self.root/'online').glob('*.json'):self.assertNotIn('test-only-secret',p.read_text())

    def test_failure_is_returned_to_model_instead_of_aborting(self):
        count=0
        def complete(cfg,key,payload,timeout):
            nonlocal count
            count+=1
            if count==1:return self.reply('printf failed >&2; exit 7')
            self.assertEqual(json.loads(payload['messages'][-1]['content'])['exit_code'],7)
            return self.reply()
        result=self.run_agent(complete)
        self.assertEqual(result['tool_calls'][0]['exit_code'],7)

    def test_nested_json_command_is_not_unwrapped_and_failure_is_returned(self):
        command=json.dumps({'command':'printf unexpectedly_unwrapped > state'})
        count=0
        def completion(cfg,key,payload,timeout):
            nonlocal count
            count+=1
            if count==1:return self.reply(command)
            self.assertNotEqual(json.loads(payload['messages'][-1]['content'])['exit_code'],0)
            return self.reply()
        result=self.run_agent(completion)
        self.assertEqual(result['status'],'PASS')
        self.assertFalse(self.guest.joinpath('state').exists())
        self.assertEqual(json.loads((self.trajectory/'steps/001/request.json').read_text())['command'],command)
        self.assertEqual(len(result['tool_calls']),1)

    def test_unknown_tool_is_rejected_before_execution(self):
        response=self.reply('true');response['choices'][0]['message']['tool_calls'][0]['function']['name']='host_shell'
        with self.assertRaises(AssertionError):self.run_agent(lambda *_:response)
        self.assertEqual(self.executions,0)
        self.assertEqual(json.loads((self.root/'online/result.json').read_text())['status'],'FAIL')

    def test_failed_request_keeps_timing_and_completed_tools(self):
        count=0
        def completion(cfg,key,payload,timeout):
            nonlocal count
            count+=1
            if count==1:return self.reply('printf saved > state')
            raise TimeoutError('timed out '+key)
        with self.assertRaises(TimeoutError):self.run_agent(completion)
        result=json.loads((self.root/'online/result.json').read_text())
        self.assertEqual(len(result['model_calls']),1)
        self.assertEqual(len(result['model_requests']),2)
        self.assertEqual(len(result['tool_calls']),1)
        self.assertEqual(self.guest.joinpath('state').read_text(),'saved')
        failed=json.loads((self.root/'online/002-timing.json').read_text())
        self.assertEqual(failed,result['model_requests'][1])
        self.assertEqual(failed['status'],'request_failed')
        self.assertEqual(failed['error_type'],'TimeoutError')
        self.assertGreater(failed['elapsed_ns'],0)
        self.assertGreater(failed['timeout_seconds'],0)
        self.assertEqual(result['model_wait_total_ns'],sum(r['elapsed_ns'] for r in result['model_requests']))
        for p in (self.root/'online').glob('*.json'):self.assertNotIn('test-only-secret',p.read_text())

    def test_failed_request_uses_monotonic_duration(self):
        record={}
        def completion(*_):raise TimeoutError('timed out')
        with patch.object(agent.time,'monotonic_ns',side_effect=[100,350]):
            with self.assertRaises(TimeoutError):
                agent.timed_complete({},'secret',{},12.5,record,self.root/'timing.json',completion)
        self.assertEqual(record['elapsed_ns'],250)
        self.assertEqual(record['timeout_seconds'],12.5)

    def test_diagnostic_multiple_requests_write_summary_once_without_tools(self):
        import diagnose_model_api as diagnostic
        source=self.root/'request.json'
        model=json.loads(self.cfg.read_text())['model']
        source.write_text(json.dumps({'model':model,'messages':[{'role':'user','content':'task'}],'max_tokens':512}))
        output=self.root/'diagnostic'
        def timed(*args):
            return agent.timed_complete(*args,completion=lambda *_:self.reply('touch NEVER_EXECUTED'))
        argv=['diagnose_model_api','--config',str(self.cfg),'--request',str(source),
              '--request',str(source),'--output',str(output)]
        with patch.object(sys,'argv',argv),patch.dict('os.environ',AGENT_SANDBOX_API_KEY='test-only-secret'), \
                patch.object(diagnostic,'timed_complete',side_effect=timed),patch('builtins.print'):
            diagnostic.main()
        result=json.loads((output/'result.json').read_text())
        self.assertEqual(result['status'],'COMPLETE')
        self.assertEqual(len(result['requests']),3)
        self.assertTrue(all(r['response_status']=='valid_tool_call' for r in result['requests']))
        self.assertEqual(len(list(output.glob('*-result.json'))),3)
        self.assertEqual(self.executions,0)

    def test_model_cannot_finish_without_tool_execution(self):
        with self.assertRaisesRegex(AssertionError,'without executing'):self.run_agent(lambda *_:self.reply())
        self.assertEqual(self.executions,0)

    def test_truncated_model_command_is_never_executed(self):
        response=self.reply('printf incomplete')
        response['choices'][0]['finish_reason']='length'
        with self.assertRaisesRegex(RuntimeError,'token limit'):self.run_agent(lambda *_:response)
        self.assertEqual(self.executions,0)

    def test_remaining_budget_is_visible_and_decreases_after_real_tool(self):
        from online_task import load
        task=load(ROOT/'configs/online-task-csv.json');task.update(cwd=str(self.guest),report_remaining_budget=True)
        requests=[]
        def completion(cfg,key,payload,timeout):
            requests.append(json.loads(json.dumps(payload)))
            return self.reply('printf budget > state') if len(requests)==1 else self.reply()
        agent.run(self.cfg,self.root/'online',self.trajectory,self.cli,'guest',{'PATH':'/usr/bin:/bin'},lambda _:None,
            completion=completion,key='test-only-secret',task=task)
        self.assertIn('3 model responses including this one; 2 shell calls',requests[0]['messages'][0]['content'])
        self.assertIn('2 model responses including this one; 1 shell calls',requests[1]['messages'][0]['content'])
        self.assertNotIn('Remaining budget',json.loads((self.root/'online/messages.json').read_text())[0]['content'])

    def test_configured_task_cwd_is_used(self):
        from online_task import load
        task=load(ROOT/'configs/online-task-csv.json');task['cwd']=str(self.guest)
        count=0
        def completion(*_):
            nonlocal count
            count+=1
            return self.reply('printf actual > state') if count==1 else self.reply()
        result=agent.run(self.cfg,self.root/'online',self.trajectory,self.cli,'guest',{'PATH':'/usr/bin:/bin'},lambda _:None,
            completion=completion,key='test-only-secret',task=task)
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(self.guest.joinpath('state').read_text(),'actual')
        self.assertEqual(json.loads((self.trajectory/'steps/001/request.json').read_text())['cwd'],str(self.guest))

    def test_budget_exhaustion_keeps_completed_tools_and_failure(self):
        with self.assertRaisesRegex(RuntimeError,'tool call budget'):
            self.run_agent(lambda *_:self.reply('printf saved > state'))
        result=json.loads((self.root/'online/result.json').read_text())
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(len(result['tool_calls']),2)
        self.assertEqual(self.guest.joinpath('state').read_text(),'saved')
        self.assertEqual(len(list((self.trajectory/'steps').iterdir())),2)

    def test_online_configuration_routes_only_through_capture(self):
        cfg=configuration(ROOT/'configs/platform-sphinx7590.json')
        check,live=commands(cfg,Path('/unused'),tool_capture=True,agent_config=self.cfg)
        self.assertIn('--agent-config',check);self.assertIn('--python-online-agent-config',live)
        with self.assertRaises(ValueError):commands(cfg,Path('/unused'),agent_config=self.cfg)

    def test_online_resource_check_keeps_source_vms_strict_and_records_other_users(self):
        from validate_isolated_agentenv import source_vm_identities_unchanged
        source={'pid':1,'cgroup':'0::/source\n','start_ticks':'1'}
        before=[source,{'pid':2,'cgroup':'0::/user.slice/user-1005\n','start_ticks':'2'}]
        after=[source,{'pid':3,'cgroup':'0::/user.slice/user-1005\n','start_ticks':'3'}]
        self.assertTrue(source_vm_identities_unchanged(before,after,'0::/source\n'))
        self.assertFalse(source_vm_identities_unchanged(before,after[1:],'0::/source\n'))
        self.assertFalse(source_vm_identities_unchanged(before,[dict(source,start_ticks='4')],'0::/source\n'))
        self.assertFalse(source_vm_identities_unchanged(before,after,'0::/missing\n'))

    def test_timed_out_creation_is_reconciled_only_with_owned_label(self):
        from validate_isolated_agentenv import late_owned_container
        def query(label,argv,timeout):
            self.assertEqual(argv,['docker','ps','-aq','--filter','label=agentsandboxbench.run_id=own-run'])
            return 'created-late\n'
        self.assertEqual(late_owned_container('own-run',query),'created-late')
        self.assertIsNone(late_owned_container('own-run',lambda *_: ''))
        with self.assertRaises(RuntimeError):late_owned_container('own-run',lambda *_:'one\ntwo')


if __name__=='__main__':unittest.main()
