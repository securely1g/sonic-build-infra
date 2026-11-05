"""Exercise configure's compiler argv against declared target GCC."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
source = Path(sys.argv[1])
work = Path.cwd()
cc = shlex.split(os.environ["CC"])
ar = shlex.split(os.environ["AR"])
ranlib = shlex.split(os.environ["RANLIB"])
include = source / "include dir"
outputs = Path("outputs with spaces")
outputs.mkdir()
libraries = Path("library output")
libraries.mkdir()
subprocess.run(cc + ["-I", str(include), "-c", str(source / "library source.c"), "-o", str(libraries / "library object.o")], check=True)
subprocess.run(ar + ["cr", str(libraries / "libprobe.a"), str(libraries / "library object.o")], check=True)
subprocess.run(ranlib + [str(libraries / "libprobe.a")], check=True)
subprocess.run(cc + ["-I" + str(include), "-c", str(source / "input source.c"), "-o", str(outputs / "explicit object.o"), "-MMD", "-MF", str(outputs / "dependency file.d")], check=True)
link = ["-I", str(include), str(source / "input source.c"), "-L" + str(libraries), "-lprobe"]
subprocess.run(cc + link + ["-o", str(outputs / "explicit program")], check=True)
subprocess.run(cc + link, check=True)
subprocess.run(cc + ["-I" + str(include), "-c", str(source / "input source.c")], check=True)
subprocess.run(cc + ["-I", str(include), "-S", str(source / "input source.c")], check=True)
Path("result.json").write_text(json.dumps({"source": str(source), "caller_cwd": str(work), "source_default_object_exists": (source / "input source.o").exists(), "source_default_assembly_exists": (source / "input source.s").exists()}, indent=2) + "\n")

# A second shared library supplies an indirect symbol. Only the second
# rpath-link directory can resolve it while linking the actual executable.
sys.dont_write_bytecode = True
sys.path.insert(0, str(source.parent))
from probe_runner import launcher
first = Path("link first")
second = Path("link second with spaces")
first.mkdir()
second.mkdir()
Path("hidden source.c").write_text("int hidden(void) { return 37; }\n")
Path("dependent source.c").write_text("int hidden(void); int probe_value(void) { return hidden(); }\n")
subprocess.run(cc + ["-shared", "-fPIC", "hidden source.c", "-Wl,-soname,libhidden.so", "-o", str(second / "libhidden.so")], check=True)
subprocess.run(cc + ["-shared", "-fPIC", "dependent source.c", "-L" + str(second), "-lhidden", "-Wl,-soname,libprobe.so", "-o", str(first / "libprobe.so")], check=True)
caller = Path("rpath caller")
caller.mkdir()
outer = work / "rpath compiler"
launcher(outer, cc, Path(os.environ["PATH"].split(os.pathsep)[0]) / "python3", link_flags=["-Wl,-rpath-link," + str(first) + ":" + str(second), "-Wl,-rpath,$ORIGIN/../link first:$ORIGIN/../link second with spaces"], root=work)
subprocess.run([str(outer), "-I", str(include), str(source / "input source.c"), "-L" + str(work / first), "-lprobe", "-o", "linked program"], cwd=caller, check=True)
