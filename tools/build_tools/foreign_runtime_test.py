"""Exercise declared autotools, interface generators and their private runtime."""

import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


def main():
    source = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="foreign-tools-") as tmp:
        root = Path(tmp)
        runtime = root / "relocated-runtime"
        shutil.copytree(source, runtime, symlinks=True)
        work = root / "source"
        work.mkdir()
        tools = runtime / "bin"
        env = {"PATH": "", "HOME": str(work), "LANG": "C", "LC_ALL": "C", "TEST_TMPDIR": str(root)}

        def run(tool, args=(), data=None):
            result = subprocess.run([str(tools / tool), *args], cwd=work,
                                    env=env, input=data, capture_output=True)
            assert result.returncode == 0, (tool, result.returncode,
                                            result.stdout.decode(errors="replace"),
                                            result.stderr.decode(errors="replace"))
            return result.stdout

        (work / "configure.ac").write_text(
            "AC_INIT([foreign-probe],[1.0])\nAM_INIT_AUTOMAKE([foreign])\n"
            "AC_CONFIG_MACRO_DIRS([m4])\nAC_PROG_CC\nLT_INIT\n"
            "AC_CONFIG_FILES([Makefile])\nAC_OUTPUT\n")
        (work / "Makefile.am").write_text("ACLOCAL_AMFLAGS = -I m4\nlib_LTLIBRARIES = libprobe.la\nlibprobe_la_SOURCES = probe.c\n")
        (work / "probe.c").write_text("int probe(void) { return 42; }\n")
        (work / "m4").mkdir()
        run("autoreconf", ["-fi"])
        for name in ("configure", "Makefile.in", "ltmain.sh", "m4/libtool.m4"):
            assert (work / name).is_file(), name
        # The wrapper tree must remain usable after Bazel moves/caches it.
        assert str(source).encode() not in (work / "configure").read_bytes()

        (work / "probe.proto").write_text(
            'syntax = "proto3"; package probe; '
            'import "google/protobuf/any.proto"; import "google/protobuf/empty.proto"; '
            'message Request { string name = 1; google.protobuf.Any payload = 2; } '
            'service Echo { rpc Send(Request) returns (google.protobuf.Empty); }\n')
        run("protoc", ["-I.", "--cpp_out=.", "--python_out=.", "--grpc_out=.",
                       "--plugin=protoc-gen-grpc=" + str(tools / "grpc_cpp_plugin"), "probe.proto"])
        run("protoc", ["-I.", "--grpc_out=.",
                       "--plugin=protoc-gen-grpc=" + str(tools / "grpc_python_plugin"), "probe.proto"])
        for name in ("probe.pb.h", "probe.pb.cc", "probe.grpc.pb.h", "probe.grpc.pb.cc", "probe_pb2.py", "probe_pb2_grpc.py"):
            assert (work / name).is_file(), name
        # Adding the standard schemas must retain the no--I default, while
        # explicit includes still take precedence and exclude unrelated cwd files.
        run("protoc", ["--descriptor_set_out=implicit.pb", "probe.proto"])
        assert (work / "implicit.pb").is_file()
        override = work / "override/google/protobuf"
        override.mkdir(parents=True)
        (override / "any.proto").write_text(
            'syntax = "proto3"; package google.protobuf; message Any { string caller_owned = 1; }\n')
        (work / "override/explicit.proto").write_text(
            'syntax = "proto3"; import "google/protobuf/any.proto"; '
            'message Explicit { google.protobuf.Any value = 1; }\n')
        run("protoc", ["-Ioverride", "--include_imports", "--descriptor_set_out=explicit.pb", "explicit.proto"])
        assert b"caller_owned" in (work / "explicit.pb").read_bytes()
        (work / "protoc.args").write_text("--proto_path=override\n--descriptor_set_out=unexpected.pb\nprobe.proto\n")
        excluded = subprocess.run([str(tools / "protoc"), "@protoc.args"], cwd=work,
                                  env=env, capture_output=True)
        assert excluded.returncode != 0 and not (work / "unexpected.pb").exists(), excluded
        (work / "probe.thrift").write_text("struct Request { 1: string name }\nservice Echo { Request send(1: Request request) }\n")
        run("thrift", ["--gen", "cpp", "probe.thrift"])
        assert (work / "gen-cpp/Echo.cpp").is_file()
        python = json.loads(run("python3", ["-c", "import json,sys;print(json.dumps(sys.path))"]))
        assert all(not p.startswith(("/usr/", "/lib/")) for p in python), python
        perl = json.loads(run("perl", ["-MJSON::PP", "-MXML::Simple", "-MData::Dumper", "-MGetopt::Long", "-e", 'print encode_json(\\%INC)']))
        assert all(str(runtime) in p for p in perl.values()), perl
        manifest = json.loads((runtime / "toolchain.json").read_text())
        graph = run("dot", ["-Tsvg"], data=b"digraph G { a -> b }\n")
        assert b"<svg" in graph and b"a" in graph
        metadata = root / "relocated-metadata"
        shutil.copytree(Path(sys.argv[2]).resolve(), metadata, symlinks=True)
        subprocess.run([str(metadata / "bin/doxygen"), "--version"], env=env, capture_output=True, check=True)
        spelling = subprocess.run([str(metadata / "bin/aspell"), "list"], input=b"switch\n", env=env, capture_output=True, check=True)
        assert not spelling.stdout, spelling
        print(json.dumps({"architecture": platform.machine(), "profile": manifest["profile"],
                          "relocated": True, "empty_path": True, "autotools": True,
                          "protobuf_cpp_python_grpc": True, "thrift_cpp": True,
                          "graphviz_svg": True, "private_python": python, "private_perl": perl}, indent=2))


if __name__ == "__main__":
    main()
