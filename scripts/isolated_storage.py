"""Owned, fully allocated ext4 fixture. Never formats or remounts existing devices."""
import json
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import subprocess

GIB = 2**30
HOST_RESERVE = 20*GIB
HOST_MARGIN = 6*GIB


def available(path):
    st = os.statvfs(path)
    return st.f_bavail*st.f_frsize


def capacity_budget(free, capacity):
    if capacity <= 0 or capacity % GIB:
        raise ValueError('capacity must be a positive whole GiB')
    required = capacity+HOST_RESERVE+HOST_MARGIN
    return dict(ready=free > required, available_bytes=free,
                required_available_bytes=required, capacity_bytes=capacity,
                host_reserve_bytes=HOST_RESERVE, host_margin_bytes=HOST_MARGIN)


def mount_identity(mountpoint, device):
    rows = json.loads(subprocess.check_output(
        ['findmnt','-J','-M',str(mountpoint),'-o','TARGET,SOURCE,FSTYPE'],timeout=10))['filesystems']
    if len(rows) != 1:
        raise ValueError('expected exactly one owned mount')
    row = rows[0]
    if Path(row['target']).resolve() != Path(mountpoint).resolve() or row['source'] != device or row['fstype'] != 'ext4':
        raise ValueError('mount identity mismatch; never fall back to the parent filesystem')
    if Path(mountpoint).stat().st_dev == Path(mountpoint).parent.stat().st_dev:
        raise ValueError('owned mount is not a separate filesystem')
    return row


def write_mounts(mountpoint):
    base = Path(mountpoint)
    return {str(base/'env'):'/workspace/env', str(base/'run'):'/run', str(base/'tmp'):'/tmp'}


def verify_container_boundary(info, mountpoint, device):
    mount_identity(mountpoint, device)
    if not info['HostConfig']['ReadonlyRootfs'] or info['HostConfig']['LogConfig']['Type'] != 'none':
        raise ValueError('container root or daemon logging permits unbounded bulk writes')
    actual = {m['Destination']:m for m in info['Mounts']}
    for source, destination in write_mounts(mountpoint).items():
        row = actual.get(destination)
        if not row or row['Type'] != 'bind' or row['Source'] != source or not row['RW']:
            raise ValueError('missing owned write mount: '+destination)
    allowed = {'/dev','/workspace/env','/run','/tmp','/workspace/config/default.toml'}
    if any(m['RW'] and m['Destination'] not in allowed for m in info['Mounts']):
        raise ValueError('unexpected writable container mount')
    return dict(readonly_root=True, daemon_log_disabled=True, write_mounts=write_mounts(mountpoint),
                exclusions=['privileged device operations', 'bounded Docker container/network metadata',
                            'small host control receipts', 'other users and the existing service'])


def confirmed_window(start, end, now=None):
    begin=datetime.fromisoformat(start)
    finish=datetime.fromisoformat(end)
    now=now or datetime.now(timezone.utc)
    if begin.tzinfo is None or finish.tzinfo is None or now.tzinfo is None:
        raise ValueError('window timestamps require a timezone')
    duration=(finish-begin).total_seconds()
    if not 0 < duration <= 1200:
        raise ValueError('window must be no longer than 20 minutes')
    if not begin <= now < finish:
        raise ValueError('confirmed window is not currently active; never renew automatically')
    return dict(status='USER_CONFIRMED',start_utc=begin.isoformat(),end_utc=finish.isoformat(),
                duration_seconds=duration,scope='no other large shared-disk writes; not a global writer lock')


def execution_window(start=None,end=None,now=None,automatic=False):
    """Use standing authorization; timestamps bound runtime, not other writers."""
    now=now or datetime.now(timezone.utc)
    if bool(start)!=bool(end):raise ValueError('provide both runtime start/end timestamps or neither')
    if not start:
        start=now.isoformat();end=(now+timedelta(minutes=20)).isoformat();automatic=True
    value=confirmed_window(start,end,now)
    if automatic:
        value.update(status='STANDING_USER_AUTHORIZATION',scope='bounded runtime only; no exclusive host or shared-disk window promised')
    return value


