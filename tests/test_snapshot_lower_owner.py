"""Temporary proc/cgroup fixtures only; no Docker, live process or device access."""
import sys,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import snapshot_lower_owner as owner

class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.proc=self.root/'proc';self.cg=self.root/'cg';self.parent=self.cg/'asbvmmtest.slice';self.service=self.parent/'docker-owned.scope'
        self.service.mkdir(parents=True);(self.service/'cgroup.procs').write_text('20\n')
        (self.proc/'sys/kernel/random').mkdir(parents=True);(self.proc/'sys/kernel/random/boot_id').write_text('boot')
        self.file=self.root/'layer';self.file.write_text('data')
        for pid,group,exe in [(10,'/asbvmmtest.slice/vmm-10','firecracker'),(20,'/asbvmmtest.slice/docker-owned.scope','uvm-ublk-daemon')]:
            p=self.proc/str(pid);(p/'fd').mkdir(parents=True)
            (p/'stat').write_text(f'{pid} (name) '+' '.join(['0']*19+['123']))
            (p/'cgroup').write_text('0::'+group+'\n');(p/'exe').symlink_to(self.root/exe)
        (self.proc/'20/fd/4').symlink_to(self.file)
        self.binding=SimpleNamespace(proc_root=self.proc,cgroup=self.parent,private_service_cgroup=self.service,cgroup_text='0::/asbvmmtest.slice/docker-owned.scope\n',cgroup_path='/asbvmmtest.slice')
        self.vm=dict(pid=10,start_ticks='123',boot_id='boot',actual_cgroup='/asbvmmtest.slice/vmm-10',sandbox_id='sid')
    def tearDown(self):self.tmp.cleanup()
    def test_sibling_service_accepted(self):
        result=owner.service_openers(self.binding,self.vm,self.file)
        self.assertEqual(result[0]['pid'],20);self.assertEqual(result[0]['inode'],self.file.stat().st_ino)
    def test_unrelated_group_rejected(self):
        (self.proc/'20/cgroup').write_text('0::/other/service\n')
        with self.assertRaisesRegex(ValueError,'escaped'):owner.service_openers(self.binding,self.vm,self.file)
    def test_stale_vm_rejected(self):
        with self.assertRaisesRegex(ValueError,'stale'):owner.service_openers(self.binding,dict(self.vm,start_ticks='9'),self.file)
    def test_wrong_inode_rejected(self):
        other=self.root/'other';other.write_text('data')
        with self.assertRaisesRegex(ValueError,'opening exact'):owner.service_openers(self.binding,self.vm,other)
    def test_provider_pid_reuse_rejected(self):
        original=owner.identity;calls=0
        def changed(pid,root):
            nonlocal calls
            value=original(pid,root)
            if pid==20:
                calls+=1
                if calls>1:value['start_ticks']='456'
            return value
        with patch.object(owner,'identity',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'identity changed'):owner.service_openers(self.binding,self.vm,self.file)
    def test_device_config_and_sandbox_must_match(self):
        env=self.root/'env';cfg=env/'snapshot-local-cache/runtime/s/memory/image.json'
        line='key=/workspace/env/snapshot-local-cache/runtime/s/memory/image.json path=/dev/ublkb3 sandbox_id=sid'
        self.assertEqual(owner.select_config(line,'sid','/dev/ublkb3',[cfg],env),cfg)
        for sid,dev,configs in [('wrong','/dev/ublkb3',[cfg]),('sid','/dev/ublkb4',[cfg]),('sid','/dev/ublkb3',[])]:
            with self.assertRaises(ValueError):owner.select_config(line,sid,dev,configs,env)
    def test_ambiguous_provider_rejected(self):
        import shutil
        shutil.copytree(self.proc/'20',self.proc/'21',symlinks=True)
        (self.service/'cgroup.procs').write_text('20\n21\n')
        with self.assertRaisesRegex(ValueError,'one private-service'):owner.service_openers(self.binding,self.vm,self.file)

if __name__=='__main__':unittest.main()
