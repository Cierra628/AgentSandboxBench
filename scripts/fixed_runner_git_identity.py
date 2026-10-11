"""Explicit Git identity for runner setup/finalization; frozen actions stay unchanged."""
from guest_runner_phases import BASELINE_BLOCK

FINAL_BLOCK = '''git -C /testbed add -A
git -C /testbed diff --binary --cached "$(cat "$out/baseline-tree.txt")" > "$out/patch.diff"
git -C /testbed reset --mixed >/dev/null
git -C /testbed status --porcelain=v1 > "$out/git-status.txt"'''
IDENTITY = {'user.name': 'asb-fixture', 'user.email': 'asb-fixture@localhost'}


def instrument(source):
    for block in (BASELINE_BLOCK, FINAL_BLOCK):
        assert source.count(block) == 1, 'unexpected runner Git block'
        source = source.replace(block, block.replace('git -C /testbed',
            'git -c user.name=asb-fixture -c user.email=asb-fixture@localhost -C /testbed'))
    return source
