"""Contract: reviewed Go content, deterministic ZIP digests, failure preserves locks.

The local GOPROXY fixture follows the Go module proxy protocol. Tests execute a
real Go SDK; expected h1 follows the published dirhash.Hash1 format, independently
of the updater. A changed archive, stale graph or partial update must be visible.
"""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('go_lock', Path(__file__).with_name('lock.py'))
lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock)


class LockTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proxy = self.root / 'proxy'
        self.sdk = os.environ.get('TEST_GO_SDK') or str(Path(os.environ['TEST_GO_SDK_ROOT_FILE']).resolve().parent)
        self.env = patch.dict(os.environ, {'GOPROXY': self.proxy.as_uri(), 'GOSUMDB': 'off',
                                         'GOMODCACHE': str(self.root / 'modcache')})
        self.env.start()
        self.addCleanup(self.env.stop)

    def module(self, version='v1.0.0'):
        path = 'example.com/sample'
        key = path + '@' + version
        folder = self.proxy / path / '@v'
        folder.mkdir(parents=True, exist_ok=True)
        files = {'go.mod': b'module example.com/sample\n\ngo 1.20\n', 'sample.go': b'package sample\n'}
        archive = folder / (version + '.zip')
        with zipfile.ZipFile(archive, 'w') as output:
            for name, content in files.items():
                output.writestr(key + '/' + name, content)
        (folder / (version + '.mod')).write_bytes(files['go.mod'])
        (folder / (version + '.info')).write_text(json.dumps({'Version': version, 'Time': '2020-01-01T00:00:00Z'}))
        records = ''.join(hashlib.sha256(files[name]).hexdigest() + '  ' + key + '/' + name + '\n' for name in sorted(files))
        h1 = 'h1:' + base64.b64encode(hashlib.sha256(records.encode()).digest()).decode()
        return key, h1, hashlib.sha256(archive.read_bytes()).hexdigest()

    def plan(self, name, modules):
        path = self.root / name
        path.write_text('{}\n')
        return {'module_sums': dict(modules), 'zip_urls': {key: '' for key, _ in modules},
                'zip_sums_files': [str(path)]}, path

    def test_lock_real_go_and_prune(self):
        key, h1, sha = self.module()
        plan, path = self.plan('lock.json', [(key, h1)])
        lock.update([plan], self.root, self.sdk)
        self.assertEqual(json.loads(path.read_text()), {key: sha})
        path.chmod(0o640)
        # Reviewed archive reused after the proxy disappears.
        os.rename(self.proxy, self.root / 'offline')
        lock.update([plan], self.root, self.sdk)
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        plan['module_sums'] = {}; plan['zip_urls'] = {}
        lock.update([plan], self.root, self.sdk)
        self.assertEqual(json.loads(path.read_text()), {})

    def test_checksum_failure_preserves_all_locks(self):
        key, h1, _ = self.module()
        other, _, _ = self.module('v1.1.0')
        first, a = self.plan('a.json', [(key, h1)])
        second, b = self.plan('b.json', [(other, 'h1:' + 'A' * 43 + '=')])
        before = (a.read_bytes(), b.read_bytes())
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            lock.update([first, second], self.root, self.sdk)
        self.assertEqual((a.read_bytes(), b.read_bytes()), before)

    def test_conflict_and_readonly_check(self):
        key, h1, _ = self.module()
        plan, path = self.plan('lock.json', [(key, h1)])
        with self.assertRaisesRegex(ValueError, 'needs updating'):
            lock.update([plan], self.root, None, check=True)
        lock.update([plan], self.root, self.sdk)
        before = path.read_bytes()
        lock.update([plan], self.root, None, check=True)
        self.assertEqual(path.read_bytes(), before)
        conflict, _ = self.plan('other.json', [(key, 'h1:conflict')])
        with self.assertRaisesRegex(ValueError, 'conflicting Go content'):
            lock.update([plan, conflict], self.root, self.sdk)
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
