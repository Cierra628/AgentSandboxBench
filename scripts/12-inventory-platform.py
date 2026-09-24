"""Read-only host capability inventory; creates only a private evidence directory."""
import ctypes,datetime,fcntl,json,mmap,os,pathlib,struct,subprocess
root=pathlib.Path(__file__).resolve().parent.parent
out=root/'.artifacts'/('platform-inventory-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
os.umask(0o077);out.mkdir()
checks={}
commands={'host':['uname','-a'],'identity':['id'],'cpu':['lscpu'],'memory':['free','-b'],'disk':['df','-B1',str(root)],'mounts':['findmnt','-t','cgroup2'],'bpf_program_query':['bpftool','-j','prog','show'],'containers':['docker','ps','--format','{{.ID}} {{.Names}} {{.Image}}'],'images':['docker','image','ls','--digests','--format','{{.Repository}} {{.Tag}} {{.Digest}} {{.ID}}'],'processes':['ps','-eo','pid,ppid,user,etime,comm']}
for name,cmd in commands.items():
 try:
  p=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
  (out/(name+'.txt')).write_text(p.stdout+p.stderr);checks[name]={'rc':p.returncode}
 except Exception as e:checks[name]={'error':str(e)}
for path in ['/proc/self/status','/proc/self/cgroup','/proc/meminfo','/proc/pressure/memory','/sys/fs/cgroup/cgroup.controllers','/sys/fs/cgroup/memory.stat','/sys/fs/cgroup/memory.current','/sys/module/ublk_drv/parameters/ublks_max']:
 try:(out/(path.strip('/').replace('/','_')+'.txt')).write_text(pathlib.Path(path).read_text())
 except OSError as e:checks[path]={'error':str(e)}
try:
 fd=os.open('/dev/kvm',os.O_RDWR);checks['kvm_api']=fcntl.ioctl(fd,0xAE00,0);os.close(fd)
except OSError as e:checks['kvm_api']={'error':str(e)}
try:
 fd=os.open('/dev/ublk-control',os.O_RDWR);os.close(fd);checks['ublk_control_open']='PASS; no device created'
except OSError as e:checks['ublk_control_open']={'error':str(e)}
try:
 page=os.sysconf('SC_PAGE_SIZE');m=mmap.mmap(-1,page);m[0]=1
 addr=ctypes.addressof(ctypes.c_char.from_buffer(m))
 with open('/proc/self/pagemap','rb',buffering=0) as f:f.seek((addr//page)*8);entry=struct.unpack('Q',f.read(8))[0]
 pfn=entry&((1<<55)-1)
 checks['self_pagemap']={'page_size':page,'present':bool(entry>>63),'pfn_visible':pfn!=0}
 if pfn:
  with open('/proc/kpageflags','rb',buffering=0) as f:f.seek(pfn*8);checks['kpageflags_read']=len(f.read(8))==8
 m.close()
except OSError as e:checks['self_pagemap']={'error':str(e)}
checks['btf_exists']=pathlib.Path('/sys/kernel/btf/vmlinux').is_file()
checks['bpf_program_load']='NOT_TESTED; query only'
checks['guest_kernel']='NOT_TESTED; host uname is not guest evidence'
checks['physical_file_probe_calibration']='NOT_TESTED'
(out/'checks.json').write_text(json.dumps(checks,indent=2)+'\n')
print('status=INFO result_dir='+str(out));print(json.dumps(checks,indent=2))
