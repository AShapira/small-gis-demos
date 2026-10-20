"""Exercise the sourced Kartoza hook, including its `|| true` invocation."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'apply-affine-fix.sh'
NAMES = ['gt-render-34.0.jar', 'affine-0.9.0.jar']


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.bundle, self.lib, self.backup = root / 'bundle', root / 'lib', root / 'backups'
        (self.bundle / 'jars').mkdir(parents=True)
        self.lib.mkdir()
        self.originals, self.patched = {}, {}
        manifest = []
        for name in NAMES:
            original, patched = ('original ' + name).encode(), ('patched ' + name).encode()
            self.originals[name], self.patched[name] = original, patched
            (self.lib / name).write_bytes(original)
            (self.bundle / 'jars' / name).write_bytes(patched)
            manifest.append(f'{name}\t{hashlib.sha256(original).hexdigest()}\t{hashlib.sha256(patched).hexdigest()}\n')
        (self.bundle / 'jars.sha256.tsv').write_text(''.join(manifest))

    def run_hook(self, mode='apply'):
        env = dict(os.environ, AFFINE_FIX_DIR=str(self.bundle), AFFINE_FIX_LIB_DIR=str(self.lib),
                   AFFINE_FIX_BACKUP_DIR=str(self.backup), AFFINE_FIX_MODE=mode)
        return subprocess.run(['bash', '-c', '. "$1" || true; printf "JVM_WOULD_START\\n"',
                               'test-hook', str(SCRIPT)], env=env, capture_output=True, text=True)

    def assert_refused(self):
        result = self.run_hook()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('JVM_WOULD_START', result.stdout)

    def test_apply_idempotence_and_restore(self):
        for _ in range(2):
            result = self.run_hook()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('JVM_WOULD_START', result.stdout)
            for name in NAMES:
                self.assertEqual((self.lib / name).read_bytes(), self.patched[name])
        for _ in range(2):
            result = self.run_hook('restore')
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in NAMES:
                self.assertEqual((self.lib / name).read_bytes(), self.originals[name])

    def test_unknown_second_jar_does_not_change_first(self):
        (self.lib / NAMES[1]).write_bytes(b'another build')
        self.assert_refused()
        self.assertEqual((self.lib / NAMES[0]).read_bytes(), self.originals[NAMES[0]])

    def test_corrupt_payload_does_not_change_either_jar(self):
        (self.bundle / 'jars' / NAMES[1]).write_bytes(b'corruption')
        self.assert_refused()
        for name in NAMES:
            self.assertEqual((self.lib / name).read_bytes(), self.originals[name])

    def test_duplicate_classpath_version_is_refused(self):
        (self.lib / 'affine-0.9.1.jar').write_bytes(b'other copy')
        self.assert_refused()

    def test_symlink_is_refused(self):
        original = self.lib / NAMES[0]
        target = self.lib / 'outside'
        original.rename(target)
        original.symlink_to(target)
        self.assert_refused()

    def test_partial_previous_apply_can_finish(self):
        name = NAMES[0]
        destination = self.backup / hashlib.sha256(self.originals[name]).hexdigest()
        destination.mkdir(parents=True)
        (destination / name).write_bytes(self.originals[name])
        (self.lib / name).write_bytes(self.patched[name])
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_hook('restore').returncode, 0)

    def test_corrupt_second_rollback_file_leaves_both_patched(self):
        self.assertEqual(self.run_hook().returncode, 0)
        name = NAMES[1]
        (self.backup / hashlib.sha256(self.originals[name]).hexdigest() / name).write_bytes(b'corrupt')
        result = self.run_hook('restore')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('JVM_WOULD_START', result.stdout)
        for name in NAMES:
            self.assertEqual((self.lib / name).read_bytes(), self.patched[name])


if __name__ == '__main__':
    unittest.main()
