import argparse
import json
import sys

from .common import load_config


def main():
    p=argparse.ArgumentParser(description='Windows Podman GeoServer raster benchmark')
    p.add_argument('action',choices=['doctor','fetch','select','prepare','convert','validate','serve',
                                   'benchmark','report','run','resume','status','smoke','estimate'])
    p.add_argument('--config',default='configs/full.yaml')
    p.add_argument('--variant')
    args=p.parse_args()
    try:
        from .controller import execute
        execute(load_config(args.config),args)
    except (ValueError,RuntimeError) as e:
        print(str(e),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
