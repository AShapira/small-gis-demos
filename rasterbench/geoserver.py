"""GeoServer REST setup and real reader/renderer compatibility probes."""
from __future__ import annotations

import io
import time
from pathlib import Path
from urllib.parse import quote

from .common import read_json, write_json

BASE='http://geoserver:8080/geoserver'
WORKSPACE='benchmark'


def session():
    import requests
    s=requests.Session()
    s.auth=('admin',Path('/run/secrets/admin_password').read_text().strip())
    return s


def checked(s,method,path,**kwargs):
    r=s.request(method,BASE+path,timeout=120,**kwargs)
    if not r.ok:
        raise RuntimeError(f'{method} {path}: HTTP {r.status_code} {r.text[:600]}')
    return r


def ready(root):
    s=session()
    for _ in range(120):
        try:
            r=s.get(BASE+'/rest/about/version.json',timeout=5)
            if r.ok:
                a=r.json()
                resources=a['about']['resource']
                version=next(x['Version'] for x in resources if x['@name']=='GeoServer')
                if version!='3.0.1': raise ValueError(f'Expected GeoServer 3.0.1, got {version}')
                write_json(Path(root)/'server-version.json',a)
                return s
        except (ValueError,KeyError):
            raise
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError('GeoServer did not become ready within 4 minutes')


def ensure_workspace(s):
    r=s.get(BASE+f'/rest/workspaces/{WORKSPACE}.json',timeout=30)
    if r.status_code==404:
        checked(s,'POST','/rest/workspaces',json={'workspace':{'name':WORKSPACE}})
    elif not r.ok:
        raise RuntimeError('Cannot inspect benchmark workspace')


def publish(s,root,v,name=None):
    from .raster import variant_path
    name=name or v['id']
    ensure_workspace(s)
    store=f'/rest/workspaces/{WORKSPACE}/coveragestores/{name}'
    url=variant_path(root,v).resolve().as_uri()
    if v['driver']!='GPKG':
        checked(s,'PUT',store+'/external.geotiff',params={'configure':'first','coverageName':name},
                data=str(variant_path(root,v)),headers={'Content-Type':'text/plain'})
    else:
        existing=s.get(BASE+store+'.json',timeout=30)
        if existing.status_code==404:
            checked(s,'POST',f'/rest/workspaces/{WORKSPACE}/coveragestores',
                json={'coverageStore':{'name':name,'workspace':{'name':WORKSPACE},
                                      'type':'GeoPackage (mosaic)','enabled':True,'url':url}})
        if s.get(BASE+store+f'/coverages/{name}.json',timeout=30).status_code==404:
            checked(s,'POST',store+'/coverages',json={'coverage':{'name':name,'nativeName':'imagery',
                    'title':name,'srs':'EPSG:32636','enabled':True}})
    checked(s,'PUT',f'/rest/layers/{WORKSPACE}:{name}',
            json={'layer':{'defaultStyle':{'name':'raster'}}})
    return name


def configure_cache(s,c,name,webp=False):
    # XML preserves XStream list and polymorphic filter types; JSON does not
    # round-trip GeoWebCache 2.0.1's default layer configuration reliably.
    import xml.etree.ElementTree as ET
    path=f'/gwc/rest/layers/{WORKSPACE}:{name}.xml'
    layer=ET.fromstring(checked(s,'GET',path).content)
    for tag in ('metaWidthHeight','mimeFormats','gridSubsets'):
        old=layer.find(tag)
        if old is not None: layer.remove(old)
    size=ET.SubElement(layer,'metaWidthHeight')
    for _ in range(2): ET.SubElement(size,'int').text=str(c['workload']['metatile'])
    formats=ET.SubElement(layer,'mimeFormats')
    for fmt in ('image/png','image/jpeg')+(('image/webp',) if webp else ()):
        ET.SubElement(formats,'string').text=fmt
    grids=ET.SubElement(layer,'gridSubsets')
    ET.SubElement(ET.SubElement(grids,'gridSubset'),'gridSetName').text='EPSG:900913'
    checked(s,'PUT',path,data=ET.tostring(layer),headers={'Content-Type':'application/xml'})


def clear_cache(s,name,fmt='image/png'):
    # Truncate only this benchmark-owned layer, never the whole server cache.
    checked(s,'POST',f'/gwc/rest/seed/{WORKSPACE}:{name}.json',
        json={'seedRequest':{'name':f'{WORKSPACE}:{name}','srs':{'number':900913},
             'zoomStart':0,'zoomStop':22,'format':fmt,'type':'truncate','threadCount':1}})
    for _ in range(120):
        r=checked(s,'GET',f'/gwc/rest/seed/{WORKSPACE}:{name}.json').json()
        tasks=r.get('long-array-array',[])
        if not tasks: return
        time.sleep(.25)
    raise RuntimeError('Cache truncation did not finish')


