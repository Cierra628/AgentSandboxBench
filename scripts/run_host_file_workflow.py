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
    modes.add_argument('--tool-replay',type=Path,help='Frozen tool task specification; guest light + host current, no PSS or mincore')
    modes.add_argument('--exp1-light',action='store_true',help='Original Prettier #14400, full 26 actions, guest light and host current only')
    modes.add_argument('--trace-dual-task',action='store_true',help='One dual-guest suffix with targeted file-cache lifecycle tracing')
    modes.add_argument('--scaling',choices=('cold','restore'),help='One service/cgroup; cumulative 1/2/4/7 cold or restored guests')
    modes.add_argument('--current-pair',action='store_true',help='One group; 1 then 2 cold guests, original prefix and current counters only')
    parser.add_argument('--persistent-input',action='store_true',help='Current pair or full current-only task: group-external disk restore and fresh in-group input files')
    parser.add_argument('--local-no-proxy',action='store_true',help='Explicit persistent single task or Prettier GRPO: local inputs, no proxies; deny-egress with DNS exception')
    parser.add_argument('--local-no-proxy-check',action='store_true',help='With local-no-proxy: one creation and metadata check only, no task actions')
    parser.add_argument('--expanded-files',action='store_true',help='Explicit full shared-file GRPO: include retained upper and other owned regular files once after paths')
    modes.add_argument('--rootfs-short',action='store_true',help='Existing snapshot, two short suffixes, selected shared files without PSS')
    modes.add_argument('--private-file-group',choices=('shared','private'),help='Two private services with one cold prefix each; explicit 19-file copy control')
    parser.add_argument('--private-cold-read',action='store_true',help='Private-file group only: initial owned-file cold cache and sparse new-page observations')
    modes.add_argument('--full-rootfs-group',choices=('shared','private'),help='One service, complete seven-guest schedule and fixed initial-lower copies')
    parser.add_argument('--copy-schedule',choices=('grpo','bpo'),default='grpo',help='Full rootfs copy mode only; old default is GRPO')
    parser.add_argument('--copy-daemon',type=Path,help='Experimental daemon for full-rootfs-group only')
    parser.add_argument('--rootfs-residency',choices=('on','off'),default='on',help='Shared full GRPO only: skip P0/P1 residency, keep all other monitoring and identity checks')
    parser.add_argument('--rootfs-cold-read',action='store_true',help='Full GRPO only: verified zero initial residency and serial background queries')
    parser.add_argument('--host-sampling',choices=('on','off'),help='Matched-task/scaling control; current-only monitor-off config disables both samplers')
    parser.add_argument('--resume-prefix',type=Path,help='Independent pair, restore scaling or rootfs short: verified exported prefix, no repeated prefix actions')
    parser.add_argument('--host-memory-detail',action='store_true',help='Independent pair only: contiguous cache/PSS queries and mapped-file coverage')
    parser.add_argument('--rootfs-reference',action='store_true',help='Scaling only: N=1/N=7 selected shared files, including rootfs reference candidates')
    args=parser.parse_args()
    if args.local_no_proxy_check and not args.local_no_proxy:parser.error('local-no-proxy-check requires local-no-proxy')
    if args.expanded_files and args.exp1_light:parser.error('expanded-files requires complete shared-file GRPO')
    explicit_host_sampling=args.host_sampling
    args.host_sampling=args.host_sampling or 'on'
    if args.persistent_input and any((args.exp1_light,args.tool_replay,args.restore_pair,args.independent_matched_task,
            args.matched_task,args.short_pair,args.trace_dual_task,args.scaling,args.rootfs_short,args.private_file_group,args.full_rootfs_group)):
        parser.error('persistent-input requires current-pair or an ordinary current-only task')
    if args.private_cold_read and not args.private_file_group:parser.error('private-cold-read requires private-file-group')
    if args.rootfs_cold_read and (not args.full_rootfs_group or args.copy_schedule!='grpo' or args.rootfs_residency!='on'):
        parser.error('cold read requires explicit full GRPO with residency on')
    if bool(args.full_rootfs_group)!=bool(args.copy_daemon):parser.error('full-rootfs-group requires copy-daemon, and only that mode uses it')
    if args.copy_schedule!='grpo' and not args.full_rootfs_group:parser.error('copy-schedule requires full-rootfs-group')
    if args.rootfs_residency=='off' and (args.full_rootfs_group!='shared' or args.copy_schedule!='grpo'):
        parser.error('residency off requires explicit shared complete GRPO')
    if args.current_pair:
        if args.rootfs_reference or args.host_sampling=='off':parser.error('current-pair requires periodic sampling without rootfs queries')
        args.scaling='cold'
    if args.rootfs_reference and (not args.scaling or args.host_sampling!='on'):parser.error('rootfs-reference requires sampled scaling')
    if args.resume_prefix and not (args.independent_matched_task or args.scaling=='restore' or args.rootfs_short):parser.error('resume-prefix requires independent-matched-task, restore scaling or rootfs-short')
    if args.rootfs_short and not args.resume_prefix:parser.error('rootfs-short requires an existing exported prefix')
    if args.scaling=='restore' and not args.resume_prefix:parser.error('restore scaling requires resume-prefix; no new checkpoint capture')
    if args.host_memory_detail and not args.independent_matched_task:parser.error('host-memory-detail requires independent-matched-task')
    if args.trace_dual_task:args.matched_task=True
    cfg=None;current_monitor_off=False
    if args.exp1_light:
        from exp1_light import configuration
        configuration(args.config)
    else:
        from platform_tasks import configuration, preflight
        cfg=configuration(args.config)
        if not cfg.get('host_file_monitor'): raise ValueError('config must explicitly enable host_file_monitor')
        if cfg.get('host_current_only') and any((args.restore_pair,args.independent_matched_task,args.matched_task,
                args.short_pair,args.tool_replay,args.trace_dual_task,args.scaling,args.rootfs_short,
                args.private_file_group,args.full_rootfs_group)):
            parser.error('host_current_only config requires the ordinary single-task entry without other modes')
        current_monitor_off=cfg.get('host_current_only') and cfg.get('monitor_mode')=='off'
        if current_monitor_off:
            if explicit_host_sampling=='on':parser.error('monitor-off config cannot enable host sampling')
            args.host_sampling='off'
    if args.host_sampling=='off' and not current_monitor_off and not args.scaling and (not args.matched_task or args.trace_dual_task):
        parser.error('host-sampling off requires matched-task, scaling or current-only monitor-off config')
    if args.local_no_proxy:
        from prettier_no_proxy import require_mode,PROXY_KEYS,FLAG
        require_mode(cfg,args.persistent_input and not args.current_pair)
        if args.local_no_proxy_check and cfg['task_id']!='prettier__prettier-6604':parser.error('local-no-proxy-check remains Prettier-only')
        if args.local_no_proxy_check and cfg['rollout']!='single':parser.error('local-no-proxy-check requires a single guest')
        if args.local_no_proxy_check and current_monitor_off:parser.error('local-no-proxy-check requires sampling-on config')
        for key in PROXY_KEYS:os.environ.pop(key,None)
        os.environ[FLAG]='on'
    if cfg is not None:
        if args.expanded_files and not (cfg.get('host_shared_files') and cfg['rollout']=='grpo' and not args.persistent_input):
            parser.error('expanded-files requires the existing complete shared-file GRPO config')
        if args.persistent_input:
            if args.current_pair:
                if cfg['task_id']!='prettier__prettier-6604' or cfg['rollout']!='single' or cfg['checkpoint_after_step'] is not None or cfg.get('monitor_mode')=='off' or cfg.get('damon_observe') or cfg.get('damon_disable'):
                    parser.error('persistent-input pair requires the ordinary Prettier host config and default reclaim')
            else:
                from host_persistent_input import single_config
                single_config(cfg,local_no_proxy=args.local_no_proxy)
        if args.tool_replay:
            from run_host_tool_replay import check_inputs
            check_inputs(args.config,args.tool_replay)
        preflight(cfg,offline=not args.persistent_input)
    if subprocess.check_output(['docker','info','--format','{{.CgroupDriver}}'],text=True,timeout=10).strip()!='systemd':
        raise ValueError('this entry requires Docker systemd cgroups')
    name='asbhost-'+time.strftime('%Y%m%dt%H%M%S',time.gmtime())+str(os.getpid())
    slice_name=name+'.slice'
    persistent=None
    if args.persistent_input:
        from host_persistent_input import settings
        persistent=ROOT/'.artifacts'/settings(cfg['task_id'])['cache_root']/('host-persistent-'+name)
    command=['systemd-run','--quiet','--wait','--pipe','--collect','--unit='+name,
             '--slice='+slice_name,'--property=MemoryAccounting=yes','--property=IOAccounting=yes','--property=TimeoutStopSec=120',
             '--working-directory='+str(ROOT),'--setenv=ASB_HOST_FILE_SLICE='+slice_name,
             '--setenv=ASB_HOST_FILE_SAMPLING='+args.host_sampling,
             '--setenv=ASB_HOST_MEMORY_DETAIL='+('on' if args.host_memory_detail else 'off'),
             '--setenv=GIT_CONFIG_COUNT=1','--setenv=GIT_CONFIG_KEY_0=safe.directory',
             '--setenv=GIT_CONFIG_VALUE_0='+str(ROOT)]
    if os.environ.get('ASB_AGENTENV_SOURCE_SERVICE'):
        command+=['--setenv=ASB_AGENTENV_SOURCE_SERVICE='+os.environ['ASB_AGENTENV_SOURCE_SERVICE']]
    if os.environ.get('ASB_ISOLATED_UNUSED_NETWORK_POOLS')=='on':command+=['--setenv=ASB_ISOLATED_UNUSED_NETWORK_POOLS=on']
    if args.local_no_proxy:
        command+=['--setenv=ASB_LOCAL_NO_PROXY=on',*['--setenv='+key+'=' for key in PROXY_KEYS]]
        if args.local_no_proxy_check:command+=['--setenv=ASB_LOCAL_NO_PROXY_CHECK=on']
    if args.rootfs_reference:command+=['--setenv=ASB_HOST_ROOTFS_REFERENCE=on']
    if args.expanded_files:command+=['--setenv=ASB_HOST_EXPANDED_FILES=on']
    if args.exp1_light:
        command+=['python3','-B',str(ROOT/'scripts/run_host_exp1_light.py'),str(args.config.resolve())]
    elif args.tool_replay:
        command+=['python3','-B',str(ROOT/'scripts/run_host_tool_replay.py'),str(args.config.resolve()),str(args.tool_replay.resolve())]
    elif args.full_rootfs_group:
        command+=['python3','-B',str(ROOT/'scripts/run_host_full_rootfs.py'),'group',str(args.config.resolve()),
            '--mode',args.full_rootfs_group,'--copy-daemon',str(args.copy_daemon.resolve()),*(['--schedule',args.copy_schedule] if args.copy_schedule!='grpo' else []),
            *(['--rootfs-residency','off'] if args.rootfs_residency=='off' else []),
            *(['--rootfs-cold-read'] if args.rootfs_cold_read else [])]
    elif args.private_file_group:
        command+=['python3','-B',str(ROOT/'scripts/run_host_private_files.py'),'group',str(args.config.resolve()),'--mode',args.private_file_group,
            *(['--cold-read'] if args.private_cold_read else [])]
    elif args.rootfs_short:
        command+=['python3','-B',str(ROOT/'scripts/run_host_rootfs_short.py'),str(args.config.resolve()),'--resume-prefix',str(args.resume_prefix.resolve())]
    elif args.scaling:
        command+=['python3','-B',str(ROOT/'scripts/run_host_scaling.py'),str(args.config.resolve()),'--mode',args.scaling,
            *(['--current-pair'] if args.current_pair else []),
            *(['--persistent-seed',str(persistent)] if persistent else []),
            *(['--resume-prefix',str(args.resume_prefix.resolve())] if args.resume_prefix else [])]
    elif args.independent_matched_task:
        command+=['python3','-B',str(ROOT/'scripts/run_host_independent_pair.py'),str(args.config.resolve()),*(['--resume-prefix',str(args.resume_prefix.resolve())] if args.resume_prefix else [])]
    elif args.short_pair or args.restore_pair or args.matched_task:
        command+=['python3','-B',str(ROOT/'scripts/run_host_file_pair.py'),'run',str(args.config.resolve()),*(['--matched-task'] if args.matched_task else ['--restore-pair'] if args.restore_pair else []),*(['--trace-dual-task'] if args.trace_dual_task else [])]
    else:
        command+=['bash',str(ROOT/'scripts/use-platform.sh'),'run',str(args.config.resolve()),
            *(['--persistent-seed',str(persistent)] if persistent else [])]
    slice_started=False;persistent_owned=False
    try:
        if persistent:
            from host_persistent_input import run_phase
            persistent.mkdir(mode=0o700,parents=True,exist_ok=False);persistent_owned=True
            if args.local_no_proxy:
                from prettier_no_proxy import prepare_metadata
                prepare_metadata(persistent/'local-no-proxy-metadata',task_id=cfg['task_id'])
            if args.current_pair:run_phase('prepare',persistent,360)
            else:
                from platform_tasks import TASKS
                run_phase('prepare',persistent,360,task_profile=TASKS[cfg['task_id']])
        slice_started=True
        result=subprocess.run(command,timeout=1500)
        return result.returncode
    except BaseException:
        # Stop only our named unit. The existing isolated controller performs owned cleanup.
        if slice_started:subprocess.run(['systemctl','stop',name+'.service'],timeout=150,check=False)
        raise
    finally:
        # systemd-run --wait has finished before the owned slice is stopped.
        try:
            if slice_started:subprocess.run(['systemctl','stop',slice_name],timeout=30,check=True)
        finally:
            if persistent_owned:
                from host_persistent_input import run_phase
                run_phase('release-source',persistent,120)


if __name__=='__main__': raise SystemExit(main())
