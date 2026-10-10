#!/usr/bin/env python3
"""Build an isolated experimental GeoTools jar; never alter the retained server."""
import difflib
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path
import tarfile
import urllib.request

spec = importlib.util.spec_from_file_location('host', Path(__file__).with_name('affine-probe.py'))
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
tag = ('35.1-crop-read-clip' if '--clip-bounds' in sys.argv else
       '35.1-uncropped' if '--skip-crops' in sys.argv else
       '35.1-crop-read-padding' if '--read-padding' in sys.argv else
       '35.1-crop-read' if '--read-crop' in sys.argv else '35.1-crop')
root = host.OUT / 'patch-builds' / tag
root.mkdir(parents=True, exist_ok=True)
source_dir = host.OUT / 'source'
source_dir.mkdir(parents=True, exist_ok=True)
revision = '820904c219b584f817dbf7341ba2c480fc1a3e06'
hashes = {'GridCoverageRenderer.java': 'bb467e3062a8391405438de41812781e2685d57db308c815d047316550e4304d',
          'GridCoverageReaderHelper.java': '613ad2b7de133f6b2d69f655633431f2e36b462bee6d48a6aebcba52a4b764c3'}
source_contents = {}
for name, checksum in hashes.items():
    path = source_dir / name
    if not path.exists():
        url = ('https://raw.githubusercontent.com/geotools/geotools/' + revision +
               '/modules/library/render/src/main/java/org/geotools/renderer/lite/gridcoverage2d/' + name)
        path.write_bytes(urllib.request.urlopen(url, timeout=60).read())
    # The connector's text representation may append a blank line. Check exact
    # code bytes after normalizing trailing whitespace to a single final newline.
    code = path.read_bytes().rstrip() + b'\n'
    if hashlib.sha256(code).hexdigest() != checksum:
        raise RuntimeError('Unexpected upstream source: ' + name)
    source_contents[name] = code.decode()
original = source_contents['GridCoverageRenderer.java']
old = '''        // if there is an interpolation, preserve padding needed by it
        if (interpolation != null && !(interpolation instanceof InterpolationNearest)) {'''
new = '''        // A crop ROI on a rotated grid is quantized in source pixels. Preserve a
        // source-pixel margin even for nearest neighbour before scaling that ROI.
        MathTransform sourceGridToCRS = inputCoverage.getGridGeometry().getGridToCRS2D();
        boolean rotatedGrid = sourceGridToCRS instanceof AffineTransform at
                && (at.getShearX() != 0 || at.getShearY() != 0);
        if (interpolation != null && (!(interpolation instanceof InterpolationNearest) || rotatedGrid)) {'''
if original.count(old) != 1:
    raise RuntimeError('Source does not match the expected GeoTools 35.1 code')
patched = original.replace(old, new)
if '--clip-bounds' in sys.argv:
    # The final renderer crop was not implicated by the isolated experiments.
    patched = original
if '--skip-crops' in sys.argv:
    anchor = '        if (advancedProjectionHandlingEnabled) {'
    start = patched.index('    private GridCoverage2D crop(')
    pos = patched.index(anchor, start)
    patched = patched[:pos] + '''        MathTransform gridTransform = inputCoverage.getGridGeometry().getGridToCRS2D();
        if (!doReprojection && interpolation instanceof InterpolationNearest
                && gridTransform instanceof AffineTransform affineGrid
                && (affineGrid.getShearX() != 0 || affineGrid.getShearY() != 0)) {
            return inputCoverage;
        }

''' + patched[pos:]
(root / 'GridCoverageRenderer.java').write_text(patched)
patch = ''.join(difflib.unified_diff(original.splitlines(True), patched.splitlines(True),
    fromfile='a/modules/library/render/src/main/java/org/geotools/renderer/lite/gridcoverage2d/GridCoverageRenderer.java',
    tofile='b/modules/library/render/src/main/java/org/geotools/renderer/lite/gridcoverage2d/GridCoverageRenderer.java'))
