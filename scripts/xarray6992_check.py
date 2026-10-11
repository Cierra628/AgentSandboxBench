"""Guest oracle or isolated post-replay benchmark testing; never install packages."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def oracle(baseline):
    import xarray as xr
    assert Path(xr.__file__).resolve().is_relative_to(Path('/testbed/xarray')), 'oracle imported a different source checkout'
    ds=xr.Dataset(coords={'x':[1,2,3],'y':('x',[4,5,6])}).set_index(x='y')
    result=ds.reset_index('x',drop=True)
    consistent=result._coord_names<=set(result._variables)
    if baseline:
        assert not consistent
        try:repr(result)
        except ValueError as error:assert '__len__() should return >= 0' in str(error)
        else:raise AssertionError('unfixed reproducer unexpectedly succeeded')
    else:
        assert consistent and len(result.data_vars)==0
        repr(result)
        kept=ds.reset_index('x');assert kept._coord_names<=set(kept._variables)
    return dict(status='PASS',phase='unfixed_negative_control' if baseline else 'small_invariant_check',
                coord_names_consistent=consistent,imported_source=xr.__file__,python=sys.executable,
                scope='single reset_index reproducer; not full task evaluation')


def official(inputs):
    assert Path.cwd()==Path('/testbed')
    task=json.loads((inputs/'swebench-task.json').read_text())
    assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()==task['base_commit']
    # The final replay patch and file hashes have already been archived. Restore
    # only test files before applying evaluation tests, as a separate phase.
    names=['xarray/tests/test_dataarray.py','xarray/tests/test_dataset.py','xarray/tests/test_groupby.py']
    subprocess.run(['git','restore','--source=HEAD','--',*names],check=True)
    subprocess.run(['git','apply','--check',str(inputs/'test.patch')],check=True)
    subprocess.run(['git','apply',str(inputs/'test.patch')],check=True)
    digest=lambda:{n:hashlib.sha256(Path(n).read_bytes()).hexdigest() for n in names}
    before=digest();required=json.loads((inputs/'required-tests.json').read_text())
    from xarray6992_test_ids import resolve_test_ids
    truncated=[n for n in required if '[' in n and not n.endswith(']')]
    aliases={};resolved=required
    if truncated:
        functions=sorted({node.split('[',1)[0] for node in truncated})
        env=dict(os.environ,PYTHONPATH='/workspace'+os.pathsep+os.environ.get('PYTHONPATH',''),
                 ASB_TEST_NODE_OUTPUT=str(inputs/'collected-test-ids.json'))
        with (inputs/'collection.log').open('x') as stream:
            collection=subprocess.run(['timeout','--kill-after=10s','30s',sys.executable,'-m','pytest',
                '--collect-only','-q','-p','xarray6992_test_ids',*functions],env=env,stdout=stream,stderr=subprocess.STDOUT)
        assert collection.returncode==0, 'test ID collection failed'
        resolved,aliases=resolve_test_ids(required,json.loads((inputs/'collected-test-ids.json').read_text()))
    (inputs/'resolved-tests.json').write_text(json.dumps(resolved,indent=2)+'\n')
    (inputs/'test-id-aliases.json').write_text(json.dumps(aliases,indent=2)+'\n')
    with (inputs/'official-test.log').open('x') as stream:
        code=subprocess.run(['timeout','--kill-after=10s','120s',sys.executable,'-m','pytest',
               '-rA','--tb=short','-o','junit_family=xunit1','--junitxml='+str(inputs/'official.xml'),
               *resolved],stdout=stream,stderr=subprocess.STDOUT).returncode
    assert digest()==before,'evaluation changed benchmark test files'
    return dict(exit_code=code,test_files_sha256=before,python=sys.executable,
                selected_test_count=len(required),test_id_aliases=aliases,
                source_sha256=hashlib.sha256(Path('xarray/core/dataset.py').read_bytes()).hexdigest())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',action='store_true');parser.add_argument('--official',type=Path)
    args=parser.parse_args()
    print(json.dumps(official(args.official) if args.official else oracle(args.baseline)))
