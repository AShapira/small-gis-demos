"""Build the separately identified, version-matched WebP server profile."""
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from bootstrap import ROOT,PODMAN,CONNECTION

lockpath=ROOT/'infra/extensions.lock.json'
lock=json.loads(lockpath.read_text())
base=json.loads((ROOT/'infra/images.lock.json').read_text())['geoserver_image']
blob=io.BytesIO()
with tarfile.open(fileobj=blob,mode='w') as tar:
    for name,a in lock['artifacts'].items():
        data=urllib.request.urlopen(a['url'],timeout=60).read()
        if hashlib.sha256(data).hexdigest()!=a['sha256']: raise ValueError('Extension checksum mismatch: '+name)
        item=tarfile.TarInfo(name);item.size=len(data);tar.addfile(item,io.BytesIO(data))
    body=(ROOT/'infra/Containerfile.webp').read_bytes()
    item=tarfile.TarInfo('infra/Containerfile.webp');item.size=len(body);tar.addfile(item,io.BytesIO(body))
name='localhost/rasterbench-geoserver-webp:3.0.1'
with tempfile.TemporaryFile() as context:
    context.write(blob.getvalue()); context.seek(0)
    subprocess.run([PODMAN,'--connection',CONNECTION,'build','--build-arg','BASE_IMAGE='+base,
                    '-f','infra/Containerfile.webp','-t',name,'-'],stdin=context,check=True)
info=json.loads(subprocess.check_output([PODMAN,'--connection',CONNECTION,'image','inspect',name]))[0]
lock.update(image=name,image_id=info['Id'],base_image=base)
lockpath.write_text(json.dumps(lock,indent=2)+'\n')
