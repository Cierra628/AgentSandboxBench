"""Saved metadata and mocked controllers only; no BPF or real service execution."""
import ast
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from host_file_trace import device_coverage,events,kernel_device,matched_device_coverage,program,summarize
from host_file_matched_task import INSTANCES,matched_paths,observe_matched


class TraceTests(unittest.TestCase):
    def test_filter_includes_real_owner_descendants_and_outside_backing(self):
        names=['folio_mapping','address_space_host','inode_i_ino','inode_i_sb','inode_i_rdev','super_block_s_dev',
            'folio_index','folio_flags','folio__flags_1','folio_memcg_data','mem_cgroup_css',
            'cgroup_subsys_state_cgroup','cgroup_subsys_state_parent','cgroup_kn','kernfs_node_id','pg_head','memcg_flags_mask']
        text=program(SimpleNamespace(st_dev=os.makedev(8,1),st_ino=12345),os.makedev(7,1),555,dict.fromkeys(names,0))
        self.assertIn('==12345',text);self.assertIn('$owned || $dev==7340033 || $backing',text)
        self.assertEqual(text.count('|| $rdev!=0'),2)
        self.assertIn('$gid==555',text);self.assertIn('unroll(6)',text)
        self.assertIn('rawtracepoint:mm_filemap_delete_from_page_cache',text)
        self.assertIn('AGGREGATE_FORMAT 2',text)
        for number in (1,2,3,4):self.assertIn('@events[$cpu,'+str(number)+']=count()',text)
        self.assertIn('@bytes[$cpu,$bin,',text);self.assertIn('@records[$cpu,$bin,',text)
        self.assertIn('@backing_stacks[$cpu,kstack(12)]',text)
        for token in ('EVENT','NUMBER','MEASUREMENT_ID','FOLIO_','CGROUP_'):self.assertNotIn(token,text)
        self.assertNotIn('$owner=cgroup',text)
        self.assertEqual(kernel_device(os.makedev(259,257)),(259<<20)|257)

    def test_window_counts_removals_and_large_folios_not_cumulative_allocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'events'
            p.write_text('READY\nADD 1 2 3 4 0 5 100 2 20 0 99 worker\nDEL 3 2 3 4 0 5 100 2 20 0 99 worker\nADD 4 2 3 4 0 5 200 0 20 0 99 worker\n')
            rows=events(p);result=summarize(rows,1,4)
            self.assertEqual(result[0]['net_bytes'],4096)
            self.assertEqual(result[0]['add_bytes'],5*4096)
            self.assertEqual(summarize(rows,2,3)[0]['net_bytes'],-4*4096)
            self.assertEqual(result[0]['owner'],20)
            p.write_text('ADD 1 2 3 4 0 5 100 0 0 1 99 worker\n')
            with self.assertRaises(AssertionError):events(p)

    def test_only_dual_paths_have_31_actual_actions(self):
        work=ROOT/'.artifacts/prettier6604/grpo-input-audit-20261008T120626Z/workload'
        paths=matched_paths(json.loads((work/'rollouts.json').read_text()),dual_only=True)
        self.assertEqual(list(paths),['backbone','N2-a','N2-b'])
        count=0
        for segments in paths.values():
            for seg in segments:
                with (work/'segments'/seg/'actions.tsv').open() as stream:count+=len(list(csv.DictReader(stream,delimiter='\t')))
        self.assertEqual(count,31)
        validator=(ROOT/'scripts/validate_isolated_agentenv.py').read_text()
        self.assertIn("if host_trace_dual:check_args+=['--host-trace-dual']",validator)
        self.assertIn("if host_trace_dual:task_args+=['--host-trace-dual']",validator)
        self.assertIn('expected_count=31 if host_trace_dual else 41',validator)
        tree=ast.parse(validator)
        self.assertTrue(any(isinstance(n,ast.Attribute) and n.attr=='stop' for n in ast.walk(tree)))


class TraceSequenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_two_restores_and_no_single_guest_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);calls=[];created={}
            class Controller:
                live={}
                async def start(self,role,**kwargs):
                    calls.append(('start',role));self.live[role]=role
                    created[role]=dict(pid=len(created)+1,start_ticks='1')
                    (out/('restored-'+role+'.json')).write_text('{"status":"PASS"}')
                    return role
                async def run_path(self,sid,role,path):calls.append(('task',role))
            c=Controller();runtime=SimpleNamespace(mark=lambda *a:None,stop=lambda *a:None)
            binding=SimpleNamespace(resolve=lambda sid:created[sid])
            def pss(group):return dict(start_monotonic_ns=0,end_monotonic_ns=1,
                processes=[dict(**created[sid],comm='firecracker',pss=4096) for sid in c.live])
            async def check(role,sid):calls.append(('check',role))
            with patch('host_file_monitor.measurement_group',return_value=(out,'owned')),\
                 patch('host_file_monitor.counters',return_value={'file':1}),\
                 patch('host_file_monitor.pids',return_value=[]),\
                 patch('host_file_monitor.pss_snapshot',side_effect=pss),\
                 patch('host_sandbox_monitor.Binding',return_value=binding),\
                 patch('host_file_matched_task.live_residency',return_value=dict(status='OBSERVED',backends=[dict(rdev=0)])):
                result=await observe_matched(c,'template','parent',runtime,SimpleNamespace(container='mock',host_trace_dual=True),out,
                    lambda *a,**k:None,{r:['inspect','fix'] for r in INSTANCES[1:]},lambda role:dict(action_count=10),check)
            self.assertEqual([g['sandboxes'] for g in result['groups']],[2])
            self.assertEqual(result['suffix_actions'],20)
            self.assertEqual([r for action,r in calls if action=='task'],['N2-a','N2-b'])
            self.assertFalse(c.live)

class AggregateTests(unittest.TestCase):
    def test_aggregate_totals_must_cover_every_event_and_byte_map_key(self):
        from host_file_trace import validate_aggregates
        data='READY\n@events[1]: 3\n@events[2]: 1\n@bytes[1, 7, 9, 0, 12, 1]: 16384\n@records[1, 7, 9, 0, 12, 1]: 3\n@bytes[1, 7, 9, 0, 12, 2]: 4096\n@records[1, 7, 9, 0, 12, 2]: 1\nDONE\n'
        rows,counts=validate_aggregates(data,'')
        self.assertEqual(sum(r['bytes'] if r['op']=='ADD' else -r['bytes'] for r in rows),12288)
        self.assertEqual(counts[1],3)
        self.assertTrue(all('cpu' not in r for r in rows))
        for bad in (data.replace('@events[1]: 3','@events[1]: 4'),data.replace('@bytes[1, 7, 9, 0, 12, 2]: 4096\n',''),data+'Lost 1 events\n'):
            with self.assertRaises(AssertionError):validate_aggregates(bad,'')

    def test_unknown_owner_is_retained_not_relabelled_as_root(self):
        from host_file_trace import validate_aggregates
        data='READY\n@events[1]: 1\n@bytes[1, 3, 9, 9, 0, 1]: 4096\n@records[1, 3, 9, 9, 0, 1]: 1\nDONE\n'
        rows,counts=validate_aggregates(data,'')
        self.assertEqual(rows[0]['owner'],0);self.assertEqual(counts[1],1)


