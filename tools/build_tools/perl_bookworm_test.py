"""Bookworm Debian Perl 5.36 XS generator and relocatable interpreter contract."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    source = Path(sys.argv[1]).resolve()
    # Exercise the public executable consumed by Bazel generation actions.
    # The runtime's bin/perl alone does not cover its outer launcher dispatch.
    public_tool = Path(sys.argv[2]).absolute()
    public = subprocess.run(
        [str(public_tool), "-MConfig", "-MJSON::PP", "-MExtUtils::Constant", "-MExtUtils::ParseXS", "-e", "print encode_json({version=>$Config{version},loaded=>\\%INC,inc=>\\@INC})"],
        env={"PATH": "", "LC_ALL": "C"}, text=True, capture_output=True,
    )
    assert public.returncode == 0, public.stderr
    public_info = json.loads(public.stdout)
    assert public_info["version"] == "5.36.0", public_info
    # A sandbox can materialize a TreeArtifact as directories containing
    # file symlinks. Resolve the declared Perl entrypoint, as the static
    # launcher does, rather than assuming the tree root itself is a symlink.
    public_runtime = (public_tool.parent / source.name / "bin/perl").resolve().parent.parent
    for filename in [*public_info["loaded"].values(), *public_info["inc"]]:
        # The declared runtime can be reached through runfiles symlinks. The
        # static launcher resolves those aliases before Perl loads modules;
        # compare physical identities so host-installed modules still fail.
        assert Path(filename).resolve().is_relative_to(public_runtime), (filename, str(public_runtime), str(Path(filename).resolve()))
    with tempfile.TemporaryDirectory(prefix="bookworm-perl-") as directory:
        work = Path(directory)
        root = (work / "runtime with spaces").resolve()
        shutil.copytree(source, root, symlinks=False)
        perl = root / "bin/perl"
        env = {"PATH": "", "HOME": directory, "LC_ALL": "C"}

        def run(arguments, extra_env=None):
            result = subprocess.run([str(perl), *arguments], env=env | (extra_env or {}), cwd=work, text=True, capture_output=True)
            assert result.returncode == 0, (arguments, result.returncode, result.stdout, result.stderr)
            return result.stdout

        info = json.loads(run(["-MConfig", "-MJSON::PP", "-MCwd", "-MDigest::SHA", "-e", "print encode_json({version=>$Config{version},exe=>$^X,loaded=>\\%INC,inc=>\\@INC})"]))
        assert info["version"] == "5.36.0", info
        assert Path(info["exe"]) == perl, info
        for name, filename in info["loaded"].items():
            assert Path(filename).resolve().is_relative_to(root), (name, filename, str(Path(filename).resolve()), str(root))
        for filename in info["inc"]:
            assert Path(filename).resolve().is_relative_to(root), filename
        assert run(["-e", 'system($^X, "-MConfig", "-e", q{print $Config{version}}); exit($? >> 8)']) == "5.36.0"
        (work / "Consumer.pm").write_text("package Consumer; sub value { 42 } 1;\n")
        assert run(["-MConsumer", "-e", "print Consumer::value()"], {"PERL5OPT": "-I" + str(work)}) == "42"
        xs = work / "Probe.xs"
        xs.write_text("MODULE = Probe PACKAGE = Probe\n\nint\nanswer()\n CODE:\n  RETVAL = 42;\n OUTPUT:\n  RETVAL\n")
        xsubpp = next(root.glob("usr/share/perl/5.36*/ExtUtils/xsubpp"))
        typemap = xsubpp.parent / "typemap"
        generated = run([str(xsubpp), "-typemap", str(typemap), str(xs)])
        assert "XS_Probe_answer" in generated and "RETVAL = 42" in generated, generated
        run(["-MExtUtils::Constant=WriteConstants", "-e", 'WriteConstants(NAME=>"Probe", NAMES=>["EINVAL"], C_FILE=>"const-c.inc", XS_FILE=>"const-xs.inc")'])
        assert "EINVAL" in (work / "const-c.inc").read_text()
        assert "constant" in (work / "const-xs.inc").read_text()
        print("Perl 5.36.0: relocated core/XS modules, self-exec, xsubpp and ExtUtils::Constant PASS")


if __name__ == "__main__":
    main()
