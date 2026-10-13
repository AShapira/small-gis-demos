"""Build the final Markdown delivery artifact from exported evidence, offline."""
import argparse
import collections
import hashlib
import json
import re
import statistics
from pathlib import Path

from rasterbench.common import read_json, utc, write_json
from scripts.finish_endurance import cycle_complete


def mean(values):
    return statistics.mean(values) if values else 0


def build(root, preview=False):
    root=Path(root); out=root/'report'
    rows=[read_json(p) for p in (root/'runs').glob('*/summary.json')]
    rows=[r for r in rows if r.get('controller_complete') and r['job'].get('plan_id')=='overnight-10']
    selected=read_json(root/'overnight-finalists.json')['variants']
    phases=collections.Counter(r['job']['phase'] for r in rows)
    if not preview:
        for cycle in (0,1):
            if not cycle_complete(rows,cycle,selected):raise ValueError(f'Endurance cycle {cycle} is incomplete')
        if phases!={'source_comparison':40,'cached_browsing':12,'core_capacity':24,'extended_capacity':48,'delivery':6,'endurance':24}:
            raise ValueError(f'Unexpected final measurement matrix: {phases}')
    dataset=read_json(root/'dataset.json'); rec=read_json(out/'recommendations.json')
    config=read_json(out/'configuration.json')
    conversion={p.stem:read_json(p) for p in (root/'conversion').glob('*.json')}
    quality={p.stem:read_json(p) for p in (root/'quality').glob('*.json') if not p.stem.endswith('.metrics')}
    total_views=sum(r['views'] for r in rows); total_requests=sum(r['requests'] for r in rows)
    failed_views=sum(round(r['view_error_fraction']*r['views']) for r in rows)
    candidates=[v['id'] for v in config['variants'] if conversion.get(v['id'],{}).get('status')=='complete']
    smallest_lossless=min((v['id'] for v in config['variants'] if v['lossless'] and v['id'] in candidates),key=lambda v:conversion[v]['bytes'])
    lines=['# GeoServer raster-compression benchmark — final 10-user study','',
           ('**PREVIEW — testing has not yet been finalized.**' if preview else '**Final measurement report. Testing ended after two complete endurance cycles.**'),'',
           f'Generated: {utc()}. Scope: Windows Podman Desktop, GeoServer 3.0.1, real Copernicus RGB imagery, 10 human-paced active users.','',
           '## Executive findings','',
           f'- Completed **{len(rows)} measurement cells**, **{total_views:,} views** and **{total_requests:,} network requests**; {failed_views:,} failed views. All reported counts exclude cache preparation requests.',
           f'- Canonical imagery contains **{dataset["valid_GB"]:.3f} GB of valid RGB samples**, before compression and excluding overviews. The uncompressed file with overviews is **{dataset["file_bytes"]/1e9:.3f} GB**.',
           f'- The smallest tested lossless file is **{smallest_lossless}**, **{conversion[smallest_lossless]["bytes"]/1e9:.3f} GB**, saving **{100*(1-conversion[smallest_lossless]["bytes"]/dataset["file_bytes"]):.1f}%** versus the uncompressed file.',
           f'- JPEG-80 source storage is **{conversion["jpeg80"]["bytes"]/1e9:.3f} GB**, saving **{100*(1-conversion["jpeg80"]["bytes"]/dataset["file_bytes"]):.1f}%**; sampled SSIM is **{quality["jpeg80"]["sampled_ssim"]:.4f}** and PSNR **{quality["jpeg80"]["psnr_db"]:.2f} dB**. This is a lossy display option.',
           '- PackBits and JPEG-80 COG were selected by the screening rule for the capacity study, alongside the uncompressed baseline. Selection favors failed-view rate and then mean run p95 latency; it does not optimize storage or prove statistical superiority.','']
    for name,p in rec['smallest_profiles'].items():
        lines.append(f'- **{name}: {p["cpus"]} vCPU / {p["memory_gib"]} GiB container / {p["heap_gib"]} GiB Java heap** is the smallest tested qualifying profile for 10 users, with the required CPU and memory headroom.')
    if not rec['smallest_profiles']:lines.append('- **No measured resource profile qualifies** under all latency, error and headroom requirements.')
    lines+=['','The local measured path excludes a production WAN, browsers downloading other page assets, and competing production layers. These measurements do not establish 100-user capacity.','',
            '## Completion and scope','',
            '| Phase | Completed cells |','|---|---:|']
    for name,count in sorted(phases.items()):lines.append(f'| {name} | {count} |')
    lines+=['','The initial screening and capacity tests measured 120 seconds after 60 seconds of warm-up, with two repetitions. Endurance tests measured 300 seconds after 60 seconds of warm-up, using 4 vCPU / 8 GiB / 4 GiB heap. Two complete 12-test cycles cover the same three shortlisted sources and four cache scenarios. GeoServer restarts between cells: this tests repeatability, not a multi-day single-JVM memory-leak soak.','',
            '## Source compression: storage, conversion and observed rendering','',
            'Latencies below are the mean of the two run-level p95 complete-view latencies at 8 vCPU / 16 GiB. They are not pooled-request percentiles. Conversion costs include the complete file and overview output; baseline zero means reuse of the prepared canonical file.','',
            '| Source | Total GB | Saved vs baseline | Conversion s | CPU s | Peak RSS GiB | Miss view p95 s | WMS view p95 s |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for name in candidates:
        a=conversion[name]
        group=[r for r in rows if r['job']['phase']=='source_comparison' and r['job']['variant']==name]
        values=[mean([r['view_latency']['p95'] for r in group if r['job']['scenario']==scenario]) for scenario in ('miss','wms')]
        lines.append(f'| {name} | {a["bytes"]/1e9:.3f} | {100*(1-a["bytes"]/dataset["file_bytes"]):.1f}% | {a.get("wall_seconds",0):.2f} | {a.get("cpu_seconds",0):.2f} | {a.get("peak_rss_bytes",0)/1024**3:.3f} | {values[0]:.4f} | {values[1]:.4f} |')
    lines+=['','### Interpretation','',
            'For archival fidelity, compare ZSTD and DEFLATE: all tested lossless candidates preserve the canonical base and overview pixels. PackBits saves little storage on this photographic dataset, despite its screening latency result. JPEG-80 offers a much larger storage reduction but changes pixels; prefer lossless storage where small features, later analysis or pixel fidelity matter. JPEG-70 offers further space savings at additional quality loss. No visual-quality threshold was specified, so neither JPEG setting is declared universally acceptable.','',
            '**COG comparison limitation:** GDAL reports `LAYOUT=COG` for the nominal DEFLATE and JPEG-80 TIFF outputs as well as the explicit COG outputs. Thus these pairs do not isolate ordinary TIFF versus COG layout. Small latency differences between them cannot be attributed to a unique COG advantage. The study serves local files and does not test remote HTTP range-read performance.','',
            '## Capacity and headroom','',
            'A profile must pass every required scenario and repetition: p95 complete-view latency below 2 seconds for hits and 5 seconds for rendering, fewer than 1% failed views, CPU p95 below 75% of quota, and peak cgroup memory below 75% of its limit. Cgroup memory includes file cache. The following are worst run-level values for each full capacity matrix.','',
            '| Source | CPU / GiB | Hit p95 s | Non-hit p95 s | Worst failed views | CPU p95 | Peak memory GiB | Qualifies |','|---|---|---:|---:|---:|---:|---:|---|']
    for d in rec['decisions']:
        group=[r for r in rows if r['job']['stage']=='capacity' and r['job']['variant']==d['variant'] and r['job']['profile']['id']==d['profile']['id']]
        if not group:continue
        hit=max(r['view_latency']['p95'] for r in group if r['job']['scenario']=='hit')
        other=max(r['view_latency']['p95'] for r in group if r['job']['scenario']!='hit')
        lines.append(f'| {d["variant"]} | {d["profile"]["cpus"]} / {d["profile"]["memory_gib"]} | {hit:.4f} | {other:.4f} | {100*max(r["view_error_fraction"] for r in group):.3f}% | {100*max(r["resources"]["cpu_utilization_p95"] for r in group):.1f}% | {max(r["resources"]["peak_cgroup_bytes"] for r in group)/1024**3:.3f} | {d["qualifies"]} |')
    lines+=['','The 12 vCPU / 24 GiB profile was not exercised in this reduced overnight scope. Resource qualification applies only to the three shortlisted sources. Reserve separate host resources for monitoring, other services, and a load generator; the measured GeoServer limit is not a total workstation requirement.','',
            '## Endurance cycles and cache behavior','',
            '| Cycle (1-based) | Source | Scenario | View p95 s | Failed views | Actual cache HIT | Views/s | CPU p95 | Peak memory GiB |','|---:|---|---|---:|---:|---:|---:|---:|---:|']
    for r in sorted((r for r in rows if r['job']['phase']=='endurance'),key=lambda r:(r['job']['cycle'],r['job']['scenario'],r['job']['variant'])):
        j=r['job'];res=r['resources']
        lines.append(f'| {j["cycle"]+1} | {j["variant"]} | {j["scenario"]} | {r["view_latency"]["p95"]:.4f} | {100*r["view_error_fraction"]:.3f}% | {100*r["hit_fraction"]:.1f}% | {r["views_per_second"]:.3f} | {100*res["cpu_utilization_p95"]:.1f}% | {res["peak_cgroup_bytes"]/1024**3:.3f} |')
    lines+=['','A miss scenario is not 100% MISS responses: rendering one 4×4 metatile creates neighboring hits. Mixed browsing targets an 80% preseeded request share, while reuse and newly filled metatiles affect the actual ratio. Direct WMS bypasses GWC; its HIT fraction is not a useful cache metric.',
            '', 'Both endurance cycles completed without failed views. Their mixed scenarios achieved approximately 99.3% actual cache hits, so they represent highly cached browsing and do not establish performance at a sustained 80% hit ratio.', '',
            '## Validity, incidents and practical limits','',
            f'- {sum(bool(r.get("valid")) for r in rows)}/{len(rows)} completed cells pass the harness validity checks. Validity is separate from meeting latency/error deployment targets.',
            '- The two measured failed views were individual HTTP 400 tile responses: one ZSTD-3 screening miss and one uncompressed 8-vCPU capacity miss. They remain included in error rates; the saved client evidence does not establish their underlying cause.',
            '- One preseed preparation failed on 2026-09-15 before measurement. Its logs and telemetry were archived. Preparation was changed to retry failures at most three times and retain each attempt; measured requests are never retried to conceal failures. Completed earlier cells retain their original source hashes.',
            '- Canonical preprocessing reprojects each source at 10 m before mosaicking. GDAL maps valid zero-valued samples away from the reserved nodata value; lossless comparisons are against these canonical display pixels, not unmodified scientific reflectance.',
            '- Filesystem cache state is uncontrolled. Frequent GeoServer restarts are not cold-disk measurements. The overnight scope did not include dedicated no-warm-up first-access cells.',
            '- Rootful cat-watch was preserved. Background statistics are recorded, but shared-host scheduling and storage effects cannot be fully isolated.',
            '- Two repetitions provide weak statistical uncertainty. Bootstrap intervals in the detailed evidence should not be interpreted as strong proof of small differences.',
            '- PNG/JPEG/WebP output experiments use the separate WebP extension profile and its encoder defaults. These are not equal-quality encodings. GeoPackage and WebP source failures describe the pinned tested configuration, not every possible GeoServer installation.','',
            '## Exact reproduction and retained evidence','',
            'Source repository: `AShapira/small-gis-demos`. Large data reside in rootless Windows Podman named volumes; the RHEL source checkout is not the serving engine. Configuration and image digests are retained with each result.','',
            '```bash','python3 -m rasterbench.cli status',
            '# Rebuild the final delivery document from exported evidence, without new load:',
            'python3 -m scripts.final_report --root /path/to/export/israel-rgb-5gb','```','',
            'Re-running the original overnight command resumes an open-ended plan; do not use it to reproduce the bounded finish without also installing the documented finish guard. Keep `STOP_AFTER_CELL` after this finalization unless deliberately starting a newly authorized study. See docs/geoserver-benchmark.md and docs/operations.md for lifecycle instructions.','',
            'Raw per-request/view gzip JSONL, deterministic traces, CPU/memory/GC/I/O telemetry, conversion/quality manifests, product checksums, source snapshots, image pins and CSV summaries accompany this report in the exported study directory. The Markdown attachment is self-contained for numerical conclusions; linked charts and visual crops require the sibling `assets/` folder.','',
            '## Detailed evidence appendix','']
    appendix=(out/'report.md').read_text()
    # Repair whitespace between table rows in the original generated Markdown.
    appendix=re.sub(r'(?m)(^\|[^\n]*\|)\n\n(?=\|)',r'\1\n',appendix)
    lines.append(appendix)
    text='\n'.join(lines)+'\n'
    path=out/('final-report-preview.md' if preview else 'geoserver-compression-final-report.md')
    path.write_text(text)
    receipt={'generated_at':utc(),'preview':preview,'file':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
             'bytes':path.stat().st_size,'cells':len(rows),'phases':dict(phases),'views':total_views,
             'requests':total_requests,'failed_views':failed_views,'smallest_profiles':rec['smallest_profiles']}
    write_json(out/('final-preview.json' if preview else 'final-report-manifest.json'),receipt)
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--preview',action='store_true')
    a=parser.parse_args();print(json.dumps(build(a.root,a.preview),indent=2))
