"""Copy the v0.1 publication allowlist into a new local directory."""
import argparse
import json
from pathlib import Path
import re
import shutil

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=ROOT/'configs/release-v0.1-files.json'
EXCLUDED={'.git','.artifacts','.cache','runtime','AgentENV','IncrementalDAX_moti','__pycache__','AGENTS.md'}
TOKEN_PATTERNS={
    'private-key':r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
    'github-token':r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b',
    'api-key':r'\bsk-[A-Za-z0-9_-]{24,}\b',
    'aws-key':r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'url-credential':r'https?://[^\s/"\x27:@]+:[^\s/"\x27@]+@',
}


def safe_relative(value):
    path=Path(value)
    if path.is_absolute() or '..' in path.parts or not path.parts or set(path.parts)&EXCLUDED:
        raise ValueError('excluded or non-relative publication path: '+value)
    return path


def prepare(output):
    output=Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'.artifacts'):
        raise ValueError('output must be a new directory inside project .artifacts')
    manifest=json.loads(MANIFEST.read_text());seen=set();sources=[];findings=[]
    for row in manifest['files']:
        src=safe_relative(row['source']);dest=safe_relative(row['destination'])
        actual=(ROOT/src).resolve()
        if not actual.is_relative_to(ROOT) or actual!=ROOT/src or not actual.is_file():
            raise ValueError('source must be a regular file without symlink components: '+str(src))
        if str(dest) in seen:raise ValueError('duplicate destination: '+str(dest))
        seen.add(str(dest));data=actual.read_bytes()
        if len(data)>2*1024**2:raise ValueError('unexpected large publication file: '+str(src))
        if actual.suffix not in ('.png',):
            content=data.decode('utf-8')
            for kind,pattern in TOKEN_PATTERNS.items():
                for match in re.finditer(pattern,content):
                    findings.append(dict(file=str(dest),line=content.count('\n',0,match.start())+1,kind=kind))
        sources.append((actual,dest))
    if findings:
        # Never print matched secret values.
        raise ValueError('possible credentials; review file/line only: '+json.dumps(findings))
    output.mkdir(parents=True)
    for source,dest in sources:
        target=output/dest;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    # The exported tree has its own layout (notably RELEASE.md -> README.md).
    # Keep its allowlist usable without the original workspace.
    public_manifest=dict(manifest,files=[dict(source=row['destination'],destination=row['destination']) for row in manifest['files']])
    (output/'configs/release-v0.1-files.json').write_text(json.dumps(public_manifest,indent=2,ensure_ascii=False)+'\n')
    record=dict(status='PASS_ALLOWLIST_EXPORT',files=len(sources),credential_pattern_findings=findings,
        scope='Allowlist, path, size, UTF-8, and known token patterns; not a comprehensive security audit',
        source_mappings=[row for row in manifest['files'] if row['source']!=row['destination']],
        git_operations=False,live_actions=0,project_license=manifest['project_license'])
    (output/'PUBLICATION_CHECK.json').write_text(json.dumps(record,indent=2)+'\n')
    print('status='+record['status']+' result_dir='+str(output)+' files='+str(len(sources)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path)
    prepare(parser.parse_args().output)
