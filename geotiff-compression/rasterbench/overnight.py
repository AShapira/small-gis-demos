"""Ten-user overnight priorities followed by continuing measurement cycles.

The execution plan is separate from immutable source preparation settings.
Every job carries its plan, configuration hash, and phase. No deadline stops it.
"""
from __future__ import annotations
import argparse
import copy
import itertools
import json
import os
import random
import time
from pathlib import Path

from .common import fingerprint,load_config,read_json,utc,write_json
from .controller import lock,run_cell,sync_load_code
from .runtime import Runtime


def resolve(base,plan,endurance=False):
    c=copy.deepcopy(base)
    c['execution_plan']=copy.deepcopy(plan)
    c['workload'].update(max_users=plan['users'],user_levels=[plan['users']],
        warmup_seconds=plan['warmup_seconds'],
        measurement_seconds=plan['endurance_measurement_seconds'] if endurance else plan['measurement_seconds'],
        max_views_per_user=plan['endurance_max_views_per_user'] if endurance else plan['max_views_per_user'])
    c['study'].update(screening_users=[plan['users']],capacity_users=[plan['users']],
                      screening_repetitions=plan['repetitions'],capacity_repetitions=plan['repetitions'])
    return c


def batch(c,plan,variants,phase,profiles,scenarios,repetitions,cycle=0,server_profile='vanilla',fmt='image/png'):
    jobs=[]
    stage='screening' if phase in ('source_comparison','cached_browsing') else 'capacity' if 'capacity' in phase else phase
    for rep,profile,scenario in itertools.product(range(repetitions),profiles,scenarios):
        order=list(variants)
        random.Random(c['workload']['seed']+cycle*997+rep*37+sum(map(ord,scenario))).shuffle(order)
        for variant in order:
            job={'stage':stage,'phase':phase,'plan_id':plan['plan_id'],'cycle':cycle,
                 'variant':variant,'profile':profile,'users':plan['users'],'scenario':scenario,
                 'repetition':rep,'output_format':fmt,'server_profile':server_profile,
                 'warmup_seconds':c['workload']['warmup_seconds'],
                 'measurement_seconds':c['workload']['measurement_seconds'],
                 'configuration_sha256':fingerprint(c)}
            job['id']='night-'+fingerprint(job)[:16];jobs.append(job)
    return jobs


def shortlist(c,rows):
    scores={}
    for v in c['variants']:
        group=[r for r in rows if r['job']['variant']==v['id'] and r['valid']]
        required={(s,n) for s in ('miss','wms') for n in range(c['study']['screening_repetitions'])}
        if {(r['job']['scenario'],r['job']['repetition']) for r in group}!=required:continue
        scores[v['id']]=(sum(r['view_error_fraction'] for r in group)/len(group),
                         sum(r['view_latency']['p95'] for r in group)/len(group))
    chosen=['none']
    for lossless in (True,False):
        candidates=[v['id'] for v in c['variants'] if v['id']!='none' and v['lossless']==lossless and v['id'] in scores]
        if not candidates:raise RuntimeError('No valid '+('lossless' if lossless else 'lossy')+' shortlist; inspect failed cells')
        chosen.append(min(candidates,key=lambda v:(scores[v],v)))
    return chosen,scores


def wait_for_worker(r):
    """Allow an orphaned prepare/convert process to reach its own checkpoint."""
    if not r.exists('container',r.worker) or not r.owned(r.worker)['State']['Running']:return
    code="import psutil,json; print(json.dumps([p.cmdline() for p in psutil.process_iter() if 'rasterbench.worker' in p.cmdline()]))"
    while json.loads(r.call('exec',r.worker,'python','-c',code)):
        time.sleep(10)


