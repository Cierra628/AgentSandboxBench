"""Plot observed host timestamps only, never interpolate a cache or PSS gap."""
import csv,sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
def rows(p):
    return list(csv.DictReader(p.open())) if p.exists() else []
p=Path(sys.argv[1]);pss=rows(p/'pss.csv');points=[r for r in rows(p/'points.csv') if r['status']=='OBSERVED']
roles=[r['role'] for r in rows(p/'roles.csv')]
if not pss:sys.exit('No per-role PSS samples')
t0=min(int(r['time_ns']) for r in pss)
fig,axes=plt.subplots(len(roles),2,figsize=(11,12),sharex=True,layout='constrained')
for role,(ax,bx) in zip(roles,axes):
    v=[r for r in pss if r['role']==role]
    ax.plot([(int(r['time_ns'])-t0)/1e9 for r in v],[float(r['pss_kib'])/1024 for r in v],'.',markersize=2,color='#13676a')
    w=[r for r in points if r['role']==role]
    bx.scatter([(int(r['read_start_ns'])-t0)/1e9 for r in w],[int(r['confirmed_pages']) for r in w],s=12,color='#aa4f1f')
    if not w:bx.text(.5,.5,'No completed action point',ha='center',transform=bx.transAxes)
    ax.set_ylabel(role)
    for a in (ax,bx):a.grid(alpha=.18)
axes[0,0].set_title('VMM PSS (MiB)');axes[0,1].set_title('Confirmed resident cache (physical pages)')
fig.supxlabel('Host monotonic time since first PSS sample (s)')
fig.savefig(p/'curves.png',dpi=150)
