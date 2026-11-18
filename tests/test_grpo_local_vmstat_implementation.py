"""Pure routing, saved-context assertions and synthetic report data; no real services."""
import ast
import copy
import csv
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import platform_tasks as platform
import host_persistent_input as persistent
import prettier_no_proxy as local
import validate_isolated_agentenv as native
import run_host_file_workflow as wrapper
import run_prettier6604_grpo as grpo
from assess_grpo_local_vmstat import parser,tree,execute
from run_grpo_local_no_proxy_controls import planned_runs,comparisons
import run_grpo_local_no_proxy_controls as controls
from report_grpo_local_no_proxy_controls import publish

META=dict(status='NOT_MEASURED',reason='explicitly skipped boundary vmstat',row_count=0,boundary_memory_rows=448)
ROLES=('backbone','early_1','early_2','early_3','late_1','late_2','late_3')


def cfg(skip=False):
    return platform.configuration(ROOT/('configs/platform-prettier6604-grpo-host-current'+('-skip-vmstat' if skip else '')+'.json'))


def saved_context():
    return dict(schedule='grpo',grpo_setup_schedule='per-guest-setup',roles=list(ROLES),local_no_proxy='PASS',
        local_no_proxy_guests={r:'toy-'+r for r in ROLES},host_before_first_tool={'counters':{'current':123}})


def toy_runs():
    result=[]
    for i,item in enumerate(planned_runs('boundary-vmstat'),1):
        skip=item['skip_vmstat']
        seconds=(10,9,11,10)[i-1]
        result.append(dict(index=i,pair=item['pair'],mode='on',boundary_vmstat_mode=item['variant'],
            light_skip_boundary_vmstat=skip,**({'boundary_vmstat':dict(META)} if skip else {}),
            paths={'backbone':['toy']},patches={'backbone':'toy patch'},official={'backbone':{'passed':0,'failed':2}},
            actual_actions=189,grpo_setup_schedule='per-guest-setup',cold_requests=[],cold_request_union_ns=100,
            sampling=dict(mode='on',boundary_rows={'guest-memory.tsv':448,'guest-vmstat.tsv':0 if skip else 448},
                host_samples=2,guest_samples=14,host_cpu_seconds=.01,host_cpu_percent=.1,guest_cpu_seconds=.1,host_max_gap_seconds=.1),
            timings_ns={k:seconds*10**9 for k in ('tool_sum','frozen_tools_and_downloads_span','cold_request_span','rollout','controller',
                'monitor_control_call_sum','monitor_control_union','final_input_query')},
            host_memory=dict(current_baseline_bytes=2**20,current_peak_bytes=2*2**20,current_peak_increase_bytes=2**20,file_peak_bytes=2**20),
            input_files=105,input_resident_bytes=1024,input_group_file_bytes=2048,query_cpu_seconds=.01,ownership='NOT_QUERIED',sessions=[]))
    return result


