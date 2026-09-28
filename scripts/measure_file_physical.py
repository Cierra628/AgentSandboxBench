"""Read-only, scoped file-content PFN collection; raw addresses stay local.

DAX input is file data offsets, never image capacity or mapping PSS. Ordinary
guest cache input can reuse the pinned file-LRU probe, but remains unattributed
until a file owner is independently established. See FILE_PHYSICAL_MEMORY.md.
"""
import argparse
import array
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import time

PAGE = 4096
PFN_MASK = (1 << 55) - 1


def parse_maps(raw):
    regions = []
    for line in raw.splitlines():
        fields = line.split(maxsplit=5)
        start, end = (int(x, 16) for x in fields[0].split('-'))
        major, minor = (int(x, 16) for x in fields[3].split(':'))
        regions.append(dict(start=start, end=end, offset=int(fields[2], 16),
                            device=os.makedev(major, minor), inode=int(fields[4]),
                            permissions=fields[1]))
    return regions


def file_address(offset, regions, stat):
    matches = [r['start'] + offset - r['offset'] for r in regions
               if r['device'] == stat.st_dev and r['inode'] == stat.st_ino
               and r['offset'] <= offset < r['offset'] + r['end'] - r['start']]
    if len(matches) != 1:
        raise RuntimeError(f'file data offset {offset} has {len(matches)} mappings')
    return matches[0]


def ram_address(pfn, slots):
    address = pfn * PAGE
    matches = [s['host'] + address - s['guest'] for s in slots
               if s['guest'] <= address < s['guest'] + s['size']]
    if len(matches) != 1:
        raise RuntimeError(f'guest PFN {pfn} has {len(matches)} RAM slots')
    return matches[0]


def validate_slots(slots):
    if not slots:
        raise RuntimeError('missing actual KVM RAM layout')
    for slot in slots:
        if slot['guest'] < 0 or slot['host'] < 0 or slot['size'] <= 0 or any(slot[k] % PAGE for k in ('guest', 'host', 'size')):
            raise RuntimeError('unaligned/empty RAM slot')
    ordered = sorted(slots, key=lambda s: s['guest'])
    for left, right in zip(ordered, ordered[1:]):
        if left['guest'] + left['size'] > right['guest']:
            raise RuntimeError('overlapping RAM slots')


def captured_slots(path, pid, ram_slot_ids):
    """Read the six-column successful-ioctl format of smoke_vm_pfn.py.

    RAM slot IDs must be classified using guest iomem, not a GPA cutoff. Keep
    raw ioctl evidence local; reject failed registration/removal for this PID.
    """
    latest = {}
    for line in Path(path).read_text().splitlines():
        fields = line.split('\t')
        if len(fields) != 6 or not fields[0].isdigit():
            continue
        if int(fields[0]) != pid:
            continue
        slot = int(fields[1])
        guest, size, host = (int(x, 16) for x in fields[2:5])
        if int(fields[5]) != 0 or size <= 0:
            raise RuntimeError('failed KVM slot registration/removal needs investigation')
        latest[slot] = dict(guest=guest, size=size, host=host)
    if not ram_slot_ids or len(set(ram_slot_ids)) != len(ram_slot_ids) or any(s not in latest for s in ram_slot_ids):
        raise RuntimeError('RAM slot IDs missing from successful capture')
    slots = [latest[s] for s in ram_slot_ids]
    validate_slots(slots)
    return slots


def read_entry(fd, address):
    raw = os.pread(fd, 8, address // PAGE * 8)
    if len(raw) != 8:
        raise RuntimeError('short pagemap read')
    entry = struct.unpack('<Q', raw)[0]
    if entry & (1 << 63) and not entry & PFN_MASK:
        raise RuntimeError('resident PFN hidden; CAP_SYS_ADMIN is required')
    return entry


def identity(vm, proc_root, cgroup_root):
    proc = proc_root / str(vm['pid'])
    ticks = int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[19])
    if ticks != vm['start_time_ticks']:
        raise RuntimeError('process start time changed')
    if Path(os.readlink(proc / 'exe')).resolve() != Path(vm['executable']).resolve():
        raise RuntimeError('process executable mismatch')
    group = vm['cgroup']
    if not group.startswith('/') or '..' in Path(group).parts:
        raise RuntimeError('invalid cgroup')
    if (proc / 'cgroup').read_text().splitlines() != ['0::' + group]:
        raise RuntimeError('process cgroup mismatch')
    if str(vm['pid']) not in (cgroup_root / group.lstrip('/') / 'cgroup.procs').read_text().split():
        raise RuntimeError('PID absent from owned cgroup')
    if vm.get('api_socket') and vm['api_socket'].encode() not in (proc / 'cmdline').read_bytes().split(b'\0'):
        raise RuntimeError('sandbox API socket mismatch')
    return proc


