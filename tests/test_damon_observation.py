"""Fake sysfs and local process only; no real module writes or sandbox services."""
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from sandbox_runtime_monitor import RuntimeMonitor, MEMFIELDS, DAMON_FIELDS, damon_values
from platform_tasks import configuration, commands, preflight
from report_damon_observation import analyze


class DamonObservationTests(unittest.TestCase):
    def test_diagnostic_config_is_opt_in_and_checked_offline(self):
        cfg=configuration(ROOT/'configs/platform-prettier6604-damon-observe.json')
        check,live=commands(cfg,Path('/unused'))
        self.assertIn('--damon-observe',check);self.assertIn('--damon-observe',live)
        self.assertEqual(preflight(cfg,offline=True)['status'],'VALIDATED')
        ordinary=configuration(ROOT/'configs/platform-prettier6604.json')
        self.assertNotIn('--damon-observe',commands(ordinary,Path('/unused'))[1])
        source=json.loads((ROOT/'configs/platform-prettier6604.json').read_text())
        for changes in (dict(light_monitor=False),dict(monitor_mode='off'),dict(damon_observe=1)):
            with tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'config.json';path.write_text(json.dumps(source|dict(damon_observe=True)|changes))
                with self.assertRaises(ValueError):configuration(path)

    def test_missing_required_counter_is_not_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in DAMON_FIELDS[:-1]:(root/name).write_text('Y' if name=='enabled' else '0')
            with self.assertRaises(FileNotFoundError):damon_values(root)

    def test_delayed_counter_update_and_reset_remain_explicit(self):
        from sandbox_runtime_monitor import PREFIX
        memory=[];damon=[]
        for i,(cache,reclaimed) in enumerate(((200,0),(100,0),(100,100))):
            prefix=dict(zip(PREFIX,[str(i*10**8)]*3+['owned','backbone','session','running','core_verify','001']))
            memory.append(dict(prefix,Cached_kib=str(cache*1024)))
            damon.append(dict(prefix,read_start_monotonic_ns=str(i*10**8),read_end_monotonic_ns=str(i*10**8+1),
                enabled='Y',kdamond_pid='7',nr_reclaim_tried_regions='1',bytes_reclaim_tried_regions=str(1000*2**20),
                nr_reclaimed_regions='1',bytes_reclaimed_regions=str(reclaimed*2**20),nr_quota_exceeds='0'))
        summary,_=analyze(memory,damon)
        self.assertEqual(summary['total_reclaimed_bytes'],100*2**20)
        self.assertEqual(summary['cached_drops_at_least_1mib'][0]['end_seconds'],.1)
        self.assertEqual(summary['counter_increases'][0]['end_seconds'],.2)
        damon[1]['bytes_reclaimed_regions']=str(200*2**20)
        reset,_=analyze(memory,damon)
        self.assertIsNone(reset['total_reclaimed_bytes']);self.assertTrue(reset['counter_resets'])
        with self.assertRaises(AssertionError):analyze(memory,damon[:-1])

    def test_local_collector_pairs_read_only_counters_with_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);raw=root/'raw';raw.mkdir();proc=root/'proc';proc.mkdir()
            (proc/'meminfo').write_text(''.join(f'{name}: 100 kB\n' for name in MEMFIELDS))
            parameters=root/'sys/module/damon_reclaim/parameters';parameters.mkdir(parents=True)
            original={name:'0' for name in DAMON_FIELDS};original.update(enabled='Y',kdamond_pid='71',bytes_reclaimed_regions='4096')
            for name,value in original.items():
                path=parameters/name;path.write_text(value+'\n');path.chmod(0o444)
            def cli(label,*argv,timeout=15):
                if argv[0]=='upload':return ''
                if argv[0]=='download':
                    shutil.copytree(argv[2],Path(argv[3])/Path(argv[2]).name);return ''
                command=list(argv[2:])
                if 'collect' in command:command+=['--proc-root',str(proc),'--sys-root',str(root/'sys')]
                return subprocess.check_output(command,text=True,timeout=timeout).strip()
            with patch('sandbox_runtime_monitor.GUEST_ROOT',str(root/'guest')),patch('sandbox_runtime_monitor.SCRIPT',str(ROOT/'scripts/sandbox_runtime_monitor.py')):
                monitor=RuntimeMonitor(raw,cli,light=True,damon_observe=True)
                try:
                    monitor.start('owned','backbone');time.sleep(.25)
                finally:
                    if monitor.active:monitor.stop('owned')
            manifest=json.loads((raw/'runtime-monitor.json').read_text());self.assertTrue(manifest['damon_observed'])
            session=manifest['sessions'][0];self.assertTrue(session['collector_reaped']);self.assertTrue(session['receipt']['damon_observed'])
            directory=raw/session['raw_directory']
            def rows(path):
                with path.open() as stream:return list(csv.DictReader(stream,delimiter='\t'))
            memory=rows(directory/'guest-memory.tsv');damon=rows(directory/'guest-damon.tsv')
            self.assertEqual(len(memory),len(damon));self.assertGreaterEqual(len(damon),3)
            for left,right in zip(memory,damon):
                self.assertEqual(left['monotonic_ns'],right['monotonic_ns'])
                self.assertEqual(right['bytes_reclaimed_regions'],'4096')
                self.assertLessEqual(int(right['read_start_monotonic_ns']),int(right['read_end_monotonic_ns']))
            self.assertEqual({p.name:p.read_text().strip() for p in parameters.iterdir()},original)
            settings=json.loads((directory/'damon-settings.json').read_text())
            self.assertEqual(settings['parameters'],original)
            self.assertFalse((directory/'guest-vmstat.tsv').exists())


if __name__=='__main__':unittest.main()
