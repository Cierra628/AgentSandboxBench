import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from diagnose_initial_git_index import index_debug,guest,summarize_stat
from report_initial_git_index import stage_entries


class IndexTests(unittest.TestCase):
    def test_refresh_keeps_modified_and_untracked_files_out_of_original_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=root/'repo';repo.mkdir()
            for args in (['init','-q'],['config','user.name','fixture'],['config','user.email','fixture@localhost']):
                subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,timeout=5)
            for name in ('clean','dirty'):(repo/name).write_text('original\n')
            subprocess.run(['git','-C',str(repo),'add','-A'],check=True,timeout=5)
            subprocess.run(['git','-C',str(repo),'commit','-qm','fixture'],check=True,timeout=5)
            # Allocate while the original inode still exists; Git builds may
            # ignore nanoseconds, so same-second writes alone are insufficient.
            replacement=repo/'replacement';replacement.write_text('original\n')
            self.assertNotEqual(replacement.stat().st_ino,(repo/'clean').stat().st_ino)
            replacement.replace(repo/'clean')
            (repo/'dirty').write_text('modified and longer\n');(repo/'added').write_text('new\n')
            source=Path(__file__).resolve().parents[1]/'scripts/command_clock.c'
            subprocess.run(['gcc','-O2',str(source),'-o',str(root/'command-clock')],check=True,timeout=10)
            out=root/'results';guest(out,repo,stat_only=True)
            result=json.loads((out/'result.json').read_text())
            self.assertEqual(result['refresh']['exit_code'],1)
            self.assertEqual(result['refresh']['stat']['entries_with_stat_difference'],1)
            self.assertEqual(stage_entries((out/'original-stage.bin').read_bytes()),stage_entries((out/'refreshed-stage.bin').read_bytes()))
            self.assertEqual(result['object_changes'],dict(added=1,removed=0,changed=1,unchanged=1))
            for name,key in (('original','initial'),('updated','updated'),('refreshed',None)):
                entries=index_debug((out/f'{name}-debug.bin').read_bytes());actuals=json.loads((out/f'{name}-actual-stat.json').read_text())
                expected=result[key] if key else result['refresh']['stat']
                self.assertEqual(summarize_stat(entries,actuals),expected)
                actuals.pop(next(iter(actuals)))
                with self.assertRaises(AssertionError):summarize_stat(entries,actuals)

    def test_stage_paths_use_same_text_type_as_debug_and_reject_duplicates(self):
        raw=b'100644 '+b'a'*40+b' 0\tname\nwith newline\0'
        parsed=stage_entries(raw)
        self.assertEqual(set(parsed),{'name\nwith newline'})
        self.assertEqual(parsed['name\nwith newline']['oid'],'a'*40)
        for invalid in (raw+raw,raw[:-1],raw.replace(b' 0\t',b' 1\t')):
            with self.assertRaises(AssertionError):stage_entries(invalid)

    def test_stat_only_change_preserves_objects_and_tree_across_index_switches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=root/'repo';repo.mkdir()
            for args in (['init','-q'],['config','user.name','fixture'],['config','user.email','fixture@localhost']):
                subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,timeout=5)
            file=repo/'name\nwith newline';file.write_text('content\n')
            subprocess.run(['git','-C',str(repo),'add','-A'],check=True,timeout=5)
            subprocess.run(['git','-C',str(repo),'commit','-qm','fixture'],check=True,timeout=5)
            file.unlink();file.write_text('content\n')
            source=Path(__file__).resolve().parents[1]/'scripts/command_clock.c'
            subprocess.run(['gcc','-O2',str(source),'-o',str(root/'command-clock')],check=True,timeout=10)
            out=root/'results';guest(out,repo)
            result=json.loads((out/'result.json').read_text())
            self.assertEqual(result['status'],'PASS')
            self.assertEqual(result['initial']['entries_with_stat_difference'],1)
            self.assertEqual(result['updated']['entries_with_stat_difference'],0)
            self.assertEqual(result['object_changes'],dict(added=0,removed=0,changed=0,unchanged=1))
            self.assertEqual([r['index_source'] for r in result['runs']],['first','original','updated','updated','original'])
            self.assertEqual(len({r['tree'] for r in result['runs']}),1)
            debug=(out/'original-debug.bin').read_bytes()
            self.assertEqual(set(index_debug(debug)),{'name\nwith newline'})
            for invalid in (debug+debug,debug.replace(b'  ctime:',b'  missing:'),b'../escape\0'+debug.split(b'\0',1)[1]):
                with self.assertRaises(AssertionError):index_debug(invalid)


if __name__=='__main__':unittest.main()
