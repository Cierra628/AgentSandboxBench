import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_memory_backing_links as m


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.layer=self.root/'layer.commit';self.layer.write_bytes(b'x')
        self.config=self.root/'image.json'
        info=m.file_identity(self.layer);self.opened={(info['device'],info['inode']):[dict(pid=7,fd='8')]}
        self.image=dict(lowers=[dict(file='layer.commit')])
        self.config.write_text(json.dumps(self.image))

    def test_complete_config_and_identity_without_payload_read(self):
        original=Path.read_text
        def read(path,*a,**k):
            self.assertEqual(path,self.config)
            return original(path,*a,**k)
        with patch.object(Path,'read_text',read):
            r=m.config_layers(self.config,self.root,self.opened)
        self.assertEqual(r['status'],'IDENTIFIED');self.assertEqual(r['config_contents'],self.image)
        self.assertEqual(r['lowers'][0]['inode'],self.layer.stat().st_ino)

    def test_missing_open_holder_is_unknown_not_zero(self):
        r=m.config_layers(self.config,self.root,{})
        self.assertEqual(r['status'],'INCOMPLETE');self.assertEqual(r['lowers'][0]['status'],'UNKNOWN')
        self.assertNotIn('resident_bytes',r['lowers'][0])

    def test_config_change_invalidates_references(self):
        original=m.file_identity;count=0
        def identity(path):
            nonlocal count
            r=original(path)
            if path==self.config:
                count+=1
                if count>1:r['mtime_ns']+=1
            return r
        with patch.object(m,'file_identity',identity):r=m.config_layers(self.config,self.root,self.opened)
        self.assertEqual(r['status'],'UNKNOWN');self.assertEqual(r['lowers'][0]['status'],'UNKNOWN')

    def test_remote_and_auxiliary_references_retained_as_unknown(self):
        for ref in [dict(file='https://invalid.example/layer'),dict(file='layer.commit',targetFile='extra')]:
            self.config.write_text(json.dumps(dict(lowers=[ref])))
            r=m.config_layers(self.config,self.root,self.opened)
            self.assertEqual(r['status'],'INCOMPLETE');self.assertEqual(r['lowers'][0]['reference'],ref)

    def test_bad_json_and_upper_do_not_pass(self):
        for text in ['{',json.dumps(dict(lowers=[dict(file='layer.commit')],upper={'data':'x'}))]:
            self.config.write_text(text);self.assertEqual(m.config_layers(self.config,self.root,self.opened)['status'],'UNKNOWN')

    def test_empty_serialized_upper_is_read_only(self):
        self.image['upper']=dict(index='',data='',target='',gzipIndex='')
        self.config.write_text(json.dumps(self.image))
        result=m.config_layers(self.config,self.root,self.opened)
        self.assertEqual(result['status'],'IDENTIFIED')
        self.assertEqual(result['config_contents']['upper'],self.image['upper'])



class ObserveTests(unittest.TestCase):
    setUp = ConfigTests.setUp
    def test_shared_device_links_and_single_query(self):
        out=self.root/'out';out.mkdir()
        template='11111111-1111-1111-1111-111111111111'
        config_path=f'/workspace/env/snapshot-local-cache/runtime/{template}/memory/image.json'
        roles=['early_1','early_2'];guests={r:'sid-'+r for r in roles}
        bindings={r:dict(pid=i+10,start_ticks='123') for i,r in enumerate(roles)}
        refs=[];ids={}
        (out/'sandbox-ids.tsv').write_text('role\tsandbox_id\tcreated_from\n'+''.join(f'{r}\t{guests[r]}\t{template}\n' for r in roles))
        for r,b in bindings.items():
            target=self.root/str(b['pid'])/'root'/config_path.lstrip('/');target.parent.mkdir(parents=True)
            target.write_text(json.dumps(self.image));(target.parent/'layer.commit').hardlink_to(self.layer)
            ids[str(b['pid'])]=dict(start_ticks='123',cgroup='0::/test\n')
            refs.append(dict(source='vmm_ram_mapping',role=r,region={'mapping_path':'/dev/ublkb8'}))
        layer=dict(block_devices={'objects':[dict(rdev=9,status='OBSERVED',aliases=refs)]})
        log=f'created or acquired shared read-only ublk device key={config_path} dev_id=8 path=/dev/ublkb8 sandbox_id=sid-early_1'
        log_path=self.root/'10/root/workspace/env/logs/server.log'
        log_path.parent.mkdir(parents=True);log_path.write_text(log)
        query=dict(m.file_identity(self.layer),stable=True,resident_bytes=4096)
        with patch.object(m,'permission_evidence',return_value={}),patch.object(m,'process_identity',return_value=ids['10']),patch.object(m,'check_process'),patch.object(m,'mincore_file',return_value=query) as q:
            result=m.observe('synthetic',out,guests,bindings,{'process_identities':ids},layer,self.opened,proc_root=self.root)
        self.assertEqual(result['status'],'IDENTIFIED',result)
        self.assertEqual(len(result['files']),1);q.assert_called_once()
        self.assertEqual(result['files'][0]['users'],roles)
        self.assertEqual(len(result['devices'][0]['configs']),2)
        with patch.object(m,'permission_evidence',return_value={}),patch.object(m,'process_identity',return_value=ids['10']),patch.object(m,'check_process',side_effect=ValueError('mapping changed')):
            bad=m.observe('synthetic',out,guests,bindings,{'process_identities':ids},layer,self.opened,proc_root=self.root)
        self.assertEqual(bad['status'],'INCOMPLETE');self.assertEqual(bad['devices'][0]['status'],'UNKNOWN')
        log_path.unlink()
        with patch.object(m,'permission_evidence',return_value={}),patch.object(m,'process_identity',return_value=ids['10']):
            missing=m.observe('synthetic',out,guests,bindings,{'process_identities':ids},layer,self.opened,proc_root=self.root)
        self.assertEqual(missing['status'],'INCOMPLETE')
        self.assertIn('FileNotFoundError',missing['error'])

if __name__=='__main__':unittest.main()
