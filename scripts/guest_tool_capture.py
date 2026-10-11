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
else:
    started=time.monotonic_ns()
    process=subprocess.run(['/bin/bash','--noprofile','--norc','-c',request['command']],cwd=request['cwd'],env=request['env'],capture_output=True,timeout=request['timeout_seconds'])
    result={'exit_code':process.returncode,'stdout_b64':base64.b64encode(process.stdout).decode(),'stderr_b64':base64.b64encode(process.stderr).decode(),
            'guest_elapsed_ns':time.monotonic_ns()-started,'cwd':request['cwd'],'env':request['env']}
print(json.dumps(result))
