"""核对保存的lower P1权限，并用无数据页的独立稀疏文件验证内核隐藏行为。

不启动服务／沙箱，不读取负载内容，不恢复旧卷，不清缓存。
"""
import argparse
from collections import Counter
import ctypes
import csv
import json
import os
from pathlib import Path
import platform
import time

from host_file_monitor import mincore_file, PAGE

ROOT = Path(__file__).resolve().parents[1]
CAP_FOWNER = 3
CAP_DAC_OVERRIDE = 1


def read(path):
    return json.loads(Path(path).read_text())


def credentials():
    fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    return dict(euid=os.geteuid(), uid_fields=fields['Uid'].split(),
        uid_map=Path('/proc/self/uid_map').read_text().split(),
        cap_eff=int(fields['CapEff'],16), cap_prm=int(fields['CapPrm'],16),
        user_namespace=os.readlink('/proc/self/ns/user'))


def drop_caps(numbers):
    class Header(ctypes.Structure):
        _fields_=[('version',ctypes.c_uint32),('pid',ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_=[('effective',ctypes.c_uint32),('permitted',ctypes.c_uint32),('inheritable',ctypes.c_uint32)]
    libc=ctypes.CDLL(None,use_errno=True)
    header=Header(0x20080522,0)
    values=(Data*2)()
    if libc.capget(ctypes.byref(header),values):raise OSError(ctypes.get_errno(),'capget')
    for number in numbers:
        i,bit=divmod(number,32)
        values[i].effective &= ~(1<<bit)
        values[i].permitted &= ~(1<<bit)
        values[i].inheritable &= ~(1<<bit)
    if libc.capset(ctypes.byref(header),values):raise OSError(ctypes.get_errno(),'capset')


def raw_control_query(fd):
    """Unprotected syscall only for this script's tiny, data-free controls.

    Kept outside the production collector so its rejected case remains
    reproducible without providing a bypass in mincore_file.
    """
    info=os.fstat(fd)
    assert info.st_blocks==0 and info.st_size==4*2**20+123
    libc=ctypes.CDLL(None,use_errno=True)
    libc.mmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_long]
    libc.mmap.restype=ctypes.c_void_p
    libc.mincore.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_ubyte)]
    libc.munmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    address=libc.mmap(None,info.st_size,0,1,fd,0)
    if address==ctypes.c_void_p(-1).value:raise OSError(ctypes.get_errno(),'mmap')
    try:
        vector=(ctypes.c_ubyte*((info.st_size+PAGE-1)//PAGE))()
        if libc.mincore(address,info.st_size,vector):raise OSError(ctypes.get_errno(),'mincore')
        resident=sum(bool(v&1) for v in vector)
    finally:
        if libc.munmap(address,info.st_size):raise OSError(ctypes.get_errno(),'munmap')
    last=os.fstat(fd)
    return dict(size=info.st_size,pages=len(vector),resident_pages=resident,
        resident_bytes=resident*PAGE,stable=all(getattr(last,k)==getattr(info,k)
            for k in ('st_dev','st_ino','st_size','st_mtime_ns')))


def query_child(path, fd, drop, label):
    # 仅子进程降能力；不exec、不修改父进程、用户代理或全局配置。
    reader,writer=os.pipe()
    pid=os.fork()
    if pid==0:
        os.close(reader)
        try:
            drop_caps(drop)
            before=credentials()
            started=time.monotonic_ns()
            value=raw_control_query(fd)
            try:
                guarded=dict(status='OBSERVED',query=mincore_file(path,file_descriptor=fd))
            except PermissionError as error:
                guarded=dict(status='REJECTED',error=str(error))
            record=dict(status='PASS',condition=label,credentials=before,
                wall_ns=time.monotonic_ns()-started,query=value,
                guarded_query=guarded,
                owner_uid=os.fstat(fd).st_uid,mode=oct(os.fstat(fd).st_mode&0o777))
            code=0
        except BaseException as error:
            record=dict(status='FAIL',condition=label,error=repr(error));code=1
        with os.fdopen(writer,'w') as stream:json.dump(record,stream)
        os._exit(code)
    os.close(writer)
    with os.fdopen(reader) as stream:value=json.load(stream)
    _,status=os.waitpid(pid,0)
    assert os.waitstatus_to_exitcode(status)==0,value
    return value


def saved_records(source):
    rows=[]
    for r in read(source)['runs_detail']:
        raw=ROOT/r['raw'];metrics=read(raw/'schedule-smoke-metrics.json');task=read(raw/'result.json')
        p=metrics['p1'];files=p['files']
        size=sum(f['size'] for f in files)
        rounded=sum((f['size']+PAGE-1)//PAGE*PAGE for f in files)
        value=p['resident_bytes']
        assert value==sum(f['resident_bytes'] for f in files)
        assert len({(f['device'],f['inode']) for f in files})==len(files)
        post=metrics['expanded_files']['permission_evidence']
        pre=task.get('mincore_permission')
        assert post['euid']==0 and post['uid_map']==['0','0','4294967295']
        assert int(post['cap_eff'],16)&(1<<CAP_FOWNER) and post['host_root_cap_fowner']
        if pre:
            assert pre['euid']==0 and int(pre['cap_eff'],16)&(1<<CAP_FOWNER)
            assert pre['uid_map']==['0','0','4294967295']
        original=[dict(path=f['path'],device=f['device'],inode=f['inode'],size=f['size'],
            resident_bytes=f['resident_bytes'],exists_now=Path(f['path']).exists()) for f in files]
        rows.append(dict(task=r['task'],round=r['round'],schedule=r['schedule'],
            raw=r['raw'],physical_files=len(files),file_bytes=size,page_rounded_bytes=rounded,
            resident_bytes=value,rounding_bytes=rounded-size,
            resident_to_file_ratio=value/size, resident_to_pages_ratio=value/rounded,
            all_resident_vectors=all(f['resident_pages']==f['pages'] for f in files),
            file_before_p1_bytes=p['counters_before']['file'],file_after_p1_bytes=p['counters_after']['file'],
            permission_before_p1=pre,permission_after_p1=post,
            old_volume_exists=(raw.parent/'volume.ext4').exists(),original_files=original))
    assert len(rows)==72
    return rows


def main(source,out,work):
    assert not out.exists() and not work.exists(),'必须使用新的输出和控制文件目录'
    parent=credentials()
    assert parent['euid']==0 and parent['uid_map']==['0','0','4294967295']
    assert parent['cap_eff']&(1<<CAP_FOWNER) and parent['cap_eff']&(1<<CAP_DAC_OVERRIDE)
    records=saved_records(source)
    work.mkdir(parents=True)
    control_size=4*2**20+123
    paths={}
    for name,owner in (('foreign-owner.sparse',65534),('root-owner.sparse',0)):
        path=work/name
        fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
        try:
            os.ftruncate(fd,control_size)
            os.fchown(fd,owner,0)
            os.fchmod(fd,0o444)
            assert os.fstat(fd).st_blocks==0,'control must have no allocated data blocks'
        finally:os.close(fd)
        paths[name]=path
    cases=[('foreign-owner.sparse',[], 'a_root_with_CAP_FOWNER'),
        ('foreign-owner.sparse',[CAP_FOWNER],'b_root_without_CAP_FOWNER_write_override_retained'),
        ('foreign-owner.sparse',[CAP_FOWNER,CAP_DAC_OVERRIDE],'c_nonowner_without_FOWNER_or_write_permission'),
        ('root-owner.sparse',[CAP_FOWNER,CAP_DAC_OVERRIDE],'d_owner_without_FOWNER_or_write_override')]
    queries=[]
    for name,drop,label in cases:
        fd=os.open(paths[name],os.O_RDONLY|os.O_CLOEXEC)
        try:queries.append(query_child(paths[name],fd,drop,label))
        finally:os.close(fd)
    assert [q['query']['resident_bytes'] for q in queries]==[0,0,(control_size+PAGE-1)//PAGE*PAGE,0],queries
    assert [q['guarded_query']['status'] for q in queries]==['OBSERVED','OBSERVED','REJECTED','OBSERVED'],queries
    assert credentials()==parent,'parent credentials changed'
    for path in paths.values():assert path.stat().st_blocks==0
    prettier=next(r for r in records if r['task']=='prettier6604' and r['round']==1 and r['schedule']=='grpo')
    missing=all(not f['exists_now'] for f in prettier['original_files']) and not prettier['old_volume_exists']
    assert missing,'original objects now exist; exact object comparison needs separate handling'
    out.mkdir(parents=True)
    (out/'evidence.json').write_text(json.dumps(dict(status='QUERY_CHECK_COMPLETE_EXACT_HISTORICAL_REPLAY_UNAVAILABLE',
        kernel=platform.release(),version=Path('/proc/version').read_text().strip(),page_size=PAGE,
        parent_credentials=parent,kernel_source=str(Path('/lib/modules')/platform.release()/'build/mm/mincore.c'),
        historical_source=str(source),original_19_query_status='UNAVAILABLE_ORIGINAL_PIDS_AND_SUCCESS_VOLUME_REMOVED',
        control_scope='New ftruncate-only holes; no payload writes/reads, fadvise, prefault, task, mount or service calls',
        controls=queries,historical_runs=records),ensure_ascii=False,indent=2)+'\n')
    with (out/'historical-runs.tsv').open('w') as stream:
        flat=[{k:v for k,v in r.items() if not isinstance(v,(dict,list))} for r in records]
        writer=csv.DictWriter(stream,fieldnames=list(flat[0]),delimiter='\t');writer.writeheader();writer.writerows(flat)
    snippets=[]
    base=ROOT/'.artifacts/schedule-layer-repeats-20261107T084610Z/r1-grpo-schedule-layer-repeats-20261107T084610Z/code'
    targets=[(Path('/lib/modules')/platform.release()/'build/mm/mincore.c',('static inline bool can_do_mincore','if (!can_do_mincore(vma))')),
        (Path('/lib/modules')/platform.release()/'build/fs/inode.c',('bool inode_owner_or_capable(',)),
        (base/'host_file_monitor.py',('def mincore_file(',)),
        (base/'host_schedule_files.py',('def permission_evidence(',)),
        (base/'schedule_smoke_metrics.py',('row = mincore_file(',)),
        (ROOT/'.artifacts/storage-isolated-service/20261107T084731Z-3799106/code/validate_isolated_agentenv.py',('def immutable_manifest(',"call('copy-'+part.replace")),
        (ROOT/'scripts/run_python_schedule.py',("result['mincore_permission'] = permission_evidence()",))]
    for path,needles in targets:
        text=path.read_text().splitlines()
        for needle in needles:
            found=[i for i,line in enumerate(text) if needle in line]
            assert found,(path,needle)
            i=found[0];snippets.append(f'\n### {path}:{i+1}\n')
            snippets.extend(f'{n+1}: {text[n]}' for n in range(max(0,i-2),min(len(text),i+24)))
    (out/'source-excerpts.txt').write_text('\n'.join(snippets)+'\n')
    (out/'REPORT.md').write_text(report(records,queries,source,work))
    print(f'status=COMPLETE controls=4 historical_runs=72 exact_19=UNAVAILABLE output={out}')


def report(records,queries,source,work):
    labels=['root，有CAP_FOWNER','root，仅去掉CAP_FOWNER，仍有写权限绕过能力',
        '非所有者root，去掉CAP_FOWNER及写权限绕过能力','文件所有者root，去掉上述两种能力']
    lines=['# mincore权限与lower P1核查（2026-11-10）','',
        '## 结论：未确认历史P1因权限失效；原19文件的同inode复查无法完成','',
        '**已复现本机内核在权限不足时成功返回全1，但现有72份结果均保存了host root、完整UID映射和CAP_FOWNER证据，不符合“没有权限所以全1”的解释。暂不将任何任务的lower P1判为权限失效。** 原值只可按“指定已准备镜像文件的终点驻留”保留，不能解释为任务新读入量、全部host缓存或部署密度收益。','',
        '**用户要求的原19文件a／b对照未完成：**原PID路径已失效，成功卷已删除，19个原对象均不可访问。没有恢复归档、挂载旧卷、解压镜像或用同内容新inode冒充原对象。下表的真实权限对照使用独立稀疏控制文件，不是对原19文件的重测，也不能重建其历史驻留。','',
        '## 1. 本机权限行为对照','',
        '当前内核为'+platform.release()+'，页大小4096字节。新建两个只读稀疏文件，仅ftruncate设置长度，不写或读取内容、不清缓存；st_blocks在前后均为0。非所有者文件UID=65534、权限0444；所有者对照UID=0、权限0444。每次只在子进程中降低能力，父进程及全局配置保持。查询复用项目mincore_file，预先打开只读FD，mmap(PROT_NONE)+mincore，不触碰映射页。','',
        '| 条件 | CAP_FOWNER | 文件总字节 | 按页取整字节 | 返回驻留字节 | 返回／文件大小 | 判断 |','| --- | --- | ---: | ---: | ---: | ---: | --- |']
    for name,q in zip(labels,queries):
        r=q['query'];size=r['size'];rounded=r['pages']*4096
        cap=bool(q['credentials']['cap_eff']&(1<<CAP_FOWNER))
        verdict='内核隐藏信息，成功返回全1' if q['condition'].startswith('c_') else '真实返回0，未分配数据页'
        lines.append(f'| {name} | {"有" if cap else "无"} | {size:,} | {rounded:,} | {r["resident_bytes"]:,} | {r["resident_bytes"]/size*100:.6f}% | {verdict} |')
    lines+=['',
        '关键点：**只移除CAP_FOWNER不等于无查询权限。** 调用者若仍是文件所有者，或拥有写权限（包括CAP_DAC_OVERRIDE绕过文件模式），内核仍返回实际状态。真正不满足所有者／能力／写权限条件时，mincore不返回EACCES，而成功填全1；本次控制文件实际没有数据页，清楚显示该区别。','',
        '## 2. 内核与项目代码','',
        '本机内核构建目录的mm/mincore.c：can_do_mincore接受inode_owner_or_capable或file_permission(MAY_WRITE)==0；do_mincore在条件不满足时执行memset(vec,1,pages)，以成功返回。fs/inode.c中的inode_owner_or_capable接受文件所有者，或UID可映射且具有CAP_FOWNER的进程。本次实际对照与此分支一致。[源码节选](source-excerpts.txt)保留本机路径和行号；它是本机构建目录源码，不将其当作旧会话内核逐字相同的证明。','',
        '项目现存代码及Prettier该次冻结代码的mincore_file本身**没有在调用前检查CAP_FOWNER**；它对mmap、mincore和munmap的非零错误返回抛出异常，没有在Python里伪造全1的fallback。权限不足时内核返回0，故仅检查系统调用返回值不能识别这种隐藏。','',
        'Prettier旧版本先查P1，再由observe_schedule_files中的permission_evidence校验euid=0、完整UID映射及CAP_FOWNER；不满足会抛PermissionError，整份终点查询变FAIL，不会静默通过。九份记录均通过并保存证据。不过它不是P1调用前的逐对象权限收据，这个前置检查缺口真实存在。冻结的查询路径没有setuid／capset／用户命名空间切换。','',
        '其余七任务的result.json另保存mincore_permission，当前run_python_schedule在调用observe前校验相同条件。72份终点expanded_files也都保存了有效权限证据。只读取这些证据和冻结源码，没有修改采集器或历史报告。','',
        '## 3. 历史读数与文件大小','',
        '以下各取首轮GRPO；19／10／9／7／10／8是该份实际物理文件数。驻留按页计数，文件尾不足4096字节也占一页，因此驻留字节可以略大于逻辑文件字节。','',
        '| 任务 | 文件数 | 文件总字节 | 逐文件按页取整 | 原P1驻留字节 | 比文件总字节多 | 原P1／文件总字节 | 原P1／按页容量 | 权限证据 |','| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    names={'prettier6604':'Prettier #6604','django16819':'Django','c4':'C4','cython':'Cython','matplotlib24177':'Matplotlib','text_editing':'文本编辑'}
    for task,name in names.items():
        r=next(r for r in records if r['task']==task and r['round']==1 and r['schedule']=='grpo')
        lines.append(f'| {name} | {r["physical_files"]} | {r["file_bytes"]:,} | {r["page_rounded_bytes"]:,} | {r["resident_bytes"]:,} | {r["resident_bytes"]-r["file_bytes"]:,} | {r["resident_to_file_ratio"]*100:.6f}% | {r["resident_to_pages_ratio"]*100:.6f}% | host root＋CAP_FOWNER |')
    lines+=['',
        'Prettier首份的5,565,911,040−5,565,854,720=56,320字节，**全部是逐文件尾页取整差**；P1占按页容量正好100%。同期cgroup file为86,470,656字节，P1/file约64.37倍。这个范围／记账差不能单独证明mincore失效，也不能当作已确认组外容量。','',
        '顺带只读核对统一索引全部72份：8种任务的P1都等于各自查询文件的按页总容量，所有文件的resident_pages==pages。72份均有查询后host root＋CAP_FOWNER证据，除Prettier9份外，其余63份还保存查询前权限证据。相同满驻留现象普遍存在，需按准备路径解释，而不能仅靠重复值判低权限。','',
        'Prettier该次冻结准备代码及commands.json确认：创建guest前cp复制下层文件，随后immutable_manifest(dest)对目标文件完整读回做既有校验；copied-input-hashes.json和immutable_cache_copy=PASS保存了结果。这足以说明下层文件在任务前已被复制并读入缓存，故终点全驻留有明确的准备阶段来源；它不能展示任务读取新页的增长。这里仅查已有代码和回执，没有重新计算摘要或读取负载数据。不能仅据此断言全部页在查询时的cgroup归属。','',
        '## 4. 哪些数据能保留','',
        '| 任务 | 本次对权限有效性的判断 | 可保留的含义 |','| --- | --- | --- |']
    for task,name in {**names,'sphinx7590':'Sphinx','xarray6992':'Xarray'}.items():
        phase='旧P1无调用前权限收据，查询后收据及冻结源码支持有效；原inode已不存在' if task=='prettier6604' else '查询前后均保存host root＋CAP_FOWNER；未发现权限失效证据'
        lines.append(f'| {name} | {phase} | 原指定文件终点P1原值保留，不作为任务新增缓存或全部host缓存 |')
    lines+=['',
        '**本次确认必须标为“权限失效”的历史任务：无。** 也不把无法复查的原19文件a／b差值写成“相同”或0；该项明确是不可测。当前证据不支持撤销lower P1列，支持保留其有限范围；若要求原inode权限对照作为强验收条件，Prettier该项验证仍缺，不能写已完成原对象复现。','',
        'current、PSS、工具与任务耗时不依赖mincore返回向量，本次没有发现需要按此假设否定它们的证据。扩展普通文件合计复用了lower P1，继续保持原PARTIAL／UNKNOWN边界，不声称本次消除了全部缓存覆盖问题。','',
        '## 证据与未执行项','',
        '- [完整证据](evidence.json)：4个控制查询的能力／身份及原始向量计数，72份历史权限、文件字节和按页容量、原19对象缺失。',
        '- [逐次历史表](historical-runs.tsv)：72份记录，各行给出原始raw路径；原索引为`'+str(source)+'`。',
        '- [源码节选](source-excerpts.txt)：本机内核和该次冻结采集／准备代码。',
        '- 新的控制文件保留在`'+str(work)+'`，两个稀疏文件各4 MiB＋123字节逻辑大小、0分配数据块；没有删除旧卷、启动服务、修改能力全局配置或改写旧results／evidence。',
        '- 未执行原19对象的有／无能力查询，因为它们已随正常清理消失；没有以新的物理文件替代。','',
        '## 下一步建议（等待确认）','',
        '若继续，先在mincore调用前补齐并记录权限条件，保留旧入口的使用合同；用本次无数据页控制文件验证“低权限不能被采集器接受为真实驻留”。完成条件是调用前证据和失败路径明确，不重跑业务。若必须复查Prettier真实19文件，需要另行确认恢复输入及资源范围；新inode只能验证新查询，不能补写旧时刻P1。历史数值及报告在确认前不修改。']
    if all('guarded_query' in q for q in queries):
        lines=lines[:lines.index('## 下一步建议（等待确认）')]
        lines=[line.replace('查询复用项目mincore_file，预先打开只读FD，mmap(PROT_NONE)+mincore，不触碰映射页。',
                           '先用本脚本限定于无数据页控制文件的原始mincore查询，再用已加权限检查的mincore_file查询同一个只读FD；均使用mmap(PROT_NONE)，不触碰映射页。')
               .replace('项目现存代码及Prettier该次冻结代码的mincore_file本身**没有在调用前检查CAP_FOWNER**；',
                        'Prettier该次冻结代码的mincore_file本身**没有在调用前检查CAP_FOWNER**；')
               .replace('没有修改采集器或历史报告。','没有修改历史报告；本轮采集器修补单列如下。') for line in lines]
        lines+=['','## 本轮调用前权限检查验证','',
            '生产采集器mincore_file现已在mmap前检查固定FD对应文件的权限，并在返回对象中记录mincore_permission。接受完整host UID映射下的CAP_FOWNER、文件所有者，或对固定FD通过内核写权限检查的调用者；无法确认就抛PermissionError，不返回驻留数。没有生产绕过开关。','',
            '| 条件 | 原始系统调用驻留字节 | 修补后采集器 |','| --- | ---: | --- |']
        for name,q in zip(labels,queries):
            guarded=q['guarded_query']
            result=(f'返回 {guarded["query"]["resident_bytes"]:,} 字节；'+guarded['query']['mincore_permission']['basis']
                    if guarded['status']=='OBSERVED' else '拒绝：'+guarded['error'])
            lines.append(f'| {name} | {q["query"]["resident_bytes"]:,} | {result} |')
        lines+=['','本节完成调用前检查和低权限控制验证，不是新业务实验，不追补任何历史P1；原19文件同inode复查仍未完成。','',
            '## 下一步建议（等待确认）','',
            '后续获准运行任务时，在首份正常结果中核对每个文件的mincore_permission及原有身份和完整性检查，不为本修补另加业务轮次。若要重查原19文件，仍需先明确可用对象；新inode不能补写旧时刻P1。']
    return '\n'.join(lines)+'\n'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'results/eight-task-schedules-20261110/summary.json')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--controls',type=Path,required=True)
    args=parser.parse_args()
    main(args.source,args.output,args.controls)
