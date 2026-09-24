"""Deterministic browser-like navigation and bounded asynchronous HTTP load."""
from __future__ import annotations

import asyncio
import gzip
import io
import json
import math
import random
import time
from pathlib import Path
from urllib.parse import urlencode

from .common import fingerprint, percentile, read_json, utc, write_json
from .geoserver import BASE, WORKSPACE, check_image, wms_params

HALF=20037508.342789244


def mercator(lon,lat):
    return HALF*lon/180, HALF*math.log(math.tan(math.pi/4+math.radians(lat)/2))/math.pi


def tile_bbox(z,x,y):
    width=2*HALF/2**z
    return [-HALF+x*width,HALF-(y+1)*width,-HALF+(x+1)*width,HALF-y*width]


def view_tiles(z,bbox):
    width=2*HALF/2**z
    left=math.floor((bbox[0]+HALF)/width)
    right=math.floor((bbox[2]+HALF-1e-6)/width)
    top=math.floor((HALF-bbox[3])/width)
    bottom=math.floor((HALF-bbox[1]-1e-6)/width)
    return [(z,x,y) for y in range(top,bottom+1) for x in range(left,right+1)]


def extent_3857(dataset):
    from osgeo import osr
    src=osr.SpatialReference(); src.ImportFromWkt(dataset['projection'])
    dst=osr.SpatialReference(); dst.ImportFromEPSG(3857)
    for s in (src,dst): s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr=osr.CoordinateTransformation(src,dst)
    gt=dataset['geotransform']; w=dataset['width']; h=dataset['height']
    pts=[tr.TransformPoint(gt[0]+x*gt[1],gt[3]+y*gt[5]) for x,y in ((0,0),(w,0),(0,h),(w,h))]
    # Inscribed rectangle keeps regional requests away from nodata borders.
    return [max(pts[0][0],pts[2][0]),max(pts[2][1],pts[3][1]),
            min(pts[1][0],pts[3][0]),min(pts[0][1],pts[1][1])]


def make_traces(c,extent,users,scenario):
    cfg=c['workload']; seed=cfg['seed']
    traces=[]
    for user in range(users):
        r=random.Random(seed+user*104729)
        lonlat=cfg['regions'][user%len(cfg['regions'])]
        region=lonlat['name']
        cx,cy=mercator(lonlat['lon'],lonlat['lat'])
        z=r.choice(cfg['zooms']); views=[]
        for index in range(cfg['max_views_per_user']):
            action=r.choices(['pan','zoom','revisit','relocate'],[.55,.25,.1,.1])[0]
            if action=='zoom': z=max(min(cfg['zooms']),min(max(cfg['zooms']),z+r.choice([-1,1])))
            if action=='relocate':
                loc=r.choice(cfg['regions']); cx,cy=mercator(loc['lon'],loc['lat'])
                region=loc['name']
            if action=='revisit' and views:
                prev=r.choice(views[-8:]); cx,cy=prev['center']; z=prev['zoom']
                region=prev['region']
            resolution=2*HALF/(256*2**z)
            width,height=[n*resolution for n in cfg['viewport']]
            if action=='pan': cx+=r.choice([-1,1])*width*.6; cy+=r.choice([-1,0,1])*height*.4
            # Small fixtures need high zooms; never silently claim these are production traces.
            while width>extent[2]-extent[0] or height>extent[3]-extent[1]:
                z+=1; resolution/=2; width/=2; height/=2
            cx=max(extent[0]+width/2,min(extent[2]-width/2,cx))
            cy=max(extent[1]+height/2,min(extent[3]-height/2,cy))
            bbox=[cx-width/2,cy-height/2,cx+width/2,cy+height/2]
            views.append({'index':index,'action':action,'zoom':z,'center':[cx,cy],'bbox':bbox,
                          'tiles':view_tiles(z,bbox),'think':r.uniform(*cfg['think_seconds']),
                          'region':region})
        traces.append(views)
    if scenario=='miss':
        # Allocate fresh metatile-sized viewports across the data, independently of timing.
        # Exhaustion is explicit; never recycle a miss trace and mislabel cache hits.
        pool=[]; m=cfg['metatile']
        for z in cfg.get('miss_zooms',[13,14,15]):
            span=2*HALF/2**z
            for y in range(math.ceil((HALF-extent[3])/span/m)*m,math.floor((HALF-extent[1])/span/m)*m,m):
                for x in range(math.ceil((extent[0]+HALF)/span/m)*m,math.floor((extent[2]+HALF)/span/m)*m,m):
                    bbox=[-HALF+x*span,HALF-(y+3)*span,-HALF+(x+4)*span,HALF-y*span]
                    pool.append((z,bbox,view_tiles(z,bbox)))
        random.Random(seed).shuffle(pool)
        if len(pool)<users: raise ValueError('Dataset too small for fresh-metatile miss test')
        for user,views in enumerate(traces):
            assigned=pool[user::users]
            traces[user]=views[:len(assigned)]
            for view,item in zip(traces[user],assigned):
                view.update(zoom=item[0],bbox=item[1],tiles=item[2],action='fresh-metatile',
                            center=[(item[1][0]+item[1][2])/2,(item[1][1]+item[1][3])/2],region='regional-grid')
    return traces