class CpuAggregateTests(unittest.TestCase):
    rdev=kernel_device(os.makedev(259,2))

    def data(self):
        return ('READY\nAGGREGATE_FORMAT 2\n'
            '@events[0, 1]: 2\n@events[1, 1]: 1\n@events[0, 2]: 1\n@events[1, 3]: 1\n@events[0, 4]: 1\n'
            f'@bytes[0, 1, 3, 9, {self.rdev}, 12, 1]: 8192\n@records[0, 1, 3, 9, {self.rdev}, 12, 1]: 2\n'
            f'@bytes[1, 1, 3, 9, {self.rdev}, 12, 1]: 4096\n@records[1, 1, 3, 9, {self.rdev}, 12, 1]: 1\n'
            f'@bytes[0, 2, 3, 9, {self.rdev}, 12, 2]: 4096\n@records[0, 2, 3, 9, {self.rdev}, 12, 2]: 1\n'
            'FLUSH 1 2 3 4 0 1 1\nFLUSH_DONE 2 2 3 0\nDONE\n')

    def test_every_cpu_is_validated_then_summed_for_existing_consumers(self):
        from host_file_trace import validate_aggregates
        rows,counts=validate_aggregates(self.data(),'')
        self.assertEqual(dict(counts),{1:3,2:1,3:1,4:1})
        self.assertEqual({r['cpu'] for r in rows},{0,1})
        self.assertEqual(len(rows),3)
        sample=dict(rdev=os.makedev(259,2),resident_bytes=8192,stable=True,
            start_monotonic_ns=1000000000,end_monotonic_ns=1050000000)
        self.assertEqual(device_coverage(rows,sample,initial_bytes=0)['status'],'MATCHES_QUERY_BOUNDS')

    def test_equal_global_sum_cannot_hide_cpu_mismatch(self):
        from host_file_trace import validate_aggregates
        bad=self.data().replace('@events[0, 1]: 2','@events[0, 1]: 1').replace('@events[1, 1]: 1','@events[1, 1]: 2')
        with self.assertRaises(AssertionError):validate_aggregates(bad,'')
        bad=self.data().replace('FLUSH_DONE 2 2 3 0','FLUSH_DONE 2 2 3 1')
        with self.assertRaises(AssertionError):validate_aggregates(bad,'')

    def test_missing_keys_duplicate_keys_format_and_loss_still_fail(self):
        from host_file_trace import validate_aggregates
        duplicate=f'@records[1, 1, 3, 9, {self.rdev}, 12, 1]: 1\n'
        for bad in (self.data().replace(duplicate,''),self.data()+duplicate,
                    self.data().replace('AGGREGATE_FORMAT 2\n',''),
                    self.data().replace('AGGREGATE_FORMAT 2','AGGREGATE_FORMAT 3'),
                    self.data().replace('FLUSH 1 2 3 4 0 1 1','FLUSH 1 2 3 4 0 1'),
                    self.data()+'Lost 1 events\n'):
            with self.assertRaises(AssertionError):validate_aggregates(bad,'')

    def test_first_insert_interleaving_model_separates_cpu_keys(self):
        # Model the compiled lookup-miss -> BPF_NOEXIST branch, not a live probe.
        def concurrent_first_inserts(keys):
            table={};lookups=[key in table for key in keys]
            for key,exists in zip(keys,lookups):
                if exists:table[key]+=1
                elif key not in table:table[key]=1
                # Existing key -> EEXIST; the compiled old branch has no retry.
            return sum(table.values())
        self.assertEqual(concurrent_first_inserts([('same-file',)]*2),1)
        self.assertEqual(concurrent_first_inserts([(0,'same-file'),(1,'same-file')]),2)

    def test_receipt_preserves_file_bin_meaning_separate_from_cpu_entries(self):
        from host_file_trace import FileCapture,validate_aggregates
        with tempfile.TemporaryDirectory() as tmp:
            capture=FileCapture.__new__(FileCapture);capture.out=Path(tmp)
            capture.rows,_=validate_aggregates(self.data(),'')
            with patch('host_file_trace.Capture.stop',return_value=dict(status='PASS',events=3,event_counts={1:3,2:1,3:1,4:1})):
                result=capture.stop()
            self.assertEqual(result['aggregation_format'],2)
            self.assertEqual(result['aggregate_cpu_entries'],3)
            self.assertEqual(result['aggregate_file_bins'],2)
            self.assertEqual(result['folio_event_count'],4)


