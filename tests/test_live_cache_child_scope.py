"""Fake proc/cgroup trees and mocked tools only; no VM, BPF attach or services."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from task_physical_cache import TaskPhysicalCache, ram_capture_program
from live_task_cache import LiveTaskCache


class ChildScopeTests(unittest.TestCase):
    def test_constructor_selects_subtree_only_for_explicit_label_without_tools(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); proc=root/'proc'; cg=root/'cg'; parent=cg/'asbvmmtest.slice'
                docker=parent/'docker-owned.scope'; docker.mkdir(parents=True)
                p=proc/'900'; p.mkdir(parents=True)
                (p/'cgroup').write_text('0::/asbvmmtest.slice/docker-owned.scope\n')
                out=root/'a/b/raw'; out.mkdir(parents=True)
                labels={'agentenv.experiment':'isolated-storage-check','agentsandboxbench.run_id':out.parents[2].name}
                if enabled:labels.update({'asb.vmm-child-cgroup':'on','asb.vmm-parent':str(parent)})
                info=dict(State=dict(Pid=900,Running=True),Config=dict(Labels=labels))
                def popen(*args, **kw):
                    kw['stdout'].write('READY\n'); kw['stdout'].flush()
                    return Mock()
                with patch('task_physical_cache.subprocess.check_output',return_value=json.dumps([info])), \
                     patch('task_physical_cache.subprocess.Popen',side_effect=popen) as tool, \
                     patch('host_existing_cache_owner.layout',return_value={'KERNFS_NODE_ID':96}) as layout:
                    cap=TaskPhysicalCache(out,'owned',Mock(),proc_root=proc,cgroup_root=cg)
                self.assertEqual(cap.cgpath,parent if enabled else docker)
                self.assertEqual(layout.call_count,int(enabled)); self.assertEqual(tool.call_count,1)
                source=(cap.directory/'capture.bt').read_text()
                self.assertEqual('ancestors[' in source,enabled)
                self.assertEqual((cap.directory/'capture-scope.json').exists(),enabled)

    def fixture(self, root):
        proc = root/'proc'; cg = root/'cg'; parent = cg/'asbvmmtest.slice'
        parent.mkdir(parents=True); proc.mkdir()
        (parent/'cgroup.procs').write_text('')
        docker = parent/'docker-owned.scope'; docker.mkdir(); (docker/'cgroup.procs').write_text('900')
        births = root/'births'; births.mkdir()
        exe = root/'firecracker'; exe.write_text('not executable; fixture only')
        cap = TaskPhysicalCache.__new__(TaskPhysicalCache)
        cap.child_scope = True; cap.cgpath = parent; cap.cgroup = '/asbvmmtest.slice'
        cap.proc_root = proc; cap.cgroup_root = cg; cap.birth_directory = births
        bindings = {}; directories = {}; events = []
        for i in range(7):
            pid = 100+i; sid = 'sandbox-'+str(i); group = parent/('vmm-'+str(i)); group.mkdir()
            (group/'cgroup.procs').write_text(str(pid))
            membership = '0::/asbvmmtest.slice/'+group.name
            p = proc/str(pid); p.mkdir()
            (p/'cgroup').write_text(membership+'\n'); (p/'comm').write_text('firecracker\n')
            (p/'stat').write_text(str(pid)+' (firecracker) '+' '.join(['S']+['0']*18+[str(200+i)]))
            (p/'status').write_text('NSpid:\t'+str(pid)+' '+str(10+i)+'\n')
            (p/'exe').symlink_to(exe)
            marker = 'unique marker '+sid; (p/'mem').write_bytes(marker.encode()+bytes(4096-len(marker)))
            (births/(group.name+'.json')).write_text(json.dumps(dict(creation='clone3_CLONE_INTO_CGROUP',
                birth_cgroup=membership, namespace_pid=10+i)))
            bindings[sid] = dict(pid=pid, start_ticks=str(200+i), cgroup=cap.cgroup)
            directory = root/sid; directory.mkdir(); directories[sid] = directory
            (directory/'marker.json').write_text(json.dumps(dict(sandbox_id=sid,pfn=0,text=marker)))
            (directory/'iomem.txt').write_text('00000000-00000fff : System RAM\n')
            events += [f'IDENTITY\t{pid}\t{group.stat().st_ino}\t{200+i}',f'{pid}\t0\t0\t1000\t0\t0']
        for directory in directories.values():
            (directory/'kvm-slots.tsv').write_text('READY\n'+'\n'.join(events)+'\n')
        cap.binding = SimpleNamespace(resolve=lambda sid: dict(bindings[sid]))
        return cap, bindings, directories

    def test_all_seven_sibling_vmm_groups_bind_actual_membership(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap, bindings, directories = self.fixture(Path(tmp))
            for sid, b in bindings.items():
                vm, proof = cap.bind(directories[sid], sid)
                self.assertEqual(vm['pid'], b['pid'])
                self.assertEqual(vm['start_time_ticks'], int(b['start_ticks']))
                self.assertTrue(vm['cgroup'].startswith(cap.cgroup+'/vmm-'))
                self.assertNotIn('docker', vm['cgroup'])
                self.assertTrue(proof['marker_verified'])
                receipt=json.loads((directories[sid]/'vmm-child-binding.json').read_text())
                self.assertEqual(receipt['registration_identity']['start_ticks'], int(b['start_ticks']))
                self.assertEqual(receipt['actual_cgroup'], vm['cgroup'])
                self.assertEqual(json.loads((directories[sid]/'vmm-identity.json').read_text()), vm)

    def test_outside_process_is_rejected_even_if_binding_resolver_returns_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap, bindings, directories = self.fixture(Path(tmp))
            proc = cap.proc_root/str(bindings['sandbox-0']['pid'])
            for path in ('/unrelated/vmm-0', cap.cgroup+'/docker-owned.scope', cap.cgroup+'/nested/vmm-0'):
                (proc/'cgroup').write_text('0::'+path+'\n')
                with self.assertRaisesRegex(AssertionError, 'outside declared children'):
                    cap.bind(directories['sandbox-0'], 'sandbox-0')

    def test_missing_or_stale_registration_never_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap, bindings, directories = self.fixture(Path(tmp))
            directory = directories['sandbox-0']; path = directory/'kvm-slots.tsv'; original = path.read_text()
            expected = next(line for line in original.splitlines() if line.startswith('IDENTITY\t100\t'))
            for bad in (original.replace(expected, ''), original.replace(expected, expected.rsplit('\t',1)[0]+'\t199'),
                        original.replace(expected, 'IDENTITY\t100\t999999999\t200')):
                path.write_text(bad)
                with self.assertRaisesRegex(AssertionError, 'registration identity'):
                    cap.bind(directory, 'sandbox-0')

    def test_reused_pid_and_bad_birth_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap, bindings, directories = self.fixture(Path(tmp))
            bindings['sandbox-0']['start_ticks'] = '999'
            with self.assertRaisesRegex(AssertionError, 'registration identity'):
                cap.bind(directories['sandbox-0'], 'sandbox-0')
            bindings['sandbox-0']['start_ticks'] = '200'
            path = cap.birth_directory/'vmm-0.json'; birth = json.loads(path.read_text())
            path.write_text(json.dumps(dict(birth, namespace_pid=999)))
            with self.assertRaisesRegex(AssertionError, 'birth PID'):
                cap.bind(directories['sandbox-0'], 'sandbox-0')
            path.unlink()
            with self.assertRaises(FileNotFoundError):cap.bind(directories['sandbox-0'], 'sandbox-0')

    def test_pid_changes_after_resolver_and_shared_child_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap, bindings, directories = self.fixture(Path(tmp))
            group = cap.cgpath/'vmm-0'; (group/'cgroup.procs').write_text('100 999')
            with self.assertRaisesRegex(AssertionError, 'membership changed'):
                cap.bind(directories['sandbox-0'], 'sandbox-0')
            (group/'cgroup.procs').write_text('100')
            stat = cap.proc_root/'100/stat'; stat.write_text(stat.read_text().rsplit(' ',1)[0]+' 999')
            with self.assertRaisesRegex(AssertionError, 'start time changed'):
                cap.bind(directories['sandbox-0'], 'sandbox-0')

    def test_legacy_binding_needs_no_new_birth_or_registration_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'cgroup.procs').write_text('7')
            proc = root/'proc/7'; proc.mkdir(parents=True); (proc/'comm').write_text('firecracker')
            (proc/'exe').symlink_to(root/'legacy-exe')
            cap = TaskPhysicalCache.__new__(TaskPhysicalCache)
            cap.child_scope=False; cap.cgpath=root; cap.proc_root=root/'proc'; cap.cgroup='/legacy'
            with patch('task_physical_cache.bind_vm',return_value=('vm','proof')) as bind:
                self.assertEqual(cap.bind(root,'old-sid'),('vm','proof'))
                bind.assert_called_once_with(root,[7],'/legacy',str(root/'legacy-exe'),'old-sid')

    def test_capture_program_retains_default_and_covers_future_children(self):
        old = ram_capture_program(123)
        self.assertIn('/cgroup == 123 && args->cmd == 0x4020ae46/',old)
        self.assertNotIn('ancestors',old); self.assertNotIn('IDENTITY',old)
        new = ram_capture_program(123,ancestor_level=2,kernfs_id_offset=96)
        self.assertIn('$cg->level >= 2',new); self.assertIn('$cg->ancestors[2]',new)
        self.assertIn('== 123',new); self.assertIn('IDENTITY\\t',new)
        self.assertIn('start_boottime',new)
        self.assertNotIn('vmm-0',new)  # No frozen list of groups created before tracing.
        with self.assertRaises(AssertionError):ram_capture_program(123,ancestor_level=0,kernfs_id_offset=96)

    def test_live_start_delegates_to_attested_binding_without_real_guest(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp); capture=d/'capture'; capture.mkdir(); (capture/'kvm-slots.tsv').write_text('READY\n')
            vm={'pid':100}; proof={'slots':[dict(guest=0,host=4096,size=8192)]}
            c=LiveTaskCache.__new__(LiveTaskCache); c.directory=d; c.expected_kernel='fixture-kernel'; c.file_cache_path=None
            c.capture=SimpleNamespace(directory=capture,ready=Mock(),bind=Mock(return_value=(vm,proof)))
            bootstrap=dict(kernel='fixture-kernel',iomem='fixture',export_pfns=[1])
            c.cli=Mock(return_value=json.dumps(bootstrap)); c._start_snapshot_queries=Mock()
            with patch('live_task_cache.identity') as identity, patch('live_task_cache.threading.Thread') as thread:
                c.start('sid')
                c.capture.bind.assert_called_once_with(d,'sid')
                identity.assert_called_once_with(vm,Path('/proc'),Path('/sys/fs/cgroup'))
                thread.return_value.start.assert_called_once()


if __name__=='__main__':unittest.main()
