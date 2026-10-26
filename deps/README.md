# APT installation links

Images that extract Debian package payloads do not run their maintainer scripts.
The following TAR targets supply command and library links for pinned Debian
APT payloads:

| Target | Installed selection |
| --- | --- |
| `//deps/vim:vim_alternatives_pkg` | Vim's `editor`, `ex`, `rview`, `rvim`, `vi`, `view`, `vim` and `vimdiff` alternatives select `vim.basic` |
| `//deps/automake:alternatives_pkg` | Automake and aclocal 1.17 |
| `//deps/compiler:alternatives_pkg` | GCC for `cc`, `c89`, `c99` and `cpp`; Clang for `c++` |
| `//deps/openmpi:alternatives_pkg` | OpenMPI command wrappers, headers, library and pkg-config aliases; AMD64 or ARM64 target paths |

Add these after the matching pinned Debian APT payloads. They create only
root-owned symlinks. They neither install the selected packages nor run
`update-alternatives`; they do not
reproduce its administrative database or manpage links. Keep package versions
and required runtime dependencies in the consumer's canonical APT lock.

The Vim links accompany `//deps/vim:vim_pkg` and `//deps/vim:vim_runtime_pkg`.
The alternatives TAR leaves `/usr/bin/vim` to the base image and creates
`/etc/alternatives/vim` pointing to `/usr/bin/vim.basic`.

Buildimage's `//platform/vs/docker-syncd-vs:docker-syncd-vs` consumes the Automake,
compiler and OpenMPI TARs. This replaces the command/link portion of its
installation-state snapshot; source-package permissions and service links remain
with the source producers. Its debug variant adds the Vim alternatives TAR after
the pinned Vim APT payloads.
The consumer's installed-image checks must also verify that link destinations
exist. `//deps:alternatives_test` checks the emitted Automake, compiler and
OpenMPI link chains and TAR metadata in the native AMD64 and ARM64 source CI
jobs.
