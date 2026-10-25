"""Export numeric metadata excerpts, never task commands, payloads, or live state."""
import argparse
import json
from pathlib import Path

from report_host_copy_summary import ROOT,SOURCES,read,summarize


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def query(value):
    rows=[]
    for index,row in enumerate(value['objects']):
        fields={key:row[key] for key in ('relative','identity','users','query_elapsed_ns','query_cpu_ns')}
        fields['path']='REDACTED/object-'+str(index)
        if 'step' in row:fields['step']=row['step']
        fields['residency']={key:row['residency'][key] for key in ('resident_pages','resident_bytes','pages','mapping_length','stable')}
        rows.append(fields)
    return {key:value[key] for key in ('status','label','elapsed_ns','cpu_ns','resident_bytes')}|dict(objects=rows)


def export(output):
    output=Path(output).resolve();assert output.is_relative_to(ROOT) and not output.exists()
    originals=summarize(SOURCES['grpo'],SOURCES['bpo'])
    output.mkdir(parents=True)
    provenance={}
    for schedule,source_path in SOURCES.items():
        source=read(source_path);groups={}
        for mode,group in source['groups'].items():
            raw=ROOT/group['storage']/'prettier6604';task=read(raw/'result.json');obs=read(raw/'host-full-rootfs.json')
            dest=output/'records'/(schedule+'-'+mode)
            task_public={key:task[key] for key in ('status','schedule','actual_action_count')}
            task_public['paths']={role:{key:p[key] for key in ('action_count','official_task_status','official_passed','official_failed')} for role,p in task['paths'].items()}
            public={key:obs[key] for key in ('profile','mode','schedule','status','pss')}
            public.update(P0=query(obs['P0']),P1=query(obs['P1']))
            if schedule=='bpo':public['generated_layers']=dict(P0=obs['generated_layers']['P0'],P1=query(obs['generated_layers']['P1']))
            write(dest/'prettier6604/result.json',task_public);write(dest/'prettier6604/host-full-rootfs.json',public)
            groups[mode]=dict(storage=str(dest.relative_to(ROOT)),P0_resident_bytes=obs['P0']['resident_bytes'],P1_resident_bytes=obs['P1']['resident_bytes'])
            provenance[schedule+'-'+mode]=dict(original_storage=group['storage'],metadata_only=True)
        write(output/(schedule+'.json'),{key:source[key] for key in ('status','schedule','actual_actions','pair','comparison')}|dict(groups=groups))
    exported=summarize(output/'grpo.json',output/'bpo.json')
    for a,b in zip(originals['schedules'],exported['schedules']):
        for key in ('P0_difference_bytes','P1_difference_bytes','runtime_difference_change_bytes','reduction_fraction','actual_actions'):assert a[key]==b[key]
        for mode in ('shared','private'):
            for key in ('P0','P1','objects','users_per_object','main_query_ns','total_query_ns','generated_layers'):assert a['groups'][mode][key]==b['groups'][mode][key]
    write(output/'provenance.json',dict(status='EXACT_NUMERIC_EXCERPT',sources=provenance,
        retained='Observed values, page counts, identities, users, task outcomes, and query costs',
        removed='Absolute paths, process IDs, live bindings, task commands/stdout/patches, state databases, and payloads',
        original_pair_paths='Historical provenance only; full private workflow directories are not bundled'))
    print('status=PASS_NUMERIC_EXCERPT output='+str(output))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    export(parser.parse_args().output)
