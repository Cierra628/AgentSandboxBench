"""Explicit light variant of the original Exp1; old task inputs stay unchanged."""
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
GUEST='/workspace/asb-exp1-light'


def configuration(path):
    from use_platform import configuration as original
    cfg=original(path)
    if cfg['backends']!=['agentenv'] or cfg['storage']!='isolated' or cfg['checkpoint_after_step'] is not None or cfg['sample_interval_seconds']!=.1:
        raise ValueError('Exp1 light requires AgentENV, isolated full replay, no checkpoint and 0.1-second sampling')
    return cfg


def mode(args,cfg):
    enabled=getattr(args,'exp1_light',False)
    if enabled and not (cfg and cfg['task_id']=='prettier__prettier-14400' and cfg['checkpoint_after_step'] is None and
                        getattr(args,'light_monitor',False) and getattr(args,'host_file_monitor',False) and
                        getattr(args,'host_current_only',False) and getattr(args,'immutable_cache_seed',None)):
        raise ValueError('Exp1 light requires explicit full platform config, light monitor, host current-only and verified cache seed')
    return enabled


def instrument(text):
    start=text.index('for action in "$actions"/*.sh; do\n')
    end=text.index('\nif [[ $end_step == 026 ]]; then',start)
    replacement=('test "$start_step" = 001 && test "$end_step" = 026\n'
        f'python3 {GUEST}/guest_exp1_actions.py --actions "$actions" --output "$out" '
        '--timeout-seconds "$timeout_seconds" --phase-file /workspace/asb-runtime-monitor/phase\n')
    return text[:start]+replacement+text[end:]


def bundle(raw,workload,runner):
    target=raw/'light-inputs';target.mkdir()
    shutil.copytree(workload/'actions',target/'actions')
    shutil.copy2(workload/'actions.tsv',target/'actions.tsv')
    (target/'runner.sh').write_text(instrument(runner.read_text()))
    shutil.copy2(ROOT/'scripts/guest_exp1_actions.py',target/'guest_exp1_actions.py')
    shutil.copy2(ROOT/'scripts/exp1_independent_check.cjs',target/'check.cjs')
    return target
