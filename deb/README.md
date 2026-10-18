# Preserve a component's Debian installation metadata

`sonic_deb` can package an existing source-built payload with the owning
component's final Debian metadata. For example, eventd must declare
`/etc/eventd.json` as a configuration file so an upgrade preserves local edits.
Copying its runtime TAR into an image does not give dpkg that information.

```starlark
sonic_deb(
    name = "eventd_deb",
    data = ":eventd_runtime",
    package = "sonic-eventd",
    version = "1.0.0-0",
    control_file = ":generated_binary_control",
    control_files = {
        "debian/sonic-eventd.conffiles": "conffiles",
        ":generated_postinst": "postinst",
    },
)
```

The example labels must be supplied by the component. `control_file` is the
**final binary package control**, typically the output of `dpkg-gencontrol`.
It is not the source `debian/control` file, which may describe several packages
and contain substitutions such as `${shlibs:Depends}`. Generate shared-library
dependencies from the actual built payload with the component's packaging
tools. Do not reuse controls captured from a different Make-built binary.

The rule checks package name, version and target architecture against its
arguments and rejects unresolved substitutions. It retains other fields,
including `Multi-Arch`, `Pre-Depends`, `Breaks`, `Replaces` and `Provides`.
Do not also set `maintainer`, `description` or `depends` when supplying this file.
Existing callers without `control_file` keep the original interface.

`control_files` maps declared labels to these standard names: `conffiles`,
`preinst`, `postinst`, `prerm`, `postrm`, `config`, `templates`, `triggers`,
`shlibs` and `symbols`. Script members get executable permissions; data members
do not. These files must already contain any required debhelper expansions.
The owning component remains responsible for selecting its existing scripts,
generating architecture-correct metadata and validating upgrade/removal behavior.

The shared rule assembles archives; it does not install packages or generate
dependency metadata. Image consumers can pass the resulting DEBs to
`rules_distroless`'s `apt_install`, which runs the actual APT/dpkg lifecycle.

## Validation

`//deb:check_control_test` checks invalid metadata.
`//deb/tests:control_archive_test` checks the actual control TAR, including
executable script permissions, configuration files, triggers and payload hashes.
These tests do not produce a DEB. `//deb/tests:example_deb` is a separate manual
packaging target; package production and installation tests require the task's
explicit DEB-build authorization. Passing the control tests alone does not
establish installed package behavior.
