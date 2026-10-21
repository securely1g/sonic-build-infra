# OCI image helpers

## Add service accounts

`account_layer` preserves `/etc/passwd` and `/etc/group` from the checked OCI
base and appends only the declared service accounts. For example, the base image
adds Redis, and Syncd adds the `_ssh` group without copying Debian's account list.
Public Distroless rules serialize additions; the shared merger checks them against
the base; the public TAR rule packages the two resulting files.

```starlark
load("@sonic_build_infra//oci:accounts.bzl", "account_layer")

account_layer(
    name = "accounts",
    base = ":base",
    architecture = "amd64",
    users = [{"username": "redis", "password": "x", "uid": 100, "gid": 101,
              "home": "/var/lib/redis", "shell": "/usr/sbin/nologin"}],
    groups = [{"name": "redis", "password": "x", "gid": 101}],
)
```

Add `:accounts` once to `oci_image.tars`. `:accounts.receipt` records the checked
base, additions and output hashes. Native AMD64 and ARM64 are supported.
Names and numeric IDs must not conflict with inherited accounts; an identical
existing account is retained. New users must have a primary group, and explicit
group members must exist. Inherited account files must be regular root-owned
files with mode `0644`. Whiteouts are honored, so removed files are not recovered
from an older layer. Existing lines remain byte-for-byte unchanged.

This creates no Debian packages, runs no installation scripts and does not modify
the base image or the build host's accounts. It updates only passwd/group;
password databases and home-directory creation are separate installation tasks.
