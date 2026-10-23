"""Run Sphinx on the directory of a declared conf.py source artifact."""

import argparse
import os
from pathlib import Path
import subprocess

from python.runfiles import runfiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-config', required=True)
    parser.add_argument('--builder', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    environment = dict(os.environ)
    for name in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONSAFEPATH'):
        environment.pop(name, None)
    resolver = runfiles.Create()
    environment.update(resolver.EnvVars())
    tool = resolver.Rlocation('sonic-build-infra/tools/build_tools/sphinx-build')
    subprocess.run([
        tool, '-b', args.builder,
        str(Path(args.source_config).absolute().parent),
        str(Path(args.output).absolute()),
    ], env=environment, check=True)


if __name__ == '__main__':
    main()
