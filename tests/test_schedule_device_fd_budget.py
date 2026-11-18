"""Child-local fd limit; /dev/null opens and proc metadata only, no live devices."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import resource
import stat
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import host_schedule_cache_layers as m


def probe(counts):
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    real_open, real_close = os.open, os.close
    before = len(os.listdir('/proc/self/fd'))
    active = {}; opened = []; closed = []; metadata_errors = []; peaks = []
    references = []
    for device, count in enumerate(counts):
        ram = device >= max(0, len(counts)-2)
        for alias in range(count):
            references.append(dict(path=f'/fake/{device}/{alias}', device=1, inode=device+1,
                rdev=device+50, process=123, region=None,
                source='open_block_fd' if alias==0 else 'vmm_ram_mapping' if ram else 'firecracker_drive', role='test'))

    def opening(path, flags):
        try:
            fd = real_open('/dev/null', flags)
        except OSError as error:
            metadata_errors.append(repr(error))
            raise
        device = int(str(path).split('/')[2])
        active[fd] = SimpleNamespace(st_dev=1, st_ino=device+1, st_rdev=device+50, st_mode=stat.S_IFBLK)
        opened.append(fd); peaks.append(len(active))
        # Check that another same-process metadata reader still has headroom.
        try:
            other = real_open('/proc/self/stat', os.O_RDONLY)
        except OSError as error:
            metadata_errors.append(repr(error))
        else:
            real_close(other)
        return fd

    def closing(fd):
        real_close(fd); active.pop(fd); closed.append(fd)

    with ExitStack() as patches:
        patches.enter_context(patch.object(m, 'check_process'))
        patches.enter_context(patch.object(m, 'capacity', return_value=4096))
        patches.enter_context(patch.object(m, 'cachestat', return_value=dict(nr_cache=1)))
        patches.enter_context(patch.object(m.os, 'open', side_effect=opening))
        patches.enter_context(patch.object(m.os, 'close', side_effect=closing))
        patches.enter_context(patch.object(m.os, 'fstat', side_effect=lambda fd: active[fd]))
        value = m.observe_devices(references, {123:{}}, method='cachestat', split_ram=True)
    after = len(os.listdir('/proc/self/fd'))
    return dict(status=value['status'], aliases=len(references), devices=len(counts),
        peak_pins=max(peaks), opened=len(opened), closed=len(closed), remaining_pins=len(active),
        descriptor_count_before=before, descriptor_count_after=after,
        metadata_errors=metadata_errors, reference_errors=len(value['errors']),
        observed_devices=value['method_totals']['cachestat']['observed_objects'],
        fixed_ram_status=value['fixed_ram_totals']['status'],
        scope='Only child process limit=64, /dev/null and proc metadata; mocked block identity/capacity/cache.')


class DeviceFdBudgetTests(unittest.TestCase):
    def run_probe(self, counts):
        child = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
            '--fd-budget-probe', json.dumps(counts)], capture_output=True, text=True, timeout=20)
        self.assertEqual(child.returncode, 0, child.stderr)
        value = json.loads(child.stdout)
        self.assertEqual(value['status'], 'OBSERVED', value)
        self.assertEqual(value['fixed_ram_status'], 'OBSERVED', value)
        self.assertFalse(value['metadata_errors'], value)
        self.assertEqual(value['reference_errors'], 0)
        self.assertEqual(value['observed_devices'], len(counts))
        self.assertLessEqual(value['peak_pins'], len(counts)+1, value)
        self.assertEqual(value['opened'], value['closed'])
        self.assertEqual(value['remaining_pins'], 0)
        self.assertEqual(value['descriptor_count_before'], value['descriptor_count_after'])

    def test_many_aliases_of_one_device_keep_metadata_reader_usable(self):
        self.run_probe([256])

    def test_c4_alias_counts_scale_pins_with_devices_not_references(self):
        self.run_probe([22, 3, 3, 3, 3, 3, 3, 3, 498, 698])

    def test_rejected_aliases_close_before_next_open_and_other_device_still_queried(self):
        refs = [dict(path=f'/fake/{i}', device=1, inode=i, rdev=i+50) for i in (10, 11, 12)]
        active = set(); next_fd = iter((10, 11, 12))
        def opening(*args):
            self.assertFalse(active, 'a rejected alias stayed open')
            fd = next(next_fd); active.add(fd); return fd
        def closing(fd):active.remove(fd)
        def info(fd):
            return SimpleNamespace(st_dev=1, st_ino=99 if fd==10 else fd,
                st_rdev=fd+50, st_mode=stat.S_IFBLK)
        def capacity(fd):
            if fd==11:raise OSError('capacity unavailable')
            return 4096
        with patch.object(m, 'check_process'), patch.object(m.os, 'open', side_effect=opening), \
             patch.object(m.os, 'close', side_effect=closing), patch.object(m.os, 'fstat', side_effect=info), \
             patch.object(m, 'capacity', side_effect=capacity), \
             patch.object(m, 'cachestat', return_value=dict(nr_cache=1)) as query:
            value = m.observe_devices(refs, {}, method='cachestat')
        self.assertFalse(active)
        self.assertEqual(value['status'], 'PARTIAL')
        self.assertEqual(len(value['errors']), 2)
        query.assert_called_once_with(12, offset=0, length=4096)
        self.assertIsNone(value['method_totals']['cachestat']['complete_bytes'])
        self.assertEqual(value['method_totals']['cachestat']['observed_objects'], 1)


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--fd-budget-probe':
        print(json.dumps(probe(json.loads(sys.argv[2]))))
    else:
        unittest.main()
