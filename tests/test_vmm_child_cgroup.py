"""Temporary counter fixtures and source checks only; no VM, cgroup or BPF creation."""
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import vmm_child_cgroup_check as check
from vmm_child_cgroup_check import service_root,GroupSampler


class VmmChildCgroupTests(unittest.TestCase):
    def info(self, enabled):
        return dict(State=dict(Pid=123),Config=dict(Labels={'asb.vmm-child-cgroup':'on',
            'asb.vmm-parent':'/sys/fs/cgroup/asbvmmtest.slice'} if enabled else {}))

    def test_parent_resolution_only_for_explicit_label(self):
        with patch.object(Path,'read_text',return_value='0::/asbvmmtest.slice/docker-example.scope\n'):
            self.assertEqual(service_root(self.info(True)),Path('/sys/fs/cgroup/asbvmmtest.slice'))
            self.assertEqual(service_root(self.info(False)),Path('/sys/fs/cgroup/asbvmmtest.slice/docker-example.scope'))

    def test_unidentified_root_membership_fails(self):
        for value in ('0::/\n','2:memory:/group\n'):
            with patch.object(Path,'read_text',return_value=value):
                with self.assertRaises(AssertionError):service_root(self.info(True))

    def test_sampler_preserves_parent_and_children_without_adding_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'group';root.mkdir()
            for name,amount in [('',100),('asb-control',30),('vmm-1',70)]:
                group=root/name;group.mkdir(exist_ok=True)
                (group/'memory.current').write_text(str(amount))
                (group/'memory.stat').write_text(f'file {amount}\nanon 0\nkernel 0\nshmem 0\n')
            sampler=GroupSampler(root,Path(tmp)/'samples')
            try:time.sleep(.12)
            finally:receipt=sampler.stop()
            self.assertEqual(receipt['status'],'PASS');self.assertGreaterEqual(receipt['samples'],1)
            rows=[json.loads(l) for l in (Path(tmp)/'samples/memory.jsonl').read_text().splitlines()]
            for r in rows:
                self.assertEqual(r['groups'][str(root)]['current'],100)
                self.assertEqual(r['groups'][str(root/'vmm-1')]['file'],70)
                self.assertEqual(len(r['groups']),3)

    def test_launcher_has_no_vmm_migration_or_fork_fallback(self):
        text=(ROOT/'scripts/vmm_cgroup_launcher.c').read_text()
        self.assertIn('.flags = CLONE_INTO_CGROUP',text)
        self.assertIn('SYS_clone3',text);self.assertNotIn('cgroup.procs',text)
        self.assertNotIn('fork(',text);self.assertIn('PR_SET_PDEATHSIG, SIGKILL',text)
        self.assertLess(text.index('read birth cgroup'),text.index('execv(real, argv);',text.index('read birth cgroup')))

    def test_endpoint_uses_saved_pss_identity_pid_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);cg=base/'cg';root=cg/'asbvmmtest.slice';root.mkdir(parents=True)
            env=base/'env';records=env/'logs/vmm-cgroups';records.mkdir(parents=True)
            out=base/'result';out.mkdir();proc=base/'proc';bindings={};pss={'processes':[]}
            for i in range(7):
                pid=100+i;ns_pid=10+i;name='vmm-'+str(i)
                group=root/name;group.mkdir();(group/'cgroup.procs').write_text(str(pid))
                membership='0::/asbvmmtest.slice/'+name
                p=proc/str(pid);p.mkdir(parents=True)
                (p/'cgroup').write_text(membership+'\n');(p/'status').write_text(f'NSpid:\t{pid} {ns_pid}\n')
                (records/(name+'.json')).write_text(json.dumps(dict(birth_cgroup=membership,namespace_pid=ns_pid,creation='clone3_CLONE_INTO_CGROUP')))
                bindings[str(i)]={'pid':pid};pss['processes'].append({'identity':{'pid':pid},'pss_kib':100})
            with patch.object(check,'CGROOT',cg),patch.object(check,'counters',return_value=dict(current=10,file=2,anon=7,kernel=1)), \
                 patch('host_existing_cache_owner.query_bounded',return_value={'status':'OBSERVED'}) as query:
                value=check.endpoint(SimpleNamespace(cgroup=root),bindings,pss,env,out,[{'path':'/fixture'}],proc_root=proc)
            self.assertEqual(len(value['children']),7)
            self.assertEqual(value['child_sum']['file'],14)
            self.assertEqual(value['parent_before']['file'],2)
            self.assertEqual(value['children'][0]['pss']['identity']['pid'],100)
            query.assert_called_once()
            with patch.object(check,'CGROOT',cg),patch.object(check,'counters',return_value=dict(current=10,file=2,anon=7,kernel=1)), \
                 patch('host_existing_cache_owner.query_bounded') as query:
                accounting=check.endpoint(SimpleNamespace(cgroup=root),bindings,pss,env,out,[],proc_root=proc,query_owners=False)
            self.assertEqual(accounting['ownership_status'],'NOT_REQUESTED_ACCOUNTING_ONLY')
            self.assertEqual(len(accounting['children']),7)
            query.assert_not_called()


if __name__=='__main__':unittest.main()
