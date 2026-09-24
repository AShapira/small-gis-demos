"""Resumable host controller. Only benchmark-owned resources are changed."""
from __future__ import annotations

import contextlib
import itertools
import json
import random
import subprocess
import time
from pathlib import Path

from .common import fingerprint, read_json, utc, write_json
from .runtime import Runtime


def jobs(c,variants,stage):
    study=c['study']; profiles=study['profiles']
    if stage=='screening': profiles=[p for p in profiles if p['id']==study['screening_profile']]
    n=study[stage+'_repetitions']; users=study[stage+'_users']
    output=[]
    for rep in range(n):
        # Balanced blocks: repeat ordering changes, every candidate sees each workload cell.
        for profile,count,scenario in itertools.product(profiles,users,study['scenarios']):
            order=list(variants); random.Random(c['workload']['seed']+rep*97+count).shuffle(order)
            for v in order:
                j={'stage':stage,'variant':v,'profile':profile,'users':count,'scenario':scenario,'repetition':rep,
                   'output_format':'image/png'}
                j['id']=stage[:3]+'-'+fingerprint(j)[:16]; output.append(j)
    return output


def estimate(c,compatible=None):
    count=len(compatible) if compatible is not None else len(c['variants'])
    screening=count*len(c['study']['screening_users'])*len(c['study']['scenarios'])*c['study']['screening_repetitions']
    capacity=3*len(c['study']['capacity_users'])*len(c['study']['scenarios'])*c['study']['capacity_repetitions']*len(c['study']['profiles'])
    extra=set(c['workload']['user_levels'])-set(c['study']['screening_users'])-set(c['study']['capacity_users'])
    browsing=3*len(extra)*len(c['study']['scenarios'])*c['study']['capacity_repetitions']
    seconds=(screening+capacity+browsing)*(c['workload']['warmup_seconds']+c['workload']['measurement_seconds'])
    return {'screening_cells':screening,'capacity_cells_max':capacity,'measurement_and_warmup_hours':seconds/3600,
            'additional_browsing_cells_max':browsing,
            'additional_time':'downloads, conversions, validation, cache seeding, first access, delivery and restarts',
            'provisional_disk_gb':c['runtime']['disk_budget_gb']}


@contextlib.contextmanager
def lock(path):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    f=path.open('a+')
    try:
        try:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except ImportError:
            import errno
            import msvcrt
            f.seek(0); f.write(' '); f.flush(); f.seek(0)
            try:
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as e:
                if e.errno in (errno.EACCES,errno.EAGAIN,errno.EDEADLK):
                    raise RuntimeError('A controller is already running for this study') from e
                raise
        except BlockingIOError:
            raise RuntimeError('A controller is already running for this study')
        yield
    finally:
        f.close()


def probe_matrix(r):
    probe_root=r.root+'/probes'
    r.action('fixture',root=probe_root)
    # Conversion failure is a compatibility outcome for a probe, not a swallowed full-data failure.
    for v in r.c['variants']:
        try: r.action('convert','--variant',v['id'],root=probe_root)
        except RuntimeError as e: print(str(e),flush=True)
    r.action('probe',root=probe_root)
    compatibility=r.read('compatibility.json',root=probe_root)
    r.put(r.worker,r.root+'/compatibility.json',json.dumps(compatibility).encode())
    delivery=r.read('delivery-compatibility.json',root=probe_root)
    r.put(r.worker,r.root+'/delivery-compatibility.json',json.dumps(delivery).encode())
    required={'none','lzw','deflate','packbits','jpeg70','jpeg80'}
    failed=[v for v in required if not compatibility.get(v,{}).get('supported')]
    if failed: raise RuntimeError('Required GeoServer compatibility failed: '+', '.join(failed))
    extension=read_json(r.repo/'infra/extensions.lock.json',{})
    if not extension.get('image_id'): raise RuntimeError('Build version-matched WebP profile using scripts/build_webp.py')
    profile=next(p for p in r.c['study']['profiles'] if p['id']==r.c['study']['screening_profile'])
    r.server_start(profile,restart=True,server_profile='webp')
    r.action('probe','--job','webp',root=probe_root)
    for name in ('compatibility-webp','delivery-compatibility-webp'):
        r.put(r.worker,r.root+'/'+name+'.json',json.dumps(r.read(name+'.json',root=probe_root)).encode())
    r.server_start(profile,restart=True)
    return compatibility


