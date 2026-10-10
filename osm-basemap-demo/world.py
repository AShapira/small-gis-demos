"""Executed inside the stock GDAL image, with /inputs read-only and /output writable."""
from pathlib import Path
import subprocess


def main():
    output = Path('/output/world.gpkg')
    temporary = Path('/output/world.partial.gpkg')
    if temporary.exists():
        temporary.unlink()
    first = True
    for scale in (110, 50, 10):
        for source, name in [('land', 'land'), ('ocean', 'ocean'), ('lakes', 'lakes'), ('admin_0_countries', 'countries'), ('admin_0_boundary_lines_land', 'boundaries'), ('populated_places', 'places')]:
            dataset = f'ne_{scale}m_{source}'
            args = ['ogr2ogr', '-f', 'GPKG']
            if not first:
                args += ['-update']
            args += [str(temporary), f'/vsizip//inputs/{dataset}.zip/{dataset}.shp', '-nln', f'world_{name}_{scale}', '-nlt', 'POINT' if name == 'places' else 'PROMOTE_TO_MULTI', '-t_srs', 'EPSG:3857', '-clipsrc', '-180', '-85.05112878', '180', '85.05112878', '-makevalid', '-lco', 'SPATIAL_INDEX=YES']
            subprocess.run(args, check=True)
            first = False
            if name == 'countries':
                subprocess.run(['ogr2ogr','-update',str(temporary),f'/vsizip//inputs/{dataset}.zip/{dataset}.shp','-dialect','SQLITE','-sql',f'SELECT MakePoint(LABEL_X, LABEL_Y, 4326) AS geom, NAME_EN FROM {dataset}','-nln',f'world_country_labels_{scale}','-t_srs','EPSG:3857','-lco','SPATIAL_INDEX=YES'],check=True)
    temporary.rename(output)


if __name__ == '__main__':
    main()
