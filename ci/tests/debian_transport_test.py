"""Check the real source-CI transport shell without network or Bazel execution."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/declared-build-tools.yml'
GENERAL = ROOT / '.github/workflows/bazel.yml'
BRANCH = 'codex/declared-tools-debian-transport'
REVISION = '18e7c652222449b906a17c2fd948e37bcad3324e'
CHECKSUM = 'fa60a63fd62f11e3c097905a3ff2a5dc4b25bd7ba42ebc9877fe7ea4dd1d3ee2'
URL = 'https://raw.githubusercontent.com/securely1g/sonic-bazel-registry/' + REVISION + '/ci/source_mirrors.cfg'
# Exact public config fixture; the hash below prevents silently changing its policy.
UPSTREAM_CONFIG = r"""# BCR pigz@2.8.bcr.2 keeps its original source integrity and upstream patches.
# Original SHA256: eb872b4f0e1f0ebe59c9f7bd8c506c4204893ba6a8492de31df416f0d5170fd0
# Bazel matches the complete URL without its https:// prefix.
rewrite zlib\.net/pigz/pigz-2\.8\.tar\.gz https://distfiles.macports.org/pigz/pigz-2.8.tar.gz

# Prefer the official pool for pinned Debian binary packages. Bazel keeps the
# repository rule's checksum; the original snapshot remains the second URL.
# Indexes and source archives must retain their snapshot URLs.
rewrite (snapshot\.debian\.org/archive/debian/[0-9]{8}T[0-9]{6}Z/pool/([^?#]+\.deb)) https://deb.debian.org/debian/pool/$2
rewrite (snapshot\.debian\.org/archive/debian/[0-9]{8}T[0-9]{6}Z/pool/([^?#]+\.deb)) https://$1
rewrite (snapshot\.debian\.org/archive/debian-security/[0-9]{8}T[0-9]{6}Z/pool/([^?#]+\.deb)) https://deb.debian.org/debian-security/pool/$2
rewrite (snapshot\.debian\.org/archive/debian-security/[0-9]{8}T[0-9]{6}Z/pool/([^?#]+\.deb)) https://$1
""".encode()


def step(name, workflow=WORKFLOW):
    text = workflow.read_text()
    return text.split('      - name: ' + name + '\n', 1)[1].split('\n      - ', 1)[0]


def script(name, workflow=WORKFLOW):
    return textwrap.dedent(step(name, workflow).split('        run: |\n', 1)[1])


def guard(text):
    start = text.index('kernel_runtime_test=//tools/build_tools:kernel_runtime_test')
    end = text.index('\nfi', start) + len('\nfi')
    return text[start:end]


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
            payload.write_bytes(UPSTREAM_CONFIG)
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
                (run_root / 'ci').mkdir()
                routes = (ROOT / 'ci/debian-content-routes.cfg').read_bytes()
                (run_root / 'ci/debian-content-routes.cfg').write_bytes(routes)
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
                    self.assertEqual((run_root / 'ci-artifacts/source-mirrors.cfg').read_bytes(), routes + payload.read_bytes())
                    self.assertEqual((run_root / 'ci-artifacts/source-mirrors.upstream.cfg').read_bytes(), payload.read_bytes())
                    self.assertEqual((run_root / 'ci-artifacts/debian-content-routes.cfg').read_bytes(), routes)
                    self.assertEqual((run_root / 'declared-tools-downloads.cfg').read_bytes(), routes + payload.read_bytes())
                    self.assertEqual((run_root / 'ci-artifacts/source-mirrors-url.txt').read_text().strip(), URL)

    def test_all_five_existing_bazel_invocations_use_one_config(self):
        actual = script('Build executable tools and exercise sandboxed invocation')
        prefix = actual.split('bazel --ignore_all_rc_files', 1)[0]
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
                       GITHUB_EVENT_NAME='pull_request', GITHUB_REPOSITORY='securely1g/sonic-build-infra',
                       SONIC_REVIEW_HEAD_REPOSITORY='securely1g/sonic-build-infra', GITHUB_HEAD_REF=BRANCH)
            result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', prefix + '\n' + '\n'.join(commands)],
                                    cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()]
            self.assertEqual(len(calls), 5)
            self.assertEqual([next(a for a in c if a in ['build', 'test', 'cquery', 'aquery']) for c in calls],
                             ['build', 'test', 'cquery', 'aquery', 'test'])
            for call in calls:
                self.assertEqual([x for x in call if x.startswith('--experimental_downloader_config=')],
                                 ['--experimental_downloader_config=' + str(root / 'declared-tools-downloads.cfg')])
                self.assertIn('--ignore_all_rc_files', call)
                self.assertIn('--host_platform=//platforms:x86_64_trixie', call)
                self.assertIn('--platforms=//platforms:x86_64_trixie', call)
                self.assertIn('--disk_cache=' + str(root / 'configure-tools-cache'), call)
                self.assertIn('--lockfile_mode=update', call)
            self.assertIn('--output_user_root=' + str(root / 'configure-tools-fresh'), calls[-1])
            self.assertIn('//tools/build_tools:kernel_runtime_tools_test', calls[1])
            self.assertIn('//tools/build_tools:kernel_runtime_tools_test', calls[-1])

    def test_both_review_guards_preserve_normal_scope(self):
        texts = [script('Build executable tools and exercise sandboxed invocation'),
                 (ROOT / 'ci/bazel-ci.sh').read_text()]
        for text in texts:
            selected = guard(text)
            for event, repository, owner, branch, tools_only in [
                ('pull_request', 'securely1g/sonic-build-infra', 'securely1g/sonic-build-infra', BRANCH, True),
                ('pull_request', 'securely1g/sonic-build-infra', 'securely1g/sonic-build-infra', 'codex/compiler-linker-argument-transport', True),
                ('pull_request', 'securely1g/sonic-build-infra', 'other/fork', BRANCH, False),
                ('pull_request', 'other/fork', 'securely1g/sonic-build-infra', BRANCH, False),
                ('pull_request', 'securely1g/sonic-build-infra', 'securely1g/sonic-build-infra', BRANCH + '-other', False),
                ('push', 'securely1g/sonic-build-infra', 'securely1g/sonic-build-infra', BRANCH, False),
                ('workflow_dispatch', 'securely1g/sonic-build-infra', 'securely1g/sonic-build-infra', BRANCH, False),
                ('pull_request', 'securely1g/sonic-build-infra', 'securely1g/sonic-build-infra', 'unrelated', False),
            ]:
                with self.subTest(event=event, repository=repository, owner=owner, branch=branch):
                    env = dict(os.environ, GITHUB_EVENT_NAME=event, GITHUB_REPOSITORY=repository,
                               SONIC_REVIEW_HEAD_REPOSITORY=owner, GITHUB_HEAD_REF=branch)
                    result = subprocess.run(['bash', '-e', '-c', selected + '\nprintf "%s" "$kernel_runtime_test"'],
                                            env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    expected = 'kernel_runtime_tools_test' if tools_only else 'kernel_runtime_test'
                    self.assertEqual(result.stdout, '//tools/build_tools:' + expected)

    def test_general_workflow_uses_same_verified_config_before_all_consumers(self):
        name = 'Fetch the verified shared downloader configuration'
        self.assertEqual(step(name, GENERAL), step(name))
        text = GENERAL.read_text()
        self.assertNotIn('${{ runner.temp }}', text)
        native = step('Build and test source rules', GENERAL).split('        run: ', 1)[1].strip()
        self.assertEqual(native, 'SONIC_BAZEL_DOWNLOADER_CONFIG="$RUNNER_TEMP/declared-tools-downloads.cfg" bash ci/bazel-ci.sh')
        self.assertLess(text.index('run: python3 -B ci/tests/debian_transport_test.py'), text.index('      - name: ' + name))
        self.assertLess(text.index('      - name: ' + name), text.index('      - name: Build and test source rules'))
        selected = '--bazel-option=--experimental_downloader_config="$RUNNER_TEMP/declared-tools-downloads.cfg"'
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
                                 ['--bazel-option=--experimental_downloader_config=' + str(config)])
                self.assertIn('--startup-option=--ignore_all_rc_files', call)
                self.assertIn('--bazel-option=--registry=https://bcr.bazel.build/', call)

    def test_four_exact_content_routes_preserve_neighboring_urls(self):
        # Bazel 8.5.1 UrlRewriter matches the complete URL after its scheme,
        # accumulates every matching rule in order, and retains rule SHA256.
        expected = {
            'pool/main/u/util-linux/libblkid1_2.41-5_arm64.deb': '4bb119d7bbfef4763bc98c3f3e117d43d2a82dc7',
            'pool/main/i/isl/libisl23_0.27-1_arm64.deb': '7425c29776fa1ebd2a4fb5602327d1f5e0f3bf6a',
            'pool/main/g/glibc/libc6_2.41-12+deb13u3_armhf.deb': '2511d1ea4cdf37ca307cc3336cbc16ff23c323ef',
            'pool/main/s/swig/swig_4.3.0-1_amd64.deb': 'a3f9a66243bb242325c131c8cf6db24f9ae52bb5',
        }
        rules = []
        for line in (ROOT / 'ci/debian-content-routes.cfg').read_text().splitlines():
            if line and not line.startswith('#'):
                kind, pattern, replacement = line.split()
                self.assertEqual(kind, 'rewrite')
                rules.append((re.compile(pattern), replacement))
        self.assertEqual(len(rules), 4)
        self.assertEqual(hashlib.sha256(UPSTREAM_CONFIG).hexdigest(), CHECKSUM)
        combined = list(rules)
        for line in UPSTREAM_CONFIG.decode().splitlines():
            if line.startswith('rewrite '):
                _, pattern, replacement = line.split()
                combined.append((re.compile(pattern), replacement))
        for path, digest in expected.items():
            url = 'snapshot.debian.org/archive/debian/20260727T143429Z/' + path
            self.assertEqual([replacement for pattern, replacement in rules if pattern.fullmatch(url)],
                             ['https://snapshot.debian.org/file/' + digest])
            ordered = []
            for pattern, replacement in combined:
                match = pattern.fullmatch(url)
                if match:
                    value = re.sub(r'\$(\d+)', lambda group: match.group(int(group[1])), replacement)
                    if value not in ordered:
                        ordered.append(value)
            self.assertEqual(ordered, ['https://snapshot.debian.org/file/' + digest,
                                       'https://deb.debian.org/debian/' + path, 'https://' + url])
            neighbor_arch = ('_amd64.deb' if '_arm64.deb' in url else '_arm64.deb')
            for other in [url + '?query=1', url + '#fragment', url.replace('20260727', '20260728'),
                          re.sub(r'_(?:amd64|arm64|armhf)\.deb$', neighbor_arch, url),
                          url.replace('.deb', '-other.deb'), url.replace('snapshot.debian.org', 'example.invalid')]:
                self.assertFalse(any(pattern.fullmatch(other) for pattern, _ in rules), other)
        bootstrap = script('Fetch the verified shared downloader configuration')
        self.assertIn('cat ci/debian-content-routes.cfg "$upstream" > "$config.tmp"', bootstrap)
        self.assertLess(bootstrap.index('sha256sum --check --strict'), bootstrap.index('cat ci/debian-content-routes.cfg'))

    def run_general_script(self, selected):
        """Execute the actual shell with inert external commands; never Bazel."""
        with tempfile.TemporaryDirectory(prefix='general transport ') as directory:
            root = Path(directory)
            (root / 'ci').mkdir()
            (root / 'ci/bazel-ci.sh').write_bytes((ROOT / 'ci/bazel-ci.sh').read_bytes())
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
                command = next(a for a in args if a in ['aquery', 'test', 'build', 'cquery', 'mod', 'query'])
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
                       BASH_ENV=str(bash_env), GITHUB_EVENT_NAME='pull_request',
                       GITHUB_REPOSITORY='securely1g/sonic-build-infra',
                       SONIC_REVIEW_HEAD_REPOSITORY='securely1g/sonic-build-infra', GITHUB_HEAD_REF=BRANCH)
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
                self.assertEqual(len(calls), 17)  # Eleven literal calls, seven cquery loop iterations.
                commands = [next(a for a in call['args'] if a in ['aquery', 'test', 'build', 'cquery', 'mod', 'query']) for call in calls]
                self.assertEqual({name: commands.count(name) for name in set(commands)},
                                 {'aquery': 3, 'test': 4, 'build': 1, 'cquery': 7, 'mod': 1, 'query': 1})
                self.assertEqual(sum(call['cwd'] == root + '/tests/shared_api_consumer' for call in calls), 2)
                for call in calls:
                    self.assertEqual([a for a in call['args'] if a.startswith('--experimental_downloader_config=')],
                                     ['--experimental_downloader_config=' + config] if selected else [])
                    self.assertIn('--ignore_all_rc_files', call['args'])
                    self.assertIn('--registry=https://bcr.bazel.build/', call['args'])
                    self.assertIn('--lockfile_mode=update', call['args'])
                root_tests = next(c['args'] for c in calls if 'test' in c['args'])
                self.assertIn('//tools/build_tools:kernel_runtime_tools_test', root_tests)
                self.assertNotIn('//tools/build_tools:kernel_runtime_test', root_tests)
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