def synthetic_report_fixture(root):
    """Layout fixture only: deliberately not a task or performance result."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    source=root/'SYNTHETIC_SOURCE';source.mkdir()
    (root/'FIXTURE_ONLY.txt').write_text('SYNTHETIC LAYOUT DATA ONLY. No task executed; not a performance result.\n')
    fig,ax=plt.subplots();ax.plot([0,.1],[1,2]);ax.set_title('SYNTHETIC layout placeholder')
    placeholder=root/'placeholder.png';fig.savefig(placeholder);plt.close(fig)
    runs=toy_runs()
    for r in runs:
        report=root/f"toy-report-{r['index']}";(report/'host-file').mkdir(parents=True)
        for name in ('overview.png','after-service-ready.png'):shutil.copyfile(placeholder,report/'host-file'/name)
        points=[dict(sandbox_id='toy-'+role,role=role,timestamp_ns=100+i,elapsed_seconds=i*.1,
            memory_used_bytes=(20+i)*2**20,cached_bytes=(10+i)*2**20,shmem_bytes=0) for role in ROLES for i in range(2)]
        with (report/'memory.tsv').open('w') as f:
            w=csv.DictWriter(f,fieldnames=list(points[0]),delimiter='\t');w.writeheader();w.writerows(points)
        r['report']=str(report)
    value=dict(status='PASS',stage='complete',comparison='boundary-vmstat',source_restored_stopped=True,
        remaining_firecrackers=[],remaining_running_containers=[],runs=runs,pairs=comparisons(runs,comparison='boundary-vmstat'),
        start_utc='SYNTHETIC',finish_utc='SYNTHETIC')
    (source/'result.json').write_text(json.dumps(value))
    return source


class LocalVmstatTests(unittest.TestCase):
    def test_boundary_report_labels_all_four_as_sampled_and_links_real_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=synthetic_report_fixture(root);out=root/'report'
            result=publish(source,out)
            self.assertEqual(result['comparison'],'boundary-vmstat')
            self.assertEqual([r['mode'] for r in result['runs']],['on']*4)
            self.assertEqual([r['boundary_vmstat_mode'] for r in result['runs']],['keep','skip','skip','keep'])
            self.assertEqual(result['runs'][1]['boundary_vmstat'],META)
            text=(out/'REPORT.md').read_text();self.assertIn('两组都开启0.1秒',text);self.assertIn('跳过减保留',text)
            self.assertNotIn('关闭没有运行采集',text)
            with (out/'runs.tsv').open() as f:rows=list(csv.DictReader(f,delimiter='\t'))
            self.assertEqual([r['mode'] for r in rows],['on']*4)
            self.assertTrue(all(r['current_peak_bytes'] for r in rows))
            self.assertEqual(len(list(out.glob('*.png'))),13)
            value=json.loads((source/'result.json').read_text());value['runs'][1]['sampling']['boundary_rows']['guest-vmstat.tsv']=1
            (source/'result.json').write_text(json.dumps(value))
            with self.assertRaises(AssertionError):publish(source,root/'bad-report')
            self.assertFalse((root/'bad-report').exists())

    def test_candidate_diff_and_real_persistent_parser_change_one_flag(self):
        a,b=cfg(),cfg(True)
        self.assertEqual({k for k in a.keys()|b.keys() if a.get(k)!=b.get(k)}, {'light_skip_boundary_vmstat'})
        with patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'},clear=True):
            args=[]
            for c in (a,b):
                persistent.single_config(c,local_no_proxy=True);local.require_mode(c,True)
                _,live=platform.commands(c,Path('/toy'),persistent_input=True)
                n=parser().parse_args(live[2:]);args.append(n)
                self.assertTrue(native.ordinary_current_only(n));self.assertEqual(persistent.native_option(n),Path('/toy'))
            self.assertEqual({k for k in vars(args[0]) if vars(args[0])[k]!=vars(args[1])[k]}, {'light_skip_boundary_vmstat'})
            self.assertTrue(grpo.skip_boundary_vmstat_options(args[1]))
            for change in ({'prettier6604_schedule':'bpo'},{'prettier6604_role':'early_1'}, {'damon_observe':True},
                {'prettier6604_grpo_create_before_setup':True},{'prettier6604_monitor_mode':'off'},
                {'light_monitor':False},{'host_current_only':False},{'host_scaling_mode':'cold'},
                {'prettier6604_shared_cache':True},{'persistent_cache_seed':None},{'immutable_cache_seed':Path('/ram')}):
                with self.subTest(change=change),self.assertRaises(ValueError):
                    grpo.skip_boundary_vmstat_options(SimpleNamespace(**(vars(args[1])|change)))
        with patch.dict(os.environ,{},clear=True),self.assertRaises(ValueError):grpo.skip_boundary_vmstat_options(args[1])

    def test_config_rejects_mixed_modes_and_non_boolean_option(self):
        original=json.loads((ROOT/'configs/platform-prettier6604-grpo-host-current-skip-vmstat.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'cfg.json'
            for change in ({'light_skip_boundary_vmstat':1},{'light_monitor':False},
                {'monitor_mode':'off','light_monitor':False},{'rollout':'bpo'}, {'grpo_create_before_setup':True},
                {'host_prefix_count':7},{'host_shared_files':True},{'task_id':'sphinx-doc__sphinx-7590'}):
                p.write_text(json.dumps(original|change))
                with self.subTest(change=change),self.assertRaises(ValueError):platform.configuration(p)
        with self.assertRaises(ValueError):persistent.single_config(cfg(True))

    def test_replay_gate_accepts_only_local_sampled_per_guest_context(self):
        args=dict(schedule='grpo',monitor_mode='continuous',light_monitor=True,light_skip_boundary_vmstat=True)
        with patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'},clear=True):
            self.assertTrue(grpo.skip_boundary_vmstat_options(SimpleNamespace(**args)))
            for change in ({'schedule':'bpo'},{'role':'early_1'},{'host_prefix_count':7},{'shared_cache':True},
                           {'damon_disable':True},{'monitor_mode':'off'},{'light_monitor':False}):
                with self.subTest(change=change),self.assertRaises(ValueError):grpo.skip_boundary_vmstat_options(SimpleNamespace(**(args|change)))
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError):grpo.skip_boundary_vmstat_options(SimpleNamespace(**args))
            self.assertTrue(grpo.skip_boundary_vmstat_options(SimpleNamespace(**(args|{'grpo_create_before_setup':True}))))

    def test_saved_report_context_is_required_without_live_environment(self):
        good=saved_context();self.assertTrue(grpo.local_current_grpo_record(good))
        guard=next(n for n in ast.walk(tree('report_prettier6604_grpo.py')) if isinstance(n,ast.If) and ast.unparse(n.test)=='skip_boundary_vmstat')
        with patch.dict(os.environ,{},clear=True):
            execute(guard,dict(skip_boundary_vmstat=True,light=True,off=False,schedule='grpo',result=good,local_current_grpo_record=grpo.local_current_grpo_record))
            for change in ({'local_no_proxy':'FAIL'},{'host_before_first_tool':{}},{'local_no_proxy_guests':{}},{'roles':list(ROLES[:-1])}):
                bad=good|change;self.assertFalse(grpo.local_current_grpo_record(bad))
                with self.subTest(change=change),self.assertRaises(AssertionError):
                    execute(guard,dict(skip_boundary_vmstat=True,light=True,off=False,schedule='grpo',result=bad,local_current_grpo_record=grpo.local_current_grpo_record))

    def test_shared_inspector_rejects_wrong_not_measured_metadata(self):
        guard=next(n for n in ast.walk(tree('run_grpo_light_pair.py')) if isinstance(n,ast.If) and ast.unparse(n.test)=='skip_boundary_vmstat')
        task=saved_context()|{'boundary_vmstat':dict(META)}
        values=dict(skip_boundary_vmstat=True,mode='on',setup_schedule='per-guest-setup',task=task,summary={'boundary_vmstat':dict(META)},variant={})
        execute(guard,values);self.assertTrue(values['variant']['light_skip_boundary_vmstat'])
        for change in ({'status':'OBSERVED'},{'row_count':1},{'boundary_memory_rows':447}):
            bad=copy.deepcopy(values);bad['task']['boundary_vmstat'].update(change)
            with self.subTest(change=change),self.assertRaises(AssertionError):execute(guard,bad)

    def test_new_plan_and_signed_comparison_do_not_redefine_old_off_on(self):
        self.assertEqual([r['variant'] for r in planned_runs()],['off','on','on','off'])
        plan=planned_runs('boundary-vmstat')
        self.assertEqual([r['mode'] for r in plan],['on']*4)
        self.assertEqual([r['variant'] for r in plan],['keep','skip','skip','keep'])
        runs=toy_runs();p=comparisons(runs,comparison='boundary-vmstat')
        self.assertEqual([p0['timings']['tool_sum']['change_seconds'] for p0 in p],[-1,1])
        self.assertIn('keep_seconds',p[0]['timings']['tool_sum']);self.assertNotIn('off_seconds',p[0]['timings']['tool_sum'])
        with self.assertRaises(StopIteration):comparisons(runs)
        for key,value in (('mode','off'),('light_skip_boundary_vmstat',False)):
            bad=copy.deepcopy(runs);bad[1][key]=value
            with self.subTest(key=key),self.assertRaises(AssertionError):comparisons(bad,comparison='boundary-vmstat')
        bad=copy.deepcopy(runs);bad[1]['sampling']['boundary_rows']['guest-vmstat.tsv']=1
        with self.assertRaises(AssertionError):comparisons(bad,comparison='boundary-vmstat')

    def test_candidate_outer_workflow_still_prepares_before_group_and_clears_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);events=[]
            def run(argv,**kw):
                if argv[0]=='systemd-run':
                    events.append('group')
                    self.assertIn('--setenv=ASB_HOST_FILE_SAMPLING=on',argv)
                    self.assertIn('--setenv=ASB_LOCAL_NO_PROXY=on',argv)
                    self.assertIn(str(ROOT/'configs/platform-prettier6604-grpo-host-current-skip-vmstat.json'),argv)
                    self.assertIn('--persistent-seed',argv)
                else:events.append('stop')
                return SimpleNamespace(returncode=0)
            with patch.dict(os.environ,{},clear=True),patch.object(wrapper,'ROOT',base),\
                patch.object(sys,'argv',['entry',str(ROOT/'configs/platform-prettier6604-grpo-host-current-skip-vmstat.json'),'--persistent-input','--local-no-proxy']),\
                patch('platform_tasks.preflight'),patch.object(wrapper.subprocess,'check_output',return_value='systemd'),\
                patch.object(wrapper.subprocess,'run',side_effect=run),\
                patch.object(local,'prepare_metadata',side_effect=lambda *a,**k:events.append('metadata')),\
                patch.object(persistent,'run_phase',side_effect=lambda action,*a,**k:events.append(action)):
                self.assertEqual(wrapper.main(),0)
            self.assertEqual(events,['metadata','prepare','group','stop','release-source'])

    def test_four_run_driver_keeps_both_sampled_and_stops_at_first_failure(self):
        for fail in (False,True):
            with self.subTest(fail=fail),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);config=root/'source.toml';config.write_text('[pool.firecracker]\nenabled=false\nstartup_prewarm=false\n[pool.block]\nenabled=false\n')
                running=[False];launched=[];checked=[];toy=toy_runs();out=root/'output'
                def command(argv,**kw):
                    if argv[:2]==['docker','inspect']:
                        return json.dumps([dict(Id=controls.SOURCE_ID,Image=controls.IMAGE,State={'Running':running[0]},
                            Config={'Env':['API_ADDR=127.0.0.1:12345']},Mounts=[dict(Source=str(config),Destination='/workspace/config/default.toml')])])
                    if argv[:2]==['docker','ps']:return ''
                    if argv[:2]==['docker','start']:running[0]=True;return ''
                    if argv[:2]==['docker','stop']:running[0]=False;return ''
                    raise AssertionError(argv)
                def launch(argv,**kw):
                    launched.append(argv);i=len(launched)
                    kw['stdout'].write('status=PASS result_dir='+str(root/'.artifacts/platform-tasks'/f'toy-{i}')+'\n')
                    return SimpleNamespace(returncode=1 if fail else 0)
                def check(path,mode,**kw):
                    i=len(checked);checked.append((path,mode,kw));row=copy.deepcopy(toy[i])
                    row.pop('boundary_vmstat_mode');row['guest_ids']=[f'toy-{i}-{r}' for r in ROLES]
                    return row
                original=Path.read_text
                def read_text(p,*a,**kw):
                    if str(p)=='/proc/meminfo':return 'MemAvailable: 134217728 kB\n'
                    return original(p,*a,**kw)
                response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=None);response.status=200
                opener=Mock();opener.open.return_value=response
                with patch.object(sys,'argv',['entry','--comparison','boundary-vmstat','--output',str(out)]),\
                    patch.object(controls,'ROOT',root),patch.object(controls.subprocess,'check_output',side_effect=command),\
                    patch.object(controls.subprocess,'run',side_effect=launch),patch.object(controls,'identities',return_value=[]),\
                    patch.object(controls.shutil,'disk_usage',return_value=SimpleNamespace(free=60*2**30)),\
                    patch.object(Path,'read_text',read_text),patch.object(controls.urllib.request,'build_opener',return_value=opener),\
                    patch.object(controls,'check',side_effect=check):
                    # Match the driver's owned output-root condition without using the real .artifacts tree.
                    out=root/'.artifacts/output';sys.argv[-1]=str(out)
                    status=controls.main()
                self.assertEqual(status,1 if fail else 0);self.assertFalse(running[0])
                self.assertEqual(len(launched),1 if fail else 4)
                if not fail:
                    self.assertEqual([c[1] for c in checked],['on']*4)
                    self.assertEqual([bool(c[2].get('skip_vmstat')) for c in checked],[False,True,True,False])
                    state=json.loads((out/'result.json').read_text());self.assertEqual(state['comparison'],'boundary-vmstat')


if __name__=='__main__':unittest.main()
