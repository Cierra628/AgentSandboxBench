"""BPO copy control: tiny temporary files and mocks, never real services/devices."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import host_full_rootfs_control as control
import host_private_files as files
import run_host_full_rootfs as run
from isolated_storage import GIB

ROLES=['backbone',*[f'{kind}_{i}' for kind in ('early','late') for i in range(1,4)]]


class BpoCopiesTests(unittest.TestCase):
    def fixture(self,mount,mode):
        layers=[dict(relative=f'layer-{i}/base',source_digest=f'sha256:{i}',size=4) for i in range(19)]
        for row in layers:
            p=mount/'env/image-cache/commits'/row['relative'];p.parent.mkdir(parents=True);p.write_bytes(b'test')
        self.enterContext(patch.object(control,'manifest_layers',return_value=layers))
        self.enterContext(patch.object(files,'counters',return_value={}))
        self.enterContext(patch.object(control,'counters',return_value={}))
        self.query=self.enterContext(patch.object(files,'mincore_file',return_value=dict(stable=True,resident_bytes=4)))
        def call(label,argv,timeout):shutil.copy2(argv[-2],argv[-1])
        control.prepare(mount,mode,call,schedule='bpo')
        out=mount/'controller/prettier6604';out.mkdir(parents=True)
        binding=SimpleNamespace(cgroup=Path('/mock/cgroup'),resolve=lambda sid:dict(pid=100+int(sid)))
        observer=control.FullRootfsControl(mode,out,Path('/mock/group'),binding,schedule='bpo');observer.before()
        guests={role:str(i) for i,role in enumerate(ROLES)}
        objects=[];roots_guests={};receipts=observer.control/'receipts';receipts.mkdir()
        for slot,rows in enumerate(observer.preparation['copies']):
            for row in rows:objects.append(dict(**row['identity'],users=ROLES if mode=='shared' else [ROLES[slot]],status='SELECTED'))
        for i,role in enumerate(ROLES):
            slot=0 if mode=='shared' else i;rows=observer.preparation['copies'][slot]
            roots_guests[role]=dict(lowers=[dict(**r['identity'],reference=dict(digest=r['digest'])) for r in rows])
            files.write(receipts/f'guest-{100+i}.json',dict(mode=mode,schedule='bpo',slot=slot,lowers=[r['identity'] for r in rows]))
        for step in [11,24]:
            p=mount/f'generated-{step}';p.write_bytes(b'test');info=control.file_identity(p)
            users=ROLES if step==11 else [r for r in ROLES if not r.startswith('early')]
            objects.append(dict(path=str(p),**info,users=users,status='SELECTED'))
            for role in users:roots_guests[role]['lowers'].append(dict(**info,reference=dict(file=str(p),digest=f'generated-{step}')))
        observer.phase('root-ready',{'backbone':guests['backbone']})
        for step,template in [(11,'early-template'),(24,'late-template')]:
            observer.phase(f'before-checkpoint-{step}',{'backbone':guests['backbone']})
            observer.phase(f'after-checkpoint-{step}',{'backbone':guests['backbone']},template=template,step=step)
        early={r:guests[r] for r in ROLES if r.startswith('early')}
        observer.phase('early-restores-ready',dict(backbone=guests['backbone'],**early),restored={r:'early-template' for r in early})
        observer.phase('all-restores-ready',guests,restored={r:'late-template' for r in ROLES if r.startswith('late')})
        return observer,guests,dict(status='IDENTIFIED',objects=objects,guests=roots_guests)

    def after(self,observer,guests,roots,*,post_error=None):
        with patch.object(control,'rootfs_inventory',return_value=roots),patch('host_prefix_compare.drives_for_vm',return_value=[]),\
             patch('host_rollout_shared.opened_regular_files',return_value={}),patch.object(control,'validate_rootfs_inventory',side_effect=post_error),\
             patch.object(control.os,'readlink',side_effect=lambda p:'/runtime/guest-'+p.split('/')[2]):
            return observer.after(guests,control.time.monotonic_ns())

    def test_initial_files_and_two_generated_layers_have_separate_queries(self):
        for mode,count in [('shared',19),('private',133)]:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                observer,guests,roots=self.fixture(Path(tmp),mode)
                self.assertEqual(self.query.call_count,count)  # All intermediate phases are metadata only.
                self.assertIsNone(observer.state['generated_layers']['P0']['resident_bytes'])
                value=self.after(observer,guests,roots)
                self.assertEqual(value['status'],'OBSERVED');self.assertEqual(self.query.call_count,2*count+2)
                self.assertEqual(value['P1']['resident_bytes'],4*count)
                generated=value['generated_layers']['P1']
                self.assertEqual(generated['resident_bytes'],8)
                self.assertEqual([len(r['users']) for r in generated['objects']],[7,4])
                self.assertEqual([r['step'] for r in generated['objects']],[11,24])
                self.assertEqual(len(roots['objects']),count+2)
                self.assertEqual(len(value['phases']),7)
                self.assertLessEqual(value['P1']['end_monotonic_ns'],generated['start_monotonic_ns'])
                self.assertLessEqual(generated['end_monotonic_ns'],value['P1_postcheck']['start_monotonic_ns'])
                self.assertTrue(all(r['residency']=='NOT_QUERIED' and r['cpu_ns']>=0 for r in value['phases']))

    def test_remerged_initial_files_changed_generated_users_and_checkpoint_sources_fail(self):
        for case in ['remerged','generated-split','wrong-users','lost-checkpoint','lost-restore','postcheck']:
            with self.subTest(case=case),tempfile.TemporaryDirectory() as tmp:
                observer,guests,roots=self.fixture(Path(tmp),'private')
                if case=='remerged':roots['guests']['early_1']['lowers'][0]['inode']=roots['guests']['backbone']['lowers'][0]['inode']
                if case=='generated-split':roots['guests']['early_1']['lowers'][19]['inode']+=1
                if case=='wrong-users':roots['objects'][-1]['users']=['backbone']
                if case=='lost-checkpoint':observer.templates.pop('early-template')
                if case=='lost-restore':observer.restored.pop('early_1')
                with self.assertRaises(ValueError):self.after(observer,guests,roots,post_error=ValueError('changed') if case=='postcheck' else None)
                self.assertEqual(files.read(observer.out/'host-full-rootfs.json')['status'],'FAIL')

    def test_bpo_budget_and_commands_are_explicit_old_default_remains_grpo(self):
        with self.assertRaises(RuntimeError):run.require_budget(90*GIB,'bpo')
        self.assertTrue(run.require_budget(90*GIB+1,'bpo')['ready'])
        with patch.object(run,'commands',return_value=([],['bash','entry','--prettier6604-readonly-replay'])):
            cmd=run.member_command({},Path('/seed'),'private',Path('/daemon'),'bpo')
            self.assertEqual(cmd[cmd.index('--prettier6604-schedule')+1],'bpo')
        with patch.object(run,'available',return_value=89*GIB),patch.object(run.subprocess,'run') as start:
            with self.assertRaises(RuntimeError):run.pair(Path('/unused'),Path('/unused'),'bpo')
            start.assert_not_called()
        p=subprocess.run([sys.executable,'-B',ROOT/'scripts/run_host_file_workflow.py','/unused','--copy-schedule','bpo'],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(p.returncode,0);self.assertIn('requires full-rootfs-group',p.stderr)


if __name__=='__main__':unittest.main()