def main():
    import yaml
    parser=argparse.ArgumentParser();parser.add_argument('--plan',default='configs/overnight-10.yaml')
    args=parser.parse_args();plan=yaml.safe_load(Path(args.plan).read_text())
    if plan['schema_version']!=1 or plan['users']<1 or plan['stop_at_target']:
        raise ValueError('Expected a continuing execution plan with positive user count')
    if plan['plan_id']!='overnight-10' or plan['users']!=10:
        raise ValueError('This execution plan is scoped to the requested 10 users')
    base=load_config(plan['source_config']);c=resolve(base,plan);r=Runtime(base)
    checkpoint=r.local/'overnight-state.json'
    state=read_json(checkpoint,{'schema_version':1,'completed_phases':[],'cycle':0})
    if state.get('plan_sha256') not in (None,fingerprint(plan)):raise ValueError('Plan changed; use an explicit new execution plan')
    def save(phase,**extra):
        state.pop('error',None)
        state.update(state='running',phase=phase,pid=os.getpid(),users=plan['users'],
                     plan_sha256=fingerprint(plan),morning_target=plan['morning_target'],
                     stop_at_target=False,updated_at=utc(),**extra)
        write_json(checkpoint,state)
    save('waiting_for_preparation')
    try:
        with lock(r.local/'controller.lock'):
            wait_for_worker(r)
            r.ensure()
            config_path=r.root+'/plans/'+plan['plan_id']+'.json'
            long_config_path=r.root+'/plans/'+plan['plan_id']+'-endurance.json'
            r.put(r.worker,config_path,json.dumps(c).encode())
            long_c=resolve(base,plan,endurance=True)
            r.put(r.worker,long_config_path,json.dumps(long_c).encode())
            r.put(r.worker,r.root+'/execution-plan.json',json.dumps(plan).encode())
            profile_by_id={p['id']:p for p in c['study']['profiles']}
            screening=profile_by_id[plan['screening_profile']]
            def report():
                r.action('report','--config',config_path)
                r.export()
                state['last_report_at']=utc();write_json(checkpoint,state)
            for step in ('prepare','convert','validate'):
                if step in state['completed_phases']:continue
                save(step);r.action(step)
                state['completed_phases'].append(step);write_json(checkpoint,state)
            if 'full_render_validation' not in state['completed_phases']:
                save('full_render_validation');r.server_start(screening,restart=True)
                r.action('probe','--job','full')
                actual=r.read('compatibility-full.json',{});compat=r.read('compatibility.json',{})
                for name,a in compat.items():
                    if a.get('supported') and not actual.get(name,{}).get('supported'):
                        a.update(supported=False,stage='full-data WMS',reason=actual.get(name,{}).get('reason','Missing evidence'))
                r.put(r.worker,r.root+'/compatibility.json',json.dumps(compat).encode())
                failed=[name for name in ('none','lzw','deflate','packbits','jpeg70','jpeg80') if not compat.get(name,{}).get('supported')]
                if failed:raise RuntimeError('Required full-data rendering failed: '+', '.join(failed))
                state['completed_phases'].append('full_render_validation');write_json(checkpoint,state)
            sync_load_code(r)
            supported=[v['id'] for v in c['variants'] if r.read('compatibility.json',{}).get(v['id'],{}).get('supported')]
            def run_batch(jobs,phase,cp=config_path):
                save(phase,total_phase_cells=len(jobs))
                results=[]
                for i,job in enumerate(jobs):
                    if (r.local/'STOP_AFTER_CELL').exists():
                        report();state.update(state='stopped',stopped_at=utc());write_json(checkpoint,state);return None
                    save(phase,cell=job,phase_cell=i+1,total_phase_cells=len(jobs))
                    results.append(run_cell(r,job,config_path=cp))
                    if (i+1)%plan['report_every_cells']==0:report()
                report();return results
            screening_jobs=batch(c,plan,supported,'source_comparison',[screening],['miss','wms'],plan['repetitions'])
            rows=run_batch(screening_jobs,'source_comparison')
            if rows is None:return
            chosen,scores=shortlist(c,rows)
            r.put(r.worker,r.root+'/overnight-finalists.json',json.dumps({'variants':chosen,'scores_error_then_latency':scores,'users':10}).encode())
            for phase,profiles,scenarios in [
                ('cached_browsing',[screening],['hit','mixed']),
                ('core_capacity',[profile_by_id[x] for x in plan['core_capacity_profiles']],c['study']['scenarios'])]:
                if run_batch(batch(c,plan,chosen,phase,profiles,scenarios,plan['repetitions']),phase) is None:return
            if 'core' not in state['completed_phases']:state['completed_phases'].append('core')
            save('core_complete_continuing',core_completed_at=state.get('core_completed_at',utc()))
            report()
            if run_batch(batch(c,plan,chosen,'extended_capacity',[profile_by_id[x] for x in plan['extended_capacity_profiles']],
                               c['study']['scenarios'],plan['repetitions']),'extended_capacity') is None:return
            if 'delivery_quality' not in state['completed_phases']:
                save('delivery_quality');r.server_start(screening,restart=True,server_profile='webp')
                r.action('delivery-quality','--config',config_path)
                state['completed_phases'].append('delivery_quality');write_json(checkpoint,state)
            delivery=r.read('delivery-compatibility-webp.json',{})
            for fmt in c['study']['delivery_formats']:
                if delivery.get(fmt,{}).get('supported'):
                    if run_batch(batch(c,plan,['none'],'delivery',[screening],['wms'],plan['repetitions'],
                                       server_profile='webp',fmt=fmt),'delivery') is None:return
            # No morning cutoff. Extend evidence with longer repeated cells at 10 users.
            while True:
                r.check_host_space()
                cycle=state['cycle']
                jobs=batch(long_c,plan,chosen,'endurance',[profile_by_id['cpu4-mem8']],
                           c['study']['scenarios'],1,cycle=cycle)
                if run_batch(jobs,'endurance',long_config_path) is None:return
                state['cycle']=cycle+1;write_json(checkpoint,state)
    except BaseException as e:
        state.update(state='failed',error=str(e),updated_at=utc());write_json(checkpoint,state)
        raise


if __name__=='__main__':main()
