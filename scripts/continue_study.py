"""Wait for an active stage, then refresh the idle worker and resume the study.

Run with `python -m scripts.continue_study --config configs/full.yaml`.
No scheduler or host power settings are changed. Only labelled benchmark
containers are refreshed; downloaded data stay in their persistent volume.
"""
import argparse
import os
import time
from types import SimpleNamespace
from rasterbench.common import load_config,write_json,utc
from rasterbench.controller import lock,execute
from rasterbench.runtime import Runtime

p=argparse.ArgumentParser();p.add_argument('--config',default='configs/full.yaml');args=p.parse_args()
c=load_config(args.config);r=Runtime(c)
write_json(r.local/'continuation.json',{'state':'waiting_for_active_stage','pid':os.getpid(),'updated_at':utc()})
print('Waiting for the current benchmark stage to release its checkpoint lock.',flush=True)
while True:
    try:
        with lock(r.local/'controller.lock'):
            if r.exists('container',r.worker) and r.owned(r.worker)['State']['Running']:
                code="import psutil,json; print(json.dumps([p.cmdline() for p in psutil.process_iter() if 'rasterbench.worker' in p.cmdline()]))"
                import json
                active=json.loads(r.call('exec',r.worker,'python','-c',code))
                if active:
                    print('A container stage is still active; preserving it.',flush=True)
                    time.sleep(10);continue
                r.remove_container(r.worker)
            break
    except RuntimeError as e:
        if 'controller is already running' not in str(e):raise
        time.sleep(10)
print('Resuming with the rebuilt worker and retained source data.',flush=True)
write_json(r.local/'continuation.json',{'state':'running','pid':os.getpid(),'updated_at':utc()})
try:
    execute(c,SimpleNamespace(action='resume',variant=None))
except BaseException as e:
    write_json(r.local/'continuation.json',{'state':'failed','error':str(e),'updated_at':utc()})
    raise
write_json(r.local/'continuation.json',{'state':'complete','updated_at':utc()})
