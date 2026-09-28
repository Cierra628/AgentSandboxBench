"""Read-only preflight; never launches a VM or changes permissions/services."""
import argparse
import ctypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def preflight():
    checks = {}
    first = None

    def check(name, callback):
        nonlocal first
        try:
            checks[name] = dict(status='PASS', value=callback())
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            checks[name] = dict(status='FAIL', error=str(exc))
            if first is None:
                first = name

    def visible_pfn():
        with mmap.mmap(-1, 4096) as marker:
            marker[0] = 1
            address = ctypes.addressof(ctypes.c_char.from_buffer(marker))
            with open('/proc/self/pagemap', 'rb', buffering=0) as stream:
                raw = os.pread(stream.fileno(), 8, address // os.sysconf('SC_PAGE_SIZE') * 8)
            if len(raw) != 8:
                raise RuntimeError('short pagemap')
            entry = struct.unpack('<Q', raw)[0]
            if not entry & (1 << 63) or not entry & ((1 << 55) - 1):
                raise RuntimeError('resident self page PFN hidden; CAP_SYS_ADMIN unavailable')
            return 'resident PFN exposed; address/PFN intentionally omitted'

    def open_device():
        fd = os.open('/dev/kvm', os.O_RDWR)
        os.close(fd)
        return 'open succeeded; no ioctl or VM creation'

    def sudo():
        result = subprocess.run(['sudo', '-n', 'true'], capture_output=True, text=True, timeout=5)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        return 'noninteractive sudo available'

    def revision(directory, expected):
        result = subprocess.run(['git', '-c', f'safe.directory={directory}', '-C', str(directory),
                                 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=10, check=True)
        head = result.stdout.strip()
        if head != expected:
            raise RuntimeError(f'fixed revision mismatch: {head}')
        return head

    check('host_pfn_visibility', visible_pfn)
    check('kvm_open', open_device)
    check('noninteractive_sudo', sudo)
    for name in ('kpageflags', 'kpagecount'):
        def readable(name=name):
            with open('/proc/' + name, 'rb', buffering=0) as stream:
                if len(stream.read(8)) != 8:
                    raise RuntimeError('short kernel page read')
            return 'readable'
        check(name, readable)
    pins = json.loads((ROOT / 'configs/source-revisions.json').read_text())
    check('IncrementalDAX_revision', lambda: revision(ROOT / 'IncrementalDAX_moti', pins['IncrementalDAX_moti']['commit']))
    check('CH_revision', lambda: revision(Path('/var/lib/trenvx/deps/cloud-hypervisor'),
                                        '9e0056eb750096f3bf8ed491c345ef9241fd527e'))
    dependencies = {}
    for name, path in dict(cloud_hypervisor='/var/lib/trenvx/deps/cloud-hypervisor/target/release/cloud-hypervisor',
        guest_kernel='/var/lib/trenvx/kernels/ch-6.1.134/vmlinux',
        seed='/var/lib/trenvx/templates/prettier-14400-ch-dax/image/rootfs.ext4',
        envd=str(ROOT / 'runtime/bin/envd-task-cgroup'),
        orchestrator=str(ROOT / 'runtime/trenvx-service-bin/orchestrator'),
        template_manager=str(ROOT / 'runtime/trenvx-service-bin/template-manager')).items():
        dependencies[name] = dict(present=Path(path).is_file(), readable=os.access(path, os.R_OK))
    free = shutil.disk_usage(ROOT).free
    return dict(status='READY' if first is None and all(x['readable'] for x in dependencies.values())
                and free > 36 * 1024**3 else 'BLOCKED',
                timestamp_ns=time.time_ns(), uid=os.getuid(), host_kernel=os.uname().release,
                host_page_size=os.sysconf('SC_PAGE_SIZE'), free_bytes=free,
                disk_stop_bytes=20 * 1024**3, full_exp1_start_bytes=36 * 1024**3,
                first_failed_check=first, checks=checks, dependencies=dependencies,
                guest_layout={'status': 'NOT_MEASURED', 'reason': 'no VM launched'},
                source_revisions=pins, scan_atomic=False,
                scope='current account on this host; no shared state mutation, cache drop or VM launch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    begin = time.monotonic_ns()
    result = preflight()
    result['scan_elapsed_ns'] = time.monotonic_ns() - begin
    result['script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(f"status={result['status']} result_dir={args.output}")
    return 0 if result['status'] == 'READY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
