"""Retain a portable archive of the exact locally built study images."""
import json,subprocess
from pathlib import Path
from rasterbench.common import load_config,sha256,write_json
from rasterbench.runtime import Runtime
r=Runtime(load_config('configs/full.yaml'))
lock=json.loads((r.repo/'infra/images.lock.json').read_text())
ext=json.loads((r.repo/'infra/extensions.lock.json').read_text())
filename='images-'+lock['worker_image_id'].removeprefix('sha256:')[:12]+'.tar'
win=r.r['export_dir'].rstrip('/')+'/'+filename
local=Path(win if __import__('os').name=='nt' else '/mnt/'+win[0].lower()+win[2:])
local.parent.mkdir(parents=True,exist_ok=True)
images=[r.r['worker_image'],r.r['geoserver_image'],ext['image']]
if not local.exists():
    subprocess.run(r.base+['save','--multi-image-archive','--format','docker-archive','--output',win,*images],check=True)
write_json(local.with_suffix('.manifest.json'),{'images':images,'worker':lock,'webp':ext,'archive_sha256':sha256(local),'archive_bytes':local.stat().st_size})
print(local,flush=True)
