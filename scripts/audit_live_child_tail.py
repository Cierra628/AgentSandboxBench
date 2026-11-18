"""Read saved collector boundaries without appending points or changing verdicts."""
import argparse
import json
from pathlib import Path
from report_experiment_schedule import periodic_closure


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('batch',type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();out=a.output.resolve();assert not out.exists();out.mkdir(parents=True)
    read=lambda p:json.loads(p.read_text())
    batch=a.batch.resolve();outer=read(batch/'result.json');raw=Path(outer['raw'])
    series=[json.loads(l) for l in (batch/'full-memory/memory.jsonl').read_text().splitlines()]
    events=read(raw/'events.json');page=[json.loads(l) for l in (raw/'live-cache/samples.jsonl').read_text().splitlines()]
    periodic=periodic_closure(series);last=series[-1];after=outer['after_cleanup']
    rows=[]
    for i,r in [(i,r) for i,r in enumerate(series) if i>=len(series)-4]+[('after_cleanup',after)]:
        em=r['experiment_memory'];rows.append(dict(index=i,monotonic_ns=r['monotonic_ns'],
            seconds_from_last_periodic=(r['monotonic_ns']-last['monotonic_ns'])/1e9,read_duration_ns=r['duration_ns'],
            parent_file_bytes=em['parent_counters']['file'],children_file_bytes=em['direct_children_sum']['file'],
            difference_bytes=em['file_closure']['parent_minus_children_bytes'],
            children_file={str(Path(k).name):v['file'] for k,v in em['direct_children'].items()}))
    value=dict(policy=periodic,tail=rows,source=str(batch),raw=str(raw),
        last_page_observation_end_ns=page[-1]['host_sample_start_ns']+page[-1]['host_elapsed_ns'],
        delete=next(e for e in events if e['stage']=='delete'),
        after_cleanup_gap_ns=after['monotonic_ns']-last['monotonic_ns'],
        observations_only=True,original_fail_unchanged=True)
    (out/'summary.json').write_text(json.dumps(value,indent=2)+'\n')
    lines=['# 单沙箱收尾时间线：离线核对','',
        '结论：原完整周期FAIL保留。周期序列结束在一个偏离点，之后另存的after_cleanup查询已经闭合；后者不是周期点，不能追加到历史序列改判。页级采集早已停止，不受这个收尾偏离点影响。','',
        '| 点 | 距最后周期点秒数 | 读数耗时毫秒 | 父file MiB | 直接子组合计file MiB | 差MiB |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        lines.append(f"| {r['index']} | {r['seconds_from_last_periodic']:.9f} | {r['read_duration_ns']/1e6:.6f} | {r['parent_file_bytes']/2**20:.6f} | {r['children_file_bytes']/2**20:.6f} | {r['difference_bytes']/2**20:.6f} |")
    lines+=['','## 确认与推测','',
        f"- 最后一份页级观察在末周期点前{(last['monotonic_ns']-value['last_page_observation_end_ns'])/1e9:.6f}秒已结束。guest删除调用也在末周期点前结束，起止时间见summary.json。",
        '- 末点只剩控制程序与空VMM子组；Docker子组早已消失。父file与上一点完全相同，控制子组file已从2228.468750降至195.625000 MiB；稍后的独立读数父子同为59.140625 MiB。原始读取顺序是父在前、子在后。',
        '- 这与大量输入缓存回收时，顺序读取及父子统计刷新不同步的解释一致。原始记录没有每个字段的单独时间、逐CPU统计或逐页owner，不能确认唯一底层原因，也不能据此称有2032.843750 MiB永久漏记账。',
        '- `GroupSampler.stop()`立即置停止标志；旧入口在after_cleanup查询后调用stop，没有固定的周期收尾观察时间。两份读数虽接近，只有前者进入周期JSONL，因此规则缺后续周期恢复点。','',
        '## 后续最小改动','',
        '仅为新的显式双沙箱校准增加固定1.2秒收尾采样：从资源清理完成边界开始，正常保留0.1秒周期，再停止。该长度覆盖原1秒恢复上限及两个标称周期，不等待“直到通过”，不挑点，不改变segments_v3。届时即便最后仍偏离，也照常FAIL，不重试。单沙箱旧入口和所有历史判定不变。', '',
        f'数据：`{batch}`。本次只读已有文件，无业务运行、无重新采集。','']
    (out/'REPORT.md').write_text('\n'.join(lines))
    print('status=COMPLETE historical_closure='+periodic['status']+' result_dir='+str(out))


if __name__=='__main__':main()
