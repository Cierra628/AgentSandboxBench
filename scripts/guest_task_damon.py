"""Temporarily disable DAMON in one owned 6.1.175 guest, then restore its setting."""
import argparse
import json
import os
from pathlib import Path
import time


def snapshot(parameters):
    value={name:(parameters/name).read_text().strip() for name in
           ('enabled','kdamond_pid','bytes_reclaim_tried_regions','bytes_reclaimed_regions')}
    assert value['enabled'] in ('Y','N')
    return dict(value,monotonic_ns=time.monotonic_ns())


def switch(parameters,enabled):
    assert enabled in ('Y','N')
    (parameters/'enabled').write_text(enabled+'\n')
    end=time.monotonic()+10
    while True:
        value=snapshot(parameters);pid=int(value['kdamond_pid'])
        if value['enabled']==enabled and (pid==-1 if enabled=='N' else pid>0):return value
        if time.monotonic()>=end:raise TimeoutError('owned guest DAMON thread did not reach requested state')
        time.sleep(.05)


def prepare(parameters,state,boot_id):
    before=snapshot(parameters)
    # Keep the original state immutable, including when disabling later fails.
    with state.open('x') as stream:json.dump(dict(boot_id=boot_id,before=before),stream)
    after=switch(parameters,'N')
    return dict(status='PASS',action='prepare',before=before,after=after)


def restore(parameters,state,boot_id):
    original=json.loads(state.read_text());assert original['boot_id']==boot_id,'different guest boot'
    before=snapshot(parameters);after=switch(parameters,original['before']['enabled'])
    return dict(status='PASS',action='restore',original_enabled=original['before']['enabled'],before=before,after=after)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','restore'))
    args=parser.parse_args()
    assert os.uname().release=='6.1.175','this write-capable helper is for the verified guest kernel only'
    state=Path('/workspace/asb-task-damon.json')
    parameters=Path('/sys/module/damon_reclaim/parameters')
    boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    print(json.dumps((prepare if args.action=='prepare' else restore)(parameters,state,boot_id)))