(root / 'geotools-35.1-affine-nearest.patch').write_text(patch)
sources = [root / 'GridCoverageRenderer.java']
if any(flag in sys.argv for flag in ('--read-crop', '--read-padding', '--skip-crops', '--clip-bounds')):
    helper_original = source_contents['GridCoverageReaderHelper.java']
    helper = helper_original.replace('    private boolean sameCRS;',
                                     '    private boolean sameCRS;\n\n    private boolean preserveRotatedGridPixels;')
    anchor = '        sameCRS = CRS.isEquivalent(mapExtent.getCoordinateReferenceSystem(), reader.getCoordinateReferenceSystem());'
    helper = helper.replace(anchor, anchor + '''
        MathTransform sourceGridToCRS = reader.getOriginalGridToWorld(PixelInCell.CELL_CENTER);
        preserveRotatedGridPixels = sameCRS && interpolation instanceof InterpolationNearest
                && sourceGridToCRS instanceof java.awt.geom.AffineTransform at
                && (at.getShearX() != 0 || at.getShearY() != 0);''')
    anchor = '    private GridCoverage2D cropCoverage(GridCoverage2D coverage, ReferencedEnvelope cropEnvelope) {'
    helper = helper.replace(anchor, anchor + '''
        // The reader already selected the source window. A model-space crop here
        // quantizes its ROI on rotated source pixels before they are enlarged.
        // Preserve those pixels for the subsequent rendering steps.
        if (preserveRotatedGridPixels) return coverage;''')
    if '--read-padding' in sys.argv:
        anchor = '        paddingRequired = (!sameCRS || !(interpolation instanceof InterpolationNearest) || isMultiCRSReader(reader))'
        helper = helper.replace(anchor, '''        if (preserveRotatedGridPixels) {
            java.awt.geom.AffineTransform at = (java.awt.geom.AffineTransform) sourceGridToCRS;
            double pixelSpanX = Math.abs(at.getScaleX()) + Math.abs(at.getShearX());
            double pixelSpanY = Math.abs(at.getShearY()) + Math.abs(at.getScaleY());
            double requestedX = mapExtent.getWidth() / mapRasterArea.getWidth();
            double requestedY = mapExtent.getHeight() / mapRasterArea.getHeight();
            this.padding = Math.max(this.padding,
                    (int) Math.ceil(2 * Math.max(pixelSpanX / requestedX, pixelSpanY / requestedY)));
        }
        paddingRequired = (preserveRotatedGridPixels || !sameCRS || !(interpolation instanceof InterpolationNearest) || isMultiCRSReader(reader))''')
    (root / 'GridCoverageReaderHelper.java').write_text(helper)
    sources.append(root / 'GridCoverageReaderHelper.java')
    patch += ''.join(difflib.unified_diff(helper_original.splitlines(True), helper.splitlines(True),
        fromfile='a/modules/library/render/src/main/java/org/geotools/renderer/lite/gridcoverage2d/GridCoverageReaderHelper.java',
        tofile='b/modules/library/render/src/main/java/org/geotools/renderer/lite/gridcoverage2d/GridCoverageReaderHelper.java'))
    (root / 'geotools-35.1-affine-nearest.patch').write_text(patch)
builder = 'affine-probe-build'
image = host.inspect(host.SERVER)['ImageName']
existing = host.podman('ps', '-a', '--filter', 'name=^' + builder + '$', '--format', '{{.Names}}').stdout.decode().strip()
if not existing:
    host.podman('run', '-d', '--name', builder, '--label', 'affine.probe=true', '--network', 'none',
                '--entrypoint', 'sleep', image, 'infinity')
else:
    import json
    info = json.loads(host.podman('inspect', builder).stdout)[0]
    if info['Config'].get('Labels', {}).get('affine.probe') != 'true':
        raise RuntimeError('Unowned build container')
    host.podman('start', builder)
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode='w') as z:
    for source in sources:
        z.add(source, arcname=source.name)
