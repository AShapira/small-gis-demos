"""Measure delivery encoding loss against the same PNG-rendered view."""
import math
from pathlib import Path
from urllib.parse import urlencode
from .common import read_json,write_json
from .geoserver import BASE,session,publish,wms_params,check_image
from .workload import extent_3857,make_traces


def quality(c,root):
    import numpy as np
    from PIL import Image
    from skimage.metrics import structural_similarity
    root=Path(root);out=root/'delivery-quality';out.mkdir(exist_ok=True)
    dataset=read_json(root/'dataset.json');s=session()
    traces=make_traces(c,extent_3857(dataset),len(c['workload']['regions']),'wms')
    compatible=read_json(root/'delivery-compatibility-webp.json',{})
    results=[]
    for variant_id in ('none','jpeg80'):
        v=next(v for v in c['variants'] if v['id']==variant_id)
        if not (root/'variants'/f'{variant_id}.tif').exists(): continue
        layer=publish(s,root,v,('quality_fixture_' if dataset['synthetic'] else 'quality_real_')+variant_id)
        for i,trace in enumerate(traces):
            view=trace[0]; w,h=c['workload']['viewport']
            p=wms_params(layer,view['bbox'],width=w,height=h)
            r=s.get(BASE+'/wms',params=p,timeout=120)
            reference=np.asarray(check_image(r.content,r.headers.get('Content-Type',''),w,h).convert('RGB'))
            for fmt,a in compatible.items():
                if not a.get('supported'): continue
                p['format']=fmt;r=s.get(BASE+'/wms',params=p,timeout=120)
                arr=np.asarray(check_image(r.content,r.headers.get('Content-Type',''),w,h).convert('RGB'))
                delta=arr.astype('float64')-reference
                mse=float(np.mean(delta**2));prefix=f'{variant_id}-{i}-{fmt.split("/")[1]}'
                Image.fromarray(reference).save(out/(prefix+'-reference.png'))
                Image.fromarray(arr).save(out/(prefix+'-decoded.png'))
                Image.fromarray(np.minimum(np.abs(delta)*8,255).astype('uint8')).save(out/(prefix+'-difference8x.png'))
                results.append({'variant':variant_id,'region':c['workload']['regions'][i]['name'],
                    'bbox':view['bbox'],'zoom':view['zoom'],'format':fmt,'bytes':len(r.content),
                    'mae':float(np.abs(delta).mean()),'rmse':math.sqrt(mse),
                    'psnr_db':10*math.log10(255**2/mse) if mse else None,'psnr_infinite':mse==0,
                    'ssim':float(structural_similarity(reference,arr,channel_axis=2,data_range=255)),
                    'prefix':prefix,'server_profile':'webp','encoder_options':'GeoServer/module defaults'})
    write_json(out/'results.json',{'synthetic':dataset['synthetic'],'views':results,
        'meaning':'Additional delivery loss relative to PNG rendering of each source. JPEG source degradation is measured separately.'})
