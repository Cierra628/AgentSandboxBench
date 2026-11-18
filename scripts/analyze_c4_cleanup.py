"""Read saved C4 service/daemon logs; no live services, devices or workload reads."""
import argparse,json,re
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
ANSI=re.compile(r'\x1b\[[0-9;]*m')
def records(path):
    for number,line in enumerate(path.read_text().splitlines(),1):
        text=ANSI.sub('',line)
        if not re.match(r'^\d{4}-\d\d-\d\dT',text):continue
        token=text.split()[0]
        yield dict(line=number,time=token,timestamp=datetime.fromisoformat(token),text=text)

def analyze(storage):
    server=list(records(storage/'service-logs/server.log'));daemon=list(records(storage/'service-logs/ublk-daemon.log'))
    starts=[r for r in server if 'deleting sandbox sandbox_id=' in r['text']];ends=[r for r in server if 'sandbox deleted sandbox_id=' in r['text']]
    assert len(starts)==len(ends)==2
    first=min(r['timestamp'] for r in starts);last=max(r['timestamp'] for r in ends)
    calls=[]
    for start in starts:
        sid=start['text'].split('sandbox_id=')[-1].strip();end=next(r for r in ends if r['text'].endswith(sid))
        calls.append(dict(sandbox_id=sid,start=start['time'],end=end['time'],seconds=(end['timestamp']-start['timestamp']).total_seconds(),start_line=start['line'],end_line=end['line']))
    devices=[]
    for start in daemon:
        if 'deleting device dev_id=' not in start['text'] or not first<=start['timestamp']<=last:continue
        dev=start['text'].split('dev_id=')[-1].strip();end=next(r for r in daemon if r['timestamp']>=start['timestamp'] and 'device deleted dev_id='+dev==r['text'].split(': ')[-1])
        devices.append(dict(device_id=int(dev),start=start['time'],end=end['time'],seconds=(end['timestamp']-start['timestamp']).total_seconds(),start_line=start['line'],end_line=end['line']))
    assert len(devices)==2
    last_device=max(datetime.fromisoformat(r['end']) for r in devices)
    return dict(storage=str(storage),sandbox_deletes=calls,device_deletes_during_sandbox_requests=devices,
        group_delete_window_seconds=(last-first).total_seconds(),
        first_delete_to_last_device_seconds=(last_device-first).total_seconds(),
        last_device_to_first_sandbox_done_seconds=(min(r['timestamp'] for r in ends)-last_device).total_seconds(),
        last_device_to_last_sandbox_done_seconds=(last-last_device).total_seconds())

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    values={mode:analyze(ROOT/'.artifacts/storage-isolated-service'/name) for mode,name in [('off','20261105T135552Z-3096987'),('on','20261105T135709Z-3099356')]}
    (args.output/'timeline.json').write_text(json.dumps(values,ensure_ascii=False,indent=2)+'\n')
    print('status=PASS scope=saved-logs-only result_dir='+str(args.output))
if __name__=='__main__':main()
