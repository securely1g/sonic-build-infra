"""Keep the SONiC account-layer name compatible with public Distroless accounts."""

load("@rules_distroless//distroless:defs.bzl", _accounts = "accounts")

account_layer = _accounts