host.podman('exec', builder, 'mkdir', '-p', '/tmp/affine-build/classes')
# Reset the disposable build directory and library from the retained original,
# so building a simpler variant after a larger one cannot inherit stale classes.
host.podman('exec', builder, 'rm', '-rf', '/tmp/affine-build/classes')
host.podman('exec', builder, 'mkdir', '-p', '/tmp/affine-build/classes')
host.podman('exec', '-i', builder, 'tar', '--no-same-owner', '-xf', '-', '-C', '/tmp/affine-build', data=buf.getvalue())
lib = '/usr/local/tomcat/webapps/geoserver/WEB-INF/lib'
original_jar = host.podman('exec', host.SERVER, 'cat', lib + '/gt-render-35.1.jar').stdout
host.podman('exec', '-i', builder, 'sh', '-c', 'cat > ' + lib + '/gt-render-35.1.jar', data=original_jar)
affine_original_jar = host.podman('exec', host.SERVER, 'cat', lib + '/affine-0.9.2.jar').stdout
host.podman('exec', '-i', builder, 'sh', '-c', 'cat > ' + lib + '/affine-0.9.2.jar', data=affine_original_jar)
host.podman('exec', builder, 'javac', '-proc:none', '-cp', lib + '/*', '-d', '/tmp/affine-build/classes',
            *['/tmp/affine-build/' + source.name for source in sources], capture=False)
host.podman('exec', builder, 'cp', lib + '/gt-render-35.1.jar', '/tmp/affine-build/gt-render-35.1.jar')
host.podman('exec', builder, 'jar', 'uf', '/tmp/affine-build/gt-render-35.1.jar', '-C', '/tmp/affine-build/classes', '.')
jar = host.podman('exec', builder, 'cat', '/tmp/affine-build/gt-render-35.1.jar').stdout
(root / 'gt-render-35.1.jar').write_bytes(jar)
imagen_metadata = None
if '--clip-bounds' in sys.argv:
    path = source_dir / 'AffineOpImage.java'
    if not path.exists():
        url = ('https://raw.githubusercontent.com/eclipse-imagen/imagen/0.9.2/'
               'modules/affine/src/main/java/org/eclipse/imagen/media/affine/AffineOpImage.java')
        path.write_text(urllib.request.urlopen(url, timeout=60).read().decode().rstrip() + '\n')
    imagen_original = path.read_text()
    expected_hash = '2b9fc568e71fbfa5e4835f37375c28ea5d7073780d75ebebcd6098395d6bd354'
    if hashlib.sha256(imagen_original.encode()).hexdigest() != expected_hash:
        raise RuntimeError('Unexpected ImageN 0.9.2 source')
    imagen_patched = imagen_original
    for axis in ('x', 'y'):
        for old, new in (
            (f'int dx1 = ceilRatio({axis}1, {axis}denom);',
             f'int dx1 = {axis}denom < 0 ? floorRatio({axis}1, {axis}denom) + 1 : ceilRatio({axis}1, {axis}denom);'),
            (f'int dx2 = floorRatio({axis}2, {axis}denom) + 1;',
             f'int dx2 = {axis}denom < 0 ? floorRatio({axis}2, {axis}denom) + 1 : ceilRatio({axis}2, {axis}denom);')):
            if imagen_patched.count(old) != 1:
                raise RuntimeError('Unexpected scanline clipping source')
            imagen_patched = imagen_patched.replace(old, new)
    anchor = '        if (clipMaxX < dst_min_x) clipMaxX = dst_min_x;'
    imagen_patched = imagen_patched.replace(anchor, anchor + '\n        if (clipMaxX < clipMinX) clipMaxX = clipMinX;')
    (root / 'AffineOpImage.java').write_text(imagen_patched)
    (root / 'imagen-0.9.2-clip.patch').write_text(''.join(difflib.unified_diff(
        imagen_original.splitlines(True), imagen_patched.splitlines(True),
        fromfile='a/modules/affine/src/main/java/org/eclipse/imagen/media/affine/AffineOpImage.java',
        tofile='b/modules/affine/src/main/java/org/eclipse/imagen/media/affine/AffineOpImage.java')))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as z:
        z.add(root / 'AffineOpImage.java', arcname='AffineOpImage.java')
        z.add(host.REPO / 'tests/java/AffineScanlineRegression.java', arcname='AffineScanlineRegression.java')
    host.podman('exec', '-i', builder, 'tar', '--no-same-owner', '-xf', '-', '-C', '/tmp/affine-build', data=buf.getvalue())
    host.podman('exec', builder, 'rm', '-rf', '/tmp/affine-build/imagen-classes', '/tmp/affine-build/test-classes')
    host.podman('exec', builder, 'mkdir', '-p', '/tmp/affine-build/imagen-classes', '/tmp/affine-build/test-classes')
    host.podman('exec', builder, 'javac', '-proc:none', '-cp', lib + '/*', '-d', '/tmp/affine-build/imagen-classes',
                '/tmp/affine-build/AffineOpImage.java', capture=False)
    host.podman('exec', '-i', builder, 'sh', '-c', 'cat > /tmp/affine-build/affine-0.9.2.jar', data=affine_original_jar)
    host.podman('exec', builder, 'jar', 'uf', '/tmp/affine-build/affine-0.9.2.jar', '-C', '/tmp/affine-build/imagen-classes', '.')
    host.podman('exec', builder, 'javac', '-proc:none', '-cp', '/tmp/affine-build/imagen-classes:' + lib + '/*',
                '-d', '/tmp/affine-build/test-classes', '/tmp/affine-build/AffineScanlineRegression.java', capture=False)
    passed = host.podman('exec', builder, 'java', '-cp', '/tmp/affine-build/test-classes:/tmp/affine-build/imagen-classes:' + lib + '/*',
                         'org.eclipse.imagen.media.affine.AffineScanlineRegression')
    (root / 'regression-patched.txt').write_bytes(passed.stdout + passed.stderr)
    print(passed.stdout.decode(), end='')
    try:
        host.podman('exec', builder, 'java', '-cp', '/tmp/affine-build/test-classes:' + lib + '/*',
                    'org.eclipse.imagen.media.affine.AffineScanlineRegression')
    except RuntimeError as error:
        if 'Expected [0,2) but got RangeInt[0, 3]' not in str(error):
            raise
        (root / 'regression-original.txt').write_text(str(error) + '\n')
    else:
        raise AssertionError('Regression unexpectedly passes on the original ImageN library')
    affine_jar = host.podman('exec', builder, 'cat', '/tmp/affine-build/affine-0.9.2.jar').stdout
    (root / 'affine-0.9.2.jar').write_bytes(affine_jar)
    host.podman('exec', builder, 'cp', '/tmp/affine-build/affine-0.9.2.jar', lib + '/affine-0.9.2.jar')
    imagen_metadata = {'source_sha256': expected_hash, 'jar_sha256': hashlib.sha256(affine_jar).hexdigest()}
