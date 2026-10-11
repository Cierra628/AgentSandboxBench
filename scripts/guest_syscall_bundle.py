"""Bundle the existing host strace and its libraries for a private guest directory."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def prepare(directory):
    directory=Path(directory);directory.mkdir()
    binary=Path('/usr/bin/strace');assert binary.is_file()
    deps=subprocess.check_output(['ldd',str(binary)],text=True,timeout=10)
    assert 'not found' not in deps
    files={binary.name:binary}
    for line in deps.splitlines():
        match=re.search(r'(?:=>\s*)?(/[^\s]+)',line)
        if match:
            path=Path(match[1]);files[path.name]=path
    assert 'ld-linux-x86-64.so.2' in files
    for name,path in files.items():shutil.copy2(path,directory/name)
    wrapper=directory/'run-strace'
    wrapper.write_text('''#!/bin/sh
set -eu
directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$directory/ld-linux-x86-64.so.2" --library-path "$directory" "$directory/strace" "$@"
''');wrapper.chmod(0o755)
    version=subprocess.check_output([str(wrapper),'--version'],text=True,timeout=10).splitlines()[0]
    manifest=dict(version=version,scope='private diagnostic tool copied from host; no package or image installation',files={p.name:dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in directory.iterdir()})
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
