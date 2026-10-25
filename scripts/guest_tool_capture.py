"""Execute a tool call with exact cwd/environment and emit its raw result."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

request=json.loads(base64.b64decode(sys.argv[1]))
if request['kind']=='state':
    result={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).is_file() else None for p in request['files']}
    if request.get('repo'):
        repo=request['repo']
        def git(*args):return subprocess.check_output(['git','-C',repo,*args])
        names=git('diff','--name-only','HEAD','-z').split(b'\0')+git('ls-files','--others','--exclude-standard','-z').split(b'\0')
        changed={}
        for name in sorted(set(names)-{b''}):
            p=Path(repo)/os.fsdecode(name)
            changed[os.fsdecode(name)]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'mode':p.stat().st_mode & 0o777} if p.is_file() else {'missing':True}
        result['repository']={'head':git('rev-parse','HEAD').decode().strip(),'status':git('status','--porcelain').decode(),
            'patch_b64':base64.b64encode(git('diff','--binary','HEAD')).decode(),'changed_files':changed}
else:
    started=time.monotonic_ns()
    process=subprocess.run(['/bin/bash','--noprofile','--norc','-c',request['command']],cwd=request['cwd'],env=request['env'],capture_output=True,timeout=request['timeout_seconds'])
    result={'exit_code':process.returncode,'stdout_b64':base64.b64encode(process.stdout).decode(),'stderr_b64':base64.b64encode(process.stderr).decode(),
            'guest_elapsed_ns':time.monotonic_ns()-started,'cwd':request['cwd'],'env':request['env']}
print(json.dumps(result))
