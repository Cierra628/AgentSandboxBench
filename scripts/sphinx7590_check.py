"""Guest-only Sphinx reproducer and selected tests; never install dependencies."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

FILES=('sphinx/domains/cpp.py','sphinx/util/cfamily.py')

def hashes():return {n:hashlib.sha256(Path(n).read_bytes()).hexdigest() for n in FILES}

def oracle(baseline):
    import sphinx.domains.cpp as cpp
    from sphinx.util.cfamily import DefinitionError
    assert Path(cpp.__file__).resolve()==Path('/testbed/sphinx/domains/cpp.py')
    class Config:
        cpp_id_attributes=['id_attr'];cpp_paren_attributes=['paren_attr']
    values=['1q_s','6.62607015e-34q_J','42_udl','1.0_km','0x1p3_udl',"'a'_udl",'"str"_udl']
    observed=[]
    for text in values:
        parser=cpp.DefinitionParser(text,location=None,config=Config());parser.allowFallbackExpressionParsing=False
        try:
            ast=parser.parse_expression();parser.assert_end()
            assert str(ast)==text;observed.append(dict(input=text,status='PASS',id2=ast.get_id(2)))
        except DefinitionError:
            assert baseline,'repaired frozen source cannot parse original UDL case'
            observed.append(dict(input=text,status='EXPECTED_FAILURE'))
    assert all(r['status']==('EXPECTED_FAILURE' if baseline else 'PASS') for r in observed)
    return dict(status='PASS',imported_source=cpp.__file__,cases=observed,
        scope='seven original UDL inputs, round-trip and ID generation; not full task correctness')

def official(inputs):
    assert Path.cwd()==Path('/testbed')
    task=json.loads((inputs/'swebench-task.json').read_text());assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()==task['base_commit']
    before=hashes()
    subprocess.run(['git','restore','--source=HEAD','--','tests/test_domain_cpp.py'],check=True)
    subprocess.run(['git','apply','--check',str(inputs/'test.patch')],check=True)
    subprocess.run(['git','apply',str(inputs/'test.patch')],check=True)
    required=json.loads((inputs/'required-tests.json').read_text());assert len(required)==len(set(required))==25
    with (inputs/'official-test.log').open('x') as log:
        p=subprocess.run(['timeout','--kill-after=10s','120s',sys.executable,'-m','pytest','-q','-o','junit_family=legacy',
            '--junitxml='+str(inputs/'official.xml'),*required],stdout=log,stderr=subprocess.STDOUT)
    assert hashes()==before,'evaluation modified source files'
    return dict(exit_code=p.returncode,source_sha256=before,test_id_aliases={},scope='all 25 selected original tests; no gold source or dependencies installed')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--baseline',action='store_true');parser.add_argument('--official',type=Path)
    args=parser.parse_args();print(json.dumps(official(args.official) if args.official else oracle(args.baseline)))
