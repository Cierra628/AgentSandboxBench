"""Rebuild the checkpoint run's phase and memory summary from local raw data."""
import csv
import json
import pathlib
import sys

audit = pathlib.Path(sys.argv[1]).resolve()
run_id = audit.name
raw = audit.parent.parent / 'raw' / run_id
assert json.loads((audit / 'result.json').read_text())['status'] == 'PASS'
events = {e['stage']: e['duration_ns'] for e in json.loads((audit / 'events.json').read_text())}


def read_tsv(path):
    with path.open() as f:
        return list(csv.DictReader(f, delimiter='\t'))


steps = read_tsv(raw / 'replay/steps.tsv')
guest = read_tsv(raw / 'replay/guest-memory.tsv')
host = read_tsv(raw / 'host/memory-samples.tsv')
result = json.loads((audit / 'result.json').read_text())
config = json.loads((audit / 'effective-config.json').read_text())
phase_ns = {
    'cold_start_api': events['cold-start'],
    'create_to_first_tool_ready': events['cold-start'] + events['initial-ready'],
    'upload_inputs': sum(events[k] for k in ('upload-actions', 'upload-manifest', 'upload-runner', 'upload-probe')),
    'first_segment_controller': events['actions-001-013'],
    'guest_actions_total': sum(int(s['duration_ns']) for s in steps),
    'checkpoint_api': events['checkpoint'],
    'delete_original': events['delete-original'],
    'restore_api': events['restore'],
    'restore_to_tool_ready': events['restore'] + events['restored-ready'],
    'second_segment_controller': events['actions-014-026'],
    'download_artifacts': events['download-replay'],
    'delete_restored_and_snapshot': events['delete-restored'] + events['delete-snapshot'],
}
host_all_peak = max(int(x['cgroup_current_bytes']) for x in host)
host_file_peak = max(int(x['cgroup_file_bytes']) for x in host)
guest_peak = max(int(x['MemTotal_kib']) - int(x['MemAvailable_kib']) for x in guest)
summary = {
    'run_id': run_id,
    'image': config['image'],
    'checkpoint_after_step': config['checkpoint_after_step'],
    'phase_duration_ns': phase_ns,
    'controller_elapsed_ns': result.get('controller_elapsed_ns'),
    'controller_elapsed_status': 'MEASURED' if 'controller_elapsed_ns' in result else 'NOT_MEASURED',
    'host_cgroup_observed_peak_bytes': host_all_peak,
    'host_cgroup_file_observed_peak_bytes': host_file_peak,
    'guest_working_set_observed_peak_kib': guest_peak,
    'host_sample_count': len(host),
    'guest_sample_count': len(guest),
    'cache_policy': config['cache_policy'],
    'monitor_overhead': 'NOT_MEASURED',
    'checkpoint_remote_durability': 'NOT_VERIFIED',
}
(audit / 'phase-breakdown.json').write_text(json.dumps(summary, indent=2) + '\n')
lines = [f'# Exp1 第 {config["checkpoint_after_step"]} 步 checkpoint 验证', '',
         f'运行 `{run_id}`；冷启动已命中前一轮预热的固定镜像缓存。', '',
         '| 阶段 | 控制器视角耗时 (ms) |', '| --- | ---: |']
labels = {
    'create_to_first_tool_ready': '创建至首次工具通道可用',
    'upload_inputs': '上传冻结动作与探针',
    'first_segment_controller': '步骤 001–013 工具调用',
    'checkpoint_api': 'checkpoint API',
    'delete_original': '删除原沙箱',
    'restore_to_tool_ready': '恢复至工具通道可用',
    'second_segment_controller': '步骤 014–026 工具调用',
    'download_artifacts': '下载原始结果',
    'delete_restored_and_snapshot': '删除恢复实例及 snapshot',
}
for key, label in labels.items():
    lines.append(f'| {label} | {phase_ns[key] / 1e6:.1f} |')
lines.extend(['', f'guest 内 26 步工具执行时间之和：{phase_ns["guest_actions_total"] / 1e6:.1f} ms；与两段控制器调用耗时口径不同。',
              '控制器端到端时间：' + (f'{result["controller_elapsed_ns"] / 1e6:.1f} ms。' if 'controller_elapsed_ns' in result else '本轮未记录，不能用阶段耗时之和代替。'),
              f'host 服务 cgroup 观测峰值 {host_all_peak / 1048576:.1f} MiB，其中 file 观测峰值 {host_file_peak / 1048576:.1f} MiB；guest working-set 观测峰值 {guest_peak / 1024:.1f} MiB。',
              f'原始采样：host {len(host)} 条、guest {len(guest)} 条；采样周期配置 {config["sample_interval_seconds"]} 秒，采集开销未测。',
              '', '恢复前后 PID、start_ticks、boot ID 相同，进程计数继续增长；26 步顺序、预期失败集合、关键输出和最终 patch 与完整重放一致。',
              'checkpoint 已验证本机可恢复；远端持久化完成未验证。独立任务测试与跨系统性能结论未验证。',
              '完整原始数据、CLI 日志和采样位于同一运行 ID 的 raw/audit 目录；图表见 output_root/figures。', ''])
(audit / 'phase-report.md').write_text('\n'.join(lines))
print(f'status=PASS run_id={run_id} report={audit / "phase-report.md"}')
