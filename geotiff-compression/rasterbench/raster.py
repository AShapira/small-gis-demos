"""Canonical mosaics, controlled conversions and streaming quality validation."""
from __future__ import annotations

import math
import os
import random
import shutil
import sqlite3
import zipfile
from pathlib import Path

from .common import measured, read_json, sha256, write_json


def gdal_setup(c):
    from osgeo import gdal
    gdal.UseExceptions()
    gdal.SetConfigOption('GDAL_NUM_THREADS',str(c['raster']['threads']))
    gdal.SetCacheMax(512 * 1024**2)
    return gdal


def variant_path(root, v):
    return Path(root)/'variants'/(v['id'] + ('.gpkg' if v['driver']=='GPKG' else '.tif'))


def storage_layout(path):
    """Encoded sample payloads are separate from container metadata and padding."""
    if Path(path).suffix=='.gpkg':
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
            levels=db.execute('SELECT zoom_level,SUM(length(tile_data)) FROM imagery GROUP BY zoom_level ORDER BY zoom_level DESC').fetchall()
        base=levels[0][1]; overviews=sum(n for _,n in levels[1:])
        detail={'zoom_payload_bytes':dict(levels)}
    else:
        import tifffile
        with tifffile.TiffFile(path) as tif:
            pages=list(tif.pages)
            base=sum(pages[0].databytecounts); overviews=sum(sum(p.databytecounts) for p in pages[1:])
            detail={'pages':[{'width':p.imagewidth,'height':p.imagelength,
                       'compression':str(p.compression.name),'tile_width':p.tilewidth,'tile_height':p.tilelength,
                       'ycbcr_subsampling':list(p.tags['YCbCrSubSampling'].value) if 'YCbCrSubSampling' in p.tags else None}
                       for p in pages]}
    return {'base_payload_bytes':base,'overview_payload_bytes':overviews,
            'metadata_padding_bytes':Path(path).stat().st_size-base-overviews,**detail}


def chunks(ds, block=512):
    for y in range(0, ds.RasterYSize, block):
        for x in range(0, ds.RasterXSize, block):
            yield x,y,min(block,ds.RasterXSize-x),min(block,ds.RasterYSize-y)


def rgb(ds, window):
    import numpy as np
    return np.stack([ds.GetRasterBand(i).ReadAsArray(*window) for i in (1,2,3)])


def description(ds):
    from osgeo import gdal
    return {'width':ds.RasterXSize,'height':ds.RasterYSize,'bands':ds.RasterCount,
            'types':[gdal.GetDataTypeName(ds.GetRasterBand(i).DataType) for i in range(1,ds.RasterCount+1)],
            'color_interpretation':[gdal.GetColorInterpretationName(ds.GetRasterBand(i).GetColorInterpretation()) for i in range(1,ds.RasterCount+1)],
            'block_size':list(ds.GetRasterBand(1).GetBlockSize()),
            'geotransform':list(ds.GetGeoTransform()),'projection':ds.GetProjection(),
            'image_structure':ds.GetMetadata('IMAGE_STRUCTURE'),
            'overviews':[[ds.GetRasterBand(1).GetOverview(i).XSize,ds.GetRasterBand(1).GetOverview(i).YSize]
                         for i in range(ds.GetRasterBand(1).GetOverviewCount())]}


def finalize_reference(c, root, ds, synthetic=False):
    import numpy as np
    pixels = 0
    for w in chunks(ds):
        pixels += int(np.count_nonzero(np.any(rgb(ds,w)!=0,axis=0)))
    total = ds.RasterXSize*ds.RasterYSize
    d = description(ds)
    if d['bands']!=3 or d['types']!=['Byte']*3 or d['color_interpretation']!=['Red','Green','Blue']:
        raise ValueError('Canonical image must be three-band uint8 RGB')
    if d['geotransform'][1]!=10 or d['geotransform'][5]!=-10:
        raise ValueError('Canonical image must use a 10 m grid')
    d.update(schema_version=1,synthetic=synthetic,valid_pixels=pixels,valid_bytes=pixels*3,
             base_sample_bytes=total*3,nodata_fraction=1-pixels/total,
             valid_GB=pixels*3/1e9,valid_GiB=pixels*3/1024**3,
             base_GB=total*3/1e9,base_GiB=total*3/1024**3,
             byte_definition='3 RGB uint8 samples per valid pixel; excludes overviews and TIFF metadata')
    if not synthetic and (pixels*3 < c['raster']['minimum_valid_bytes'] or
                          d['nodata_fraction'] > c['raster']['maximum_nodata_fraction']):
        write_json(root/'dataset-rejected.json',d)
        raise ValueError('Mosaic does not meet valid-size/nodata contract; inspect dataset-rejected.json')
    ds = None
    d['file_bytes']=(root/'reference.tif').stat().st_size
    d['sha256']=sha256(root/'reference.tif')
    write_json(root/'dataset.json',d)
    print(f"Reference accepted: {d['valid_bytes']/1e9:.3f} GB valid RGB; nodata {d['nodata_fraction']:.3%}",flush=True)


