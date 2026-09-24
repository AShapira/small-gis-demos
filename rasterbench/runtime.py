"""Host-side Windows Podman control with ownership checks and no host mounts."""
from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

from .common import fingerprint, read_json, utc, write_json


class Runtime:
    def __init__(self,c):
        self.c=c; self.r=c['runtime']; self.prefix=self.r['prefix']+'-'+c['study_id']
        self.pm=os.environ.get('RASTERBENCH_PODMAN') or shutil.which('podman.exe')
        if not self.pm and os.name == 'nt':
            p=Path(os.environ.get('LOCALAPPDATA', ''))/'Programs'/'Podman'/'podman.exe'
            self.pm=str(p) if p.is_file() else shutil.which('podman')
        if not self.pm: raise RuntimeError('Windows Podman executable not found; set RASTERBENCH_PODMAN')
        self.base=[self.pm,'--connection',self.r['connection']]
        self.worker=self.prefix+'-worker'; self.server=self.prefix+'-gs'; self.loader=self.prefix+'-load'
        self.volume=self.prefix+'-data'; self.gsvolume=self.prefix+'-catalog'; self.network=self.prefix+'-net'
        self.root='/data/'+c['study_id']
        self.repo=Path(__file__).resolve().parents[1]
        self.local=self.repo/'.runs'/c['study_id']; self.local.mkdir(parents=True,exist_ok=True)
        old=read_json(self.local/'config.json')
        if old and fingerprint(old)!=fingerprint(c):
            raise ValueError('Configuration changed for this study ID. Choose a new study_id for reproducibility.')
        if not old: write_json(self.local/'config.json',c)

    def call(self,*args,input=None,check=True,timeout=None):
        p=subprocess.run(self.base+list(args),input=input,capture_output=True,timeout=timeout)
        if check and p.returncode:
            # Never include input: it can contain a secret or a configuration file.
            raise RuntimeError(f'Podman {args[0]} failed: '+p.stderr.decode(errors='replace')[-3000:])
        return p.stdout.decode(errors='replace').strip()

    def exists(self,kind,name):
        p=subprocess.run(self.base+[kind,'exists',name],capture_output=True)
        return p.returncode==0

    def owned(self,name):
        a=json.loads(self.call('inspect',name))[0]
        if a['Config'].get('Labels',{}).get('rasterbench.study')!=self.c['study_id']:
            raise RuntimeError(f'Refusing to change non-benchmark container {name}')
        return a

    def remove_container(self,name):
        if self.exists('container',name):
            self.owned(name); self.call('rm','-f',name)

    def label(self): return ['--label','rasterbench.study='+self.c['study_id']]

    def secret(self,suffix,value):
        name=self.prefix+'-'+suffix
        if not self.exists('secret',name): self.call('secret','create',name,'-',input=value)
        return name

    def put(self,container,path,data):
        blob=io.BytesIO()
        with tarfile.open(fileobj=blob,mode='w') as tar:
            entry=tarfile.TarInfo(path.lstrip('/')); entry.size=len(data); entry.mode=0o644
            tar.addfile(entry,io.BytesIO(data))
        self.call('exec','-i',container,'tar','xf','-','-C','/',input=blob.getvalue())

    def sync_code(self):
        blob=io.BytesIO()
        with tarfile.open(fileobj=blob,mode='w') as tar:
            for name in ('rasterbench','tests','scripts'):
                tar.add(self.repo/name,arcname=name,filter=lambda info: None if '__pycache__' in info.name else info)
        self.call('exec','-i',self.worker,'tar','--no-same-owner','-xf','-','-C','/app',input=blob.getvalue())
        import hashlib
        hashes={str(p.relative_to(self.repo)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (self.repo/'rasterbench').glob('*.py')}
        self.put(self.worker,self.root+'/code-manifest.json',json.dumps(hashes).encode())

    def ensure(self):
        info=json.loads(self.call('info','--format','json'))
        if not info['host']['security']['rootless']:
            raise RuntimeError('Benchmark requires the rootless Windows Podman connection')
        self.check_host_space()
        for v in (self.volume,self.gsvolume):
            if not self.exists('volume',v): self.call('volume','create',*self.label(),v)
            info_volume=json.loads(self.call('volume','inspect',v))[0]
            if info_volume.get('Labels',{}).get('rasterbench.study')!=self.c['study_id']:
                raise RuntimeError('Refusing to mount an unowned persistent volume: '+v)
        if not self.exists('network',self.network): self.call('network','create',*self.label(),self.network)
        network=json.loads(self.call('network','inspect',self.network))[0]
        if network.get('labels',network.get('Labels',{})).get('rasterbench.study')!=self.c['study_id']:
            raise RuntimeError('Refusing to use an unowned network: '+self.network)
        credentials=[]
        secret_dir=Path(os.environ.get('RASTERBENCH_CDSE_SECRET_DIR',
            str(self.repo/'.secrets')))
        for kind in ('username','password'):
            target=secret_dir/('cdse_'+kind)
            name=self.prefix+'-cdse-'+kind
            if not self.exists('secret',name):
                if not target.is_file(): raise RuntimeError('CDSE files missing; set RASTERBENCH_CDSE_SECRET_DIR')
                self.secret('cdse-'+kind,target.read_bytes())
            credentials+=['--secret',f'{name},target=cdse_{kind},mode=0400',
                          '-e',f'CDSE_{kind.upper()}_FILE=/run/secrets/cdse_{kind}']
        admin=self.secret('admin',secrets.token_urlsafe(32).encode())
        if not self.exists('container',self.worker):
            self.call('run','-d','--name',self.worker,*self.label(),
                '--network',self.network,'--cap-drop=all','--security-opt=no-new-privileges',
                '--cpus',str(self.r['worker_cpus']),'--memory',str(self.r['worker_memory_gib'])+'g',
                '--volume',self.volume+':/data',*credentials,
                '--secret',f'{admin},target=admin_password,mode=0400',
                '--entrypoint','sleep',self.r['worker_image'],'infinity')
        else:
            a=self.owned(self.worker)
            if not a['State']['Running']: self.call('start',self.worker)
        self.put(self.worker,'/data/config.json',json.dumps(self.c).encode())
        self.call('exec',self.worker,'mkdir','-p',self.root)
        self.sync_code()
        write_json(self.local/'engine.json',{'recorded_at':utc(),'podman':info,
            'worker_image':self.owned(self.worker)['Image'],
            'geoserver_image':self.r['geoserver_image']})
        self.put(self.worker,self.root+'/engine.json',(self.local/'engine.json').read_bytes())
        self.put(self.worker,self.root+'/config.json',json.dumps(self.c).encode())
        self.put(self.worker,self.root+'/windows-space.json',(self.local/'windows-space.json').read_bytes())
        for name in ('images.lock.json','extensions.lock.json','requirements.lock.txt'):
            self.put(self.worker,self.root+'/dependencies/'+name,(self.repo/'infra'/name).read_bytes())
        snapshot=io.BytesIO()
        with tarfile.open(fileobj=snapshot,mode='w') as tar:
            for name in ('rasterbench','scripts','tests','configs','infra','docs','README.md','pyproject.toml','THIRD_PARTY_NOTICES.md'):
                tar.add(self.repo/name,arcname=name,filter=lambda info:None if '__pycache__' in info.name else info)
        import hashlib
        digest=hashlib.sha256(snapshot.getvalue()).hexdigest()
        relative='dependencies/source-'+digest[:16]+'.tar'
        self.put(self.worker,self.root+'/'+relative,snapshot.getvalue())
        self.put(self.worker,self.root+'/source-snapshot.json',json.dumps({'path':relative,'sha256':digest}).encode())

    def check_host_space(self):
        import base64
        drive=self.r['export_dir'][0].upper()
        if drive not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ': raise ValueError('Export directory must name a Windows drive')
        powershell='powershell.exe' if os.name=='nt' else '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
        script="$ProgressPreference='SilentlyContinue'; Get-PSDrive "+drive+" | Select-Object Used,Free | ConvertTo-Json -Compress"
        result=subprocess.run([powershell,'-NoProfile','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()],
                              capture_output=True,text=True,timeout=30,check=True)
        space=json.loads(result.stdout)
        space.update(drive=drive,recorded_at=utc(),note='Physical Windows export drive; verify Podman machine resides on this drive')
        write_json(self.local/'windows-space.json',space)
        if space['Free']<(self.r['disk_budget_gb']+self.r['disk_reserve_gb'])*1e9:
            raise RuntimeError('Windows physical drive lacks configured budget plus reserve; inspect windows-space.json')

    def worker_args(self,action,*extra,root=None):
        return ['exec',self.worker,'python','-m','rasterbench.worker',action,'--config','/data/config.json',
                '--root',root or self.root,*extra]

    def action(self,action,*extra,root=None):
        args=self.worker_args(action,*extra,root=root)
        log=self.local/(action+'.log')
        with log.open('ab',buffering=0) as f:
            print(f'{action}: log {log}',flush=True)
            p=subprocess.run(self.base+args,stdout=f,stderr=subprocess.STDOUT)
        if p.returncode:
            raise RuntimeError(f'{action} failed (exit {p.returncode}); inspect {log}')

    def read(self,relative,default=None,root=None):
        out=self.call('exec',self.worker,'cat',(root or self.root)+'/'+relative,check=False)
        return json.loads(out) if out else default

    def server_start(self,profile,restart=False,server_profile='vanilla'):
        if self.exists('container',self.server):
            a=self.owned(self.server)
            if not restart and a['State']['Running']:
                return
            self.remove_container(self.server)
        heap=profile['heap_gib']
        image=self.r['geoserver_image']
        if server_profile=='webp':
            image=read_json(self.repo/'infra/extensions.lock.json')['image_id']
        opts=f'-Xms{heap}g -Xmx{heap}g -XX:+UseG1GC -XX:NativeMemoryTracking=summary -Xlog:gc*:file=/opt/geoserver_data/gc.log:time,uptime,level,tags:filecount=5,filesize=20m'
        self.call('run','-d','--name',self.server,*self.label(),'--network',self.network,
            '--network-alias','geoserver','--cpus',str(profile['cpus']),
            '--memory',str(profile['memory_gib'])+'g','--memory-swap',str(profile['memory_gib'])+'g',
            '-p',f'127.0.0.1:{self.r["port"]}:8080',
            '-v',self.volume+':/data:ro','-v',self.gsvolume+':/opt/geoserver_data',
            '--secret',f'{self.prefix}-admin,target=admin_password,mode=0444',
            '-e','GEOSERVER_ADMIN_PASSWORD_FILE=/run/secrets/admin_password',
            '-e','SKIP_DEMO_DATA=true','-e','INSTALL_EXTENSIONS=false',
            '-e','EXTRA_JAVA_OPTS='+opts,image)
        self.action('probe','--job','ready',root=self.root)
        self.put(self.worker,self.root+'/server-profile.json',json.dumps({**profile,'server_profile':server_profile,'image':image}).encode())

    def export(self):
        dest=self.r['export_dir'].rstrip('/')+'/'+self.c['study_id']
        # Stream an archive; Windows podman cp can resolve WSL paths inconsistently.
        local=Path(dest if os.name=='nt' else '/mnt/'+dest[0].lower()+dest[2:])
        local.mkdir(parents=True,exist_ok=True)
        names=['report','runs','conversion','quality','delivery-quality','dependencies','download-checksums',
               'config.json','dataset.json','selection.json','compatibility.json','compatibility-webp.json','compatibility-full.json',
               'delivery-compatibility.json','delivery-compatibility-webp.json','code-manifest.json','engine.json',
               'prepare.metrics.json','fetch.metrics.json','worker-environment.json','windows-space.json','storage-estimate.json','source-snapshot.json','execution-plan.json','plans','overnight-finalists.json']
        available=json.loads(self.call('exec',self.worker,'python','-c',
            'import json,pathlib; root=pathlib.Path('+repr(self.root)+'); print(json.dumps([n for n in '+repr(names)+' if (root/n).exists()]))'))
        cmd=self.base+['exec',self.worker,'tar','cf','-','-C',self.root,*available]
        p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        with tarfile.open(fileobj=p.stdout,mode='r|') as t:
            for member in t:
                if member.isfile():
                    target=local/member.name
                    if not target.resolve().is_relative_to(local.resolve()): raise ValueError('Unsafe export path')
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with target.open('wb') as f: shutil.copyfileobj(t.extractfile(member),f)
        _,err=p.communicate()
        if p.returncode: raise RuntimeError(err.decode())
        return local
