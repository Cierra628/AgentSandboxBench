"""Opt-in light rounds alongside the unchanged Prettier BPO business checks."""
from pathlib import Path
from host_sandbox_monitor import Binding
from run_guest_light_memory import bind_vmm
from light_memory_round import Sampler


def diagnostic_kernel_args(path):
    """Only the opt-in BPO route requires the release used by the cache exporter."""
    if path is None:return []
    import mmap
    from platform_tasks import ROOT
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT/'.artifacts') or not path.is_file():
        raise ValueError(f'missing or non-project diagnostic guest kernel: {path}')
    with path.open('rb') as f:
        if f.read(4) != b'\x7fELF':raise ValueError(f'not a diagnostic ELF: {path}')
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as image:
            if image.find(b'Linux version 6.1.175-asb-cache-v3 ') < 0:
                raise ValueError(f'expected diagnostic guest kernel 6.1.175-asb-cache-v3: {path}')
    return ['--diagnostic-guest-kernel', str(path)]


def options(args,*,native=False):
    if not getattr(args,'light_rollout',False):return False
    schedule=getattr(args,'prettier6604_schedule' if native else 'schedule','grpo')
    if schedule!='bpo' or not getattr(args,'schedule_smoke_metrics',False):
        raise ValueError('light rollout requires complete BPO schedule smoke')
    if any(getattr(args,k,None) for k in ('schedule_expanded_files','schedule_cache_layers','schedule_sampling_control')):
        raise ValueError('light rollout excludes expanded queries and sampling controls')
    return True


class LightRollout:
    def __init__(self,container,out,roles,*,page_cache=None):
        self.page_cache=page_cache
        self.out=Path(out);self.binding=Binding(container,out);self.by_role={}
        self.sampler=Sampler(self.binding.cgroup,{},self.out,lifecycle=True,expected_roles=roles)
    def before_create(self,role,*,restored):
        if self.page_cache:self.page_cache.prepare(role,restored=restored)
    def register(self,role,sid):
        vm=bind_vmm(self.binding,sid,self.out,role)
        if self.page_cache:self.page_cache.register(role,vm)
        self.sampler.activate(role,vm);self.by_role[role]=vm
    def saving(self,role):
        self.sampler.transition(role,'SAVING')
        if self.page_cache:self.page_cache.saving(role)
    def saved(self,role):
        if self.page_cache:self.page_cache.saved(role,self.by_role[role])
        self.sampler.resume_same(role,self.by_role[role])
    def stop(self):
        try:
            if self.page_cache:self.page_cache.stop()
        finally:
            result=self.sampler.stop()
        return result
