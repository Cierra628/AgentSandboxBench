"""Offline monitoring controls and exact limits for dynamic Jest output."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prettier6604_monitor_pairs import output_signature
from validate_isolated_agentenv import prettier_capacity
from isolated_storage import GIB


class PrettierMonitorTests(unittest.TestCase):
    def text(self,path='tests/typescript/conformance/types/one/jsfmt.spec.js',duration='1.2'):
        return (f'PASS {path}\n\nTest Suites: 32 passed, 32 total\nTests: 66 passed, 66 total\nSnapshots: 66 passed, 66 total\nTime: {duration}s\nDone in {duration}s.\n').encode()

    def signature(self,text,kind='stdout',segment='backbone_finish',step='005'):
        return output_signature(text,segment,step,kind,['tests/typescript/conformance/types/one/jsfmt.spec.js','tests/typescript/conformance/types/two/jsfmt.spec.js'])

    def test_known_tail_output_can_change_time_and_last_valid_suite_only(self):
        a=self.text();b=self.text('tests/typescript/conformance/types/two/jsfmt.spec.js','9.9')
        self.assertEqual(self.signature(a),self.signature(b))
        self.assertNotEqual(self.signature(a,'stderr'),self.signature(b,'stderr'))
        self.assertNotEqual(self.signature(a,segment='read_repo',step='001'),self.signature(b,segment='read_repo',step='001'))

    def test_new_failure_counts_or_unknown_test_are_rejected(self):
        for value in (self.text().replace(b'66 passed, 66 total',b'65 passed, 1 failed, 66 total'),self.text('tests/unexpected/jsfmt.spec.js')):
            with self.assertRaises(AssertionError):self.signature(value)
        self.assertNotEqual(self.signature(self.text()),self.signature(self.text()+b'FAIL unexpected\n'))

    def test_other_output_and_unrecognized_timing_text_stay_strict(self):
        self.assertNotEqual(self.signature(self.text()),self.signature(self.text().replace(b'Time: 1.2s',b'Time: unknown')))
        self.assertNotEqual(self.signature(self.text()),self.signature(self.text()+b'new warning\n'))

    def test_known_node_pid_and_jest_warning_order_are_dynamic_only(self):
        def warning(name,paths):
            return ('This warning is caused by a @providesModule declaration with the same name across two different files.\n'
                    'jest-haste-map: @providesModule naming collision:\n'
                    f'  Duplicate module name: {name}\n'
                    f'  Paths: {paths}\n\n').encode()
        blocks=[warning('ES6_ExportFrom_Source2','/testbed/tests/flow/es6modules/ES6_ExportFrom_Source2.js collides with /testbed/tests/flow/declare_export/ES6_ExportFrom_Source2.js'),
                warning('ES6_Default_NamedClass2','/testbed/tests/flow/es6modules/ES6_Default_NamedClass2.js collides with /testbed/tests/flow/declare_export/ES6_Default_NamedClass2.js'),
                warning('ES6_Named1','/testbed/tests/flow/es6modules/ES6_Named1.js collides with /testbed/tests/flow/declare_export/ES6_Named1.js')]
        stderr_a=("(node:123) [DEP0128] DeprecationWarning: Invalid 'main' field in '/testbed/node_modules/find-parent-dir/package.json' of 'find-parent-dir.js'. Please either fix that or report it to the module author\n"
                  "(Use `node --trace-deprecation ...` to show where the warning was created)\n").encode()
        stderr_b=stderr_a.replace(b'123',b'987')
        self.assertEqual(self.signature(stderr_a,kind='stderr',segment='read_repo',step='004'),self.signature(stderr_b,kind='stderr',segment='read_repo',step='004'))
        self.assertNotEqual(self.signature(stderr_a,kind='stderr',segment='read_repo',step='004'),self.signature(stderr_b.replace(b'DEP0128',b'DEP9999'),kind='stderr',segment='read_repo',step='004'))
        summary=b'Test Suites: 2 passed, 2 total\nTests: 2 passed, 2 total\nSnapshots: 2 passed, 2 total\n'
        a=b''.join(blocks)+summary
        b=b''.join(reversed(blocks))+summary
        self.assertEqual(self.signature(a,segment='core_verify',step='005'),self.signature(b,segment='core_verify',step='005'))
        self.assertNotEqual(self.signature(a,segment='core_verify',step='005'),self.signature(b.replace(b'/testbed/tests/flow/es6modules/ES6_Named1.js',b'/testbed/changed'),segment='core_verify',step='005'))
        inserted=blocks[0]+b'new intervening warning\n'+b''.join(blocks[1:])+summary
        self.assertNotEqual(self.signature(a,segment='core_verify',step='005'),self.signature(inserted,segment='core_verify',step='005'))
        self.assertNotEqual(self.signature(stderr_a,kind='stderr'),self.signature(stderr_b,kind='stderr'))

    def test_original_custom_test_writes_snapshot_and_retains_outcomes(self):
        text=b'Test Suites: 1 passed, 1 total\nTests: 1 passed, 1 total\nSnapshots: 1 written, 1 total\n'
        a=text+'  ✓ parenthesizedType.ts (2ms)\n'.encode()
        b=text+'  ✓ parenthesizedType.ts (3ms)\n'.encode()
        self.assertEqual(self.signature(a,step='002'),self.signature(b,step='002'))
        for replacement in (b'1 passed, 1 total',b'1 updated, 1 total',b'1 failed, 1 total'):
            with self.assertRaises(AssertionError):self.signature(a.replace(b'1 written, 1 total',replacement),step='002')
        self.assertNotEqual(self.signature(a,step='002'),self.signature(a+b'new warning\n',step='002'))

    def test_eight_gib_is_only_for_one_readonly_root_without_snapshot(self):
        args=SimpleNamespace(prettier6604_readonly_replay=True,prettier6604_task=Path('task'))
        self.assertEqual(prettier_capacity(args,None,None,'grpo',False,Path('seed')),8*GIB)
        for export,role,checkpoint,seed in [(Path('export'),None,False,Path('seed')),(None,'early_1',False,Path('seed')),(None,None,True,Path('seed')),(None,None,False,None)]:
            with self.assertRaises(ValueError):prettier_capacity(args,export,role,'grpo',checkpoint,seed)


if __name__=='__main__':unittest.main()
