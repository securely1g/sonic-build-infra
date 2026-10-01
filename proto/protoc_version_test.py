import subprocess
import sys

from python.runfiles import runfiles


resolver = runfiles.Create()
assert resolver is not None
compiler = resolver.Rlocation(sys.argv[1])
assert compiler
version = subprocess.check_output([compiler, "--version"], text=True).strip()
assert version == "libprotoc 3.21.12", version

# Exercise every language output used by DASH, including proto3 optional fields.
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    source = root / "probe.proto"
    source.write_text('syntax = "proto3";\nimport "google/protobuf/timestamp.proto";\nmessage Probe { optional string name = 1; google.protobuf.Timestamp time = 2; }\n')
    subprocess.run([compiler, "--proto_path=" + directory, "--cpp_out=" + directory, "--python_out=" + directory, "--pyi_out=" + directory, "--experimental_allow_proto3_optional", str(source)], check=True)
    for name in ("probe.pb.cc", "probe.pb.h", "probe_pb2.py", "probe_pb2.pyi"):
        assert (root / name).stat().st_size > 0, name
    assert "google/protobuf/timestamp.pb.h" in (root / "probe.pb.h").read_text()
    assert "class Probe" in (root / "probe_pb2.pyi").read_text()