def summarize(rows, *, failed=False):
    known, unknown, anonymous = set(), set(), set()
    named = any(row['owner'] is not None for row in rows)
    counts = dict(nonresident_mappings=0, swapped_mappings=0, resident_mappings=0)
    for row in rows:
        entry = row['entry']
        if not entry & (1 << 63):
            counts['swapped_mappings' if entry & (1 << 62) else 'nonresident_mappings'] += 1
            continue
        pfn = entry & PFN_MASK
        if not pfn:
            raise RuntimeError('cannot summarize hidden PFN')
        counts['resident_mappings'] += 1
        if row['kind'] == 'file_lru_unattributed':
            unknown.add(pfn)
        elif row['kind'] == 'anonymous_cow':
            anonymous.add(pfn)
        elif row['kind'] == 'file_content':
            known.add(pfn)
        else:
            raise RuntimeError('unknown page classification')
    return dict(**counts, file_content_unique_pages=None if failed or not named else len(known),
                file_content_physical_bytes=None if failed or not named else len(known) * PAGE,
                observed_file_content_unique_pages=len(known),
                unattributed_unique_pages=len(unknown - known),
                anonymous_cow_unique_pages=len(anonymous),
                deduplication='one host PFN counted once across all declared VMs/files',
                scope='declared file data only; not all guest files or whole-machine memory')


