"""State probe: anonymous random memory, open descriptor after unlink, and a live counter."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time

SOCKET='/workspace/asb-checkpoint-probe.sock'
if sys.argv[1]=='serve':
    blob=os.urandom(256*1024);count=0
    path=Path('/workspace/asb-open-file');path.write_text('original open descriptor\n');opened=path.open('rb');path.unlink();path.write_text('replacement pathname\n')
    identity=dict(pid=os.getpid(),start_ticks=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19],boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(SOCKET);server.listen(4);server.settimeout(.1)
        while True:
            count+=1
            try:peer,_=server.accept()
            except socket.timeout:continue
            with peer:
                data=peer.recv(64)
                opened.seek(0)
                value=dict(identity,count=count,anonymous_sha256=hashlib.sha256(blob).hexdigest(),open_fd_sha256=hashlib.sha256(opened.read()).hexdigest(),
                    pathname_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                peer.sendall(json.dumps(value).encode());peer.shutdown(socket.SHUT_WR)
                if data==b'stop':break
else:
    deadline=time.monotonic()+5
    while True:
        try:
            with socket.socket(socket.AF_UNIX) as peer:
                peer.settimeout(3);peer.connect(SOCKET);peer.sendall(sys.argv[1].encode());data=b''
                while chunk:=peer.recv(4096):data+=chunk
            print(data.decode());break
        except (FileNotFoundError,ConnectionRefusedError):
            if time.monotonic()>deadline:raise
            time.sleep(.05)
