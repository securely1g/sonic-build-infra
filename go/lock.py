#!/usr/bin/env python3
"""Update reviewed module ZIP digests from Gazelle-exported resolution plans."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def selected_sdk(plans):
    """Select deterministically after verifying every plan's execution SDK."""
    if not plans:
        raise ValueError('no Go ZIP resolution plans')
    roots = set()
    for plan in plans:
        value = plan.get('sdk_root')
        if not isinstance(value, str) or not value:
            raise ValueError('Go ZIP plan is missing sdk_root')
        roots.add(Path(value).resolve(strict=True))
    identities = set()
    for root in sorted(roots):
        version_file = (root / 'VERSION').read_bytes()
        lines = version_file.decode().splitlines()
        if not lines:
            raise ValueError('Go SDK VERSION has no version line')
        version = lines[0]
        executable = root / 'bin/go'
        result = subprocess.run([str(executable), 'version'], check=True,
                                env={**os.environ, 'GOTOOLCHAIN': 'local', 'GOWORK': 'off'},
                                text=True, capture_output=True)
        identity = result.stdout.strip().split()
        if len(identity) != 4 or identity[:2] != ['go', 'version'] or identity[2] != version:
            raise ValueError('Go SDK executable and VERSION identity differ')
        with executable.open('rb') as binary:
            binary_digest = hashlib.file_digest(binary, 'sha256').hexdigest()
        defaults = root / 'go.env'
        defaults_digest = hashlib.sha256(defaults.read_bytes()).hexdigest() if defaults.exists() else None
        identities.add((version_file, identity[3], binary_digest, defaults_digest))
    if len(identities) != 1:
        raise ValueError('Go ZIP plans select incompatible SDK versions, execution platforms or executable identities; unify plans or update compatible groups separately')
    chosen = min(roots)
    print('Go SDK identity:', version, identity[3], binary_digest, flush=True)
    return chosen


def update(plans, root, sdk, check=False):
    """Verify new ZIPs with the configured Go SDK before changing any lock."""
    required = {}
    destinations = {}
    for plan in plans:
        sums = plan['module_sums']
        for key in plan['zip_urls']:
            expected = sums.get(key)
            if not expected or not expected.startswith('h1:'):
                raise ValueError(f'missing Go content checksum: {key}')
            old = required.get(key)
            if old and old != expected:
                raise ValueError(f'conflicting Go content checksum: {key}')
            required[key] = expected
        for name in plan['zip_sums_files']:
            path = Path(name).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError(f'lock outside workspace: {path}')
            destinations.setdefault(path, set()).update(plan['zip_urls'])
    if not destinations:
        raise ValueError('no root ZIP locks configured; add go_deps.zip_lock(file = ...)')
    existing = {}
    for path in destinations:
        for key, digest in json.loads(path.read_text()).items():
            if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError(f'invalid ZIP SHA-256: {key} in {path}')
            if key in existing and existing[key] != digest:
                raise ValueError(f'conflicting ZIP SHA-256: {key}')
            existing[key] = digest
    if check:
        for path, keys in destinations.items():
            if set(json.loads(path.read_text())) != keys:
                raise ValueError(f'ZIP lock needs updating: {path}')
        return
    digests = {}
    # Go implements GOPROXY fallback, private modules, authentication, sumdb and
    # module ZIP validation. This download is only performed by explicit updates.
    with tempfile.TemporaryDirectory(prefix='sonic-go-lock-') as directory:
        Path(directory, 'go.mod').write_text('module sonic.invalid/zip-lock\n')
        env = {**os.environ, 'GOWORK': 'off', 'GOTOOLCHAIN': 'local',
               'GOMODCACHE': str(Path(directory) / 'modcache'),
               'GOPATH': os.environ.get('GOPATH', str(Path(directory) / 'gopath'))}
        missing = sorted(set(required) - existing.keys())
        digests.update({key: existing[key] for key in required if key in existing})
        if missing:
            print(f'Verifying {len(missing)} module ZIPs with the configured Go SDK', flush=True)
            result = subprocess.run([str(Path(sdk) / 'bin/go'), 'mod', 'download', '-json', *missing],
                                    cwd=directory, env=env, text=True, capture_output=True)
            if result.returncode:
                raise ValueError(f'download failed: {result.stdout.strip()} {result.stderr.strip()}')
            decoder = json.JSONDecoder()
            output = result.stdout.lstrip()
            seen = set()
            while output:
                data, end = decoder.raw_decode(output)
                output = output[end:].lstrip()
                key = data['Path'] + '@' + data['Version']
                if key not in missing or key in seen:
                    raise ValueError(f'unexpected module download result: {key}')
                seen.add(key)
                if data.get('Sum') != required[key]:
                    raise ValueError(f'Go content checksum mismatch: {key}: {data.get("Sum")} != {required[key]}')
                with open(data['Zip'], 'rb') as archive:
                    digests[key] = hashlib.file_digest(archive, 'sha256').hexdigest()
            if seen != set(missing):
                raise ValueError('Go did not return every requested module ZIP')
    # Stage all files before replacement; a resolution/download/checksum failure
    # leaves every original lock byte-for-byte unchanged.
    staged = []
    try:
        for path, keys in sorted(destinations.items()):
            content = json.dumps({key: digests[key] for key in sorted(keys)}, indent=2) + '\n'
            if content == path.read_text():
                continue
            with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
                output.write(content)
                os.chmod(output.name, path.stat().st_mode & 0o777)
                staged.append((Path(output.name), path))
        for temporary, path in staged:
            os.replace(temporary, path)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    parser.add_argument('--bazel', default='bazel')
    parser.add_argument('--startup-option', action='append', default=[])
    parser.add_argument('--bazel-option', action='append', default=[])
    parser.add_argument('--plan', action='append', required=True,
                        help='exported @repo//:zip-lock.json; repeat for isolated extensions or explicit repositories')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    env = {**os.environ, 'SONIC_GO_LOCK_UPDATE': '1'}
    def bazel(command, *rest):
        return subprocess.check_output([args.bazel, *args.startup_option, command,
                                        *args.bazel_option, *rest], cwd=args.workspace, env=env, text=True).strip()
    mapping = json.loads(bazel('mod', 'dump_repo_mapping', ''))
    plans = []
    for label in args.plan:
        repo, filename = label.removeprefix('@').split('//:', 1)
        if repo not in mapping or filename != 'zip-lock.json':
            raise ValueError(f'expected an imported repository ZIP plan: {label}')
        canonical = mapping[repo]
        bazel('fetch', '--repo=@@' + canonical)
        base = Path(bazel('info', 'output_base'))
        plan = json.loads((base / 'external' / canonical / filename).read_text())
        required = {'sdk_root', 'module_sums', 'zip_urls', 'zip_sums_files'}
        if not isinstance(plan, dict) or required - plan.keys():
            raise ValueError(f'ZIP plan {label} lacks required SDK/lock fields; select the Gazelle version providing ZIP plans')
        if not isinstance(plan['module_sums'], dict) or not isinstance(plan['zip_urls'], dict) or not isinstance(plan['zip_sums_files'], list):
            raise ValueError(f'ZIP plan {label} has invalid checksum or lock fields')
        plans.append(plan)
    sdk = selected_sdk(plans)
    print('Go SDK:', sdk, flush=True)
    update(plans, args.workspace, sdk, check=args.check)
    print('Go ZIP locks verified' if args.check else 'Go ZIP locks updated')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'go-lock: {error}')
