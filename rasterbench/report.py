"""Offline HTML/Markdown report reconstructed exclusively from saved evidence."""
from __future__ import annotations

import csv
import json
import math
import shutil
from collections import defaultdict
from pathlib import Path

from .common import percentile, read_json, utc, write_json


def confidence(values,seed=1234):
    """Bootstrap repeated-run values, not correlated individual tile requests."""
    import random
    if len(values)<2: return None
    r=random.Random(seed)
    means=[sum(r.choices(values,k=len(values)))/len(values) for _ in range(2000)]
    return [percentile(means,.025),percentile(means,.975)]


def recommendations(c,rows):
    decisions=[]
    target_users=c['workload']['max_users']
    for variant in sorted({r['job']['variant'] for r in rows if r['job']['stage']=='capacity'}):
        eligible=[]
        for profile in c['study']['profiles']:
            group=[r for r in rows if r['job']['stage']=='capacity' and r['job']['variant']==variant
                   and r['job']['profile']['id']==profile['id'] and r['job']['users']==target_users]
            reasons=[]
            expected=len(c['study']['scenarios'])*c['study']['capacity_repetitions']
            cells={(r['job']['scenario'],r['job']['repetition']) for r in group}
            required={(s,n) for s in c['study']['scenarios'] for n in range(c['study']['capacity_repetitions'])}
            if len(group)!=expected or cells!=required: reasons.append('incomplete scenario/repetition matrix')
            for r in group:
                limit=c['acceptance']['hit_view_p95_seconds'] if r['job']['scenario']=='hit' else c['acceptance']['render_view_p95_seconds']
                if not r.get('valid') or r.get('synthetic') or not r.get('controller_complete'):
                    reasons.append('invalid, unfinished or synthetic evidence')
                if r['view_latency']['p95'] is None or r['view_latency']['p95']>=limit: reasons.append('view latency target')
                if r['view_error_fraction']>=c['acceptance']['max_view_error_fraction']: reasons.append('view error target')
                res=r.get('resources',{})
                headroom=1-c['acceptance']['minimum_headroom_fraction']
                if res.get('cpu_utilization_p95') is None or res['cpu_utilization_p95']>headroom: reasons.append('CPU headroom')
                if res.get('memory_utilization_peak') is None or res['memory_utilization_peak']>headroom: reasons.append('memory headroom')
            if not reasons: eligible.append(profile)
            decisions.append({'variant':variant,'profile':profile,'qualifies':not reasons,'reasons':sorted(set(reasons))})
    winners={}
    for d in decisions:
        if d['qualifies']:
            v=d['variant']; p=d['profile']
            if v not in winners or (p['cpus'],p['memory_gib'])<(winners[v]['cpus'],winners[v]['memory_gib']): winners[v]=p
    return {'decisions':decisions,'smallest_profiles':winners,
            'note':'Only complete real-data capacity matrices can qualify. CPU p95 and peak cgroup memory must leave 25% headroom.'}


