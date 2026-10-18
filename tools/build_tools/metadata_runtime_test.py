"""Generate real control metadata without building a Debian archive."""

import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile


def main():
    parser = argparse.ArgumentParser()
    for name in ("gencontrol", "shlibdeps", "gensymbols", "library", "runtime", "binary", "libc-data", "libc-control"):
        parser.add_argument("--" + name, required=True, type=lambda value: Path(value).absolute())
    args = parser.parse_args()
    expected_arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    manifest = json.loads((args.runtime / "toolchain.json").read_text())
    assert manifest["architecture"] == expected_arch, manifest
    with tempfile.TemporaryDirectory(prefix="dpkg-metadata-") as temporary:
        root = Path(temporary)
        environment = {"PATH": "", "HOME": temporary, "LANG": "C", "LC_ALL": "C",
                       "DEB_VENDOR": "UnspecifiedHostVendor"}

        def run(executable, arguments, *, success=True):
            result = subprocess.run([str(executable), *map(str, arguments)], cwd=root,
                                    env=environment, text=True, capture_output=True)
            if success and result.returncode:
                raise AssertionError(f"{executable}: {result.returncode}\n{result.stdout}\n{result.stderr}")
            if not success:
                assert result.returncode, "metadata tool unexpectedly accepted missing declared inputs"
            return result

        # No system Perl modules, dpkg data files or installed package database.
        perl = args.runtime / "bin/perl"
        report = json.loads(run(perl, ["-MJSON::PP", "-MDpkg::Vendor", "-e", r'''
print encode_json({loaded => \%INC, inc => \@INC,
                   datadir => $Dpkg::DATADIR, admindir => $Dpkg::ADMINDIR,
                   vendor => Dpkg::Vendor::get_current_vendor(),
                   library_paths => [Dpkg::Shlibs::get_library_paths()]});
''']).stdout)
        # Bazel may materialize the tree directory and symlink its individual
        # files. Compare the lookup paths, rather than resolving each file to
        # a different physical cache location.
        declared_root = args.runtime
        assert all(Path(value).is_relative_to(declared_root)
                   for value in report["loaded"].values()), report
        assert Path(report["datadir"]).is_relative_to(declared_root), report
        assert Path(report["admindir"]).is_relative_to(declared_root), report
        assert report["library_paths"] == [], report
        assert report["vendor"] == "Debian", report

        debian = root / "debian"
        debian.mkdir()
        (debian / "control").write_text("""Source: metadata-probe
Section: utils
Priority: optional
Maintainer: SONiC contributors <securely1g@gmail.com>

Package: metadata-probe
Architecture: any
Depends: ${shlibs:Depends}
Description: Check declared Debian metadata tools
 This executable is compiled from source by Bazel.
""")
        (debian / "changelog").write_text("""metadata-probe (1.0-1) unstable; urgency=medium

  * Exercise the declared metadata tools.

 -- SONiC contributors <securely1g@gmail.com>  Sun, 18 Oct 2026 00:00:00 +0000
""")
        # A source library owns its symbols and ABI version policy. Generate
        # those fields from its actual ELF and the same original changelog.
        library_package = root / "library-package"
        (library_package / "DEBIAN").mkdir(parents=True)
        (library_package / "usr/lib").mkdir(parents=True)
        library = library_package / "usr/lib/libmetadata-probe.so.1"
        shutil.copyfile(args.library, library)
        symbols = root / "symbols"
        run(args.gensymbols, ["-plibmetadata-probe1", "-P" + str(library_package),
                            "-e" + str(library), "-O" + str(symbols)])
        assert "libmetadata-probe.so.1 libmetadata-probe1 #MINVER#" in symbols.read_text()
        assert "metadata_probe_value@Base 1.0-1" in symbols.read_text()
        package = root / "packages/libc6"
        package.mkdir(parents=True)
        with tarfile.open(args.libc_data) as archive:
            archive.extractall(package, filter="data")
        controls = package / "DEBIAN"
        controls.mkdir()
        with tarfile.open(args.libc_control) as archive:
            archive.extractall(controls, filter="data")
        library = next(package.rglob("libc.so.6"))
        substvars = root / "substvars"
        result = run(args.shlibdeps, ["-O", "-l" + str(library.parent), "-S" + str(package), args.binary])
        assert "shlibs:Depends=libc6 (>= " in result.stdout, result
        substvars.write_text(result.stdout)

        # A host libc must not rescue an undeclared dependency.
        missing = run(args.shlibdeps, ["-O", args.binary], success=False)
        assert "cannot find library" in missing.stderr, missing
        # Nor may the host package database supply omitted symbols/shlibs.
        for control in ("shlibs", "symbols"):
            (controls / control).unlink(missing_ok=True)
        missing = run(args.shlibdeps, ["-O", "-l" + str(library.parent), args.binary], success=False)
        assert "no dependency information" in missing.stderr, missing

        staged = root / "staged"
        staged.mkdir()
        result = run(args.gencontrol, ["-pmetadata-probe", "-P" + str(staged), "-T" + str(substvars), "-O"])
        assert "Package: metadata-probe\n" in result.stdout, result
        assert "Architecture: " + expected_arch + "\n" in result.stdout, result
        assert "Version: 1.0-1\n" in result.stdout, result
        assert "Depends: libc6 (>= " in result.stdout, result
        assert "${" not in result.stdout, result
        assert "uninitialized value" not in result.stderr, result
        print(result.stdout)
        print(json.dumps({"architecture": expected_arch, "versions": manifest["versions"]}, indent=2))


if __name__ == "__main__":
    main()
