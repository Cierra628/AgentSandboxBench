"""Short real OverlayFS origin fixture in a private host mount namespace."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

from experiment_disk_guard import guarded_run, writer_lock
from guest_file_origins import observe, overlay_layout

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value): path.write_text(json.dumps(value, indent=2) + '\n')


def inside(out):
    mounts = []; checks = {}; status = 'FAIL'; stage = 'fixture'
    counter = 0
    def run(*args):
        nonlocal counter
        counter += 1
        value = subprocess.run(args, capture_output=True, timeout=10)
        dump(out/f'command-{counter:02d}.json', dict(argv=list(args), exit_code=value.returncode,
            stdout=value.stdout.decode(errors='replace'), stderr=value.stderr.decode(errors='replace')))
        value.check_returncode()
    def mount(target, *args):
        run('mount', *args, str(target)); mounts.append(target)
    def unmount(target):
        run('umount', str(target)); mounts.remove(target)
    def readonly(target):
        mount(target, '--bind', str(target)); run('mount', '-o', 'remount,bind,ro', str(target))
    def overlay(merged, lowers, upper):
        merged.mkdir(); (upper/'root').mkdir(parents=True); (upper/'work').mkdir()
        mount(merged, '-t', 'overlay', 'overlay', '-o',
            'lowerdir='+':'.join(map(str,lowers))+f',upperdir={upper}/root,workdir={upper}/work')
    try:
        assert os.readlink('/proc/self/ns/mnt') != (out/'parent-mount-ns.txt').read_text().strip()
        base = out/'base'; (base/'testbed/opaque').mkdir(parents=True)
        for name in ('base','change','old','same','gone','opaque/old'):
            (base/'testbed'/name).write_text('original '+name+'\n')
        readonly(base)
        first = out/'merged-first'; old = out/'old'
        overlay(first, [base], old)
        (first/'testbed/change').write_text('old generation\n')
        (first/'testbed/old').write_text('old generation only\n')
        (first/'testbed/gone').unlink()
        shutil.rmtree(first/'testbed/opaque'); (first/'testbed/opaque').mkdir()
        (first/'testbed/opaque/child').write_text('replacement child\n')
        unmount(first); readonly(old/'root')
        second = out/'merged-second'; new = out/'new'
        overlay(second, [old/'root',base], new)
        (second/'testbed/change').write_text('new generation\n')
        unmount(second); readonly(new/'root')
        merged = out/'merged-final'; upper = out/'upper'
        overlay(merged, [new/'root',old/'root',base], upper)
        (merged/'testbed/same').write_text('original same\n')
        (merged/'testbed/private').write_text('private writable content\n')
        cfg = dict(files=[
            dict(path='/testbed/'+name, expected_origin=origin, expected_status=state)
            for name,origin,state in [
                ('base','lower:2','SOURCE_VERIFIED'),('change','lower:0','SOURCE_VERIFIED'),
                ('old','lower:1','SOURCE_VERIFIED'),('same','upper','SOURCE_VERIFIED'),
                ('private','upper','SOURCE_VERIFIED'),('gone','lower:1','DELETED'),
                ('opaque/old','lower:1','DELETED'),('opaque/child','lower:1','SOURCE_VERIFIED'),
                ('missing',None,'NOT_FOUND')]])
        dump(out/'manifest.json',cfg)
        info = Path('/proc/self/mountinfo').read_text(); (out/'mountinfo.txt').write_text(info)
        layout = overlay_layout(info, str(merged))
        metadata = observe(cfg, layout, merged); dump(out/'metadata-result.json',metadata)
        assert metadata['status']=='PASS'
        checks['metadata_sources']='PASS'
        stage='content'
        content = observe(cfg, layout, merged, True); dump(out/'content-result.json',content)
        assert content['status']=='PASS'
        assert [r['status'] for r in content['files']] == [r['expected_status'] for r in cfg['files']]
        assert all(r['content_verified'] for r in content['files'] if r['status']=='SOURCE_VERIFIED')
        assert (base/'testbed/same').read_bytes() == (upper/'root/testbed/same').read_bytes()
        assert (base/'testbed/same').stat().st_ino != (upper/'root/testbed/same').stat().st_ino
        checks['content_and_equal_content_source']='PASS'
        checks['deletion_and_opaque']='PASS'
        checks['ordered_three_lowers_and_upper']='PASS'
        status='PASS'
    except BaseException:
        checks[stage]='FAIL'; (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        failures = []
        for target in reversed(mounts[:]):
            try: unmount(target)
            except BaseException as error: failures.append(str(error))
        remaining = [line for line in Path('/proc/self/mountinfo').read_text().splitlines() if str(out) in line]
        dump(out/'cleanup.json',dict(errors=failures,remaining_mounts=remaining))
        checks['cleanup']='PASS' if not failures and not remaining else 'FAIL'
        if checks['cleanup']!='PASS': status='FAIL'
        dump(out/'result.json',dict(status=status,checks=checks,
            scope='real host OverlayFS directories; no VM, DAX, checkpoint sealer or physical memory measurement'))
        print(f'status={status} result_dir={out}',flush=True)
    return 0 if status=='PASS' else 1


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inside',type=Path)
    args=parser.parse_args()
    if args.inside: return inside(args.inside.resolve(strict=True))
    assert os.geteuid()==0
    out=ROOT/'.artifacts/file-origin-fixture'/f'{time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())}-{os.getpid()}'
    out.mkdir(parents=True)
    sources={name:(ROOT/'scripts'/name).read_bytes() for name in
        ('check_file_origins.py','guest_file_origins.py','experiment_disk_guard.py')}
    for name,data in sources.items(): (out/name).write_bytes(data)
    dump(out/'config.json',dict(host_kernel=os.uname().release,
        sources={name:hashlib.sha256(data).hexdigest() for name,data in sources.items()},
        vm_count=0, global_cache_drop=False, physical_memory='NOT_MEASURED'))
    (out/'parent-mount-ns.txt').write_text(os.readlink('/proc/self/ns/mnt')+'\n')
    try:
        with writer_lock(), (out/'run.log').open('w') as log:
            if shutil.disk_usage(out).free < 22 * 2**30: raise RuntimeError('fixture requires 22 GiB free')
            guarded_run(['timeout','--signal=TERM','--kill-after=5s','90','unshare','--mount','--propagation','private',
                sys.executable,str(out/'check_file_origins.py'),'--inside',str(out)],directory=out,log=log,timeout=100)
        assert all((ROOT/'scripts'/name).read_bytes()==data for name,data in sources.items())
        assert not any(str(out) in line for line in Path('/proc/self/mountinfo').read_text().splitlines())
        value=json.loads((out/'result.json').read_text());assert value['status']=='PASS'
    except BaseException as error:
        dump(out/'outer-error.json',dict(status='FAIL',error=repr(error)))
        print(f'status=FAIL result_dir={out}');return 1
    print(f'status=PASS result_dir={out}');return 0


if __name__=='__main__': raise SystemExit(main())
