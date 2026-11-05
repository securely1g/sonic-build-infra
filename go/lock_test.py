"""Contract: reviewed Go content, deterministic ZIP digests, failure preserves locks.

The local GOPROXY fixture follows the Go module proxy protocol. Tests execute a
real Go SDK; expected h1 follows the published dirhash.Hash1 format, independently
of the updater. A changed archive, stale graph or partial update must be visible.
"""
import contextlib
import io
import shutil
import subprocess
import sys
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

    def test_new_lock_uses_current_proxy_zip_with_warm_go_cache(self):
        key, h1, first_sha = self.module()
        warm = self.root / 'warm'
        warm.mkdir()
        (warm / 'go.mod').write_text('module sonic.invalid/warm\n\ngo 1.20\n')
        result = subprocess.run(
            [str(Path(self.sdk) / 'bin/go'), 'mod', 'download', '-json', key],
            cwd=warm, env={**os.environ, 'GOWORK': 'off', 'GOTOOLCHAIN': 'local'},
            text=True, capture_output=True, check=True)
        cached_zip = Path(json.loads(result.stdout)['Zip'])
        self.assertEqual(hashlib.sha256(cached_zip.read_bytes()).hexdigest(), first_sha)

        # Another proxy can encode the same module contents as different ZIP bytes.
        current_proxy = self.root / 'current-proxy'
        shutil.copytree(self.proxy, current_proxy)
        current_zip = current_proxy / 'example.com/sample/@v/v1.0.0.zip'
        with zipfile.ZipFile(current_zip, 'a') as archive:
            archive.comment = b'current proxy'
        current_sha = hashlib.sha256(current_zip.read_bytes()).hexdigest()
        self.assertNotEqual(first_sha, current_sha)

        plan, path = self.plan('lock.json', [(key, h1)])
        plan['zip_urls'][key] = current_zip.as_uri()
        with patch.dict(os.environ, {'GOPROXY': current_proxy.as_uri()}):
            lock.update([plan], self.root, self.sdk)
        self.assertEqual(json.loads(path.read_text()), {key: current_sha})

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


    def sdk_plan_pair(self):
        key, h1, digest = self.module()
        plans, paths = [], []
        for name in ('a.json', 'b.json'):
            plan, path = self.plan(name, [(key, h1)])
            plan['sdk_root'] = self.sdk
            # A valid stale entry makes an accidental update observable.
            path.write_text(json.dumps({'example.com/stale@v1.0.0': 'b' * 64}) + '\n')
            plans.append(plan)
            paths.append(path)
        return plans, paths, {key: digest}

    def run_plan_cli(self, plans, check=False):
        """Only replace Bazel plan transport; real SDK identity and update run.

        The independent acceptance suite also exercises these plans through
        actual Bazel repository mappings. This unit fixture writes exactly the
        exported JSON files and supplies only dump_repo_mapping/fetch/info.
        """
        base = self.root / 'bazel-output'
        mapping = {}
        for number, plan in enumerate(plans):
            name = 'plan_' + str(number)
            mapping[name] = name
            folder = base / 'external' / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'zip-lock.json').write_text(json.dumps(plan))
        def transport(command, **kwargs):
            if command == ['bazel', 'mod', 'dump_repo_mapping', '']:
                return json.dumps(mapping)
            if command[:2] == ['bazel', 'fetch']:
                self.assertIn(command[2], ['--repo=@@' + name for name in mapping])
                return ''
            self.assertEqual(command, ['bazel', 'info', 'output_base'])
            return str(base)
        argv = ['go-lock', '--workspace', str(self.root)]
        for name in mapping:
            argv.extend(['--plan', '@' + name + '//:zip-lock.json'])
        if check:
            argv.append('--check')
        with patch.object(sys, 'argv', argv), patch.object(lock.subprocess, 'check_output', side_effect=transport), contextlib.redirect_stdout(io.StringIO()) as output:
            lock.main()
        return output.getvalue()

    def assert_plan_rejected(self, plans, paths, error_type, diagnostic):
        before = [path.read_bytes() for path in paths]
        modes = [path.stat().st_mode for path in paths]
        with self.assertRaisesRegex(error_type, diagnostic):
            self.run_plan_cli(plans)
        self.assertEqual([path.read_bytes() for path in paths], before)
        self.assertEqual([path.stat().st_mode for path in paths], modes)
        self.assertFalse((self.root / "modcache").exists())

    def copy_sdk(self, name):
        """Independent physical SDK identity files; complete runtime closure.

        Large immutable SDK directories are linked to the actual selected SDK.
        go, VERSION and go.env are real copies so identity mutations stay local.
        """
        root = self.root / name
        root.mkdir()
        for item in Path(self.sdk).iterdir():
            if item.name == "bin":
                (root / "bin").mkdir()
                for executable in item.iterdir():
                    if executable.name == "go":
                        shutil.copy2(executable, root / "bin/go")
                    else:
                        (root / "bin" / executable.name).symlink_to(executable.resolve())
            elif item.name in ("VERSION", "go.env"):
                shutil.copy2(item, root / item.name)
            else:
                (root / item.name).symlink_to(item.resolve(), target_is_directory=item.is_dir())
        return root

    def alternate_sdk(self, name, version=None, failed=False):
        root = self.root / name
        (root / 'bin').mkdir(parents=True)
        (root / 'VERSION').write_text(version or (Path(self.sdk) / 'VERSION').read_text())
        executable = root / 'bin/go'
        if failed:
            executable.write_text('#!/bin/sh\nexit 17\n')
            executable.chmod(0o755)
        else:
            executable.symlink_to(Path(self.sdk) / 'bin/go')
        return str(root)

    def test_sdk_same_root_updates_with_real_go(self):
        plans, paths, expected = self.sdk_plan_pair()
        output = self.run_plan_cli(plans)
        version = (Path(self.sdk) / 'VERSION').read_text().splitlines()[0]
        self.assertIn('Go SDK identity: ' + version + ' ', output)
        self.assertEqual([json.loads(path.read_text()) for path in paths], [expected, expected])

    def test_sdk_symlink_alias_updates_with_real_go(self):
        plans, paths, expected = self.sdk_plan_pair()
        alias = self.root / 'sdk-alias'
        alias.symlink_to(Path(self.sdk).resolve(), target_is_directory=True)
        plans[1]['sdk_root'] = str(alias)
        self.run_plan_cli(plans)
        self.assertEqual([json.loads(path.read_text()) for path in paths], [expected, expected])

    def test_sdk_compatible_copies_update_and_check_independent_of_plan_order(self):
        plans, paths, expected = self.sdk_plan_pair()
        roots = [self.copy_sdk('b-copy'), self.copy_sdk('a-copy')]
        for plan, root in zip(plans, roots):
            plan['sdk_root'] = str(root)
        chosen = min(root.resolve() for root in roots)
        self.assertNotEqual(roots[0].resolve(), roots[1].resolve())
        output = self.run_plan_cli(plans)
        self.assertIn('Go SDK: ' + str(chosen) + '\n', output)
        self.assertEqual([json.loads(path.read_text()) for path in paths], [expected, expected])
        for path in paths:
            path.chmod(0o640)
        before = [path.read_bytes() for path in paths]
        os.rename(self.proxy, self.root / 'offline')
        for order in (plans, list(reversed(plans))):
            output = self.run_plan_cli(order, check=True)
            self.assertIn('Go SDK: ' + str(chosen) + '\n', output)
            self.run_plan_cli(order)
            self.assertEqual([path.read_bytes() for path in paths], before)
            self.assertEqual([path.stat().st_mode & 0o777 for path in paths], [0o640, 0o640])

    def test_sdk_binary_identity_conflict_preserves_locks(self):
        plans, paths, _ = self.sdk_plan_pair()
        root = self.copy_sdk('different-go-binary')
        executable = root / 'bin/go'
        executable.chmod(executable.stat().st_mode | 0o200)
        # ELF ignores trailing payload; real go version still executes normally.
        with executable.open('ab') as binary:
            binary.write(b'\nindependent SDK identity test\n')
        plans[1]['sdk_root'] = str(root)
        self.assert_plan_rejected(plans, paths, ValueError, 'incompatible SDK.*unify plans or update compatible groups separately')

    def test_sdk_default_settings_conflict_preserves_locks(self):
        plans, paths, _ = self.sdk_plan_pair()
        root = self.copy_sdk('different-go-defaults')
        defaults = root / 'go.env'
        defaults.chmod(defaults.stat().st_mode | 0o200)
        defaults.write_bytes(defaults.read_bytes() + b'\nGOTOOLCHAIN=local\n')
        plans[1]['sdk_root'] = str(root)
        self.assert_plan_rejected(plans, paths, ValueError, 'incompatible SDK.*unify plans or update compatible groups separately')

    def test_sdk_missing_defaults_is_distinct_from_present_defaults(self):
        plans, paths, _ = self.sdk_plan_pair()
        root = self.copy_sdk('missing-go-defaults')
        (root / 'go.env').unlink()
        plans[1]['sdk_root'] = str(root)
        self.assert_plan_rejected(plans, paths, ValueError, 'incompatible SDK.*unify plans or update compatible groups separately')

    def test_sdk_full_version_metadata_conflict_preserves_locks(self):
        plans, paths, _ = self.sdk_plan_pair()
        root = self.copy_sdk('different-version-metadata')
        version = root / 'VERSION'
        version.chmod(version.stat().st_mode | 0o200)
        version.write_bytes(version.read_bytes() + b'\nidentity metadata differs\n')
        plans[1]['sdk_root'] = str(root)
        self.assert_plan_rejected(plans, paths, ValueError, 'incompatible SDK.*unify plans or update compatible groups separately')

    def test_sdk_missing_root_preserves_locks(self):
        plans, paths, _ = self.sdk_plan_pair()
        del plans[1]['sdk_root']
        self.assert_plan_rejected(plans, paths, ValueError, 'lacks required SDK/lock fields')

    def test_sdk_nonexistent_root_preserves_locks(self):
        plans, paths, _ = self.sdk_plan_pair()
        plans[1]['sdk_root'] = str(self.root / 'does-not-exist')
        self.assert_plan_rejected(plans, paths, FileNotFoundError, 'No such file or directory')

    def test_sdk_version_identity_mismatch_preserves_locks(self):
        plans, paths, _ = self.sdk_plan_pair()
        root = self.alternate_sdk('corrupt-version', version='go0.0.0\n')
        for plan in plans:
            plan['sdk_root'] = root
        self.assert_plan_rejected(plans, paths, ValueError, 'SDK executable and VERSION identity differ')

    def test_sdk_empty_version_preserves_locks(self):
        plans, paths, _ = self.sdk_plan_pair()
        root = self.copy_sdk('empty-version')
        version = root / 'VERSION'
        version.chmod(version.stat().st_mode | 0o200)
        version.write_bytes(b'')
        plans[1]['sdk_root'] = str(root)
        self.assert_plan_rejected(plans, paths, ValueError, 'VERSION has no version line')

    def test_sdk_failed_executable_preserves_locks(self):
        plans, paths, _ = self.sdk_plan_pair()
        root = self.alternate_sdk('failed-go', failed=True)
        for plan in plans:
            plan['sdk_root'] = root
        self.assert_plan_rejected(plans, paths, subprocess.CalledProcessError, 'exit status 17')

    def test_sdk_missing_plan_fields_preserves_locks(self):
        plans, paths, _ = self.sdk_plan_pair()
        del plans[1]['module_sums']
        self.assert_plan_rejected(plans, paths, ValueError, 'lacks required SDK/lock fields')

    def test_sdk_wrong_plan_field_types_preserves_locks(self):
        plans, paths, _ = self.sdk_plan_pair()
        plans[1]['module_sums'] = []
        self.assert_plan_rejected(plans, paths, ValueError, 'invalid checksum or lock fields')


if __name__ == '__main__':
    unittest.main()
