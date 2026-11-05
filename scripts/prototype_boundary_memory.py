"""Boundary memory helper for the local prototype and explicit merged-timer GRPO."""
import os
import time

START_CODE='''import time
import sys
sys.path.insert(0,sys.argv[1])
from prototype_boundary_memory import sample_memory
sample_memory(*sys.argv[2:])
print(time.monotonic_ns())'''
END_CODE='''import time
ended=time.monotonic_ns()
import sys
sys.path.insert(0,sys.argv[1])
from prototype_boundary_memory import sample_memory
sample_memory(*sys.argv[2:])
print(ended)'''

FIELDS=('MemTotal','MemFree','Buffers','Cached','Shmem','AnonPages','Active(file)','Inactive(file)','Dirty')


def sample_memory(phase,step,destination,phase_path,meminfo,sandbox,branch,segment):
    if os.path.isdir(os.path.dirname(phase_path)):
        with open(phase_path,'w') as stream:
            stream.write(f'{"waiting" if phase=="complete" else phase}\t{segment}\t{step}\n')
    timestamp=time.time_ns();values={}
    with open(meminfo) as stream:
        for line in stream:
            parts=line.split()
            if parts and parts[0].endswith(':') and parts[0][:-1] in FIELDS:
                values[parts[0][:-1]]=int(parts[1])
    row=[timestamp,phase,sandbox,branch,segment,step,*[values.get(k,0) for k in FIELDS]]
    with open(destination,'a') as stream:stream.write('\t'.join(map(str,row))+'\n')
    if phase=='before':
        with open(phase_path,'w') as stream:stream.write(f'running\t{segment}\t{step}\n')
