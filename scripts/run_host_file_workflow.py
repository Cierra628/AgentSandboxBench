"""Create an owned systemd slice before input preparation or any guest process."""
import argparse
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('config',type=Path)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--restore-pair',action='store_true',help='One actual snapshot, N=1/N=2 short restore observations')
    modes.add_argument('--independent-matched-task',action='store_true',help='One exported checkpoint; separate fresh writable services for N=1/N=2')
    modes.add_argument('--matched-task',action='store_true',help='One step-11 snapshot; identical early_1 suffix for N=1/N=2')
    modes.add_argument('--short-pair',action='store_true',help='Two cold guests and synthetic marker checks only; no benchmark task')
    modes.add_argument('--trace-dual-task',action='store_true',help='One dual-guest suffix with targeted file-cache lifecycle tracing')
    modes.add_argument('--scaling',choices=('cold','restore'),help='One service/cgroup; cumulative 1/2/4/7 cold or restored guests')
    modes.add_argument('--rootfs-short',action='store_true',help='Existing snapshot, two short suffixes, selected shared files without PSS')
    modes.add_argument('--private-file-group',choices=('shared','private'),help='Two private services with one cold prefix each; explicit 19-file copy control')
    modes.add_argument('--full-rootfs-group',choices=('shared','private'),help='One service, complete seven-guest schedule and fixed initial-lower copies')
    parser.add_argument('--copy-schedule',choices=('grpo','bpo'),default='grpo',help='Full rootfs copy mode only; old default is GRPO')
    parser.add_argument('--copy-daemon',type=Path,help='Experimental daemon for full-rootfs-group only')
    parser.add_argument('--host-sampling',choices=('on','off'),default='on',help='Matched-task or scaling timing control; scaling keeps fixed phase observations')
    parser.add_argument('--resume-prefix',type=Path,help='Independent pair, restore scaling or rootfs short: verified exported prefix, no repeated prefix actions')
    parser.add_argument('--host-memory-detail',action='store_true',help='Independent pair only: contiguous cache/PSS queries and mapped-file coverage')
    parser.add_argument('--rootfs-reference',action='store_true',help='Scaling only: N=1/N=7 selected shared files, including rootfs reference candidates')
    args=parser.parse_args()
    if bool(args.full_rootfs_group)!=bool(args.copy_daemon):parser.error('full-rootfs-group requires copy-daemon, and only that mode uses it')
    if args.copy_schedule!='grpo' and not args.full_rootfs_group:parser.error('copy-schedule requires full-rootfs-group')
    if args.rootfs_reference and (not args.scaling or args.host_sampling!='on'):parser.error('rootfs-reference requires sampled scaling')
    if args.resume_prefix and not (args.independent_matched_task or args.scaling=='restore' or args.rootfs_short):parser.error('resume-prefix requires independent-matched-task, restore scaling or rootfs-short')
    if args.rootfs_short and not args.resume_prefix:parser.error('rootfs-short requires an existing exported prefix')
    if args.scaling=='restore' and not args.resume_prefix:parser.error('restore scaling requires resume-prefix; no new checkpoint capture')
    if args.host_memory_detail and not args.independent_matched_task:parser.error('host-memory-detail requires independent-matched-task')
    if args.trace_dual_task:args.matched_task=True
    if args.host_sampling=='off' and not args.scaling and (not args.matched_task or args.trace_dual_task):
        parser.error('host-sampling off requires matched-task without tracing; explicit scaling also supports this option')
    from platform_tasks import configuration, preflight
    cfg=configuration(args.config)
    if not cfg.get('host_file_monitor'): raise ValueError('config must explicitly enable host_file_monitor')
    preflight(cfg,offline=True)
    if subprocess.check_output(['docker','info','--format','{{.CgroupDriver}}'],text=True,timeout=10).strip()!='systemd':
        raise ValueError('this entry requires Docker systemd cgroups')
    name='asbhost-'+time.strftime('%Y%m%dt%H%M%S',time.gmtime())+str(os.getpid())
    slice_name=name+'.slice'
    command=['systemd-run','--quiet','--wait','--pipe','--collect','--unit='+name,
             '--slice='+slice_name,'--property=MemoryAccounting=yes','--property=IOAccounting=yes','--property=TimeoutStopSec=120',
             '--working-directory='+str(ROOT),'--setenv=ASB_HOST_FILE_SLICE='+slice_name,
             '--setenv=ASB_HOST_FILE_SAMPLING='+args.host_sampling,
             '--setenv=ASB_HOST_MEMORY_DETAIL='+('on' if args.host_memory_detail else 'off'),
             '--setenv=GIT_CONFIG_COUNT=1','--setenv=GIT_CONFIG_KEY_0=safe.directory',
             '--setenv=GIT_CONFIG_VALUE_0='+str(ROOT)]
    if os.environ.get('ASB_AGENTENV_SOURCE_SERVICE'):
        command+=['--setenv=ASB_AGENTENV_SOURCE_SERVICE='+os.environ['ASB_AGENTENV_SOURCE_SERVICE']]
    if args.rootfs_reference:command+=['--setenv=ASB_HOST_ROOTFS_REFERENCE=on']
    if args.full_rootfs_group:
        command+=['python3','-B',str(ROOT/'scripts/run_host_full_rootfs.py'),'group',str(args.config.resolve()),
            '--mode',args.full_rootfs_group,'--copy-daemon',str(args.copy_daemon.resolve()),*(['--schedule',args.copy_schedule] if args.copy_schedule!='grpo' else [])]
    elif args.private_file_group:
        command+=['python3','-B',str(ROOT/'scripts/run_host_private_files.py'),'group',str(args.config.resolve()),'--mode',args.private_file_group]
    elif args.rootfs_short:
        command+=['python3','-B',str(ROOT/'scripts/run_host_rootfs_short.py'),str(args.config.resolve()),'--resume-prefix',str(args.resume_prefix.resolve())]
    elif args.scaling:
        command+=['python3','-B',str(ROOT/'scripts/run_host_scaling.py'),str(args.config.resolve()),'--mode',args.scaling,
            *(['--resume-prefix',str(args.resume_prefix.resolve())] if args.resume_prefix else [])]
    elif args.independent_matched_task:
        command+=['python3','-B',str(ROOT/'scripts/run_host_independent_pair.py'),str(args.config.resolve()),*(['--resume-prefix',str(args.resume_prefix.resolve())] if args.resume_prefix else [])]
    elif args.short_pair or args.restore_pair or args.matched_task:
        command+=['python3','-B',str(ROOT/'scripts/run_host_file_pair.py'),'run',str(args.config.resolve()),*(['--matched-task'] if args.matched_task else ['--restore-pair'] if args.restore_pair else []),*(['--trace-dual-task'] if args.trace_dual_task else [])]
    else:
        command+=['bash',str(ROOT/'scripts/use-platform.sh'),'run',str(args.config.resolve())]
    try:
        result=subprocess.run(command,timeout=1500)
        return result.returncode
    except BaseException:
        # Stop only our named unit. The existing isolated controller performs owned cleanup.
        subprocess.run(['systemctl','stop',name+'.service'],timeout=150,check=False)
        raise
    finally:
        # systemd-run --wait has finished before the owned slice is stopped.
        subprocess.run(['systemctl','stop',slice_name],timeout=30,check=True)


if __name__=='__main__': raise SystemExit(main())
