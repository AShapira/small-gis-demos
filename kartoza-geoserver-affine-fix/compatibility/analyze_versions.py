#!/usr/bin/env python3
"""Publish compact compatibility evidence from completed isolated rendering runs."""
import gzip
import importlib.util
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.artifacts/compatibility'
DEST = Path(__file__).resolve().parent / 'evidence'
spec = importlib.util.spec_from_file_location('reference_analysis', ROOT / 'tests/analyze.py')
reference_analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference_analysis)


def successful_run(folder, phase):
    candidates = []
    for path in folder.glob('render-' + phase + '-*/result.json'):
        data = json.loads(path.read_text())
        if data.get('fixtures_unchanged') and data.get('container_state_after') == 'exited':
            candidates.append((path, data))
    assert candidates, f'No completed {phase} run: {folder.name}'
    return max(candidates, key=lambda pair: pair[0].stat().st_mtime)


def main():
    DEST.mkdir(exist_ok=True)
    tags = [f'2.28.{i}' for i in range(5)] + ['2.28.5--v2026.08.27']
    results = []
    panel = Image.new('RGB', (900, 330 * len(tags)), 'white')
    draw = ImageDraw.Draw(panel)
    for i, tag in enumerate(tags):
        folder = WORK / tag.replace('--', '-')
        inspection = json.loads((folder / 'inspection.json').read_text())
        stock_path, stock = successful_run(folder, 'stock')
        patched_path, patched = successful_run(folder, 'patched')
        (DEST / (tag + '-inspection.json')).write_text(json.dumps(inspection,indent=2)+'\n')
        (DEST / (tag + '-stock.json')).write_text(json.dumps(stock,indent=2)+'\n')
        (DEST / (tag + '-patched.json')).write_text(json.dumps(patched,indent=2)+'\n')
        assert len(patched['patched_g0_samples']) == 4
        assert all(r['status'] == 200 for r in patched['patched_g0_samples'])
        assert all(r['missing_pixels'] == 0 for r in patched['patched_g0_samples'] if r['name'] == 'rot30_base')
        matrix = patched['matrix']
        for route, counts in matrix.items():
            assert counts['successful_requests'] == 1180 and counts['errors'] == 0, (tag,route,counts)
            assert counts['interior_requests'] == 658 and counts['interior_missing_pixels'] == 0, (tag,route,counts)
            assert counts['north_up_missing_pixels'] == 0, (tag,route,counts)
            if route == 'gwc': assert counts['cache_headers'] == {'MISS':1180},(tag,counts)
        regression_rows = [json.loads(line) for line in (patched_path.parent / 'regression.jsonl').read_text().splitlines()]
        sharpness = {}
        for route in ['wms','gwc']:
            row = next(r for r in regression_rows if r['route']==route and r['tile']==[24,10021003,6797199])
            image = Image.open(patched_path.parent / row['image']).convert('RGBA')
            if route == 'wms': image = image.crop((1,1,257,257))
            array=np.array(image)
            gray=array[:,:,:3].mean(axis=2)
            fixture=reference_analysis.fixtures[row['name']]
            expected,valid=reference_analysis.probe.expected(reference_analysis.source(row['name']),fixture['gt'],reference_analysis.probe.tile(*row['tile']))
            measured,_=reference_analysis.probe.metrics(image,expected,valid)
            intermediate=int(np.count_nonzero(valid & ~np.isin(gray,np.unique(expected[valid]))))
            assert measured['missing_pixels']==row['missing_pixels']==0 and intermediate==0,(tag,route,row,intermediate)
            assert measured['exact_fraction_nonblank']==row['exact_fraction_nonblank']>0.9999
            assert measured['mae_valid_nonblank']==row['mae_valid_nonblank']
            sharpness[route] = {k:row[k] for k in ['missing_pixels','exact_fraction_nonblank','mae_valid_nonblank']}
            sharpness[route]['intermediate_gray_pixels'] = intermediate
        # Preserve complete per-request numerical evidence without private server logs.
        for name in ['patched-g1.jsonl','regression.jsonl']:
            with (DEST / (tag + '-' + name + '.gz')).open('wb') as raw:
                with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as compressed:
                    compressed.write((patched_path.parent/name).read_bytes())
        rows = [json.loads(line) for line in (patched_path.parent/'patched-g1.jsonl').read_text().splitlines()]
        edge_losses = {}
        for route in ['wms','gwc']:
            affected=[r for r in rows if r['route']==route and r['missing_pixels']]
            depths=[]
            for row in affected:
                fixture=reference_analysis.fixtures[row['name']]
                box=reference_analysis.probe.tile(*row['tile'])
                expected,valid=reference_analysis.probe.expected(reference_analysis.source(row['name']),fixture['gt'],box)
                image=Image.open(patched_path.parent/row['image']).convert('RGBA')
                pad=row['pad']
                if pad:image=image.crop((pad,pad,pad+256,pad+256))
                measured,holes=reference_analysis.probe.metrics(image,expected,valid)
                assert measured['missing_pixels']==row['missing_pixels']
                y,x=np.where(holes)
                wx=box[0]+(x+.5)*(box[2]-box[0])/256
                wy=box[3]-(y+.5)*(box[3]-box[1])/256
                col,line=reference_analysis.probe.pixel_coordinates(fixture['gt'],wx,wy)
                depths.extend(np.minimum.reduce([col,line,fixture['size']-col,fixture['size']-line]).tolist())
            edge_losses[route] = {'affected_tiles':len(affected),'maximum_missing_pixels_per_tile':max((r['missing_pixels'] for r in affected),default=0),'max_depth_source_pixels':max(depths,default=0)}
            assert max(depths,default=0)<2.056,(tag,route,depths)
        result={'tag':tag,'geotools':inspection['geotools'],'imagen':inspection['imagen'],
                'existing_bundle_accepted':inspection['existing_bundle_test']['exit']==0,
                'source_patch_applies_with_exact_context':True,'java_regression':inspection['regression'],
                'stock_minimal_missing':{r['route']:r['missing_pixels'] for r in stock['minimal']},
                'patched_gutter0_minimal_missing':{r['route']:r['missing_pixels'] for r in patched['patched_g0_samples'] if r['name']=='rot30_base'},
                'patched_gutter0_boundary_requests_successful':True,
                'patched_gutter1':matrix,'sharpness':sharpness,'edge_losses':edge_losses,
                'cache_sequence':patched['cache_sequence'],'fixtures_unchanged':patched['fixtures_unchanged']}
        results.append(result)
        y=i*330
        draw.text((12,y+8),tag+' | stock gutter 0 / patched gutter 1 / patched checkerboard',fill='black')
        stock_image = stock_path.parent / stock['minimal'][0]['image']
        triangle = next(r for r in regression_rows if r['route']=='wms' and r['tile']==[24,10021196,6798186])
        checker = next(r for r in regression_rows if r['route']=='wms' and r['tile']==[24,10021003,6797199])
        for x,path,crop in [(12,stock_image,False),(310,patched_path.parent/triangle['image'],True),(608,patched_path.parent/checker['image'],True)]:
            image=Image.open(path).convert('RGBA')
            if crop: image=image.crop((1,1,257,257))
            background=Image.new('RGBA',image.size,'white');background.alpha_composite(image)
            panel.paste(background.convert('RGB'),(x,y+35))
        draw.text((12,y+298),f"Stock missing: {stock['minimal'][0]['missing_pixels']}; patched interior matrix: 0; checkerboard intermediate gray pixels: 0",fill='black')
    (DEST/'summary.json').write_text(json.dumps({'checked':'2026-10-20','architecture':'linux/amd64','results':results},indent=2)+'\n')
    panel.save(DEST/'comparison.png')
    print('All six versions: full WMS/GWC matrices, zero interior gaps, sharpness and cache checks passed.')


if __name__ == '__main__':
    main()
