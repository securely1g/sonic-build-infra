These small inputs reproduce the finalized-control consumer in SONiC's OTEL migration. No Debian package is built or fetched by the test.

`control` is the finalized control from the verified official otelcol-contrib 0.144.0 AMD64 release package. `otelcol-contrib.service` is the corresponding unmodified source unit from release commit 9e444f54f4033bf6b7c254f0a8797ffd3917b6de. The service is included in the source-built runtime TAR at `lib/systemd/system/otelcol-contrib.service`.

Release package: https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v0.144.0/otelcol-contrib_0.144.0_linux_amd64.deb
Package SHA256: e83278a763b9c7779f9facf3ac2356b7add8975a5f14d4606a625605f50eee94
Source unit: https://github.com/open-telemetry/opentelemetry-collector-releases/blob/9e444f54f4033bf6b7c254f0a8797ffd3917b6de/distributions/otelcol-contrib/otelcol-contrib.service
Upstream license: Apache-2.0.

The test stages only the unit because its presence is the exact trigger for the invalid source-helper invocation. It checks the actual finalized package identity and absence of synthesized dependencies/maintainer scripts. Existing source-template tests still exercise real ELF dependencies and Python/Perl/debconf/init helpers; a source-template unit without compat must still fail.
