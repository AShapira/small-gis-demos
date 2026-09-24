"""Read-only measurements of this cgroup, JVM, VM, and competing services."""
import json
import re
import time
from pathlib import Path

from .common import percentile, utc


def keyvalues(text):
    result={}
    for line in text.splitlines():
        parts=line.split()
        if len(parts)==2:
            try: result[parts[0]]=int(parts[1])
            except ValueError: pass
    return result


def sample():
    import psutil
    result={'at':utc(),'monotonic':time.monotonic(),'vm_available_bytes':psutil.virtual_memory().available}
    for name in ('cpu.stat','memory.current','memory.stat','memory.events','io.stat'):
        p=Path('/sys/fs/cgroup')/name
        result[name]=p.read_text() if p.exists() else None
    return result


SERVER_SCRIPT="""p=$(pgrep -o java)
for f in cpu.stat memory.current memory.stat memory.events io.stat; do
  echo SECTION:$f
  cat /sys/fs/cgroup/$f
done
echo SECTION:process
cat /proc/$p/status
echo SECTION:jstat
jstat -gc $p 2>/dev/null || true
echo SECTION:cache
du -sb /opt/geoserver_data/gwc 2>/dev/null || true
echo SECTION:vm
cat /proc/meminfo
"""


def server_sample(runtime,phase):
    raw=runtime.call('exec',runtime.server,'sh','-c',SERVER_SCRIPT,check=False,timeout=20)
    sections={}
    for part in raw.split('SECTION:')[1:]:
        key,_,value=part.partition('\n'); sections[key]=value.strip()
    result={'at':utc(),'monotonic':time.monotonic(),'phase':phase, 'raw':sections}
    result['cpu']=keyvalues(sections.get('cpu.stat',''))
    result['memory_stat']=keyvalues(sections.get('memory.stat',''))
    try: result['memory_bytes']=int(sections['memory.current'])
    except (KeyError,ValueError): result['memory_bytes']=None
    m=re.search(r'VmRSS:\s+(\d+)',sections.get('process',''))
    result['rss_bytes']=int(m.group(1))*1024 if m else None
    lines=sections.get('jstat','').splitlines()
    if len(lines)>=2:
        try:
            result['jvm']={k:float(v) for k,v in zip(lines[0].split(),lines[1].split())}
        except ValueError: pass
    vm=re.search(r'MemAvailable:\s+(\d+)',sections.get('vm',''))
    result['vm_available_bytes']=int(vm.group(1))*1024 if vm else None
    try: result['cache_bytes']=int(sections.get('cache','').split()[0])
    except (ValueError,IndexError): result['cache_bytes']=None
    return result


def summarize(rows,profile):
    measurement=[r for r in rows if r.get('phase')=='measurement']
    cpu=[]; throttle=[]
    for a,b in zip(measurement,measurement[1:]):
        dt=b['monotonic']-a['monotonic']
        if dt>0 and 'usage_usec' in a.get('cpu',{}) and 'usage_usec' in b.get('cpu',{}):
            cpu.append((b['cpu']['usage_usec']-a['cpu']['usage_usec'])/1e6/dt/profile['cpus'])
            throttle.append(max(0,b['cpu'].get('throttled_usec',0)-a['cpu'].get('throttled_usec',0))/1e6)
    memories=[r['memory_bytes'] for r in measurement if r.get('memory_bytes') is not None]
    rss=[r['rss_bytes'] for r in measurement if r.get('rss_bytes') is not None]
    heap=[sum(r['jvm'].get(k,0) for k in ('S0U','S1U','EU','OU'))*1024 for r in measurement if r.get('jvm')]
    def io_totals(row):
        totals={}
        for line in row.get('raw',{}).get('io.stat','').splitlines():
            for token in line.split()[1:]:
                key,_,value=token.partition('=')
                if value.isdigit():totals[key]=totals.get(key,0)+int(value)
        return totals
    io_delta={};gc_delta={}
    if len(measurement)>1:
        a,b=measurement[0],measurement[-1]
        first,last=io_totals(a),io_totals(b)
        io_delta={k:last[k]-first.get(k,0) for k in last}
        gc_delta={k:b.get('jvm',{}).get(k,0)-a.get('jvm',{}).get(k,0) for k in ('YGC','YGCT','FGC','FGCT','GCT')}
    return {'samples':len(measurement),'cpu_utilization_p95':percentile(cpu,.95),
        'disk_io_delta':io_delta,'gc_delta':gc_delta,
        'vm_available_min_bytes':min((r['vm_available_bytes'] for r in measurement if r.get('vm_available_bytes') is not None),default=None),
        'peak_file_cache_bytes':max((r.get('memory_stat',{}).get('file',0) for r in measurement),default=None),
        'cpu_throttled_seconds':sum(throttle),'peak_cgroup_bytes':max(memories,default=None),
        'peak_rss_bytes':max(rss,default=None),'peak_heap_used_bytes':max(heap,default=None),
        'peak_cache_bytes':max((r['cache_bytes'] for r in measurement if r.get('cache_bytes') is not None),default=None),
        'memory_utilization_peak':max(memories)/(profile['memory_gib']*1024**3) if memories else None,
        'complete':bool(cpu and memories and rss and heap)}
