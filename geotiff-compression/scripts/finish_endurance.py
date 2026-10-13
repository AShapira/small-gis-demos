"""Request a safe stop after every cell in a specified endurance cycle completes.

Runs beside the existing controller without modifying its live Python process.
Never starts load, restarts containers, deletes data or touches unrelated services.
"""
import argparse
import json
import os
import time
from pathlib import Path

from rasterbench.common import load_config, read_json, utc, write_json
from rasterbench.controller import lock
from rasterbench.runtime import Runtime


def cycle_complete(rows, cycle, variants):
    expected={(v,s) for v in variants for s in ('hit','miss','mixed','wms')}
    completed={(r['job']['variant'],r['job']['scenario']) for r in rows
               if r.get('controller_complete') and r['job'].get('plan_id')=='overnight-10'
               and r['job'].get('phase')=='endurance' and r['job'].get('cycle')==cycle
               and r['job'].get('users')==10}
    return completed==expected


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cycle',type=int,required=True)
    args=parser.parse_args();r=Runtime(load_config('configs/full.yaml'))
    status=r.local/'finish-state.json'
    def save(state,**extra):
        write_json(status,dict(state=state,pid=os.getpid(),target_cycle=args.cycle,
                               updated_at=utc(),**extra))
    with lock(r.local/'finish.lock'):
        save('waiting_for_cycle')
        variants=r.read('overnight-finalists.json')['variants']
        query=("import json; from pathlib import Path; "
               "rows=[json.loads(p.read_text()) for p in Path("+repr(r.root+"/runs")+").glob('*/summary.json')]; "
               "print(json.dumps([{'job':r['job'],'controller_complete':r.get('controller_complete',False)} "
               "for r in rows if r['job'].get('phase')=='endurance']))")
        while True:
            state=read_json(r.local/'overnight-state.json',{})
            if state.get('state')=='stopped':
                save('controller_stopped',cycle_verified=False);return
            rows=json.loads(r.call('exec',r.worker,'python','-c',query))
            if cycle_complete(rows,args.cycle,variants):
                # The controller exports a report at every cycle boundary. This
                # marker is picked up before it starts another measurement cell.
                (r.local/'STOP_AFTER_CELL').write_text(
                    f'User requested finish after endurance cycle {args.cycle}; verified {utc()}\n')
                save('stop_requested',cycle_verified=True)
                break
            time.sleep(5)
        while read_json(r.local/'overnight-state.json',{}).get('state') not in ('stopped','failed'):
            time.sleep(5)
        save('ready_for_final_report',cycle_verified=True)


if __name__=='__main__':main()