def run_cell(r,job,root=None,config_path='/data/config.json'):
    from .telemetry import server_sample,summarize
    root=root or r.root
    code_manifest=r.read('code-manifest.json',{})
    previous=r.read('runs/'+job['id']+'/summary.json',root=root)
    if previous and previous.get('controller_complete'): return previous
    profile=job['profile']
    if root==r.root:
        state=read_json(r.local/'state.json',{})
        if state and not job.get('plan_id'):
            state.update(cell=job,updated_at=utc());write_json(r.local/'state.json',state)
    loader_image=json.loads(r.call('image','inspect',r.r['worker_image']))[0]['Id']
    # A fresh process for each cell makes profile and first-access attribution explicit.
    r.server_start(profile,restart=True,server_profile=job.get('server_profile','vanilla'))
    job_path=root+'/jobs/'+job['id']+'.json'
    r.put(r.worker,job_path,json.dumps(job).encode())
    args=['run','--rm','--name',r.loader,*r.label(),'--network',r.network,
          '--cpus',str(r.r['load_cpus']),'--memory',str(r.r['load_memory_gib'])+'g',
          '--volume',r.volume+':/data','--workdir','/data/runtime',
          '--secret',f'{r.prefix}-admin,target=admin_password,mode=0400',
          '--cap-drop=all','--security-opt=no-new-privileges','--entrypoint','python',r.r['worker_image'],
          '-m','rasterbench.worker','load','--config',config_path,'--root',root,'--job',job_path]
    # Worker code is snapshotted into the data volume and used by load containers.
    args[args.index('--cap-drop=all'):args.index('--cap-drop=all')]=['-e','PYTHONPATH=/data/runtime:/opt/copernicus']
    log=r.local/(job['id']+'.log'); rows=[]
    print(f"Running {job['stage']} {job['variant']} {job['scenario']} {job['users']} users {profile['id']}",flush=True)
    r.remove_container(r.loader)
    with log.open('wb') as f:
        p=subprocess.Popen(r.base+args,stdout=f,stderr=subprocess.STDOUT)
        while p.poll() is None:
            phase=r.read('runs/'+job['id']+'/phase.json',{},root=root).get('phase','preparation')
            try:
                row=server_sample(r,phase)
                # Observe competing services through the separate rootful engine, without exec or mutation.
                bg=subprocess.run([r.pm,'--connection',r.r['machine']+'-root','stats','--no-stream','--format','json'],
                                  capture_output=True,timeout=15)
                row['background_stats']=json.loads(bg.stdout) if bg.returncode==0 and bg.stdout.strip() else None
                rows.append(row)
                with (r.local/(job['id']+'.telemetry.jsonl')).open('a') as telemetry:
                    telemetry.write(json.dumps(row)+'\n')
            except Exception as e:
                rows.append({'at':utc(),'phase':phase,'telemetry_error':str(e)})
            time.sleep(2)
    remote=root+'/runs/'+job['id']
    r.put(r.worker,remote+'/telemetry.jsonl',('\n'.join(json.dumps(x) for x in rows)+'\n').encode())
    if p.returncode: raise RuntimeError(f'Load cell failed; inspect {log}')
    result=r.read('runs/'+job['id']+'/summary.json',root=root)
    result['resources']=summarize([x for x in rows if 'monotonic' in x],profile)
    result['background']='cat-watch preserved; shared Windows/WSL workstation'
    result['code_manifest']=code_manifest
    result['source_snapshot']=r.read('source-snapshot.json',{})
    result['command']=r.base+args
    result['runtime_images']={name:r.owned(name)['Image'] for name in (r.server,r.worker)}
    result['runtime_images'][r.loader]=loader_image
    if not result['resources']['complete']:
        result['validity_issues'].append('incomplete_resource_telemetry'); result['valid']=False
    r.put(r.worker,remote+'/summary.json',json.dumps(result).encode())
    # Retain final raw GC evidence per cell before the next server restart.
    gc=r.call('exec',r.server,'cat','/opt/geoserver_data/gc.log',check=False)
    r.put(r.worker,remote+'/gc.log',gc.encode())
    # Cache accounting is saved; cleanup operates only on this cell's layer.
    script="from rasterbench.geoserver import session,clear_cache; clear_cache(session(),"+repr('run_'+job['id'].replace('-','_'))+","+repr(job['output_format'])+")"
    r.call('exec',r.worker,'python','-c',script)
    result['controller_complete']=True
    r.put(r.worker,remote+'/summary.json',json.dumps(result).encode())
    return result