def generate(c,root):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import markdown
    target_users=c['workload']['max_users']
    root=Path(root); out=root/'report'; out.mkdir(exist_ok=True)
    (out/'assets').mkdir(exist_ok=True)
    rows=[read_json(p) for p in sorted((root/'runs').glob('*/summary.json'))]
    if c.get('execution_plan'):
        rows=[r for r in rows if r['job'].get('plan_id')==c['execution_plan']['plan_id']]
    dataset=read_json(root/'dataset.json',{})
    compat=read_json(root/'compatibility.json',{})
    extension_compat=read_json(root/'compatibility-webp.json',{})
    delivery_compat=read_json(root/'delivery-compatibility-webp.json',{})
    conversion={p.stem:read_json(p) for p in (root/'conversion').glob('*.json')}
    quality={p.stem:read_json(p) for p in (root/'quality').glob('*.json') if not p.stem.endswith('.metrics')}
    rec=recommendations(c,rows)
    write_json(out/'recommendations.json',rec)
    write_json(out/'summaries.json',rows)
    write_json(out/'configuration.json',c)
    summary_rows=[]
    for r in rows:
        j=r['job']; res=r.get('resources',{})
        summary_rows.append({'run_id':j['id'],'stage':j['stage'],'variant':j['variant'],'scenario':j['scenario'],
            'profile':j['profile']['id'],'users':j['users'],'repetition':j['repetition'],
            'valid':r['valid'],'synthetic':r['synthetic'],'view_p95_seconds':r['view_latency']['p95'],
            'request_p95_seconds':r['request_latency']['p95'],'request_p99_seconds':r['request_latency']['p99'],
            'views_per_second':r['views_per_second'],'requests_per_second':r['requests_per_second'],
            'view_error_fraction':r['view_error_fraction'],'hit_fraction':r['hit_fraction'],
            'response_bytes':r['response_bytes'],'cpu_utilization_p95':res.get('cpu_utilization_p95'),
            'peak_memory_bytes':res.get('peak_cgroup_bytes'),'peak_heap_bytes':res.get('peak_heap_used_bytes'),
            'seed_seconds':r['seed_seconds'],'output_format':j['output_format']})
    if summary_rows:
        with (out/'runs.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=summary_rows[0]); w.writeheader(); w.writerows(summary_rows)
    lines=['# GeoServer raster-compression benchmark',f'Generated: {utc()}',
        '## Study status',
        f"Completed measurement cells: **{len(rows)}**. Dataset: **{'synthetic fixture' if dataset.get('synthetic') else 'Copernicus RGB' if dataset else 'not prepared'}**."]
    if not rows: lines.append('Performance, capacity and deployment recommendations are pending. No timings are inferred from file sizes.')
    if rec['smallest_profiles']:
        lines.append(f'Qualified profiles at {target_users} active users:')
        for v,p in rec['smallest_profiles'].items(): lines.append(f"- **{v}**: {p['cpus']} vCPU, {p['memory_gib']} GiB container memory, {p['heap_gib']} GiB heap.")
    else:
        lines.append('**No deployment profile is qualified by the currently available evidence.** See recommendations.json for missing tests or failed thresholds.')
    lines+=['## Dataset and provenance',
        f"Base sample bytes: {dataset.get('base_sample_bytes','pending')}; valid sample bytes: {dataset.get('valid_bytes','pending')}; nodata fraction: {dataset.get('nodata_fraction','pending')}.",
        f"Dimensions: {dataset.get('width','pending')} × {dataset.get('height','pending')}. Valid imagery: {dataset.get('valid_GB','pending')} GB / {dataset.get('valid_GiB','pending')} GiB.",
        'The 5 GB requirement counts valid RGB uint8 samples before compression, excluding overviews. The common reference is a 10 m EPSG:32636 mosaic. The data contain real regional imagery, including Israel and neighboring areas. No repeated pixels or artificial resolution increase are used to reach the target.',
        'TCI is a display product. Lossless results mean preservation of the canonical display pixels; they do not establish preservation of original scientific reflectance. Catalogue cloud percentages rank scenes and do not prove individual sample windows are cloud-free.',
        'Product IDs, acquisition dates, footprint selection, archive checksums, source order, image IDs, Python dependencies, OS package versions and deployed code hashes are retained with the study.',
        '## Format compatibility', '| Candidate | GeoServer support | Explanation |','|---|---|---|']
    for v in c['variants']:
        a=compat.get(v['id'],{})
        lines.append(f"| {v['id']} | {a.get('supported','pending')} | {str(a.get('reason',a.get('stage','pending'))).replace('|','/').replace(chr(10),' ')[:250]} |")
    if extension_compat:
        lines+=['### Version-matched WebP profile','| Candidate | Reader/render result | Explanation |','|---|---|---|']
        for name,a in extension_compat.items():
            lines.append(f"| {name} | {a['supported']} | {a.get('reason','Three-scale WMS probe passed').replace('|','/')} |")
        lines+=['| Output | WMS | GeoWebCache |','|---|---|---|']
        for name,a in delivery_compat.items(): lines.append(f"| {name} | {a['supported']} | {a.get('gwc_supported',False)} |")
    lines+=['ZSTD tests a modern lossless decoder; PackBits measures run-length compression on photographic data; neither is assumed to be faster or smaller before measurement. COG changes TIFF organization while keeping codec settings aligned. GeoPackage adds SQLite tile lookup and is therefore a storage-layout comparison as well as a codec comparison.',
        'WebP source reading and WMS output are separate capabilities. An installed output encoder does not prove that WebP-compressed TIFF or GeoPackage is readable. Unsupported configurations remain visible in the compatibility results.',
        'JPEG2000 is excluded because the standard JPEG2K extension depends on Kakadu. No proprietary server codec is introduced.',
        '## Conversion cost and disk space',
        '| Candidate | File GB | Ratio to reference | Conversion seconds | CPU seconds | Peak RSS GiB | MAE | PSNR dB | SSIM (sampled) |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    storage=[]
    for v in c['variants']:
        a=conversion.get(v['id'],{}); q=quality.get(v['id'],{})
        if a.get('status')!='complete': continue
        b=a.get('bytes',0); ref=dataset.get('file_bytes',1)
        psnr='infinite' if q.get('psnr_infinite') else str(q.get('psnr_db','pending'))
        lines.append(f"| {v['id']} | {b/1e9:.3f} | {b/ref:.3f} | {a.get('wall_seconds',0):.2f} | {a.get('cpu_seconds',0):.2f} | {a.get('peak_rss_bytes',0)/1024**3:.3f} | {q.get('mae','pending')} | {psnr} | {q.get('sampled_ssim','pending')} |")
        storage.append((v['id'],b/1e9))
    if storage:
        fig,ax=plt.subplots(figsize=(10,5)); ax.barh([x[0] for x in storage],[x[1] for x in storage]); ax.set_xlabel('Source file GB, including overviews'); fig.tight_layout(); fig.savefig(out/'assets/storage.png',dpi=150); plt.close(fig)
        lines.append('![Source storage](assets/storage.png)')
    lines+=['### Base samples, overviews and conversion working files',
            '| Candidate | Encoded base GB | Encoded overviews GB | Metadata/padding GB | Peak temporary file GB |',
            '|---|---:|---:|---:|---:|']
    costs=[]
    for name,a in conversion.items():
        if a.get('status')!='complete': continue
        layout=a.get('storage_layout',{})
        if not layout: continue
        item={'variant':name,'base_bytes':layout['base_payload_bytes'],'overview_bytes':layout['overview_payload_bytes'],
              'metadata_bytes':layout['metadata_padding_bytes'],'temporary_peak_bytes':a.get('peak_temporary_file_bytes',0)}
        costs.append(item)
        lines.append('| '+name+' | '+' | '.join(f'{item[k]/1e9:.4f}' for k in ('base_bytes','overview_bytes','metadata_bytes','temporary_peak_bytes'))+' |')
    if costs:
        with (out/'storage.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=costs[0]);w.writeheader();w.writerows(costs)
    preparation=read_json(root/'prepare.metrics.json',{})
    if preparation:
        lines.append(f"Canonical preparation: {preparation.get('wall_seconds')} wall seconds, {preparation.get('cpu_seconds')} CPU seconds, {preparation.get('peak_rss_bytes')} peak RSS bytes and {preparation.get('peak_temporary_file_bytes')} peak temporary-file bytes. This shared cost precedes candidate conversion.")
    lines+=['Source files include overview storage. The uncompressed reference is hard-linked into the variant directory and occupies disk once. Download archives, conversion working files and the GWC tile cache are separate costs. Per-cell cache sizes and seeding times quantify the additional delivery-storage cost; caches are truncated only after their measurements are saved.',
        'Compression can reduce disk traffic but introduces decode work. Conversion cost is paid when preparing or updating imagery; serving cost is paid on requests requiring raster access. Ratios alone cannot determine deployment performance.',
        '## Serving performance and cache behavior',
        'Hit scenarios preseed the exact trace. Miss scenarios use fresh 4×4 metatile regions; neighboring requests may become hits as a metatile is rendered. Mixed scenarios seed approximately 80% of the requested metatile-weighted working set. Actual response headers determine HIT/MISS/BYPASS classification.',
        'Direct WMS bypasses GeoWebCache. Its first requests and requests after a server restart can still benefit from Linux and Windows filesystem caches. This study never labels a restart as a cold-disk test.',
        'Users navigate 1024×768 views, issue at most six simultaneous tile requests, retain the preceding viewport tiles, and pause 3–8 seconds after completion. Per-request statistics and complete-view statistics are both reported. Slow or stalled clients reduce achieved activity rate; throughput and view counts must therefore be considered alongside latency.',
        'Miss sweeps also use zoom 15 to provide enough fresh metatiles for the configured users. At this latitude that display scale oversamples 10 m source pixels; it does not add source detail. Results remain grouped by zoom.',
        '### Repeated-run results',
        '| Stage | Candidate | Scenario | Users | Profile | Output | Server | Mean run p95 view seconds | Bootstrap 95% interval | Valid repeats |',
        '|---|---|---|---:|---|---|---|---:|---|---:|']
    groups=defaultdict(list)
    for r in rows:
        j=r['job']; groups[(j['stage'],j['variant'],j['scenario'],j['users'],j['profile']['id'],j['output_format'],j.get('server_profile','vanilla'))].append(r)
    for key,group in sorted(groups.items()):
        values=[r['view_latency']['p95'] for r in group if r['valid'] and r['view_latency']['p95'] is not None]
        avg=f'{sum(values)/len(values):.4f}' if values else 'unavailable'
        lines.append('| '+' | '.join(map(str,key))+f' | {avg} | {confidence(values)} | {len(values)}/{len(group)} |')
    for scenario in c['study']['scenarios']:
        chosen=[r for r in rows if r['job']['stage']=='screening' and r['job']['users']==target_users and r['job']['scenario']==scenario and r['valid']]
        if chosen:
            variants=sorted({r['job']['variant'] for r in chosen})
            vals=[[r['view_latency']['p95'] for r in chosen if r['job']['variant']==v] for v in variants]
            fig,ax=plt.subplots(figsize=(11,5)); ax.boxplot(vals,tick_labels=variants); ax.set_ylabel('Complete view p95 per run (seconds)'); ax.tick_params(axis='x',rotation=40); ax.set_title(f'{target_users} active users: {scenario}'); fig.tight_layout(); fig.savefig(out/f'assets/{scenario}.png',dpi=150); plt.close(fig)
            lines.append(f'![{scenario} latency](assets/{scenario}.png)')
    lines+=[f'Bootstrap intervals resample repeated runs, not correlated tiles. Screening uses {c["study"]["screening_repetitions"]} repeats and capacity uses {c["study"]["capacity_repetitions"]}; small repeat counts provide weak uncertainty estimates. Invalid runs remain in raw results and are excluded from recommendations.',
        '### First rendered view after GeoServer restart',
        'Filesystem caches remain uncontrolled. Publication reads metadata before the first GetMap. The value below isolates the first completed view; the full-cell p95 above also includes subsequent views.',
        '| Source | First-view seconds |','|---|---:|']
    for r in rows:
        if r['job']['stage']=='first_access': lines.append(f"| {r['job']['variant']} | {r.get('first_view_seconds','unavailable')} |")
    lines+=[
        '## Memory, CPU and deployment sizing',
        'The planned starting allocation is 8 vCPU, 16 GiB container memory and an 8 GiB Java heap. Available resource profiles are 2/4, 4/8, 8/16 and 12/24 vCPU/GiB profiles. Heap is half of each memory limit, leaving space for native buffers, threads, image decoding and filesystem cache.',
        'CPU utilization is derived from cgroup usage deltas relative to the assigned CPU quota. RSS, heap occupancy/GC, cgroup memory and file-cache accounting describe different components and must not be added as if disjoint. The qualification rule conservatively includes file cache in the peak cgroup memory headroom calculation.',
        f'A profile qualifies only after all required {target_users}-user capacity cells meet p95 view targets, fewer than 1% failed views, and at least 25% CPU/memory headroom. Missing resource telemetry, a saturated generator, exhausted miss traces, synthetic data or failed cache contracts prevent qualification.',
        'Cat-watch stays running. Its observed CPU/memory activity and VM memory availability are stored alongside server measurements. Results apply to this shared workstation and local network path, not automatically to another CPU, storage device, WAN or deployment.',
        f'Core cells measure {c["workload"]["measurement_seconds"]}-second windows following warm-up. Continuing endurance cells can use longer windows, recorded in each job. They do not establish long-term memory stability or a production availability guarantee. Telemetry collection itself adds small subprocess and filesystem-accounting costs to each cell.',
        '## Visual quality',
        'Every decoded base pixel and reference overview pixel is checked for lossless candidates. Lossy MAE/RMSE/PSNR use valid reference samples; SSIM is a deterministic spatial sample. RGB-to-YCbCr conversion and JPEG chroma subsampling are part of the measured JPEG configuration.',
        'Lossy comparisons use the same canonical reference. Examine fine urban edges, roads, fields, gradients and seams; a single global metric does not establish visual acceptability for every use case. Amplified difference images use an 8× absolute difference and must not be interpreted as ordinary viewing appearance.']
    for v in c['variants']:
        q=quality.get(v['id'],{})
        if not v['lossless'] and q.get('samples'):
            sample=q['samples'][0]; prefix=sample['prefix']
            for suffix in ('reference','candidate','difference8x'):
                name=f'{prefix}-{suffix}.png'; shutil.copy2(root/'quality'/name,out/'assets'/name)
            lines.append(f"### {v['id']} sample\n\nReference / candidate / amplified difference:\n\n![reference](assets/{prefix}-reference.png) ![candidate](assets/{prefix}-candidate.png) ![difference](assets/{prefix}-difference8x.png)")
    delivery=read_json(root/'delivery-quality/results.json',{})
    lines+=['## Additional delivery encoding loss',
            'The WebP extension profile compares PNG, JPEG and WebP responses from identical source views. Encoder defaults are retained and recorded; these are not equal-quality encoders. The version-matched WebP module calls the ImageIO writer with its defaults and does not expose a quality parameter. Measurements on JPEG-80 sources quantify the extra loss from delivery encoding after source compression.',
            '| Source | Region | Output | Response bytes | MAE | RMSE | PSNR dB | SSIM |',
            '|---|---|---|---:|---:|---:|---:|---:|']
    for v in delivery.get('views',[]):
        lines.append(f"| {v['variant']} | {v['region']} | {v['format']} | {v['bytes']} | {v['mae']:.4f} | {v['rmse']:.4f} | {v['psnr_db']} | {v['ssim']:.5f} |")
    if not delivery: lines.append('Delivery quality measurements are pending.')
    lines+=['## Reproduction and limitations',
        'Use the saved configuration and image lock with the documented PowerShell launcher. `resume` preserves the product selection and skips verified artifacts/completed cells; changing configuration requires a new study ID. `report` rebuilds this document from saved data without new HTTP performance tests.',
        'Full raw requests and views are gzip JSONL files under `runs/<id>`. Each run also retains its deterministic trace, telemetry, GC log, phase record and JSON summary. Conversion and quality metrics, the source manifest and dependency inventory remain in the named data volume.',
        'Read README.md and docs/methodology.md for commands, pinning, lifecycle, interpretation and safe cleanup.',
        '## Technical references',
        '- [GeoServer 3.0.1](https://geoserver.org/release/3.0.1/)',
        '- [GeoServer raster GeoPackage](https://docs.geoserver.org/3.0.x/en/user/data/raster/geopkg/)',
        '- [GeoWebCache response headers](https://docs.geoserver.org/3.0.x/en/user/geowebcache/responseheaders/)',
        '- [GeoServer WebP output](https://docs.geoserver.org/3.0.x/en/user/community/webp/)',
        '- [GDAL GeoTIFF](https://gdal.org/en/stable/drivers/raster/gtiff.html)',
        '- [GDAL COG](https://gdal.org/en/stable/drivers/raster/cog.html)',
        '- [Copernicus downloader](https://github.com/AShapira/copernicus-downloader)']
    text='\n\n'.join(lines)+'\n'
    # Consecutive table rows must be adjacent in Markdown.
    text=text.replace('|\n\n|','|\n|')
    (out/'report.md').write_text(text)
    html=markdown.markdown(text,extensions=['tables','fenced_code'])
    (out/'report.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>GeoServer raster benchmark</title><style>body{font:16px/1.6 system-ui;max-width:1250px;margin:40px auto;padding:0 24px;color:#172533}h1,h2{line-height:1.2}h2{margin-top:2.5em}table{border-collapse:collapse;display:block;overflow:auto;font-size:13px}td,th{border:1px solid #cbd5df;padding:7px;text-align:left}th{background:#eef3f7}img{max-width:100%;height:auto}code{background:#eef3f7;padding:2px 4px}a{color:#005f99}</style>'+html+'</html>')
    print('Report generated: '+str(out/'report.html'),flush=True)