def tile_url(layer,tile,fmt):
    z,x,y=tile
    return BASE+'/gwc/service/wmts?'+urlencode({'SERVICE':'WMTS','VERSION':'1.0.0','REQUEST':'GetTile',
        'LAYER':f'{WORKSPACE}:{layer}','STYLE':'','FORMAT':fmt,'TILEMATRIXSET':'EPSG:900913',
        'TILEMATRIX':f'EPSG:900913:{z}','TILEROW':y,'TILECOL':x})


def metatile_key(tile,m=4):
    z,x,y=tile; return z,x//m,y//m


def seed_tiles(traces,scenario,fraction,seed):
    unique=sorted({tuple(tile) for trace in traces for v in trace for tile in v['tiles']})
    if scenario=='hit': return unique
    if scenario!='mixed': return []
    groups={}
    for tile in unique: groups.setdefault(metatile_key(tile),[]).append(tile)
    keys=sorted(groups); random.Random(seed).shuffle(keys)
    # Weight by actual trace requests, not merely geographic metatile count.
    weights={k:0 for k in keys}
    for trace in traces:
        for v in trace:
            for tile in v['tiles']: weights[metatile_key(tile)]+=1
    target=sum(weights.values())*fraction; selected=[]; count=0
    for k in keys:
        if count>=target: break
        selected.extend(groups[k]); count+=weights[k]
    return selected


async def preseed(request, tiles, layer, fmt, evidence, attempts=3, retry_delay=1):
    """Retry preparation only; persist every attempt and fail closed on exhaustion."""
    sem=asyncio.Semaphore(8)
    failures=0; failed_attempts=0
    with Path(evidence).open('a') as log:
        async def one(tile):
            nonlocal failed_attempts
            async with sem:
                for attempt in range(1,attempts+1):
                    row=await request(tile_url(layer,tile,fmt),256,256)
                    log.write(json.dumps(dict(row,tile=tile,attempt=attempt,at=utc()))+'\n')
                    log.flush()
                    if row['ok']:return False
                    failed_attempts+=1
                    if attempt<attempts:await asyncio.sleep(retry_delay*attempt)
                return True
        for i in range(0,len(tiles),256):
            failures+=sum(await asyncio.gather(*(one(t) for t in tiles[i:i+256])))
    if failures:
        raise RuntimeError(f'{failures} preseed requests failed after {attempts} attempts; inspect {evidence}')
    return failed_attempts