containerfile = 'FROM ' + image + '\nCOPY gt-render-35.1.jar ' + lib + '/gt-render-35.1.jar\n'
if imagen_metadata:
    containerfile += 'COPY affine-0.9.2.jar ' + lib + '/affine-0.9.2.jar\n'
(root / 'Containerfile').write_text(containerfile)
context = io.BytesIO()
with tarfile.open(fileobj=context, mode='w') as z:
    for name in ('Containerfile', 'gt-render-35.1.jar'):
        z.add(root / name, arcname=name)
# Windows Podman's streamed build context currently mis-translates its temporary
# path under WSL. Commit only this disposable, network-isolated builder instead.
host.podman('exec', builder, 'cp', '/tmp/affine-build/gt-render-35.1.jar', lib + '/gt-render-35.1.jar')
config = json.loads(host.podman('image', 'inspect', image).stdout)[0]['Config']
host.podman('commit', '--change', 'ENTRYPOINT ' + json.dumps(config.get('Entrypoint') or []),
            '--change', 'CMD ' + json.dumps(config.get('Cmd') or []),
            builder, 'localhost/affine-probe:' + tag, capture=False)
metadata = {'upstream': 'GeoTools 35.1', 'revision': revision, 'source_sha256': hashes,
            'jar_sha256': hashlib.sha256(jar).hexdigest(), 'image': 'localhost/affine-probe:' + tag,
            'baseline_image': image, 'baseline_jar_sha256': hashlib.sha256(original_jar).hexdigest()}
metadata['imagen'] = imagen_metadata
(root / 'build.json').write_text(json.dumps(metadata, indent=2) + '\n')
host.podman('stop', builder)
print(root)
