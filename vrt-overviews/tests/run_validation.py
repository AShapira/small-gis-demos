"""Run the generated-data suite and retain fixtures, logs, and a JSON summary.

Run with QGIS Python: python tests/run_validation.py --output-directory C:\\tests\\vrt-run
The output directory must not already exist. No production folders are read.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import unittest

from osgeo import gdal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory', required=True, type=Path)
    args = parser.parse_args()
    args.output_directory.mkdir(parents=True, exist_ok=False)
    os.environ['VRT_TEST_ROOT'] = str(args.output_directory.resolve())
    started = time.monotonic()
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern='test_*.py')
    with (args.output_directory / 'unittest.log').open('w', encoding='utf-8') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    report = dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                  skipped=len(result.skipped), successful=result.wasSuccessful(),
                  elapsed_seconds=round(time.monotonic() - started, 3),
                  python=sys.version, gdal=gdal.VersionInfo('--version'))
    (args.output_directory / 'results.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)
    print(f'Evidence: {args.output_directory}', flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
