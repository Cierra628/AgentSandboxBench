"""Plot saved initial-cache comparison results; never query a live guest."""
import argparse
import json
from pathlib import Path


def plot(summary,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    data=json.loads(summary.read_text());assert data['status']=='PASS'
    output.mkdir(parents=True,exist_ok=False)
    fig,axes=plt.subplots(1,3,figsize=(12,3.8))
    metrics=[('task_start_guest_cache_bytes',2**20,'Initial guest file cache (MiB)'),
             ('tool_sum_ns',1e9,'Suffix tool time (s)'),
             ('virtio_read_bytes',2**20,'Guest virtio reads (MiB)')]
    colors=['#23619b','#d6742b']
    for ax,(metric,scale,title) in zip(axes,metrics):
        for offset,role,color in zip([-.18,.18],['early_1','early_2'],colors):
            values=[run['roles'][role][metric]/scale for run in data['runs']]
            bars=ax.bar([i+offset for i in range(4)],values,width=.34,label=role,color=color)
            ax.bar_label(bars,fmt='%.2f',padding=3,fontsize=8,rotation=90)
        ax.set_xticks(range(4),['1: keep','2: drop','3: drop','4: keep'])
        ax.set_title(title,fontsize=11);ax.spines[['top','right']].set_visible(False)
        ax.set_ylim(0,ax.get_ylim()[1]*1.18)
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='lower center',ncol=2,frameon=False)
    fig.suptitle('Same task, different initial guest cache; two runs per condition',fontsize=12)
    fig.tight_layout(rect=[0,.08,1,1])
    fig.savefig(output/'initial-cache.png',dpi=160);fig.savefig(output/'initial-cache.svg');plt.close(fig)
    return output/'initial-cache.png'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(plot(args.summary,args.output))
