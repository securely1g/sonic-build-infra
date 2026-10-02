# Source pull request CI

`Bazel (AMD64)` and `Bazel (ARM64)` run on native GitHub-hosted Ubuntu
runners in the same pinned Debian Trixie image. PR events cover all branches
and paths; push events also validate `master`. Checkout uses the proposed merge
revision, so the checks exercise the current PR with its target branch.

Run `bash ci/bazel-ci.sh` on a native AMD64 or ARM64 Debian Trixie host with
Bazel 8.5.1, a C/C++ compiler, binutils, Git, Python, tar and xz installed.
The script pins registry resolution independently of local Bazel settings and
selects matching execution/target platforms. The repository deliberately excludes
its large generated module lock; CI retains the resolved lock as evidence.

Both CI jobs set `SONIC_PACKAGE_INSTALL_TEST=1` to install the sample runtime
and matching debug Debian packages using the container's `dpkg`, run the installed
binary, verify package checksums and compare the installed ELF build IDs and
debug line information. For the same local check, set that variable when running
the script as root **inside a disposable Debian container**: it installs files
under `/usr`. Without the variable, the script still builds the packages and
runs the sandboxed packaging regression tests, but skips system installation.

The explicit source tests cover C/C++ linking, shared-library/PIC behavior,
stripping and deployment debug-provider/content behavior. The shared API branch
also runs the nine external-consumer analysis/sysroot tests and rejects the
unsafe archive fixture. The wheel-layer branch also runs wheel installation,
archive ownership and debug-header ownership tests. These checks do not claim
ARMHF execution, an installed SONiC image, or full component downstream coverage.

Packaging tests exercise pinned `dpkg-deb`, missing and late parent directories,
forward hardlinks, symlinks, sparse files, checksums, deterministic output and
invalid paths. The protoc smoke test generates C++, Python and Python type stubs.
Each job also packages the text-only fixture for the opposite target architecture
and checks its control metadata, proving packaging tools remain native under
`cfg = "exec"`. This is a packaging check, not a claim of cross C++ compilation.

Each run retains test XML/logs, build events, native host metadata, revision,
resolved module locks, sample executables, and their runtime/debug deployment
tars, matching Debian packages and installation evidence. Successful runs include
checksums in `provenance.json`. Download the
architecture-specific artifact from the Actions run page. Partial evidence is
retained when a validation step fails.

Both architecture checks must be required on the PR target branch. Repository
settings enforce this separately from the workflow; skipped or missing jobs
are not successful source coverage.
