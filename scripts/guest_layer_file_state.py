"""Check explicit file origins in the pinned one-layer TrEnv-X guest."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


def metadata(path):
    st=path.stat()
    assert stat.S_ISREG(st.st_mode) and not path.is_symlink(), 'non-regular candidate'
    return {key:getattr(st,key) for key in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')}


def absent_shadow(root,path,allow_file=False):
    target=root/str(path).lstrip('/')
    chain=[root];candidate=root
    for part in path.parts[1:]:candidate=candidate/part;chain.append(candidate)
    for candidate in chain:
        if not candidate.exists() and not candidate.is_symlink():continue
        st=candidate.lstat()
        if not (candidate==target and allow_file and stat.S_ISREG(st.st_mode)):
            if candidate==target or not stat.S_ISDIR(st.st_mode):
                raise RuntimeError('upper/lower shadow or unsupported ancestor: '+str(candidate))
        for name in os.listxattr(candidate):
            # Linux v6.1.134 uses impure=y for readdir inode-number handling;
            # it is not an opaque directory or redirected file lookup.
            if name in ('trusted.overlay.impure','user.overlay.impure') and stat.S_ISDIR(st.st_mode):
                if os.getxattr(candidate,name)==b'y':continue
            if name.startswith(('trusted.overlay.','user.overlay.')) and not name.endswith('.origin'):
                raise RuntimeError('unsupported overlay attribute '+name+' on '+str(candidate))


def observe(cfg,content=False):
    base=Path('/rom');upper=base/'overlay/root';delta=base/'overlay/dax-lowers/1/delta'
    assert upper.is_dir() and delta.is_dir(),'pinned guest layer layout unavailable'
    rows=[]
    for item in cfg['files']:
        path=Path(item['path']);assert str(path).startswith('/testbed/') and '..' not in path.parts
        assert item['origin'] in ('checkpoint','base')
        absent_shadow(upper,path)
        if item['origin']=='base':absent_shadow(delta,path)
        candidate=(delta if item['origin']=='checkpoint' else base)/str(path).lstrip('/')
        absent_shadow(delta if item['origin']=='checkpoint' else base,path,allow_file=True)
        visible=metadata(path);underlying=metadata(candidate)
        assert visible['st_size']==underlying['st_size'] and visible['st_mtime_ns']==underlying['st_mtime_ns']
        row=dict(path=str(path),origin=item['origin'],candidate=str(candidate),visible=visible,underlying=underlying)
        if content:
            row['visible_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            row['candidate_sha256']=hashlib.sha256(candidate.read_bytes()).hexdigest()
            assert row['visible_sha256']==row['candidate_sha256'],'merged file differs from declared layer'
            assert metadata(path)==visible and metadata(candidate)==underlying
        rows.append(row)
    return dict(status='PASS',files=rows,content_reads=content,
                scope='one sealed layer; reject shadows and unsupported overlay metadata; no generic layer resolution')


if __name__=='__main__':
    cfg=json.loads(Path(sys.argv[1]).read_text());print(json.dumps(observe(cfg,content=len(sys.argv)>2),indent=2))