def check_image(data,content_type,width,height):
    from PIL import Image
    if not content_type.lower().startswith('image/'):
        import re
        detail=re.sub(r'<[^>]+>',' ',data[:4000].decode('utf-8','replace'))
        raise ValueError('Non-image response: '+' '.join(detail.split())[:800])
    if data.lstrip().startswith(b'<'):
        raise ValueError('XML response instead of imagery')
    im=Image.open(io.BytesIO(data)); im.load()
    if im.size!=(width,height): raise ValueError('Unexpected response dimensions')
    return im


def wms_params(name,bbox,crs='EPSG:3857',width=512,height=512,fmt='image/png'):
    return {'service':'WMS','version':'1.1.1','request':'GetMap','layers':f'{WORKSPACE}:{name}',
            'styles':'','srs':crs,'bbox':','.join(map(str,bbox)),'width':width,'height':height,
            'format':fmt,'transparent':'false','format_options':'antialias:off',
            'interpolations':'nearest neighbor'}


def probe(c,root,ready_only=False,profile='vanilla'):
    root=Path(root); s=ready(root)
    if ready_only: return
    from osgeo import gdal,osr
    from .raster import variant_path
    suffix='' if profile=='vanilla' else '-'+profile
    prefix='full_' if profile=='full' else 'probe_'
    results={}
    reference=gdal.Open(str(root/'reference.tif'))
    gt=reference.GetGeoTransform()
    x0,y1=gt[0],gt[3]; x1=x0+reference.RasterXSize*gt[1]; y0=y1+reference.RasterYSize*gt[5]
    source=reference.GetSpatialRef(); target=osr.SpatialReference(); target.ImportFromEPSG(3857)
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr=osr.CoordinateTransformation(source,target)
    a,b=tr.TransformPoint(x0,y0),tr.TransformPoint(x1,y1)
    box=[a[0],a[1],b[0],b[1]]
    refs={}
    for v in c['variants']:
        if not variant_path(root,v).exists():
            results[v['id']]={'supported':False,'stage':'conversion','reason':'Conversion failed or unavailable'}
            continue
        try:
            name=publish(s,root,v,prefix+v['id'])
            configure_cache(s,c,name)
            cases=[]
            for scale in (1,.25,.025):
                cx,cy=(box[0]+box[2])/2,(box[1]+box[3])/2
                w,h=(box[2]-box[0])*scale,(box[3]-box[1])*scale
                bbox=[cx-w/2,cy-h/2,cx+w/2,cy+h/2]
                r=checked(s,'GET','/wms',params=wms_params(name,bbox))
                im=check_image(r.content,r.headers.get('Content-Type',''),512,512).convert('RGB')
                import numpy as np
                arr=np.array(im)
                if arr.std()<1: raise ValueError('Blank/constant rendered image')
                if v['id']=='none': refs[scale]=arr
                if v['lossless'] and scale in refs and not np.array_equal(arr,refs[scale]):
                    raise ValueError('Lossless WMS rendering differs from uncompressed')
                cases.append({'scale':scale,'bytes':len(r.content),'http_status':r.status_code})
            results[v['id']]={'supported':True,'stage':'GeoServer WMS','cases':cases,
                'driver':v['driver'],'compression':v['compression'],'server_version':'3.0.1'}
        except Exception as e:
            results[v['id']]={'supported':False,'stage':'GeoServer WMS','reason':str(e)}
        print(v['id']+': '+str(results[v['id']]),flush=True)
        write_json(root/('compatibility'+suffix+'.json'),results)
    # Output support is an independent capability, regardless of source reader support.
    write_json(root/('compatibility'+suffix+'.json'),results)
    delivery={}
    configure_cache(s,c,prefix+'none',webp=profile=='webp')
    for fmt in c['study']['delivery_formats']:
        try:
            r=checked(s,'GET','/wms',params=wms_params(prefix+'none',box,fmt=fmt))
            check_image(r.content,r.headers.get('Content-Type',''),512,512)
            delivery[fmt]={'supported':True,'bytes':len(r.content)}
            from .workload import view_tiles,tile_url
            tile=view_tiles(15,box)[0]
            url=tile_url(prefix+'none',tile,fmt)
            for _ in range(2):
                tile_response=s.get(url,timeout=120)
                check_image(tile_response.content,tile_response.headers.get('Content-Type',''),256,256)
            delivery[fmt]['gwc_supported']=tile_response.headers.get('geowebcache-cache-result','').upper()=='HIT'
        except Exception as e:
            if fmt in delivery: delivery[fmt].update(gwc_supported=False,gwc_reason=str(e))
            else: delivery[fmt]={'supported':False,'reason':str(e)}
    write_json(root/('delivery-compatibility'+suffix+'.json'),delivery)
