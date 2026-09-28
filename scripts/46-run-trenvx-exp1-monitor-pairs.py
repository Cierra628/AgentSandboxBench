"""Alternating Exp1 off/light pairs with per-arm correctness and storage checks."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import statistics
import subprocess
import time
import traceback
from plan_trenvx_storage import budget


ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT/'.artifacts/trenvx-service-smoke'
REFERENCE = RUNS/'20260927T110632Z-2094910/replay'
ENTRY = ROOT/'scripts/45-replay-trenvx-exp1.sh'


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def pressure():
    return dict(loadavg=os.getloadavg(), pressure={name:
        (Path('/proc/pressure')/name).read_text().strip() for name in ('cpu', 'io', 'memory')})


def verify(run, mode):
    result = json.loads((run/'result.json').read_text())
    assert result['status']=='PASS' and result['full_trajectory_verified'] is True
    assert result['host_cleanup']['cgroup_removed'] is True
    assert result['host_cleanup']['owned_loop_backings']==[]
    assert result['data_retention']['status']=='PASS'
    assert result['data_retention']['source_removed'] is True
    assert not (run/'data').exists()
    manifest = json.loads((run/'archive-manifest.json').read_text())
    assert (run/'data.tar.gz').stat().st_size==manifest['archive_bytes']
    assert manifest['gzip_test']=='PASS' and manifest['tar_compare']=='PASS'
    assert manifest['archive_sha256']==result['data_retention']['archive_sha256']
    assert (run/'replay/actions.tsv').read_bytes()==(REFERENCE/'actions.tsv').read_bytes()
    assert (run/'replay/original-head.txt').read_bytes()==(REFERENCE/'original-head.txt').read_bytes()
    assert (run/'replay/patch.diff').read_bytes()==(REFERENCE/'patch.diff').read_bytes()
    with (run/'replay/steps.tsv').open(newline='') as source:
        steps=list(csv.DictReader(source,delimiter='\t'))
    assert [row['step'] for row in steps]==[f'{i:03d}' for i in range(1,27)]
    assert {row['step']:int(row['exit_code']) for row in steps if int(row['exit_code'])}=={'008':127,'016':127}
    with (run/'replay/guest-memory.tsv').open() as source:
        assert len(source.readlines())==1
    host = run/'host/memory-samples.tsv'
    if mode=='light':
        with host.open(newline='') as source:
            samples=list(csv.DictReader(source,delimiter='\t'))
        assert len(samples)>=2
    else:
        assert not host.exists()
        samples=[]
    timing=json.loads((run/'replay-all.json').read_text())['controller_elapsed_ns']
    assert timing>0
    guards=[json.loads(line) for line in (run/'disk-guard.jsonl').read_text().splitlines()]
    assert len(guards)==4 and all(row['status']=='PASS' for row in guards)
    config=json.loads((run/'config.json').read_text())
    assert config['archive_compressor']=='pigz-fast'
    assert config['host_sample_interval_seconds']==(1.0 if mode=='light' else 0)
    return dict(run_id=run.name, mode=mode, status='PASS',
        observed_min_free_bytes=min(row['observed_min_free_bytes'] for row in guards),
        controller_runner_call_ns=timing,
        guest_action_sum_ns=sum(int(row['duration_ns']) for row in steps),
        host_sample_count=len(samples), archive_bytes=manifest['archive_bytes'],
        archive_compressor=manifest.get('compressor','gzip'),
        archive_elapsed_seconds=manifest['archive_elapsed_seconds'],
        input_sha256=digest(run/'replay/actions.tsv'),patch_sha256=digest(run/'replay/patch.diff'))


def main(pair_count=2):
    if pair_count not in (2,5):
        raise ValueError('supported sizes: two-pair smoke or five-pair series')
    os.umask(0o077)
    ident=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}'
    out=ROOT/'.artifacts'/('trenvx-monitor-pair-smoke' if pair_count==2 else 'trenvx-monitor-pairs')/ident
    out.mkdir(parents=True)
    shutil.copy2(Path(__file__),out/'driver.py')
    runs=[]
    orders=[('off','light') if index%2==0 else ('light','off') for index in range(pair_count)]
    protocol=dict(pair_count=pair_count,orders=orders,host_sample_interval_seconds=1,
                  guest_sampling=False,archive_compressor='pigz-fast',
                  scope='runner tool-call duration; host run-owned sandbox parent cgroup',
                  interpretation='descriptive paired differences; no causal or 5% acceptance claim',
                  cache_policy='no global cache clearing; inherited harness defaults',
                  order_balance='five pairs have three off-first and two light-first pairs',
                  stop_policy='stop on first failure; no automatic retries or added pairs')
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    result=dict(status='FAIL',run_id=ident,scope='TrEnv-X host sampler off/light paired collection',
                pair_count_planned=pair_count,runs=runs,performance_conclusion='NOT_VALIDATED')
    frozen_sources=None
    fixed_config=None
    try:
        assert REFERENCE.is_dir()
        for pair, order in enumerate(orders,1):
            for arm, mode in enumerate(order,1):
                free=shutil.disk_usage(out).free
                plan=budget(free, 2*pair_count-len(runs))
                (out/f'storage-plan-{pair}-{arm}.json').write_text(json.dumps(plan,indent=2)+'\n')
                if plan['status'] != 'READY':
                    raise RuntimeError(f'insufficient space for remaining batch: {plan}')
                sources={name:digest(ROOT/'scripts'/name) for name in (
                    '45-replay-trenvx-exp1.sh','smoke_trenvx_service.py','replay_trenvx_exp1.py',
                    'archive_trenvx_run_data.py','experiment_disk_guard.py','plan_trenvx_storage.py')}
                if frozen_sources is None:
                    frozen_sources=sources
                    (out/'source-hashes.json').write_text(json.dumps(sources,indent=2)+'\n')
                assert sources==frozen_sources, 'harness changed during batch'
                label=f'pair-{pair}-arm-{arm}-{mode}'
                cmd=['bash',str(ENTRY),'--archive-compressor','pigz-fast']
                if mode=='light': cmd+=['--host-sample-interval','1']
                before=pressure()
                start=time.monotonic_ns()
                with (out/(label+'.log')).open('w') as log:
                    process=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                                             start_new_session=True)
                    try:
                        exit_code=process.wait(timeout=1800)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid,signal.SIGTERM)
                        try:
                            process.wait(timeout=20)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid,signal.SIGKILL)
                            process.wait(timeout=10)
                        raise
                elapsed=time.monotonic_ns()-start
                lines=(out/(label+'.log')).read_text()
                match=re.search(r'result_dir=(\S+)',lines)
                assert match, f'{label}: no result_dir; see log'
                run=Path(match.group(1)).resolve(strict=True)
                assert run.parent==RUNS.resolve(strict=True)
                assert exit_code==0, f'{label}: command exit {exit_code}; run={run}'
                verified=verify(run,mode)
                config=json.loads((run/'config.json').read_text())
                identity={key:config[key] for key in ('seed_sha256','ch_sha256','kernel_sha256',
                    'envd_sha256','binaries','patches','source_revisions','cpu','memory_mib',
                    'host_kernel','disk_reserve_bytes','disk_poll_seconds')}
                if fixed_config is None:
                    fixed_config=identity
                    (out/'fixed-environment.json').write_text(json.dumps(identity,indent=2)+'\n')
                assert identity==fixed_config, 'effective environment changed during batch'
                verified.update(pair=pair,arm=arm,free_before_bytes=free,
                                free_after_bytes=shutil.disk_usage(out).free,
                                process_including_archive_ns=elapsed,
                                host_before=before,host_after=pressure())
                runs.append(verified)
                (out/'progress.json').write_text(json.dumps(runs,indent=2)+'\n')
        pairs=[]
        for pair in range(1,pair_count+1):
            arms={row['mode']:row for row in runs if row['pair']==pair}
            pairs.append(dict(pair=pair,order=[row['mode'] for row in runs if row['pair']==pair],
                controller_runner_call_light_vs_off_percent=100*(
                    arms['light']['controller_runner_call_ns']/arms['off']['controller_runner_call_ns']-1)))
        changes=[item['controller_runner_call_light_vs_off_percent'] for item in pairs]
        result.update(status='PASS',pairs=pairs,descriptive_percent=dict(
            mean=statistics.mean(changes),median=statistics.median(changes),
            minimum=min(changes),maximum=max(changes)))
        lines=['# TrEnv-X Exp1 host 采样配对记录','',
            f'{pair_count} 对依次交替采用 off→light、light→off 顺序，均执行冻结的 26 步轨迹。'
            'light 为 1 秒一次的本次沙箱父 cgroup 采样；两次运行都关闭虚拟机内部内存采样。',
            '完整轨迹、预期失败、patch、清理和镜像归档逐次核对。下表的控制器调用只覆盖 runner 工具调用；'
            '运行加归档耗时另记于 result.json。报告逐对差异及波动，不据此宣称达到 5% 开销目标。','',
            '| 组 / 次序 | 模式 | runner 调用 (ms) | guest 动作和 (ms) | host 样本 | 归档方式 | 归档 (GiB) |',
            '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
        for row in runs:
            lines.append(f"| {row['pair']} / {row['arm']} | {row['mode']} | "
                f"{row['controller_runner_call_ns']/1e6:.1f} | "
                f"{row['guest_action_sum_ns']/1e6:.1f} | {row['host_sample_count']} | "
                f"{row['archive_compressor']} | "
                f"{row['archive_bytes']/1024**3:.2f} |")
        lines += ['',*(f"第 {item['pair']} 对 light/off 调用时长变化 "
            f"{item['controller_runner_call_light_vs_off_percent']:+.1f}%；不作因果推断。"
            for item in pairs),'']
        (out/'report.md').write_text('\n'.join(lines))
    except BaseException as exc:
        result['error']=repr(exc)
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(f"status={result['status']} result_dir={out}")
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pairs',type=int,choices=(2,5),default=2)
    raise SystemExit(main(parser.parse_args().pairs))
