"""Offline checks of the exact opt-in flag expressions; no main() execution."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
ROOT=Path(__file__).resolve().parents[1]

def expression(path,kind,predicate):
    tree=ast.parse((ROOT/'scripts'/path).read_text())
    nodes=[n for n in ast.walk(tree) if isinstance(n,kind) and predicate(n)]
    assert len(nodes)==1,(path,len(nodes))
    return nodes[0]

def evaluate(node,values):return eval(compile(ast.Expression(node),'<saved-source>','eval'),values)

class ProviderOptionsTests(unittest.TestCase):
    def test_provider_no_longer_selects_old_restore_workflow_for_sessions(self):
        n=expression('validate_isolated_agentenv.py',ast.Assign,lambda n:any(isinstance(t,ast.Name) and t.id=='lower_cache_probe' for t in n.targets))
        for sessions,expected in [(True,False),(False,True)]:
            actual=evaluate(n.value,dict(args=SimpleNamespace(guest_cache_rollout_sessions=sessions),pair_cache_probe=False,provider_binary=Path('/local/provider'),prettier_cache_pair=False))
            self.assertEqual(actual,expected)
    def test_wrapper_passes_binary_and_get_data_only_when_enabled(self):
        n=expression('run_live_child_calibration.py',ast.Starred,lambda n: isinstance(n.value,ast.IfExp) and isinstance(n.value.test,ast.Name) and n.value.test.id=='provider_binary')
        self.assertEqual(evaluate(n.value,dict(provider_binary=None)),[])
        self.assertEqual(evaluate(n.value,dict(provider_binary='/owned/binary')),['--snapshot-provider-binary','/owned/binary','--snapshot-provider-get-data'])
    def test_systemd_child_gets_same_explicit_binary(self):
        n=expression('run_live_child_calibration.py',ast.Starred,lambda n:isinstance(n.value,ast.IfExp) and isinstance(n.value.test,ast.Attribute) and n.value.test.attr=='provider_binary')
        self.assertEqual(evaluate(n.value,dict(args=SimpleNamespace(provider_binary=None))),[])
        self.assertEqual(evaluate(n.value,dict(args=SimpleNamespace(provider_binary=Path('/owned/binary')))),['--provider-binary','/owned/binary'])
    def test_runner_receives_provider_option(self):
        n=expression('validate_isolated_agentenv.py',ast.Starred,lambda n:isinstance(n.value,ast.IfExp) and isinstance(n.value.body,ast.List) and any(isinstance(x,ast.Constant) and x.value=='--provider-cache' for x in n.value.body.elts))
        self.assertEqual(evaluate(n.value,dict(provider_binary=None)),[])
        self.assertEqual(evaluate(n.value,dict(provider_binary='/owned/binary')),['--provider-cache'])

if __name__=='__main__':unittest.main()
