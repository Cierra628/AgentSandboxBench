#!/usr/bin/env python3
"""Apply/check only the recorded patches on the recorded research revisions."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
def git(source,*args,check=True):
    return subprocess.run(['git','-C',str(source),*map(str,args)],capture_output=True,text=True,check=check)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true',help='verify already-applied state without changes');args=parser.parse_args()
    revisions=json.loads((ROOT/'configs/source-revisions.json').read_text())
    parent=ROOT/'IncrementalDAX_moti';fork=parent/'baselines/TrEnv-X'
    for source,revision in [(parent,revisions['IncrementalDAX_moti']['commit']),(fork,revisions['IncrementalDAX_moti']['submodules']['baselines/TrEnv-X'])]:
        if git(source,'rev-parse','HEAD').stdout.strip()!=revision:raise RuntimeError(f'pinned revision mismatch: {source}')
    patches=[(parent,'incrementaldax-platform.patch'),(fork,'trenvx-task-cgroup.patch'),(parent,'incrementaldax-task-checkpoint.patch')]
    # Preflight the whole plan before mutating any checkout.
    pending=[]
    for source,name in patches:
        patch=ROOT/'patches'/name
        if git(source,'apply','--reverse','--check',patch,check=False).returncode==0:
            print(f'already applied: {name}');continue
        if args.check:raise RuntimeError(f'not applied or diverged: {name}')
        git(source,'apply','--check',patch);pending.append((source,patch))
    for source,patch in pending:
        git(source,'apply',patch);print(f'applied: {patch.name}')
    # Audit mirrored implementation sources, so a stale patch cannot silently
    # differ from the code used by the offline contracts/kernel fixture.
    pairs=[(p,fork/'packages/envd/internal/taskcgroup'/p.name) for p in (ROOT/'backend/trenvx/taskcgroup').glob('*.go')]
    pairs += [(ROOT/'backend/trenvx/quiesced_checkpoint.py',parent/'motivation/experiments/exp3-RL-fork/quiesced_checkpoint.py'),(ROOT/'backend/trenvx/tests/taskcgroup_integration_test.go',fork/'packages/envd/internal/process/taskcgroup_integration_test.go')]
    for expected,actual in pairs:
        if expected.read_bytes()!=actual.read_bytes():raise RuntimeError(f'implementation/patch mismatch: {actual}')
    print('PASS pinned revisions, patch state, and implementation parity')
if __name__=='__main__':main()
