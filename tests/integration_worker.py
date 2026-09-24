"""Run inside the worker after fixture conversion and GeoServer startup.

Synthetic integration acceptance only; results never qualify deployment profiles.
"""
import asyncio
import copy
from pathlib import Path
from rasterbench.common import read_json,write_json
from rasterbench.geoserver import session,clear_cache
from rasterbench.workload import run
from rasterbench.raster import validate
from rasterbench.report import generate

async def main():
    c=copy.deepcopy(read_json('/data/config.json'))
    root=Path('/data')/c['study_id']/'probes' if 'study_id' in c else Path('/data/israel-rgb-5gb/probes')
    c['workload'].update(warmup_seconds=1,measurement_seconds=8,max_views_per_user=128,
                         think_seconds=[.1,.2],zooms=[15,16],miss_zooms=[16,17])
    for v in c['variants']:
        if read_json(root/'compatibility.json',{}).get(v['id'],{}).get('supported'):
            validate(c,root,v['id'])
    results=[]
    for scenario in ['wms','hit','miss','mixed']:
        job={'id':'integration-'+scenario,'stage':'integration','variant':'none',
             'profile':c['study']['profiles'][2],'users':2,'scenario':scenario,
             'repetition':0,'output_format':'image/png'}
        await run(c,root,job)
        result=read_json(root/'runs'/job['id']/'summary.json')
        assert result['requests']>0 and result['view_error_fraction']==0, result
        if scenario=='hit': assert result['hit_fraction']>=.99,result
        if scenario=='miss': assert result['cache_counts'].get('MISS',0)>0,result
        results.append(result)
        clear_cache(session(),'run_'+job['id'].replace('-','_'))
    generate(c,root)
    assert (root/'report/report.html').exists()
    write_json(root/'integration-results.json',{'synthetic':True,'passed':True,'runs':results})
    print('INTEGRATION_PASS',flush=True)

if __name__=='__main__': asyncio.run(main())
