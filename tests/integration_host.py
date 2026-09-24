"""Short real-container test of resource telemetry and cell resume."""
import copy,json
from rasterbench.common import load_config
from rasterbench.runtime import Runtime
from rasterbench.controller import run_cell,sync_load_code
c=load_config('configs/full.yaml');r=Runtime(c);r.sync_code();sync_load_code(r)
smoke=copy.deepcopy(c)
smoke['workload'].update(warmup_seconds=1,measurement_seconds=20,think_seconds=[.1,.2],zooms=[15],max_views_per_user=128)
root=r.root+'/probes'; config_path=root+'/load-config.json'
r.put(r.worker,config_path,json.dumps(smoke).encode())
job={'id':'host-integration-wms-v1','stage':'integration','variant':'none','profile':c['study']['profiles'][0],
     'users':2,'scenario':'wms','repetition':0,'output_format':'image/png'}
a=run_cell(r,job,root,config_path)
assert a['resources']['complete'] and a['controller_complete'] and a['synthetic'],a
assert run_cell(r,job,root,config_path)==a
print('HOST_INTEGRATION_PASS',json.dumps(a['resources']))
