"""Exercise the original C4 executor with an already-created guest; mocked CLI only."""
import csv
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'tests'))
import test_external_workload as fixtures
from external_workload import C4,load
import run_external_workload as controller


class C4DensityChildTests(unittest.TestCase):
    def test_existing_guest_barrier_validation_and_sibling_cleanup(self):
        class Monitor:
            def __init__(self,*args,**kwargs):self.active={}
            def start(self,sid,*args):self.active[sid]=True
            def stop(self,sid,*args):self.active.pop(sid)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config,_=fixtures.ExternalWorkloadTests().fixture(root,C4);cfg=json.loads(config.read_text())
            cfg.update(repository='local-fixture',revision='fixed-revision',image='fixture-image',validation_scope='fixture')
            config.write_text(json.dumps(cfg));clock=root/'experiments/container-screening/harness/clock_ns.c';clock.parent.mkdir(parents=True);clock.write_text('/* mocked compiler */')
            parent=root/'controller';parent.mkdir();group=parent/'density';group.mkdir();out=parent/'density-guest-00';ctx=group/'guest-00.json';sid='11111111-1111-1111-1111-111111111111'
            ctx.write_text(json.dumps(dict(name='guest-00',task='c4',sandbox_id=sid,container='owned',output=str(out))))
            (group/'paths.release').write_text('continue\n')
            args=SimpleNamespace(config=config,upstream=root,output=out,config_home=root/'cfg',service_url='http://127.0.0.1:1',container='owned',density_context=ctx)
            seen=[]
            def fake_run(argv,**kwargs):
                argv=list(map(str,argv));seen.append(argv);stdout=''
                if argv[0]=='git':stdout=cfg['revision']
                elif any('agentenv_cold_start.py' in v for v in argv):raise AssertionError('extra guest creation')
                elif 'download' in argv:
                    target=out/'input';target.mkdir();logs=target/'logs';logs.mkdir();expected=load(config,root)[2]
                    with (target/'tools.tsv').open('w') as f:
                        w=csv.DictWriter(f,fieldnames=['step','start_monotonic_ns','end_monotonic_ns','duration_ns','exit_code'],delimiter='\t');w.writeheader()
                        for i,r in enumerate(expected,1):w.writerow(dict(step=i,start_monotonic_ns=i*100,end_monotonic_ns=i*100+50,duration_ns=50,exit_code=r['exit_code']))
                    (logs/'033.stdout').write_text('CONSTRAINT VIOLATIONS: NONE\nFINAL VERDICT: BYTE-EXACT ROUNDTRIP ON c4_sample\n');(logs/'034.stdout').write_text('9898 files, structure + content\n')
                elif 'list' in argv:stdout='[{"sandboxID":"22222222-2222-2222-2222-222222222222"}]'
                elif 'exec' in argv and 'chmod 755' in argv[-1]:stdout='100'
                return SimpleNamespace(returncode=0,stdout=stdout)
            with patch.object(controller.subprocess,'run',side_effect=fake_run),patch.object(controller,'RuntimeMonitor',Monitor):status=controller.run(args)
            result=json.loads((out/'result.json').read_text());self.assertEqual(status,0,result)
            self.assertEqual(result['cleanup']['sandbox_deletion'],'DELEGATED_TO_DENSITY_COORDINATOR')
            self.assertEqual(result['roundtrip_validation'],'PASS');self.assertEqual(result['actions'],36)
            self.assertTrue((group/'guest-00-tools.json').exists())
            self.assertEqual(sum('delete' in a for a in seen),0)
            self.assertEqual(sum('list' in a for a in seen),0)


if __name__=='__main__':unittest.main()