def fixture(c, root, _=None):
    from osgeo import osr
    import numpy as np
    gdal = gdal_setup(c)
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    p=root/'reference.tif'
    ds=gdal.GetDriverByName('GTiff').Create(str(p),2048,2048,3,gdal.GDT_Byte,
        ['TILED=YES','BLOCKXSIZE=512','BLOCKYSIZE=512','COMPRESS=NONE','BIGTIFF=YES','INTERLEAVE=PIXEL'])
    sr=osr.SpatialReference(); sr.ImportFromEPSG(32636)
    ds.SetProjection(sr.ExportToWkt()); ds.SetGeoTransform([670000,10,0,3550000,0,-10])
    rng=np.random.default_rng(1234)
    y,x=np.mgrid[:2048,:2048]
    for band in range(1,4):
        a=((x//(band*3)+y//(band+2)+rng.integers(0,25,(2048,2048)))%254+1).astype('uint8')
        a[:80,:80]=0
        ds.GetRasterBand(band).WriteArray(a)
        ds.GetRasterBand(band).SetColorInterpretation([gdal.GCI_RedBand,gdal.GCI_GreenBand,gdal.GCI_BlueBand][band-1])
    ds.SetMetadataItem('NODATA_VALUES','0 0 0')
    ds.BuildOverviews('AVERAGE',c['raster']['overviews'])
    ds.FlushCache(); finalize_reference(c,root,ds,True)


def prepare(c,root,_=None):
    gdal=gdal_setup(c); root=Path(root)
    old=read_json(root/'dataset.json')
    if old and not old['synthetic'] and (root/'reference.tif').exists() and sha256(root/'reference.tif')==old['sha256']:
        print('Verified canonical reuse'); return
    selection=read_json(root/'selection.json')
    if not selection:
        raise ValueError('Run fetch first')
    from .fetch import deserialize
    import copernicus_downloader as d
    work=root/'mosaic'; work.mkdir(exist_ok=True)
    with measured(root/'prepare.metrics.json',temporary_paths=[root/'reference.partial.tif']) as stats:
        warps=[]
        for i,record in enumerate(selection['products']):
            p=deserialize(record)
            archive=root/'downloads'/d.product_filename(p)
            check=read_json(root/'download-checksums'/f'{p.id}.json')
            if not check or sha256(archive)!=check['sha256']:
                raise ValueError(f'Unverified product: {p.id}')
            with zipfile.ZipFile(archive) as z:
                members=[n for n in z.namelist() if '/R10m/' in n and n.endswith('_TCI_10m.jp2')]
            if len(members)!=1:
                raise ValueError(f'Expected one TCI member in {p.id}')
            src=f'/vsizip/{archive}/{members[0]}'
            warped=work/f'{i:03d}.vrt'
            # One warp per product avoids GDAL's multiple-source VRT limitation.
            ds=gdal.Warp(str(warped),src,format='VRT',dstSRS=c['raster']['crs'],
                xRes=10,yRes=10,resampleAlg='near',srcNodata=0,dstNodata=0,
                warpOptions=['UNIFIED_SRC_NODATA=YES'],multithread=False)
            if ds is None: raise ValueError('Source warp failed')
            ds=None; warps.append(str(warped))
        vrt=gdal.BuildVRT(str(work/'mosaic.vrt'),warps,resolution='user',xRes=10,yRes=10,
                          outputBounds=c['raster']['bounds'],srcNodata=0,VRTNodata=0)
        temp=root/'reference.partial.tif'
        ds=gdal.Translate(str(temp),vrt,format='GTiff',outputType=gdal.GDT_Byte,
            creationOptions=['BIGTIFF=YES','TILED=YES','BLOCKXSIZE=512','BLOCKYSIZE=512',
                             'COMPRESS=NONE','INTERLEAVE=PIXEL','SPARSE_OK=FALSE'])
        for b in range(1,4): ds.GetRasterBand(b).DeleteNoDataValue()
        ds.SetMetadataItem('NODATA_VALUES','0 0 0')
        ds.BuildOverviews('AVERAGE',c['raster']['overviews']); ds.FlushCache(); ds=None; vrt=None
        temp.replace(root/'reference.tif')
        ds=gdal.Open(str(root/'reference.tif'))
        finalize_reference(c,root,ds)
        stats['output_bytes']=(root/'reference.tif').stat().st_size


def creation_options(c,v):
    comp=v['compression']; block=c['raster']['block_size']; drv=v['driver']
    if drv=='GPKG':
        return [f'TILE_FORMAT={comp}',f'BLOCKSIZE={block}','RASTER_TABLE=imagery',
                'TILING_SCHEME=CUSTOM',f'QUALITY={v.get("quality",80)}','VERSION=1.3']
    if drv=='COG':
        opts=[f'COMPRESS={comp}',f'BLOCKSIZE={block}','BIGTIFF=YES','OVERVIEWS=FORCE_USE_EXISTING',
              f'NUM_THREADS={c["raster"]["threads"]}']
        if 'level' in v: opts.append(f'LEVEL={v["level"]}')
        if 'quality' in v: opts.append(f'QUALITY={v["quality"]}')
        if 'predictor' in v: opts.extend(['PREDICTOR=YES','OVERVIEW_PREDICTOR=YES'])
        return opts
    opts=['BIGTIFF=YES','TILED=YES',f'BLOCKXSIZE={block}',f'BLOCKYSIZE={block}',
          'INTERLEAVE=PIXEL',f'COMPRESS={comp}','COPY_SRC_OVERVIEWS=YES',
          f'NUM_THREADS={c["raster"]["threads"]}']
    if 'predictor' in v: opts.append(f'PREDICTOR={v["predictor"]}')
    if comp=='DEFLATE': opts.append(f'ZLEVEL={v["level"]}')
    if comp=='ZSTD': opts.append(f'ZSTD_LEVEL={v["level"]}')
    if comp=='JPEG': opts.extend([f'JPEG_QUALITY={v["quality"]}','PHOTOMETRIC=YCBCR'])
    if comp=='WEBP': opts.extend([f'WEBP_LEVEL={v["quality"]}','WEBP_LOSSLESS=FALSE'])
    return opts


def convert(c,root,variant=None):
    gdal=gdal_setup(c); root=Path(root)
    (root/'variants').mkdir(exist_ok=True)
    source=root/'reference.tif'
    compatibility=read_json(root/'compatibility.json',{})
    for v in c['variants']:
        if variant and v['id']!=variant: continue
        if compatibility.get(v['id'],{}).get('supported') is False:
            continue
        dest=variant_path(root,v); marker=root/'conversion'/f'{v["id"]}.json'
        old=read_json(marker)
        if old and old['status']=='complete' and dest.exists() and sha256(dest)==old['sha256']:
            if 'storage_layout' not in old:
                old['storage_layout']=storage_layout(dest);write_json(marker,old)
            continue
        if v['id']=='none':
            if dest.exists(): dest.unlink()
            os.link(source,dest)
            write_json(marker,{'schema_version':1,'status':'complete','variant':v['id'],
                'wall_seconds':0,'cpu_seconds':0,'peak_rss_bytes':0,'bytes':dest.stat().st_size,
                'sha256':sha256(dest),'storage_layout':storage_layout(dest),
                'note':'hard link to canonical; preparation measured separately'})
            continue
        print('Converting '+v['id'],flush=True)
        temp=dest.with_name(dest.stem+'.partial'+dest.suffix)
        if temp.exists(): temp.unlink()
        opts=creation_options(c,v)
        with measured(marker,{'variant':v['id'],'driver':v['driver'],'creation_options':opts},temporary_paths=[temp]) as stats:
            src=gdal.Open(str(source))
            ds=gdal.Translate(str(temp),src,format=v['driver'],creationOptions=opts)
            if v['driver']=='GPKG':
                # Allocate pyramid, then replace its pixels from canonical overviews.
                # Never use JPEG base pixels as the final overview reference.
                ds.BuildOverviews('NEAREST',c['raster']['overviews'])
                for j in range(src.GetRasterBand(1).GetOverviewCount()):
                    target=ds.GetRasterBand(1).GetOverview(j)
                    ref=src.GetRasterBand(1).GetOverview(j)
                    if (ref.XSize,ref.YSize)!=(target.XSize,target.YSize):
                        raise ValueError('GeoPackage overview grid mismatch')
                    for y in range(0,ref.YSize,512):
                        for x in range(0,ref.XSize,512):
                            for b in range(1,4):
                                arr=src.GetRasterBand(b).GetOverview(j).ReadAsArray(x,y,min(512,ref.XSize-x),min(512,ref.YSize-y))
                                ds.GetRasterBand(b).GetOverview(j).WriteArray(arr,x,y)
            ds.FlushCache(); ds=None; src=None
            temp.replace(dest)
            stats.update(bytes=dest.stat().st_size,sha256=sha256(dest),storage_layout=storage_layout(dest))


def validate(c,root,variant=None):
    import numpy as np
    from PIL import Image
    from skimage.metrics import structural_similarity
    gdal=gdal_setup(c); root=Path(root)
    src=gdal.Open(str(root/'reference.tif'))
    quality=root/'quality'; quality.mkdir(exist_ok=True)
    for v in c['variants']:
        if variant and v['id']!=variant: continue
        dest=variant_path(root,v)
        if not dest.exists(): continue
        marker=quality/f'{v["id"]}.json'; old=read_json(marker)
        digest=sha256(dest)
        if old and old.get('sha256')==digest and old.get('status')=='complete': continue
        print('Validating '+v['id'],flush=True)
        ds=gdal.Open(str(dest))
        if (ds.RasterXSize,ds.RasterYSize)!=(src.RasterXSize,src.RasterYSize):
            raise ValueError('Candidate dimensions differ')
        if max(abs(a-b) for a,b in zip(ds.GetGeoTransform(),src.GetGeoTransform()))>1e-7:
            raise ValueError('Candidate georeferencing differs')
        if not ds.GetSpatialRef().IsSame(src.GetSpatialRef()): raise ValueError('CRS differs')
        count=0; absolute=0.; squared=0.; max_error=0; differing=0
        windows=list(chunks(src)); rng=random.Random(c['workload']['seed'])
        sample_ids=set(rng.sample(range(len(windows)),min(c['raster']['quality_sample_count'],len(windows))))
        ssims=[]; samples=[]
        with measured(quality/f'{v["id"]}.metrics.json'):
            for k,w in enumerate(windows):
                a=rgb(src,w); b=rgb(ds,w)
                diff=a.astype('int16')-b.astype('int16')
                # All pixels are compared for bit-exactness, including nodata.
                differing+=int(np.count_nonzero(diff))
                mask=np.any(a!=0,axis=0)
                d=diff[:,mask].astype('float64')
                count+=d.size; absolute+=np.abs(d).sum(); squared+=(d*d).sum()
                max_error=max(max_error,int(np.abs(diff).max()))
                if k in sample_ids and min(a.shape[1:])>=7:
                    s=structural_similarity(a.transpose(1,2,0),b.transpose(1,2,0),channel_axis=2,data_range=255)
                    ssims.append(float(s))
                    if len(samples)<6:
                        prefix=f'{v["id"]}-{len(samples)}'
                        Image.fromarray(a.transpose(1,2,0)).save(quality/f'{prefix}-reference.png')
                        Image.fromarray(b.transpose(1,2,0)).save(quality/f'{prefix}-candidate.png')
                        Image.fromarray(np.minimum(np.abs(diff)*8,255).astype('uint8').transpose(1,2,0)).save(quality/f'{prefix}-difference8x.png')
                        samples.append({'prefix':prefix,'pixel_window':w,'ssim':float(s)})
            overviews=[]
            if ds.GetRasterBand(1).GetOverviewCount()!=src.GetRasterBand(1).GetOverviewCount():
                raise ValueError('Overview count differs')
            for j in range(src.GetRasterBand(1).GetOverviewCount()):
                ab=src.GetRasterBand(1).GetOverview(j); bb=ds.GetRasterBand(1).GetOverview(j)
                if (ab.XSize,ab.YSize)!=(bb.XSize,bb.YSize): raise ValueError('Overview shape differs')
                errors=0; sq=0.; n=0
                for y in range(0,ab.YSize,512):
                    for x in range(0,ab.XSize,512):
                        for band in range(1,4):
                            aa=src.GetRasterBand(band).GetOverview(j).ReadAsArray(x,y,min(512,ab.XSize-x),min(512,ab.YSize-y))
                            ba=ds.GetRasterBand(band).GetOverview(j).ReadAsArray(x,y,aa.shape[1],aa.shape[0])
                            dd=aa.astype('float64')-ba
                            errors+=int(np.count_nonzero(dd)); sq+=(dd*dd).sum(); n+=dd.size
                overviews.append({'level':c['raster']['overviews'][j],'different_samples':errors,'rmse':math.sqrt(sq/n)})
            mse=squared/count if count else 0
            result={'schema_version':1,'status':'complete','variant':v['id'],'sha256':digest,
                'lossless':v['lossless'],'different_samples':differing,'valid_samples':count,
                'mae':absolute/count if count else 0,'rmse':math.sqrt(mse),
                'psnr_db':10*math.log10(255**2/mse) if mse else None,'psnr_infinite':bool(mse==0),
                'max_error':max_error,'sampled_ssim':float(np.mean(ssims)) if ssims else None,
                'samples':samples,'overviews':overviews,'raster':description(ds),
                'file_bytes':dest.stat().st_size}
            if v['lossless'] and (differing or any(o['different_samples'] for o in overviews)):
                result['status']='failed'; write_json(marker,result)
                raise ValueError(f'Lossless pixel validation failed: {v["id"]}')
            write_json(marker,result)
        ds=None
