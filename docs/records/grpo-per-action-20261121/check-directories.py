"""Offline: real directory writers, fixture process identity, no VMM or service."""
import json, sys, tempfile, threading, unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
from grpo_cold_points import ColdPoints
from run_guest_light_memory import bind_vmm
from light_memory_round import Sampler

class Directories(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root=Path(tmp.name); self.out=root/'controller/work/task'; self.out.mkdir(parents=True)
        proc=root/'proc/42';proc.mkdir(parents=True)
        (proc/'status').write_text('NSpid: 42 9\nUid: 0 0 0 0\n')
        (proc/'cgroup').write_text('0::/test.slice/vmm\n')
        cg=root/'sys/fs/cgroup/test.slice/vmm';cg.mkdir(parents=True)
        birth=self.out.parent.parent/'env/logs/vmm-cgroups/vmm.json';birth.parent.mkdir(parents=True)
        birth.write_text(json.dumps(dict(birth_cgroup='0::/test.slice/vmm',namespace_pid=9,creation='clone3_CLONE_INTO_CGROUP')))
        self.binding=Mock();self.binding.resolve.side_effect=lambda sid:dict(pid=42)
        for name,value in [('run_guest_light_memory.Path',lambda p:root/p.lstrip('/')),
                           ('run_guest_light_memory.check_vmm',Mock()),('grpo_cold_points.TaskPhysicalCache',Mock())]:
            ctx=patch(name,value);ctx.start();self.addCleanup(ctx.stop)
        self.c=ColdPoints(self.out,'fixture',None,self.binding)
    def cache(self,role):self.c.prepare(role,restored=False)
    def pss(self,role):return bind_vmm(self.binding,'sandbox',self.out,role)
    def coexist(self,methods):
        for fn in methods:fn('backbone')
        p=self.out/'live-cache/backbone/vmm-child-binding.json'
        self.assertEqual(json.loads(p.read_text())['vmm_binding']['sandbox_id'],'sandbox')
        self.assertTrue((self.c.root/'live-cache/backbone/code').is_dir())
    def test_1_cache_then_pss(self):self.coexist([self.cache,self.pss])
    def test_2_pss_then_cache(self):self.coexist([self.pss,self.cache])
    def test_3_duplicate_pss(self):
        self.pss('backbone')
        with self.assertRaises(FileExistsError):self.pss('backbone')
    def test_4_duplicate_cache(self):
        self.cache('backbone')
        with self.assertRaisesRegex(ValueError,'expected new cold role'):self.cache('backbone')
    def test_5_other_role(self):
        self.cache('other');vm=self.pss('other')
        s=Sampler.__new__(Sampler);s.lock=threading.Lock();s.lifecycle=True;s.error=None;s.transitions={'backbone':{}}
        with self.assertRaisesRegex(ValueError,'role must enter transition first'):s.activate('other',vm)
if __name__=='__main__':unittest.main(verbosity=2)
