import ctypes,json,mmap,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from agentenv_whole_cache import bind_vm

class BindingTests(unittest.TestCase):
    def test_unregistered_concurrent_vmm_is_not_a_candidate(self):
        with tempfile.TemporaryDirectory() as td,mmap.mmap(-1,4096) as memory:
            d=Path(td);memory[:6]=b'marker';address=ctypes.addressof(ctypes.c_char.from_buffer(memory))
            (d/'marker.json').write_text(json.dumps(dict(sandbox_id='guest',text='marker',pfn=1)))
            (d/'iomem.txt').write_text('owned RAM')
            (d/'kvm-slots.tsv').write_text(f'{os.getpid()}\t0\t0\t1000\t{address:x}\t0\n')
            with patch('agentenv_whole_cache.identity'),patch('agentenv_whole_cache.slots_for_ram',return_value=[dict(guest=4096,host=address,size=4096)]) as slots:
                vm,_=bind_vm(d,[999999999,os.getpid()],'/owned','/owned/firecracker','guest')
            self.assertEqual(vm['pid'],os.getpid());self.assertEqual(slots.call_count,1)

if __name__=='__main__':unittest.main()