async def run(c,root,job):
    import aiohttp
    import psutil
    from .geoserver import session, publish, configure_cache, clear_cache
    root=Path(root); out=root/'runs'/job['id']; out.mkdir(parents=True,exist_ok=True)
    # The host alone marks completion after attaching resource telemetry.
    if read_json(out/'summary.json',{}).get('controller_complete'): return
    dataset=read_json(root/'dataset.json'); cfg=c['workload']; users=job['users']
    if users>cfg['max_users']: raise ValueError('Requested users exceed configured maximum')
    variant=next(v for v in c['variants'] if v['id']==job['variant'])
    fmt=job.get('output_format',cfg['output_format'])
    # Every cell gets a distinct layer/cache namespace; no random query-string cache busting.
    layer='run_'+job['id'].replace('-','_')
    s=session(); publish(s,root,variant,layer); configure_cache(s,c,layer,webp=job.get('server_profile')=='webp')
    write_json(out/'phase.json',{'phase':'preparation','started_at':utc(),'job':job})
    if job['scenario']!='wms': clear_cache(s,layer,fmt)
    traces=make_traces(c,extent_3857(dataset),users,job['scenario'])
    write_json(out/'trace.json',{'schema_version':1,'sha256':fingerprint(traces),'users':traces})
    fmt=job.get('output_format',cfg['output_format'])
    timeout=aiohttp.ClientTimeout(total=cfg['request_timeout'])
    started_at=utc(); request_rows=[]; view_rows=[]; lags=[]; mem=[]; cpu=[]
    proc=psutil.Process(); proc.cpu_percent(); stop=asyncio.Event()
    async def watch():
        while not stop.is_set():
            t=time.monotonic(); await asyncio.sleep(.5)
            lags.append(max(0,time.monotonic()-t-.5)); mem.append(proc.memory_info().rss)
            cpu.append(proc.cpu_percent()/100)
    errors=0
    connector=aiohttp.TCPConnector(limit=users*cfg['parallel_tiles']+8,ttl_dns_cache=300)
    async with aiohttp.ClientSession(timeout=timeout,connector=connector) as http:
        async def request(url,width,height):
            start=time.monotonic(); row={'url':url,'started_monotonic':start,'ok':False}
            try:
                async with http.get(url) as response:
                    data=await response.read()
                    row.update(status=response.status,bytes=len(data),cache=response.headers.get('geowebcache-cache-result','BYPASS').upper(),
                               content_type=response.headers.get('Content-Type',''))
                    if response.status!=200: raise ValueError(f'HTTP {response.status}')
                    # Full decoding catches corrupt images and XML exception bodies.
                    check_image(data,row['content_type'],width,height)
                    row['ok']=True
            except Exception as e:
                row['error']=type(e).__name__+': '+str(e)[:200]
            row['seconds']=time.monotonic()-start
            return row
        async def warmup():
            end=time.monotonic()+cfg['warmup_seconds']
            async def one(user):
                i=0
                while time.monotonic()<end:
                    view=traces[user][i%len(traces[user])]
                    url=BASE+'/wms?'+urlencode(wms_params(layer,view['bbox'],width=cfg['viewport'][0],height=cfg['viewport'][1],fmt=fmt))
                    await request(url,*cfg['viewport']); i+=1
                    await asyncio.sleep(view['think'])
            await asyncio.gather(*(one(u) for u in range(users)))
        if not job.get('first_access'):
            await warmup()
        seeds=seed_tiles(traces,job['scenario'],cfg['mixed_seed_fraction'],cfg['seed'])
        seed_requests=list({metatile_key(t,cfg['metatile']):t for t in seeds}.values())
        seed_start=time.monotonic(); seed_failed_attempts=0
        if seeds:
            seed_failed_attempts=await preseed(request,seed_requests,layer,fmt,out/'preseed.jsonl')
        seed_seconds=time.monotonic()-seed_start
        write_json(out/'phase.json',{'phase':'measurement','started_at':utc(),'seed_tiles':len(seeds),
                                    'seed_seconds':seed_seconds,'job':job})
        run_start=time.monotonic(); deadline=run_start+cfg['measurement_seconds']
        monitor=asyncio.create_task(watch())
        with gzip.open(out/'requests.jsonl.gz','wt',compresslevel=1) as reqfile, \
             gzip.open(out/'views.jsonl.gz','wt',compresslevel=1) as viewfile:
            async def user_run(user):
                previous=set(); sem=asyncio.Semaphore(cfg['parallel_tiles']); count=0
                # Deterministic stagger avoids a synthetic simultaneous login storm.
                await asyncio.sleep(random.Random(cfg['seed']+user).uniform(0,min(3,cfg['measurement_seconds']/10)))
                while time.monotonic()<deadline:
                    if count>=len(traces[user]):
                        if job['scenario']=='miss': return 'trace_exhausted'
                        count=0
                    v=traces[user][count]; count+=1; begin=time.monotonic()
                    rows=[]
                    if job['scenario']=='wms':
                        url=BASE+'/wms?'+urlencode(wms_params(layer,v['bbox'],width=cfg['viewport'][0],height=cfg['viewport'][1],fmt=fmt))
                        rows=[await request(url,*cfg['viewport'])]; reused=0
                    else:
                        tiles={tuple(t) for t in v['tiles']}; needed=sorted(tiles-previous); reused=len(tiles&previous)
                        async def tile_request(t):
                            async with sem: return await request(tile_url(layer,t,fmt),256,256)
                        rows=await asyncio.gather(*(tile_request(t) for t in needed))
                        previous=tiles if all(r['ok'] for r in rows) else set()
                    for row in rows:
                        row.update(user=user,view=v['index'],zoom=v['zoom'],region=v['region'])
                        reqfile.write(json.dumps(row)+'\n')
                        request_rows.append({k:row.get(k) for k in ('seconds','ok','cache','bytes','zoom')})
                    row={'user':user,'view':v['index'],'seconds':time.monotonic()-begin,
                         'ok':all(r['ok'] for r in rows),'requests':len(rows),'browser_reused_tiles':reused,
                         'zoom':v['zoom'],'action':v['action'],'region':v['region'],
                         'since_start_seconds':begin-run_start}
                    view_rows.append(row); viewfile.write(json.dumps(row)+'\n')
                    remaining=deadline-time.monotonic()
                    if remaining>0: await asyncio.sleep(min(v['think'],remaining))
                return 'complete'
            states=await asyncio.gather(*(user_run(u) for u in range(users)))
        elapsed=time.monotonic()-run_start; stop.set(); await monitor
    # Release only this run's server cache to keep the multi-day disk footprint bounded.
    # Capture size before truncation via host telemetry; never flush OS caches.
    write_json(out/'phase.json',{'phase':'complete','finished_at':utc(),'job':job})
    def distribution(rows):
        vals=[r['seconds'] for r in rows]
        return {f'p{int(q*100)}':percentile(vals,q) for q in (.5,.95,.99)}
    hit=sum(r['cache']=='HIT' for r in request_rows); n=len(request_rows)
    summary={'schema_version':1,'job':job,'started_at':started_at,'finished_at':utc(),
        'synthetic':dataset['synthetic'],'trace_sha256':fingerprint(traces),'seconds':elapsed,
        'requests':n,'views':len(view_rows),'requests_per_second':n/elapsed,'views_per_second':len(view_rows)/elapsed,
        'request_latency':distribution(request_rows),'view_latency':distribution(view_rows),
        'view_error_fraction':sum(not r['ok'] for r in view_rows)/max(1,len(view_rows)),
        'request_error_fraction':sum(not r['ok'] for r in request_rows)/max(1,n),
        'response_bytes':sum(r['bytes'] or 0 for r in request_rows),
        'cache_counts':{k:sum(r['cache']==k for r in request_rows) for k in sorted({r['cache'] for r in request_rows},key=str)},
        'hit_fraction':hit/n if n else 0,'seed_tiles':len(seeds),'seed_requests':len(seed_requests),'seed_seconds':seed_seconds,
        'seed_failed_attempts':seed_failed_attempts,
        'trace_exhausted_users':states.count('trace_exhausted'),
        'per_user_views':{str(u):sum(r['user']==u for r in view_rows) for u in range(users)},
        'users_without_completed_view':sum(not any(r['user']==u for r in view_rows) for u in range(users)),
        'views_exceeding_latency_target':sum(r['seconds']>=c['acceptance']['hit_view_p95_seconds' if job['scenario']=='hit' else 'render_view_p95_seconds'] for r in view_rows),
        'first_view_seconds':view_rows[0]['seconds'] if view_rows else None,
        'generator':{'peak_rss_bytes':max(mem,default=0),'cpu_cores_p95':percentile(cpu,.95),
                     'event_loop_lag_p95_seconds':percentile(lags,.95)},
        'zoom_view_latency':{str(z):distribution([r for r in view_rows if r['zoom']==z]) for z in sorted({r['zoom'] for r in view_rows})}}
    issues=[]
    if not n or not view_rows: issues.append('no_network_samples')
    if states.count('trace_exhausted'): issues.append('finite_miss_trace_exhausted')
    if job['scenario']=='hit' and summary['hit_fraction']<c['acceptance']['minimum_hit_fraction']: issues.append('cache_hit_contract_failed')
    if (percentile(lags,.95) or 0)>.05 or (percentile(cpu,.95) or 0)>c['runtime']['load_cpus']*.85: issues.append('generator_saturation')
    summary['valid']=not issues; summary['validity_issues']=issues
    write_json(out/'summary.json',summary)
    print(json.dumps({'job':job['id'],'view_p95':summary['view_latency']['p95'],'valid':summary['valid']}),flush=True)
