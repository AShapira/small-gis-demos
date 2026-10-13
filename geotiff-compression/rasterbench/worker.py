"""Container-side entrypoint. Does not receive a Podman socket."""
import argparse
import json
import sys
from pathlib import Path

from .common import read_json, write_json


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['fetch','select','prepare','fixture','convert','validate','probe',
                                      'load','report','doctor','telemetry','delivery-quality'])
    p.add_argument('--config', default='/data/config.json')
    p.add_argument('--root', default='/data/study')
    p.add_argument('--variant')
    p.add_argument('--job')
    args = p.parse_args()
    c = read_json(args.config)
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    if args.action in ('fetch','select'):
        from .fetch import run
        run(c, root, args.action == 'select')
    elif args.action in ('prepare','fixture','convert','validate'):
        from . import raster
        getattr(raster, args.action)(c, root, args.variant)
    elif args.action == 'probe':
        from .geoserver import probe
        probe(c, root, args.job == 'ready',profile=args.job if args.job in ('webp','full') else 'vanilla')
    elif args.action == 'load':
        import asyncio
        from .workload import run
        asyncio.run(run(c, root, read_json(args.job)))
    elif args.action == 'delivery-quality':
        from .delivery import quality
        quality(c,root)
    elif args.action == 'report':
        from .report import generate
        generate(c, root)
    elif args.action == 'telemetry':
        from .telemetry import sample
        print(json.dumps(sample()))
    elif args.action == 'doctor':
        from osgeo import gdal
        import shutil
        import platform
        write_json(root/'worker-environment.json', {'gdal':gdal.VersionInfo('--version'),
            'python':sys.version,'platform':platform.platform(),'free_bytes':shutil.disk_usage(root).free,
            'pip_freeze':Path('/opt/python-lock.txt').read_text(),
            'os_packages':Path('/opt/os-packages.txt').read_text()})
        print('Worker dependencies and storage recorded')


if __name__ == '__main__':
    main()