class DeviceCoverageTests(unittest.TestCase):
    rdev=os.makedev(259,2)

    def row(self,bin_number,op,size,owner=55,**kwargs):
        return dict(bin=bin_number,op=op,bytes=size,owner=owner,device=3,
                    inode=kernel_device(self.rdev),rdev=kernel_device(self.rdev),events=1,**kwargs)

    def sample(self,resident=8192,**kwargs):
        return dict(device=6,inode=8,rdev=self.rdev,resident_bytes=resident,stable=True,
                    start_monotonic_ns=1000000000,end_monotonic_ns=1050000000,**kwargs)

    def test_outside_owner_and_unrelated_device_are_separate(self):
        rows=[self.row(1,'ADD',4096),self.row(2,'ADD',4096,owner=1)]
        unrelated=self.row(3,'ADD',1024*1024,owner=1);unrelated['rdev']+=1;rows.append(unrelated)
        r=device_coverage(rows,self.sample(),initial_bytes=0)
        self.assertEqual(r['status'],'MATCHES_QUERY_BOUNDS')
        self.assertEqual(r['query_upper_bytes'],8192)
        rows.append(self.row(4,'DEL',4096,owner=1))
        self.assertEqual(device_coverage(rows,self.sample(4096),initial_bytes=0)['status'],'MATCHES_QUERY_BOUNDS')

    def test_261_page_gap_is_not_accepted_by_matching_event_counts(self):
        r=device_coverage([self.row(1,'ADD',410185728)],self.sample(411254784),initial_bytes=0)
        self.assertEqual(r['status'],'COVERAGE_GAP')
        self.assertEqual(r['unexplained_resident_bytes'],261*4096)
        excess=device_coverage([self.row(1,'ADD',8192)],self.sample(4096),initial_bytes=0)
        self.assertEqual(excess['status'],'COVERAGE_GAP');self.assertEqual(excess['event_excess_bytes'],4096)

    def test_query_boundary_bins_are_bounds_not_interpolated(self):
        r=device_coverage([self.row(1,'ADD',8192),self.row(10,'ADD',8192),self.row(10,'DEL',4096)],self.sample(12288),initial_bytes=0)
        self.assertEqual((r['query_lower_bytes'],r['query_upper_bytes']),(4096,16384))
        self.assertEqual(r['status'],'MATCHES_QUERY_BOUNDS')

    def test_initial_cache_and_unknown_owners_are_not_zero(self):
        rows=[self.row(1,'ADD',8192)]
        self.assertEqual(device_coverage(rows,self.sample())['status'],'INITIAL_STATE_UNKNOWN')
        self.assertEqual(device_coverage(rows,self.sample(12288),initial_bytes=4096)['status'],'MATCHES_QUERY_BOUNDS')
        rows[0]['owner']=0
        r=device_coverage(rows,self.sample(),initial_bytes=0)
        self.assertEqual(r['status'],'OWNER_UNKNOWN');self.assertEqual(r['unknown_owner_add_bytes'],8192)

    def test_missing_events_identity_conflict_and_unstable_queries(self):
        self.assertEqual(device_coverage([],self.sample(0),initial_bytes=0)['status'],'MISSING_TARGET_EVENTS')
        rows=[self.row(1,'ADD',4096),self.row(2,'ADD',4096)];rows[1]['inode']+=1
        self.assertEqual(device_coverage(rows,self.sample(),initial_bytes=0)['status'],'IDENTITY_AMBIGUOUS')
        sample=self.sample();sample['stable']=False
        self.assertEqual(device_coverage(rows,sample,initial_bytes=0)['status'],'QUERY_UNSTABLE')

    def test_matched_identity_and_initial_inventory_are_required(self):
        rows=[self.row(1,'ADD',8192)];sample=self.sample(kind='memory-device')
        phase=dict(sandboxes=2,guests=[dict(restore_begin_ns=100000001)]*2,
            residency=dict(status='OBSERVED',backends=[dict(device=6,inode=8,rdev=self.rdev)]*2,files=[sample]))
        matched=dict(status='PASS',groups=[phase])
        targets=dict(block_device_scope='ALL_BLOCK_DEVICES',initial_block_devices=[],initial_inventory_ns=0)
        self.assertEqual(matched_device_coverage(rows,matched,targets,100000000)[0]['status'],'MATCHES_QUERY_BOUNDS')
        for old in ({},dict(targets,initial_block_devices=[kernel_device(self.rdev)])):
            self.assertEqual(matched_device_coverage(rows,matched,old,100000000)[0]['status'],'INITIAL_STATE_UNKNOWN')
        phase['residency']['backends'][1]=dict(device=6,inode=9,rdev=self.rdev)
        with self.assertRaises(AssertionError):matched_device_coverage(rows,matched,targets,100000000)

    def test_capture_receipt_keeps_output_and_coverage_status_separate(self):
        from host_file_trace import FileCapture
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);capture=FileCapture.__new__(FileCapture)
            capture.out=out;capture.ready_ns=100000000
            capture.rows=[self.row(2,'ADD',4096)]
            capture.targets=dict(block_device_scope='ALL_BLOCK_DEVICES',initial_block_devices=[],initial_inventory_ns=0)
            phase=dict(sandboxes=2,guests=[dict(restore_begin_ns=150000000)]*2,
                residency=dict(status='OBSERVED',backends=[dict(device=6,inode=8,rdev=self.rdev)]*2,
                    files=[self.sample(kind='memory-device')]))
            path=out/'matched.json';path.write_text(json.dumps(dict(status='PASS',groups=[phase])))
            with patch('host_file_trace.Capture.stop',return_value=dict(status='PASS',events=1,event_counts={1:1})):
                result=capture.stop(matched=path)
            self.assertEqual(result['status'],'PASS')
            self.assertEqual(result['coverage_status'],'INCOMPLETE')
            self.assertEqual(result['device_coverage'][0]['unexplained_resident_bytes'],4096)
            self.assertEqual(json.loads((out/'result.json').read_text())['coverage_status'],'INCOMPLETE')

    def test_runtime_cleanup_does_not_accept_incomplete_coverage(self):
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        block=next(n for n in ast.walk(tree) if isinstance(n,ast.If)
                   and isinstance(n.test,ast.Compare) and isinstance(n.test.left,ast.Name)
                   and n.test.left.id=='host_trace')
        code=compile(ast.fix_missing_locations(ast.Module(body=[block],type_ignores=[])),'cleanup','exec')
        for status,success in [('INCOMPLETE',False),('MATCHES_QUERY_BOUNDS',True)]:
            env=dict(host_trace=SimpleNamespace(stop=lambda **kw:dict(status='PASS',coverage_status=status)),
                     mount=Path('/not-used'),receipt=dict(checks={}),success=True)
            exec(code,env)
            self.assertEqual(env['success'],success)
            self.assertEqual(env['receipt']['checks']['host_shared_device_event_coverage'],'PASS' if success else 'FAIL')


if __name__=='__main__':unittest.main()