def collect(manifest, *, proc_root=Path('/proc'), cgroup_root=Path('/sys/fs/cgroup')):
    started = time.monotonic_ns()
    rows, observations, cgroups = [], [], {}
    failure = None
    location = 'page-size'
    try:
        if os.sysconf('SC_PAGE_SIZE') != PAGE or manifest['page_size'] != PAGE:
            raise RuntimeError('only verified 4096-byte host pages supported')
        if not manifest['vms'] or len({vm['id'] for vm in manifest['vms']}) != len(manifest['vms']):
            raise RuntimeError('missing/duplicate VM identities')
        for vm in manifest['vms']:
            location = f"{vm['id']}:ownership"
            proc = identity(vm, proc_root, cgroup_root)
            maps = (proc / 'maps').read_text()
            regions = parse_maps(maps)
            requests, files = [], []
            observation = dict(id=vm['id'], pid=vm['pid'], cgroup=vm['cgroup'],
                               process_kind=vm.get('process_kind', 'vmm'),
                               start_time_ticks=vm['start_time_ticks'], memory_layout=vm.get('ram_slots'),
                               files=[], guest_memory=vm.get('guest_memory', {'status': 'NOT_MEASURED'}))
            observations.append(observation)
            # These are independent measurements, with independent failure states.
            try:
                pss = re.search(r'^Pss:\s+(\d+) kB$', (proc / 'smaps_rollup').read_text(), re.M)
                if not pss:
                    raise RuntimeError('missing Pss')
                observation['process_pss'] = dict(status='PASS', bytes=int(pss[1]) * 1024,
                                             scope='this process smaps_rollup')
            except (OSError, RuntimeError) as exc:
                observation['process_pss'] = dict(status='NOT_MEASURED', bytes=None, error=str(exc))
            if vm['cgroup'] not in cgroups:
                try:
                    group = cgroup_root / vm['cgroup'].lstrip('/')
                    current = int((group / 'memory.current').read_text())
                    memory_stat = dict(line.split() for line in (group / 'memory.stat').read_text().splitlines())
                    cgroups[vm['cgroup']] = dict(status='PASS', current_bytes=current,
                        stat_bytes={k: int(memory_stat[k]) for k in ('anon', 'file', 'kernel')},
                        scope='whole declared cgroup; may include other members; not process PSS')
                except (OSError, ValueError, KeyError) as exc:
                    cgroups[vm['cgroup']] = dict(status='NOT_MEASURED', current_bytes=None, error=str(exc))
            for mapped in vm.get('files', []):
                location = f"{vm['id']}:file:{mapped['owner']}"
                path = Path(mapped['backing']).resolve(strict=True)
                if not path.is_relative_to(Path(manifest['owned_root']).resolve()):
                    raise RuntimeError('backing file outside run-owned directory')
                stat = path.stat()
                if 'guest_path' in mapped:
                    if stat.st_mode & 0o222 or not mapped.get('immutable_image'):
                        raise RuntimeError('ext4 data resolution requires a run-owned immutable image')
                    offsets = ext4_data_offsets(path, mapped['guest_path'])
                else:
                    offsets = mapped['offsets']
                observation['files'].append(dict(owner=mapped['owner'], device=stat.st_dev,
                    inode=stat.st_ino, data_mapping_pages=len(offsets), backing=str(path)))
                if not offsets or len(set(offsets)) != len(offsets):
                    raise RuntimeError('missing/duplicate file data offsets')
                for offset in offsets:
                    if offset < 0 or offset >= stat.st_size or offset % PAGE:
                        raise RuntimeError('unaligned/out-of-file data offset')
                    requests.append((file_address(offset, regions, stat), 'file_content', mapped['owner'],
                                     mapped.get('allow_private_cow', False)))
                files.append((path, stat))
            probe = vm.get('file_lru_probe')
            if probe:
                location = f"{vm['id']}:guest-layout"
                if probe['page_size'] != PAGE or probe['byteorder'] != 'little':
                    raise RuntimeError('unsupported guest page/probe encoding')
                if vm['layout_source'] != 'successful-kvm-slot-registration':
                    raise RuntimeError('RAM layout lacks successful KVM registration evidence')
                if vm['slot_capture_start_ticks'] != vm['start_time_ticks']:
                    raise RuntimeError('KVM capture process identity mismatch')
                slots = captured_slots(vm['slot_capture'], vm['pid'], vm['ram_slot_ids'])
                if slots != vm['ram_slots']:
                    raise RuntimeError('declared RAM layout differs from successful KVM capture')
                observation['slot_capture_sha256'] = hashlib.sha256(Path(vm['slot_capture']).read_bytes()).hexdigest()
                # The pinned probe scans only up to 5 GiB. Fail on any uncovered RAM.
                if max(s['guest'] + s['size'] for s in slots) > probe['scan_limit_bytes']:
                    raise RuntimeError('guest file-LRU probe does not cover actual RAM layout')
                marker = json.loads(Path(probe['marker']).read_text())
                if marker['sandbox_id'] != vm['id']:
                    raise RuntimeError('guest marker sandbox mismatch')
                marker_address = ram_address(marker['marker_pfn'], slots)
                with (proc / 'mem').open('rb', buffering=0) as memory:
                    needle = marker['marker_ascii'].encode()
                    if os.pread(memory.fileno(), len(needle), marker_address) != needle:
                        raise RuntimeError('guest marker does not match this VMM/layout')
                pfns = array.array('I')
                pfns.frombytes(Path(probe['pfns']).read_bytes())
                if os.sys.byteorder != 'little':
                    pfns.byteswap()
                if len(pfns) != marker['file_pfn_count'] or len(set(pfns)) != len(pfns):
                    raise RuntimeError('guest file-LRU count/uniqueness mismatch')
                for pfn in pfns:
                    requests.append((ram_address(pfn, slots), 'file_lru_unattributed', None, False))
            if not requests:
                raise RuntimeError('no declared file pages')
            location = f"{vm['id']}:pagemap-open"
            with (proc / 'pagemap').open('rb', buffering=0) as pagemap:
                entries = []
                for index, (address, kind, owner, cow) in enumerate(requests):
                    location = f"{vm['id']}:pagemap:{index}"
                    entry = read_entry(pagemap.fileno(), address)
                    if kind == 'file_content' and entry & (1 << 63) and not entry & (1 << 61):
                        if not cow:
                            raise RuntimeError('named file mapping became anonymous; ownership unverified')
                        kind = 'anonymous_cow'
                    rows.append(dict(vm=vm['id'], owner=owner, kind=kind, address=address, entry=entry))
                    entries.append((address, entry))
                location = f"{vm['id']}:pagemap-recheck"
                for address, entry in entries:
                    if read_entry(pagemap.fileno(), address) != entry:
                        raise RuntimeError('page residency/PFN changed during scan')
            location = f"{vm['id']}:identity-recheck"
            identity(vm, proc_root, cgroup_root)
            if (proc / 'maps').read_text() != maps:
                raise RuntimeError('process mappings changed during scan')
            for path, before in files:
                after = path.stat()
                if any(getattr(before, name) != getattr(after, name) for name in
                       ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
                    raise RuntimeError('backing file changed during scan')
            observation['ownership_verified'] = True
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        failure = dict(location=location, error=str(exc))
    totals = summarize(rows, failed=failure is not None)
    status = ('FAIL' if failure else 'PARTIAL' if totals['unattributed_unique_pages']
              or totals['file_content_physical_bytes'] is None else 'PASS')
    result = dict(status=status, page_size=PAGE, timestamp_ns=time.time_ns(),
                  first_failure=failure, scan_elapsed_ns=time.monotonic_ns() - started,
                  file_content=totals, vms=observations, host_cgroup_memory=cgroups,
                  counter_scope='observed mappings only; a failure leaves unscanned pages unknown',
                  limitations=['non-atomic scan; rechecks cannot rule out all migration/races',
                               'file-LRU pages have no inode attribution and may include filesystem metadata',
                               'anonymous CoW pages reported separately from file content',
                               'guest, cgroup, PSS and file-content measures must not be added'])
    return result, rows


def ext4_data_offsets(image, guest_path):
    """Resolve initialized leaf extents only on a run-owned immutable image.

    Caller must establish that the image is immutable, not a mounted live upper.
    Reject sparse/unwritten/non-extent files rather than inventing missing pages.
    """
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', guest_path) or '..' in Path(guest_path).parts:
        raise ValueError('unsupported debugfs pathname')
    def query(command):
        result = subprocess.run(['debugfs', '-R', command, str(image)], capture_output=True,
                                text=True, timeout=30, check=True)
        return result.stdout
    header = query('stats')
    if not re.search(r'Block size:\s+4096\b', header):
        raise RuntimeError('only 4096-byte ext4 blocks verified')
    stat = query('stat ' + guest_path)
    if 'Type: regular' not in stat or not int(re.search(r'Flags: (0x[0-9a-fA-F]+)', stat)[1], 16) & 0x80000:
        raise RuntimeError('not a regular extent file')
    size = int(re.search(r'\bSize:\s+(\d+)', stat)[1])
    raw = query('dump_extents -l ' + guest_path)
    offsets, logical = [], set()
    pattern = r'^\s*(\d+)/\s*(\d+)\s+\d+/\s*\d+\s+(\d+)\s*-\s*(\d+)\s+(\d+)\s*-\s*(\d+)\s+(\d+)\s*(.*)$'
    for line in raw.splitlines()[1:]:
        match = re.match(pattern, line)
        if not match:
            raise RuntimeError('unrecognized ext4 extent row')
        level, depth, start, end, physical, physical_end, length = map(int, match.groups()[:7])
        if level != depth or match[8].strip() or end - start + 1 != length or physical_end - physical + 1 != length:
            raise RuntimeError('non-leaf/unwritten/inconsistent ext4 extent')
        if logical.intersection(range(start, end + 1)):
            raise RuntimeError('overlapping ext4 logical extents')
        logical.update(range(start, end + 1))
        offsets.extend(block * PAGE for block in range(physical, physical_end + 1))
    if logical != set(range((size + PAGE - 1) // PAGE)):
        raise RuntimeError('sparse/incomplete ext4 file; physical ownership unsupported')
    return offsets


def audit_run(directory):
    saved = json.loads((directory / 'result.json').read_text())
    rows = json.loads((directory / 'pages.json').read_text())
    computed = summarize(rows, failed=saved['first_failure'] is not None)
    if computed != saved['file_content']:
        raise RuntimeError('saved counters differ from raw PFN observations')
    expected = ('FAIL' if saved['first_failure'] else 'PARTIAL' if computed['unattributed_unique_pages']
                or computed['file_content_physical_bytes'] is None else 'PASS')
    if saved['status'] != expected:
        raise RuntimeError('saved measurement status inconsistent with missing/partial data')
    manifest = (directory / 'manifest.json').read_bytes()
    if hashlib.sha256(manifest).hexdigest() != saved['manifest_sha256']:
        raise RuntimeError('manifest hash mismatch')
    return dict(audit_status='PASS', measurement_status=saved['status'], file_content=computed,
                scope='offline counter/hash consistency only; not live mapping validation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', nargs='?', type=Path)
    parser.add_argument('--output', type=Path, help='new local result directory')
    parser.add_argument('--audit-run', type=Path, help='offline re-count; retains original measurement status')
    args = parser.parse_args()
    if args.audit_run:
        if args.manifest or args.output:
            parser.error('--audit-run cannot be combined with a live collection')
        print(json.dumps(audit_run(args.audit_run), indent=2))
        return 0
    if args.manifest is None or args.output is None:
        parser.error('live collection requires manifest and --output')
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    raw = args.manifest.read_bytes()
    result, rows = collect(json.loads(raw))
    result['manifest_sha256'] = hashlib.sha256(raw).hexdigest()
    (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    (args.output / 'pages.json').write_text(json.dumps(rows) + '\n')
    (args.output / 'manifest.json').write_bytes(raw)
    print(f"status={result['status']} result_dir={args.output}")
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