def sync_load_code(r):
    # /data/runtime is private to this benchmark volume. The reader sees /data read-only.
    r.call('exec',r.worker,'mkdir','-p','/data/runtime')
    r.call('exec',r.worker,'cp','-r','/app/rasterbench','/data/runtime/')


def finalists(c,results):
    scores={}
    for v in c['variants']:
        rows=[r for r in results if r['job']['variant']==v['id'] and r['job']['users']==100
              and r['job']['scenario'] in ('wms','miss') and r['valid'] and r['view_latency']['p95'] is not None]
        expected=c['study']['screening_repetitions']*2
        if len(rows)==expected:
            # Fast exception responses must never win a compression comparison.
            scores[v['id']]=(sum(r['view_error_fraction'] for r in rows)/len(rows),
                             sum(r['view_latency']['p95'] for r in rows)/len(rows))
    selected=['none']
    for lossless in (True,False):
        group=[v['id'] for v in c['variants'] if v['lossless']==lossless and v['id']!='none' and v['id'] in scores]
        if group: selected.append(min(group,key=lambda v:(scores[v],v)))
    return selected,scores


def execute(c,args):
    if args.action=='estimate': print(json.dumps(estimate(c),indent=2)); return
    r=Runtime(c)
    if args.action=='status':
        result=read_json(r.local/'state.json',{'state':'no staged controller checkpoint'})
        result['continuation']=read_json(r.local/'continuation.json')
        result['overnight']=read_json(r.local/'overnight-state.json')
        if result['overnight']:
            result['state']=result['overnight']['state']
            result['stage']=result['overnight']['phase']
        if r.exists('container',r.worker) and r.owned(r.worker)['State']['Running']:
            code="""import json,pathlib
p=pathlib.Path(ROOT)
def records(relative):
 return [json.loads(x.read_text()) for x in (p/relative).glob('*.json')]
d=records('download-checksums')
print(json.dumps({'verified_products':len(d),'verified_download_bytes':sum(x['bytes'] for x in d),
 'dataset_accepted':(p/'dataset.json').exists(),'conversions_complete':sum(x.get('status')=='complete' for x in records('conversion')),
 'completed_cells':sum(json.loads(x.read_text()).get('controller_complete',False) for x in (p/'runs').glob('*/summary.json'))}))
""".replace('ROOT',repr(r.root))
            result['artifacts']=json.loads(r.call('exec',r.worker,'python','-c',code))
        print(json.dumps(result,indent=2)); return
    with lock(r.local/'controller.lock'):
        r.ensure()
        if args.action in ('doctor','select','fetch','prepare','convert','validate'):
            r.action(args.action,*(['--variant',args.variant] if args.variant else [])); return
        profile=next(p for p in c['study']['profiles'] if p['id']==c['study']['screening_profile'])
        if args.action=='serve': r.server_start(profile); return
        if args.action=='report': r.action('report'); print(r.export()); return
        if args.action=='smoke':
            r.server_start(profile); probe_matrix(r); return
        stages=['doctor','compatibility','fetch','prepare','convert','validate','benchmark','report']
        if args.action=='benchmark': stages=['benchmark','report']
        state=read_json(r.local/'state.json',{'schema_version':1,'completed_stages':[]})
        try:
            for stage in stages:
                if stage in state['completed_stages']: continue
                state.update(state='running',stage=stage,updated_at=utc()); write_json(r.local/'state.json',state)
                if stage=='compatibility': r.server_start(profile); probe_matrix(r)
                elif stage=='benchmark':
                    sync_load_code(r)
                    compat=r.read('compatibility.json',{})
                    variants=[v['id'] for v in c['variants'] if compat.get(v['id'],{}).get('supported')]
                    write_json(r.local/'estimate.json',estimate(c,variants))
                    screening=[]
                    for j in jobs(c,variants,'screening'):
                        state['cell']=j; write_json(r.local/'state.json',state)
                        screening.append(run_cell(r,j))
                    selected,scores=finalists(c,screening)
                    r.put(r.worker,r.root+'/finalists.json',json.dumps({'variants':selected,'screening_scores':scores}).encode())
                    extra=set(c['workload']['user_levels'])-set(c['study']['screening_users'])-set(c['study']['capacity_users'])
                    for users,scenario,v,rep in itertools.product(sorted(extra),c['study']['scenarios'],selected,range(c['study']['capacity_repetitions'])):
                        j={'stage':'browsing','variant':v,'profile':profile,'users':users,'scenario':scenario,
                           'repetition':rep,'output_format':'image/png'}
                        j['id']='browse-'+fingerprint(j)[:16];run_cell(r,j)
                    for j in jobs(c,selected,'capacity'):
                        state['cell']=j; write_json(r.local/'state.json',state); run_cell(r,j)
                    # Delivery encoding is orthogonal to source storage; hold source fixed.
                    delivery=r.read('delivery-compatibility-webp.json',{})
                    if c['study']['delivery']:
                        r.server_start(profile,restart=True,server_profile='webp')
                        r.action('delivery-quality')
                        for fmt in c['study']['delivery_formats']:
                            if not delivery.get(fmt,{}).get('supported'): continue
                            for scenario in ['wms']+(['hit','miss','mixed'] if delivery[fmt].get('gwc_supported') else []):
                                for rep in range(3):
                                    j={'stage':'delivery','variant':'none','profile':profile,'users':100,'scenario':scenario,
                                       'repetition':rep,'output_format':fmt,'server_profile':'webp'}
                                    j['id']='del-'+fingerprint(j)[:16]; run_cell(r,j)
                    if c['study']['first_access']:
                        for v in selected:
                            j={'stage':'first_access','variant':v,'profile':profile,'users':1,'scenario':'wms',
                               'repetition':0,'output_format':'image/png','first_access':True}
                            j['id']='first-'+fingerprint(j)[:16]; run_cell(r,j)
                elif stage=='validate':
                    r.action('validate')
                    r.server_start(profile,restart=True)
                    r.action('probe','--job','full')
                    actual=r.read('compatibility-full.json',{})
                    compatibility=r.read('compatibility.json',{})
                    for name,a in compatibility.items():
                        if a.get('supported') and not actual.get(name,{}).get('supported'):
                            a.update(supported=False,stage='full-data WMS',reason=actual.get(name,{}).get('reason','No full-data rendering evidence'))
                    r.put(r.worker,r.root+'/compatibility.json',json.dumps(compatibility).encode())
                    failed=[v for v in ('none','lzw','deflate','packbits','jpeg70','jpeg80') if not compatibility.get(v,{}).get('supported')]
                    if failed: raise RuntimeError('Required full-data rendering failed: '+', '.join(failed))
                else: r.action(stage)
                state['completed_stages'].append(stage); write_json(r.local/'state.json',state)
            r.export()
            state.update(state='complete',finished_at=utc()); write_json(r.local/'state.json',state)
        except BaseException as e:
            state.update(state='interrupted' if isinstance(e,KeyboardInterrupt) else 'failed',error=str(e),updated_at=utc())
            write_json(r.local/'state.json',state)
            raise
