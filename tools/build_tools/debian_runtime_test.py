"""Exercise Debian's original script generators with an isolated tool runtime."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    for name in ('installdeb', 'installdebconf', 'makeshlibs', 'perl', 'systemduser', 'python', 'tmpfiles', 'library', 'runtime'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    paths = {key: value.absolute() for key, value in vars(args).items()}
    receipt = json.loads((paths['runtime'] / 'toolchain.json').read_text())
    assert receipt['profile'] == 'debian', receipt
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        debian = root / 'debian'
        package = debian / 'sonic-script-probe'
        for directory in ('DEBIAN', 'etc', 'usr/lib', 'usr/bin', 'usr/lib/systemd/user', 'usr/lib/python3/dist-packages'):
            (package / directory).mkdir(parents=True, exist_ok=True)
        (debian / 'control').write_text('''Source: sonic-script-probe
Section: misc
Priority: optional
Maintainer: SONiC <securely1g@gmail.com>
Build-Depends: debhelper-compat (= 13)
Rules-Requires-Root: no

Package: sonic-script-probe
Architecture: any
Depends: ${misc:Depends}, ${perl:Depends}, ${python3:Depends}
Description: Check original Debian installation script generation
''')
        (debian / 'changelog').write_text('''sonic-script-probe (1.0-1) unstable; urgency=medium

  * Test maintainer scripts.

 -- SONiC <securely1g@gmail.com>  Sun, 18 Oct 2026 00:00:00 +0000
''')
        (package / 'etc/sonic-script-probe.conf').write_text('enabled=yes\n')
        (package / 'usr/lib/python3/dist-packages/sonic_script_probe.py').write_text('enabled = True\n')
        shutil.copyfile(paths['library'], package / 'usr/lib/libmetadata-probe.so.1')
        shutil.copyfile(paths['library'], package / 'usr/lib/python3/dist-packages/_probe.so')
        (package / 'usr/bin/sonic-script-probe').write_text('#!/usr/bin/perl\nprint "ok\\n";\n')
        (package / 'usr/bin/sonic-script-probe').chmod(0o755)
        (package / 'usr/lib/systemd/user/sonic-script-probe.service').write_text('[Unit]\nDescription=Probe\n[Service]\nExecStart=/usr/bin/sonic-script-probe\n[Install]\nWantedBy=default.target\n')
        (debian / 'sonic-script-probe.config').write_text('#!/bin/sh\n. /usr/share/debconf/confmodule\ndb_input low sonic-script-probe/enabled || true\ndb_go\n')
        (debian / 'sonic-script-probe.postinst').write_text('#!/bin/sh\nset -e\n. /usr/share/debconf/confmodule\n#DEBHELPER#\n')
        (debian / 'sonic-script-probe.maintscript').write_text('rm_conffile /etc/sonic-script-probe.old 0.9~\n')
        (debian / 'sonic-script-probe.tmpfiles').write_text('d /run/sonic-script-probe 0755 root root -\n')
        (debian / 'sonic-script-probe.templates').write_text('Template: sonic-script-probe/enabled\nType: boolean\nDefault: false\n_Description: Enable the probe?\n')
        po = debian / 'po'
        po.mkdir()
        (po / 'POTFILES.in').write_text('[type: gettext/rfc822deb] sonic-script-probe.templates\n')
        (po / 'fr.po').write_text('''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
"Language: fr\\n"

msgid "Enable the probe?"
msgstr "Activer la sonde ?"
''')
        environment = dict(os.environ, PATH='', DH_COMPAT='13', DEB_HOST_ARCH=receipt['architecture'], LANG='C', LC_ALL='C')
        environment.pop('PERL5LIB', None)
        environment.pop('PERL5OPT', None)
        for name in ('installdebconf', 'systemduser', 'tmpfiles', 'perl', 'python', 'makeshlibs', 'installdeb'):
            subprocess.run([str(paths[name]), '-psonic-script-probe'], cwd=root, env=environment, check=True)
        controls = package / 'DEBIAN'
        assert '/etc/sonic-script-probe.conf' in (controls / 'conffiles').read_text()
        assert 'ldconfig' in (controls / 'triggers').read_text()
        assert 'libmetadata-probe 1 sonic-script-probe' in (controls / 'shlibs').read_text()
        templates = (controls / 'templates').read_text()
        assert 'Description-fr' in templates and 'Activer la sonde ?' in templates, templates
        assert 'py3compile -p sonic-script-probe:' in (controls / 'postinst').read_text()
        assert 'py3clean -p sonic-script-probe:' in (controls / 'prerm').read_text()
        assert 'systemd-tmpfiles' in (controls / 'postinst').read_text()
        assert (package / 'usr/lib/tmpfiles.d/sonic-script-probe.conf').is_file()
        architecture = {'amd64': 'x86_64-linux-gnu', 'arm64': 'aarch64-linux-gnu'}[receipt['architecture']]
        assert (package / f'usr/lib/python3/dist-packages/_probe.cpython-313-{architecture}.so').is_file()
        assert 'db_purge' in (controls / 'postrm').read_text()
        assert 'deb-systemd-helper --user' in (controls / 'postinst').read_text()
        assert '#DEBHELPER#' not in (controls / 'postinst').read_text()
        assert 'rm_conffile /etc/sonic-script-probe.old' in (controls / 'preinst').read_text()
        substvars = (debian / 'sonic-script-probe.substvars').read_text()
        assert 'debconf' in substvars and 'init-system-helpers' in substvars and 'perl:Depends=perl:any' in substvars, substvars
        for name in ('preinst', 'postinst', 'postrm', 'config'):
            assert (controls / name).stat().st_mode & 0o777 == 0o755, name
        print(json.dumps(dict(architecture=receipt['architecture'], generated_controls=sorted(p.name for p in controls.iterdir()), substitutions=substvars), indent=2))


if __name__ == '__main__':
    main()