class Boundary:
    """Trusted experiment path validation, not a security boundary against hostile root."""
    def __init__(self, path, directory=None, project_root=None):
        root=Path(project_root or Path(__file__).resolve().parents[1]).resolve()
        path=Path(path)
        if path.is_symlink() or path.resolve()!=path or path.parent.parent!=root/'.artifacts/storage-isolated-trenvx':
            raise ValueError('boundary manifest must belong to a new owned TrEnv-X fixture')
        value=json.loads(path.read_text())
        required={'version','workspace','mountpoint','device','image','capacity_bytes','image_device','image_inode',
                  'initial_host_available_bytes','raw_root','volume_reserve_bytes','window'}
        if set(value)!=required or value['version']!=1 or value['workspace']!=str(root):
            raise ValueError('invalid boundary manifest')
        self.root=root
        self.mountpoint=Path(value['mountpoint'])
        self.image=Path(value['image'])
        self.raw_root=Path(value['raw_root'])
        self.device=value['device']
        self.volume_reserve_bytes=value['volume_reserve_bytes']
        self.initial_host_available_bytes=value['initial_host_available_bytes']
        if (self.mountpoint!=path.parent/'mount' or self.image!=path.parent/'volume.ext4' or
                self.raw_root!=self.mountpoint/'trenvx-runs' or not self.raw_root.is_dir()):
            raise ValueError('unexpected owned fixture paths')
        if directory is not None and not Path(directory).resolve().is_relative_to(self.raw_root):
            raise ValueError('writer directory is outside the owned volume')
        if self.volume_reserve_bytes!=512*2**20 or self.initial_host_available_bytes<=60*GIB:
            raise ValueError('original host launch budget or cleanup reserve not satisfied')
        if not capacity_budget(self.initial_host_available_bytes,value['capacity_bytes'])['ready']:
            raise ValueError('capacity exceeds initial host budget')
        confirmed_window(value['window']['start_utc'],value['window']['end_utc'])
        mount_identity(self.mountpoint,self.device)
        self.volume_device=self.mountpoint.stat().st_dev
        if self.volume_device!=Path(self.device).stat().st_rdev:
            raise ValueError('loop device does not own this filesystem')
        self.image_identity=(value['image_device'],value['image_inode'])
        self.capacity_bytes=value['capacity_bytes']
        s=self.image.lstat()
        if (self.image.is_symlink() or (s.st_dev,s.st_ino)!=self.image_identity or
                s.st_size!=self.capacity_bytes or s.st_blocks*512<self.capacity_bytes):
            raise ValueError('fixed backing file changed or became sparse')
        attached=subprocess.check_output(['losetup','-j',str(self.image)],timeout=10).decode()
        if not attached.startswith(self.device+':'):
            raise ValueError('loop backing file identity mismatch')
        self.value=value

    def check(self, *, volume_headroom=0, allow_expired=False):
        if not allow_expired:
            confirmed_window(self.value['window']['start_utc'],self.value['window']['end_utc'])
        if self.mountpoint.stat().st_dev!=self.volume_device or self.raw_root.stat().st_dev!=self.volume_device:
            raise ValueError('owned mount disappeared; refusing parent-filesystem fallback')
        s=self.image.lstat()
        if ((s.st_dev,s.st_ino)!=self.image_identity or s.st_size!=self.capacity_bytes or s.st_blocks*512<self.capacity_bytes):
            raise ValueError('backing allocation changed during run')
        if available(self.root)<=HOST_RESERVE:
            raise RuntimeError('host disk reserve reached')
        if available(self.mountpoint)<=self.volume_reserve_bytes+volume_headroom:
            raise RuntimeError('owned volume cleanup reserve reached')
        return available(self.root)

    def host_free(self):
        return self.check()


def configured_boundary(directory=None):
    manifest=os.environ.get('ASB_STORAGE_BOUNDARY')
    return Boundary(manifest,directory) if manifest else None
