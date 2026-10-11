"""Read-only CPU allocation/policy inventory; never declares idle CPUs exclusive."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import time


def expand(text):
    out=set()
    for item in text.replace(' ',',').split(','):
        if not item:continue
        a,sep,b=item.partition('-')
        out.update(range(int(a),int(b)+1) if sep else [int(a)])
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpus',default='2,4')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    out=root/'.artifacts/cpu-allocation'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}')
    os.umask(0o077);out.mkdir(parents=True)
    primary=expand(args.cpus);policies={};siblings=set()
    for cpu in primary:
        p=Path(f'/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list')
        siblings.update(expand(p.read_text()))
    for cpu in sorted(siblings):
        p=Path(f'/sys/devices/system/cpu/cpu{cpu}')
        names=('related_cpus','affected_cpus','scaling_driver','scaling_governor','scaling_available_frequencies',
               'scaling_available_governors','scaling_min_freq','scaling_max_freq')
        policies[str(cpu)]={name:(p/'cpufreq'/name).read_text().strip() for name in names}
        policies[str(cpu)].update(policy_path=str((p/'cpufreq').resolve()),
            physical_package_id=(p/'topology/physical_package_id').read_text().strip(),
            core_id=(p/'topology/core_id').read_text().strip(),siblings=(p/'topology/thread_siblings_list').read_text().strip())
    processes=[];affinities=Counter();errors=0
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            # Exclude kernel threads, without collecting command lines/environment.
            if not (proc/'exe').exists():continue
            for task in (proc/'task').iterdir():
                allowed=os.sched_getaffinity(int(task.name))
                if allowed & siblings:
                    affinities[','.join(map(str,sorted(allowed)))]+=1
                    processes.append(dict(pid=int(proc.name),tid=int(task.name),uid=proc.stat().st_uid,
                        comm=(task/'comm').read_text().strip(),allowed_count=len(allowed),overlap=sorted(allowed & siblings)))
        except (OSError,ValueError):errors+=1
    groups=[]
    for f in Path('/sys/fs/cgroup').rglob('cpuset.cpus.partition'):
        try:
            group=f.parent
            assigned=(group/'cpuset.cpus').read_text().strip()
            exclusive=group/'cpuset.cpus.exclusive'
            groups.append(dict(path=str(group),partition=f.read_text().strip(),cpus=assigned,
                effective=(group/'cpuset.cpus.effective').read_text().strip(),
                exclusive=exclusive.read_text().strip() if exclusive.exists() else None))
        except OSError:errors+=1
    result=dict(status='READ_ONLY_COMPLETE',candidate_primary_cpus=sorted(primary),candidate_with_siblings=sorted(siblings),
        policies=policies,isolated=Path('/sys/devices/system/cpu/isolated').read_text().strip(),
        nohz_full=Path('/sys/devices/system/cpu/nohz_full').read_text().strip(),
        allowed_userspace_thread_count=len(processes),allowed_userspace_uid_counts=dict(Counter(str(p['uid']) for p in processes)),
        affinity_sets=dict(affinities),cpuset_groups=groups,transient_read_errors=errors,
        reservation_status='NOT_CONFIRMED; scheduling eligibility/partitions cannot establish authorization',
        mutations='none')
    (out/'inventory.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'eligible-threads-private.json').write_text(json.dumps(processes,indent=2)+'\n')
    print(f"status=READ_ONLY_COMPLETE candidate={sorted(siblings)} eligible_threads={len(processes)} result_dir={out}")


if __name__=='__main__':main()
