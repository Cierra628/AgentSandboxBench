"""Offline comparison of successful common-prefix runs; preserve failed runs."""
import argparse
import hashlib
import json
from pathlib import Path

from prettier6604_inputs import ROOT
from report_prettier6604_grpo import audit
from shared_task_cache import recount


def hashes(raw):
    return {str(p.relative_to(raw)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(raw.rglob('*')) if p.is_file()}


def report(workflows, output):
    output=output.resolve()
    assert not output.exists() and output.is_relative_to(ROOT/'.artifacts')
    runs=[];baseline=None;source=None;seed=None;inputs={}
    for workflow in workflows:
        workflow=workflow.resolve();receipt=json.loads((workflow/'result.json').read_text())
        cfg=json.loads((workflow/'effective-config.json').read_text())
        assert receipt['status']=='PASS' and receipt['cache_released'] and cfg['shared_cache_measurement']
        assert all(receipt['cleanup'].values())
        child=Path(receipt['storage_result_dir']);outer=json.loads((child/'result.json').read_text())
        assert outer['status']=='PASS' and all(outer['cleanup'].values())
        raw=child/'prettier6604';assert not output.is_relative_to(child) and not output.is_relative_to(workflow)
        before=hashes(raw)
        task,_,_=audit(raw,Path(cfg['inputs']))
        shared=recount(raw);saved=json.loads((raw/'shared-cache/summary.json').read_text())
        assert all(saved[k]==v for k,v in shared.items())
        effective=json.loads((raw/'effective-config.json').read_text())
        comparable={k:effective[k] for k in ('task_id','base_commit','source_image','guest_cpu','guest_memory_mib',
                      'sampling_sha256','runner_sha256','rollouts_sha256','manifest_sha256')}
        current_source=json.loads((raw/'physical-source-sha256.json').read_text())
        members=json.loads((raw/'shared-cache/members.json').read_text())
        pinned=[m.get('calibration_pinned_bytes',0) for m in members]
        assert all(n in (0,12288) for n in pinned)
        current_seed=outer['immutable_cache_seed']['uncompressed_sha256']
        if baseline is None:baseline,source,seed=comparable,current_source,current_seed
        else:
            assert comparable==baseline and current_seed==seed
            assert set(source)==set(current_source)
            allowed={'shared_task_cache.py'}
            if all(pinned):allowed.update({'task_physical_cache.py','cached_file_inventory.py','guest_cached_file_inventory.py'})
            assert {k for k in source if source[k]!=current_source.get(k)} <= allowed
        runtime_path=Path(receipt['report_dir'])/'runtime-metrics.json'
        runtime=json.loads(runtime_path.read_text());assert runtime['status']=='PASS' and runtime['raw_hashes_unchanged']
        shared.update(analysis_elapsed_ns=saved['analysis_elapsed_ns'],collector_cpu_seconds=saved['collector_cpu_seconds'])
        shared['limitations'][0]='Measured image layers are warm converted-seed copies on the owned ext4 loop volume; the separate RAM preparation cache is excluded.'
        runs.append(dict(workflow=str(workflow),raw=str(raw),root_count=cfg['root_count'],actions=task['actions'],
                         executed_physical_source_sha256=current_source,
                         calibration_pinned_bytes_per_sandbox=pinned,
                         platform_status=task['status'],physical_measurement=shared,
                         per_role=task['per_role'],timing=task['timing'],cleanup=outer['cleanup'],
                         guest_samples={r['role']:r['sample_count'] for r in runtime['per_sandbox']},
                         raw_file_count=len(before),observed_volume_used_peak_bytes=outer['observed_volume_used_peak_bytes'],
                         observed_host_available_min_bytes=outer['observed_host_available_min_bytes']))
        assert hashes(raw)==before
        inputs[str(raw)]=before
    runs.sort(key=lambda r:r['root_count']);counts=[r['root_count'] for r in runs]
    assert counts in ([1,2],[1,2,7])
    output.mkdir(parents=True,exist_ok=False)
    summary=dict(status='PASS',scope='one run per configuration, measurement correctness and completeness; no variance estimate',
                 report_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                 effective_common_config=baseline,physical_source_sha256=source,immutable_seed_sha256=seed,runs=runs,
                 raw_hashes_unchanged=True,raw_file_count=sum(r['raw_file_count'] for r in runs),
                 completed_instance_counts=counts,comparison_coverage='PASS' if counts==[1,2,7] else 'PARTIAL',
                 limitations=['Common tool prefix, digest, per-VM quota and warm seed match; VM count and aggregate resources differ.',
                              'Preparation and barrier waiting differ with VM count; DAMON and residency may evolve before the joint pause.',
                              'Original paths diverge after step 11; full-run timings compare different workloads and include analysis.',
                              'Shared-layer duplicate subtraction is a counting correction, not a measured saving against an unshared backend.',
                              'Provider anonymous buffers, non-LRU guest contents and total system memory are outside the physical sets.'])
    summary['limitations'].append('The first single-root source prepared then paused; the corrected multi-root source prepares and immediately pauses each VM. For one VM the operation order is unchanged; source versions are recorded per run.')
    if any(any(r['calibration_pinned_bytes_per_sandbox']) for r in runs):
        summary['limitations'].append('The retry holds only three calibration pages (12 KiB per VM) with mlock until measurement release; prior successful points used unpinned calibration. These pages remain included and task-cache policy is unchanged. This is not an identical monitoring-cost configuration.')
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (output/'input-sha256.json').write_text(json.dumps(inputs,indent=2)+'\n')
    (output/'report-source.py').write_bytes(Path(__file__).read_bytes())
    import matplotlib
    matplotlib.use('Agg');matplotlib.rcParams['svg.hashsalt']='shared-task-cache-v1'
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,4),constrained_layout=True)
    for ax,key,title in zip(axes,['guest_cache','backing_cache'],['Guest file-LRU physical cache','Host layer and private-drive physical cache'],strict=True):
        x=list(range(len(runs)));measurements=[r['physical_measurement'][key] for r in runs]
        ax.bar([i-.18 for i in x],[m['summed_bytes']/2**20 for m in measurements],width=.36,label='Sum of per-VM sets')
        ax.bar([i+.18 for i in x],[m['unique_bytes']/2**20 for m in measurements],width=.36,label='Unique host PFNs')
        ax.set_xticks(x,[f'{r["root_count"]} VM(s)' for r in runs]);ax.set(ylabel='MiB',title=title);ax.legend();ax.grid(axis='y',alpha=.2)
    fig.savefig(output/'comparison.svg',metadata={'Date':None});plt.close(fig)
    lines=['# Prettier #6604：相同第11步边界的物理缓存比较','',
           '每个配置实际运行一次，完整保留原动作、退出码、patch及模型测试结果；这里比较计数，不估计性能方差。','',
           '| 实例数 | 完整动作数 | guest文件缓存去重（MiB） | host镜像及私有盘缓存去重（MiB） | host按实例相加的重复量（MiB） | 分析时间（秒） |',
           '| --- | --- | ---: | ---: | ---: | ---: |']
    for r in runs:
        p=r['physical_measurement']
        lines.append(f"| {r['root_count']} | {r['actions']} | {p['guest_cache']['unique_bytes']/2**20:.2f} | {p['backing_cache']['unique_bytes']/2**20:.2f} | {p['backing_cache']['duplicate_bytes_if_summed']/2**20:.2f} | {p['analysis_elapsed_ns']/1e9:.3f} |")
    lines+=['','镜像层由温热的RAM准备缓存复制到本次ext4临时卷；测量的是这些卷内层文件的驻留，包含层格式和元数据，不包含另外的RAM准备缓存。它不是每个任务的独占内存。重复量说明直接相加会多计多少，不代表相对另一后端的节省。guest/host逻辑内存、VMM PSS及以上物理量不相加。','',
            '所有实例在前11步完成后等待测量，之后的原分支负载不同；DAMON、准备等待和分析暂停会影响驻留及总用时，不将全程时延差异归因于page cache。','']
    if counts==[1,2]:lines+=['七实例本轮失败，不纳入成功数据表；1/2/7配置的完整比较仍为部分完成。','']
    if any(any(r['calibration_pinned_bytes_per_sandbox']) for r in runs):
        lines+=['新复验用mlock固定每VM三个校准文件页（12 KiB），测量完成后释放；旧成功配置未固定。这部分仍计入物理量，任务文件和缓存策略不变；三次不是完全相同的采集开销配置。','']
    (output/'report.md').write_text('\n'.join(lines))
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workflows',nargs='+',type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();result=report(args.workflows,args.output)
    print('status='+result['status']+' result_dir='+str(args.output))
