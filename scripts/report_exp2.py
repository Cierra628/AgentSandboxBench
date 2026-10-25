"""Recompute Exp2 correctness, transition timing and default runtime curves."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from run_exp2 import check_steps, rows, verify_boundary
from report_sandbox_runtime import write_report


def report(raw, output):
    raw=Path(raw);output=Path(output)
    assert not output.exists() and not output.resolve().is_relative_to(raw.resolve())
    result=json.loads((raw/'result.json').read_text());assert result['status']=='PASS'
    cfg=json.loads((raw/'effective-input.json').read_text());workload=raw/'frozen-input'
    for name,digest in cfg['file_sha256'].items():assert hashlib.sha256((workload/name).read_bytes()).hexdigest()==digest
    timing=[];memory=[];focused=[]
    for n in range(1,5):
        duration=check_steps(raw/'rounds'/str(n),n,cfg,workload)
        proof=json.loads((raw/'boundaries'/f'{n}.json').read_text())
        assert proof['status']=='PASS' and proof['after_round']==n and proof['next_round']==(n+1 if n<4 else None)
        verify_boundary(proof['before'],proof['after'],proof['probe_before'],proof['probe_after'])
        assert proof['after']['progress']==str(n)
        baseline=rows(raw/'rounds'/str(n)/'guest-memory.tsv')[0]
        assert baseline['phase']=='baseline'
        timing.append(dict(round=n,actions=8,guest_action_sum_ns=duration))
        memory.append(dict(round=n,runner_baseline_nonfree_bytes=(int(baseline['MemTotal_kib'])-int(baseline['MemFree_kib']))*1024,
            runner_baseline_file_cache_estimate_bytes=max(0,int(baseline['Cached_kib'])+int(baseline['Buffers_kib'])-int(baseline['Shmem_kib']))*1024,
            before_drop_cached_bytes=proof['memory_before_drop_kib']['Cached']*1024,
            at_snapshot_cached_bytes=proof['memory_at_snapshot_kib']['Cached']*1024,
            immediate_restored_cached_bytes=proof['memory_immediate_restored_kib']['Cached']*1024))
    for n,s in [(3,'008'),(4,'006')]:
        directory=raw/'rounds'/str(n)
        log=(directory/f'stdout/{s}.log').read_text()+(directory/f'stderr/{s}.log').read_text()
        clean=re.sub(r'\x1b\[[0-9;]*m','',log)
        match=re.search(r'Tests:\s+(\d+) passed,\s+(\d+) total',clean)
        assert match and match[1]==match[2], 'focused Jest outcome missing or failed'
        focused.append(dict(round=n,step=s,passed=int(match[1]),total=int(match[2])))
    final=json.loads((raw/'final-state.json').read_text())
    assert final==json.loads((raw/'boundaries/4.json').read_text())['after']
    assert final['files']['/testbed/src/language-js/printer-estree.js']==result['expected_final_source_sha256']
    assert cfg['oracle'] in (raw/'rounds/4/stdout/004.log').read_text()
    transitions=json.loads((raw/'transitions.json').read_text())
    assert [v['after_round'] for v in transitions]==[1,2,3,4] and transitions==result['transitions']
    assert result['action_count']==32 and result['checkpoint_count']==result['restore_count']==4
    assert all(result['cleanup'].values())
    output.mkdir(parents=True)
    summary=dict(status='PASS',experiment='exp2',task_id=cfg['task_id'],backend=result.get('backend','agentenv'),cache_mode=result['cache_mode'],action_count=32,
        checkpoint_count=4,restore_count=4,rounds=timing,boundary_memory=memory,transitions=transitions,focused_tests=focused,
        guest_action_sum_ns=sum(v['guest_action_sum_ns'] for v in timing),controller_elapsed_ns=result['controller_elapsed_ns'],
        final_patch_sha256=hashlib.sha256(final['patch'].encode()).hexdigest(),final_source_sha256=result['expected_final_source_sha256'],
        official_task_status='NOT_RUN',restore_checks='PASS',cleanup=result['cleanup'],
        notes=['Guest-only drop_caches=3 affects file cache and reclaimable slab; host caches untouched.',
            'Original 20 ms runner and added continuous sampler/state probes are present in both modes; not a reproduction of original absolute timing.',
            'Cached and Cached+Buffers-Shmem are logical counters; no physical PFN scan in this experiment.',
            'One run per configuration; no statistical performance or zero-monitoring-overhead claim.'])
    assert summary['guest_action_sum_ns']==result['guest_action_sum_ns']
    summary['runtime_metrics']=write_report(raw,output)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    lines=['# Exp2：四轮保存与恢复','',f"{summary['backend']} / {result['cache_mode']}：32步、四次保存与恢复、文件/进度/进程核对通过。",'',
        '| 轮次 | 动作 | guest工具用时（秒） | 开始时非空闲内存（MiB） | 开始时逻辑文件缓存估计（MiB） |',
        '| --- | ---: | ---: | ---: | ---: |']
    for t,m in zip(timing,memory):lines.append(f"| {t['round']} | 8 | {t['guest_action_sum_ns']/1e9:.3f} | {m['runner_baseline_nonfree_bytes']/2**20:.2f} | {m['runner_baseline_file_cache_estimate_bytes']/2**20:.2f} |")
    lines+=['','| 轮后 | checkpoint（秒） | restore至工具可用（秒） | 模板提升（秒） | 状态核对（秒） | guest清缓存（秒） |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for t in transitions:lines.append(f"| {t['after_round']} | {t['checkpoint_ns']/1e9:.3f} | {t['restore_ns']/1e9:.3f} | {t['image_rebuild_ns']/1e9:.3f} | {t['state_verification_ns']/1e9:.3f} | {t['guest_cache_clear_ns']/1e9:.3f} |")
    lines+=['',f"工具之和 {summary['guest_action_sum_ns']/1e9:.3f} 秒；控制器生命周期 {summary['controller_elapsed_ns']/1e9:.3f} 秒。原聚焦Jest测试通过；未执行独立任务测试。",'',
        '内存为逻辑计数，非空闲内存=MemTotal−MemFree；文件缓存估计=Cached+Buffers−Shmem。持续曲线及host进程指标另列，不能相加。',
        '仅drop组在guest执行sync及drop_caches=3；它也影响可回收slab。本次每配置一次，只验证运行和数据完整性，不作性能归因。','']
    (output/'summary.md').write_text('\n'.join(lines))
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('raw',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();report(args.raw,args.output);print('status=PASS result_dir='+str(args.output))
