"""Run the 26 frozen shell actions with one persistent monotonic timer."""
import argparse
from pathlib import Path
import subprocess
import time


def run(actions, output, timeout_seconds, *, cwd=Path('/testbed'), phase_file=None):
    actions,output=Path(actions),Path(output)
    files=sorted(actions.glob('*.sh'))
    assert [p.stem for p in files]==[f'{i:03d}' for i in range(1,27)]
    with (output/'steps.tsv').open('a',buffering=1) as steps:
        for action in files:
            step=action.stem
            if phase_file is not None:Path(phase_file).write_text('running\t\t'+step+'\n')
            start=time.monotonic_ns()
            with (output/'stdout'/f'{step}.log').open('wb') as stdout,(output/'stderr'/f'{step}.log').open('wb') as stderr:
                result=subprocess.run(['timeout','--signal=TERM','--kill-after=5s',str(timeout_seconds)+'s',
                    'bash',str(action.resolve())],cwd=cwd,stdout=stdout,stderr=stderr)
            duration=time.monotonic_ns()-start
            code=result.returncode if result.returncode>=0 else 128-result.returncode
            steps.write(f'{step}\t{code}\t{duration}\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--actions',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout-seconds',type=int,default=60);parser.add_argument('--phase-file',type=Path)
    args=parser.parse_args()
    if args.timeout_seconds!=60:parser.error('frozen task timeout must remain 60 seconds')
    run(args.actions,args.output,args.timeout_seconds,phase_file=args.phase_file)
