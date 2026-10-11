"""Recount four diagnostic replays and export public phase durations."""
import argparse
import html
import json
from pathlib import Path
import statistics

from run_trenvx_runner_phases import ROOT, audit


def summarize(directory):
    checked = audit(directory)
    source = json.loads((directory/'result.json').read_text())
    rows = []
    for run in source['runs']:
        phases = run['phases']; timing = run['timing_ns']; clock = phases['clock']
        rows.append(dict(run_id=run['run_id'], raw_dir=str(Path(run['raw_dir']).relative_to(ROOT)),
            calibration_before_replay=run['calibration_before_replay'],
            phase_seconds={k:v/1e9 for k,v in phases['phase_ns'].items()},
            initial_git_commands=phases['baseline_commands'],
            guest_runner_seconds=clock['monotonic_raw_ns']/1e9,
            guest_action_sum_seconds=sum(run['action_ns'].values())/1e9,
            controller_runner_seconds=timing['controller_runner_call_ns']/1e9,
            controller_end_to_end_seconds=timing['controller_end_to_end_ns']/1e9,
            controller_outside_guest_clock_seconds=(timing['controller_runner_call_ns']-clock['monotonic_raw_ns'])/1e9,
            clock_realtime_minus_monotonic_ns=clock['realtime_ns']-clock['monotonic_raw_ns'],
            guest_cpu_seconds=(clock['user_ns']+clock['system_ns'])/1e9,
            action_seconds={k:v/1e9 for k,v in run['action_ns'].items()},
            patch_sha256=run['patch_sha256']))
    values = {}
    for name in ('controller_runner_seconds','guest_runner_seconds','guest_action_sum_seconds',
                 'controller_end_to_end_seconds','controller_outside_guest_clock_seconds'):
        sequence = [row[name] for row in rows]
        values[name] = dict(mean=statistics.mean(sequence), minimum=min(sequence), maximum=max(sequence))
    for name in ('entry','baseline','actions','finalize'):
        sequence = [row['phase_seconds'][name] for row in rows]
        values['phase_'+name+'_seconds'] = dict(mean=statistics.mean(sequence), minimum=min(sequence), maximum=max(sequence))
    return dict(status='PASS', third_acceptance='PARTIAL', source_dir=str(directory.relative_to(ROOT)),
        protocol=source['protocol'], calibration=checked['calibration'], runs=rows, statistics=values,
        measured_scope='four new off-sampling diagnostic Exp1 replays; fixture activity before first replay only',
        units='seconds unless an explicit _ns field; Git real/user/system fields are seconds; boundary_ns is nanoseconds',
        phase_scope=dict(entry='runner entry through environment initialization',
                         baseline='initial four Git commands plus nearby runner setup',
                         actions='26 frozen actions plus loop housekeeping',
                         finalize='final Git add/diff/reset/status and result-file copies'),
        limits=['not a new monitoring overhead comparison', 'initial reset CPU/wall recorded; final reset not individually timed',
                'controller minus guest duration includes SDK and clock boundaries; not pure RPC latency',
                'no memory sampling, physical scans, checkpoint or restore in this diagnostic',
                'cannot assign Git waiting cause from these timers alone; old 60-run statistics unchanged'])


def plot(summary):
    width = 1040; scale = 760/26
    colors = {'entry':'#8b5cf6','baseline':'#2563eb','actions':'#16a34a','finalize':'#ea580c'}
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="420" viewBox="0 0 {width} 420">',
             '<rect width="1040" height="420" fill="white"/>',
             '<g font-family="sans-serif" font-size="14" fill="#1f2937">',
             '<text x="24" y="28" font-size="19">TrEnv-X Exp1：四次关闭采样的诊断计时</text>',
             '<text x="24" y="52">色块为来宾 runner 阶段；工具步骤计时之和另列。单位：秒</text>']
    labels = {'entry':'初始化','baseline':'Git 准备','actions':'动作循环','finalize':'结果整理'}
    for index,(name,color) in enumerate(colors.items()):
        x = 170+index*185
        parts += [f'<rect x="{x}" y="70" width="15" height="15" fill="{color}"/>',
                  f'<text x="{x+22}" y="83">{labels[name]}</text>']
    for index,row in enumerate(summary['runs']):
        y = 120+index*62; x = 170
        parts.append(f'<text x="24" y="{y+18}">第 {index+1} 次</text>')
        for name,color in colors.items():
            seconds = row['phase_seconds'][name]; w = seconds*scale
            parts.append(f'<rect x="{x:.3f}" y="{y}" width="{w:.3f}" height="26" fill="{color}"><title>{html.escape(labels[name])}: {seconds:.6f} s</title></rect>')
            if w > 58:
                parts.append(f'<text x="{x+w/2:.3f}" y="{y+18}" text-anchor="middle" fill="white">{seconds:.3f}</text>')
            x += w
        parts.append(f'<text x="170" y="{y+44}">工具之和 {row["guest_action_sum_seconds"]:.3f}；初始 reset {row["initial_git_commands"]["reset"]["real_seconds"]:.3f}；控制器调用 {row["controller_runner_seconds"]:.3f}</text>')
    parts += ['<text x="24" y="393">第一轮重放前有小负载校准。此图不用于监控开销验收，也未证明等待原因。</text>', '</g></svg>']
    return '\n'.join(parts)+'\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--svg', type=Path)
    args = parser.parse_args()
    summary = summarize(args.input.resolve(strict=True))
    with args.output.open('x') as stream:
        json.dump(summary, stream, indent=2, ensure_ascii=False); stream.write('\n')
    if args.svg:
        with args.svg.open('x') as stream:
            stream.write(plot(summary))
    print(f'status=PASS replays=4 third_acceptance=PARTIAL output={args.output}')
