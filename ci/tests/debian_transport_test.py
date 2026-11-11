"""Check the real source-CI transport shell without network or Bazel execution."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/declared-build-tools.yml'
GENERAL = ROOT / '.github/workflows/bazel.yml'
REVISION = '97cc502556b7ec3435d2d67b105315bcc18bb47e'
CHECKSUM = 'ddd6f40504a30e138a62be0d728e1a352391d0f781c927eaf4ea45c44d8a0720'
URL = 'https://raw.githubusercontent.com/securely1g/sonic-bazel-registry/' + REVISION + '/ci/source_mirrors.cfg'
def step(name, workflow=WORKFLOW):
    text = workflow.read_text()
    return text.split('      - name: ' + name + '\n', 1)[1].split('\n      - ', 1)[0]


def script(name, workflow=WORKFLOW):
    return textwrap.dedent(step(name, workflow).split('        run: |\n', 1)[1])



class DebianTransportTest(unittest.TestCase):
    def test_exact_public_config_pin_and_early_test_wiring(self):
        workflow = WORKFLOW.read_text()
        fetch = step('Fetch the verified shared downloader configuration')
        self.assertIn('SOURCE_MIRRORS_URL: ' + URL, fetch)
        self.assertIn('SOURCE_MIRRORS_SHA256: ' + CHECKSUM, fetch)
        self.assertLess(workflow.index('run: python3 -B ci/tests/debian_transport_test.py'),
                        workflow.index('      - name: Fetch the verified shared downloader configuration'))
        self.assertLess(workflow.index('      - name: Fetch the verified shared downloader configuration'),
                        workflow.index('      - name: Build executable tools and exercise sandboxed invocation'))

    def test_actual_bootstrap_rejects_corruption_before_bazel(self):
        bootstrap = script('Fetch the verified shared downloader configuration')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = root / 'bin'
            commands.mkdir()
            payload = root / 'payload'
            payload.write_bytes(b'rewrite files.example/(.*) https://mirror.example/$1\n')
            curl = commands / 'curl'
            curl.write_text('#!/usr/bin/python3\n' + textwrap.dedent('''
                import json, os, sys
                from pathlib import Path
                args = sys.argv[1:]
                Path('curl-call.json').write_text(json.dumps(args))
                data = Path(os.environ['TEST_PAYLOAD']).read_bytes()
                if os.environ['TEST_CORRUPT'] == 'true': data += b'changed'
                Path(args[args.index('--output') + 1]).write_bytes(data)
            '''))
            curl.chmod(0o755)
            bazel = commands / 'bazel'
            bazel.write_text('#!/bin/sh\nprintf invoked > bazel-called\n')
            bazel.chmod(0o755)
            for corrupt in [True, False]:
                run_root = root / str(corrupt)
                run_root.mkdir()
                env = dict(os.environ, PATH=str(commands) + ':/usr/bin:/bin', RUNNER_TEMP=str(run_root),
                           SOURCE_MIRRORS_URL=URL, SOURCE_MIRRORS_SHA256=hashlib.sha256(payload.read_bytes()).hexdigest(),
                           TEST_PAYLOAD=str(payload), TEST_CORRUPT=str(corrupt).lower())
                result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', bootstrap + '\nbazel probe'],
                                        cwd=run_root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, not corrupt, result.stderr)
                self.assertEqual((run_root / 'bazel-called').exists(), not corrupt)
                self.assertEqual((run_root / 'ci-artifacts/source-mirrors.cfg').exists(), not corrupt)
                args = json.loads((run_root / 'curl-call.json').read_text())
                self.assertEqual(args[-1], URL)
                self.assertEqual(args[args.index('--max-time') + 1], '60')
                if not corrupt:
                    self.assertEqual((run_root / 'ci-artifacts/source-mirrors.cfg').read_bytes(), payload.read_bytes())
                    self.assertEqual((run_root / 'declared-tools-downloads.cfg').read_bytes(), payload.read_bytes())
                    self.assertEqual((run_root / 'ci-artifacts/source-mirrors-url.txt').read_text().strip(), URL)

    def test_all_five_existing_bazel_invocations_use_one_config(self):
        actual = script('Build executable tools and exercise sandboxed invocation')
        # Exercise the actual flag and target-selection shell without the
        # unrelated host and APT evidence setup inherited from the base.
        prefix = 'flags=(' + actual.split('flags=(', 1)[1].split('bazel --ignore_all_rc_files', 1)[0]
        prefix += 'worker_tests=()' + actual.split('worker_tests=()', 1)[1].split('bazel --ignore_all_rc_files test', 1)[0]
        commands = []
        lines = actual.splitlines()
        for index, line in enumerate(lines):
            if line.startswith('bazel --ignore_all_rc_files'):
                command = [line]
                while command[-1].rstrip().endswith('\\'):
                    index += 1
                    command.append(lines[index])
                commands.append('\n'.join(command))
        self.assertEqual(len(commands), 5)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'ci-artifacts/libtool').mkdir(parents=True)
            stub = root / 'bazel'
            stub.write_text('#!/usr/bin/python3\nimport json,sys\nfrom pathlib import Path\n'
                            'with Path("calls.jsonl").open("a") as out: out.write(json.dumps(sys.argv[1:])+"\\n")\n')
            stub.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + ':/usr/bin:/bin', RUNNER_TEMP=str(root),
                       SONIC_BAZEL_REGISTRY='https://example.invalid/registry', PLATFORM='x86_64_trixie',
                       ARCHITECTURE='amd64')
            result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', prefix + '\n' + '\n'.join(commands)],
                                    cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()]
            self.assertEqual(len(calls), 5)
            self.assertEqual([next(a for a in c if a in ['build', 'test', 'cquery', 'aquery']) for c in calls],
                             ['build', 'test', 'cquery', 'aquery', 'test'])
            for call in calls:
                self.assertEqual([x for x in call if 'downloader_config=' in x],
                                 ['--downloader_config=' + str(root / 'declared-tools-downloads.cfg')])
                self.assertIn('--ignore_all_rc_files', call)
                self.assertIn('--host_platform=//platforms:x86_64_trixie', call)
                self.assertIn('--platforms=//platforms:x86_64_trixie', call)
                self.assertIn('--disk_cache=' + str(root / 'configure-tools-cache'), call)
                self.assertIn('--lockfile_mode=update', call)
            self.assertIn('--output_user_root=' + str(root / 'configure-tools-fresh'), calls[-1])
            self.assertIn('//tools/build_tools:kernel_runtime_test', calls[1])
            self.assertIn('//tools/build_tools:kernel_runtime_test', calls[-1])

    def test_general_workflow_uses_same_verified_config_before_all_consumers(self):
        name = 'Fetch the verified shared downloader configuration'
        self.assertEqual(step(name, GENERAL), step(name))
        text = GENERAL.read_text()
        self.assertNotIn('${{ runner.temp }}', text)
        native = step('Build and test source rules', GENERAL).split('        run: ', 1)[1].strip()
        self.assertEqual(native, 'SONIC_BAZEL_DOWNLOADER_CONFIG="$RUNNER_TEMP/declared-tools-downloads.cfg" bash ci/bazel-ci.sh')
        self.assertLess(text.index('run: python3 -B ci/tests/debian_transport_test.py'), text.index('      - name: ' + name))
        self.assertLess(text.index('      - name: ' + name), text.index('      - name: Build and test source rules'))
        selected = '--bazel-option=--downloader_config="$RUNNER_TEMP/declared-tools-downloads.cfg"'
        self.assertEqual(text.count(selected), 3)
        commands = []
        for name in ['Reject conflicting sysroot package files',
                     'Validate native Debian GCC, Clang and their shared sysroot',
                     'Validate Debian cross GCC and Clang with an AMD64 source generator']:
            command = textwrap.dedent(step(name, GENERAL).split('        run: >-\n', 1)[1])
            commands.append(' '.join(command.splitlines()))
        with tempfile.TemporaryDirectory(prefix='transport arguments ') as directory:
            root = Path(directory)
            stub = root / 'python3'
            stub.write_text('#!/usr/bin/python3\nimport json,sys\nfrom pathlib import Path\n'
                            'with Path("calls.jsonl").open("a") as out: out.write(json.dumps(sys.argv[1:])+"\\n")\n')
            stub.chmod(0o755)
            config = root / 'declared-tools-downloads.cfg'
            config.write_text('transport fixture\n')
            shell = root / 'bash'
            shell.write_text('#!/usr/bin/python3\nimport json,os,sys\nfrom pathlib import Path\n'
                             'Path("native-call.json").write_text(json.dumps({"args":sys.argv[1:],"config":os.environ.get("SONIC_BAZEL_DOWNLOADER_CONFIG")}))\n')
            shell.chmod(0o755)
            result = subprocess.run(['/bin/bash', '-e', '-c', native + '\n' + '\n'.join(commands)], cwd=root,
                                    env=dict(os.environ, PATH=str(root) + ':/usr/bin:/bin',
                                             RUNNER_TEMP=str(root),
                                             SONIC_BAZEL_REGISTRY='https://example.invalid/registry'),
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((root / 'native-call.json').read_text()),
                             {'args': ['ci/bazel-ci.sh'], 'config': str(config)})
            calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()]
            self.assertEqual(len(calls), 3)
            for call in calls:
                self.assertEqual([arg for arg in call if 'downloader_config' in arg],
                                 ['--bazel-option=--downloader_config=' + str(config)])
                self.assertIn('--startup-option=--ignore_all_rc_files', call)
                self.assertIn('--bazel-option=--registry=https://bcr.bazel.build/', call)

    def run_general_script(self, selected):
        """Execute the actual shell with inert external commands; never Bazel."""
        with tempfile.TemporaryDirectory(prefix='general transport ') as directory:
            root = Path(directory)
            (root / 'ci').mkdir()
            (root / 'ci/bazel-ci.sh').write_bytes((ROOT / 'ci/bazel-ci.sh').read_bytes())
            (root / 'ci/source_mirrors.cfg').write_bytes((ROOT / 'ci/source_mirrors.cfg').read_bytes())
            for path in ['python/wheel_layer.bzl', 'tar/root_owned_tar.bzl',
                         'tools/build_tools/prepare_rootfs.py', 'tools/build_tools/docbook_runtime_test.py',
                         'tests/shared_api_consumer/MODULE.bazel']:
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
            commands = root / 'commands'
            commands.mkdir()
            for name, output in [('uname', 'x86_64'), ('dpkg', 'amd64'), ('dpkg-query', 'fixture'),
                                 ('git', 'reviewed-source'), ('cp', ''), ('python3', '')]:
                stub = commands / name
                stub.write_text('#!/bin/sh\nprintf "%s\\n" "' + output + '"\n')
                stub.chmod(0o755)
            bazel = commands / 'bazel'
            bazel.write_text('#!/usr/bin/python3\n' + textwrap.dedent('''
                import hashlib, json, os, sys
                from pathlib import Path
                args = sys.argv[1:]
                root = Path(os.environ['TEST_ROOT'])
                with (root / 'calls.jsonl').open('a') as out:
                    out.write(json.dumps({'cwd': str(Path.cwd()), 'args': args}) + '\\n')
                command = next(a for a in args if a in ['run', 'aquery', 'test', 'build', 'cquery', 'mod', 'query'])
                if command == 'query':
                    print('The Debian sysroot contains unexpected absolute symlinks')
                    sys.exit(1)
                if command == 'build':
                    for name in ['hello', 'hello_cpp']:
                        path = root / 'bazel-bin/tests' / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text('#!/bin/sh\\nexit 0\\n')
                        path.chmod(0o755)
                if command == 'cquery':
                    path = root / 'mock-output' / hashlib.sha256(args[-1].encode()).hexdigest()
                    path.parent.mkdir(exist_ok=True)
                    path.write_text('plain test bytes')
                    print(path)
            '''))
            bazel.chmod(0o755)
            # Intercept only the native-host identity read, without changing the
            # real script or the host. All external commands above are inert.
            bash_env = root / 'mock-host.sh'
            bash_env.write_text('function .() { if [[ "$1" == /etc/os-release ]]; then '
                                'ID=debian; VERSION_CODENAME=trixie; else builtin . "$@"; fi; }\n')
            config = root / 'selected config.cfg'
            config.write_text('plain transport fixture\n')
            choices = {'valid': str(config), 'missing': str(root / 'missing'),
                       'relative': 'selected config.cfg', 'directory': str(commands)}
            link = root / 'linked-config'
            link.symlink_to(config)
            choices['symlink'] = str(link)
            env = dict(os.environ, PATH=str(commands) + ':/usr/bin:/bin', TEST_ROOT=str(root),
                       BASH_ENV=str(bash_env))
            env.pop('SONIC_BAZEL_DOWNLOADER_CONFIG', None)
            if selected is not None:
                env['SONIC_BAZEL_DOWNLOADER_CONFIG'] = choices[selected]
            result = subprocess.run(['bash', 'ci/bazel-ci.sh'], cwd=root, env=env,
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True)
            calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()] if (root / 'calls.jsonl').exists() else []
            return result, calls, str(config), str(root)

    def test_all_general_root_and_nested_calls_receive_selected_config(self):
        for selected in ['valid', None]:
            with self.subTest(selected=selected):
                result, calls, config, root = self.run_general_script(selected)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertEqual(len(calls), 18)
                commands = [next(a for a in call['args'] if a in ['run', 'aquery', 'test', 'build', 'cquery', 'mod', 'query']) for call in calls]
                self.assertEqual({name: commands.count(name) for name in set(commands)},
                                 {'run': 1, 'aquery': 3, 'test': 4, 'build': 1, 'cquery': 7, 'mod': 1, 'query': 1})
                self.assertEqual(sum(call['cwd'] == root + '/tests/shared_api_consumer' for call in calls), 2)
                for call in calls:
                    expected_config = config if selected else root + '/ci/source_mirrors.cfg'
                    self.assertEqual([a for a in call['args'] if 'downloader_config=' in a],
                                     ['--downloader_config=' + expected_config])
                    self.assertIn('--ignore_all_rc_files', call['args'])
                    self.assertIn('--registry=https://bcr.bazel.build/', call['args'])
                    self.assertIn('--lockfile_mode=update', call['args'])
                lock_check = next(c for c in calls if 'run' in c['args'])
                self.assertEqual(lock_check['cwd'], root)
                self.assertEqual(lock_check['args'][-3:], ['//apt:lock', '--', 'check'])
                root_tests = next(c['args'] for c in calls if 'test' in c['args'])
                self.assertIn('//tools/build_tools:kernel_runtime_test', root_tests)
                self.assertNotIn('//tools/build_tools:kernel_runtime_tools_test', root_tests)
                self.assertIn('//swig/tests:defines_test', root_tests)

    def test_invalid_general_config_stops_before_any_bazel(self):
        for selected in ['missing', 'relative', 'directory', 'symlink']:
            with self.subTest(selected=selected):
                result, calls, _, _ = self.run_general_script(selected)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('absolute regular non-symlink file', result.stderr + result.stdout)
                self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
