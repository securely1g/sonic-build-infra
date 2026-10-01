FROM debian:trixie-20260918@sha256:9cc080028c43b27d2074d63a5f9caf7166d731494965616c1a6d2827a004585c

ARG TARGETARCH
ARG SOURCE_REVISION
ARG RECIPE_SHA256
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8

# Use the same snapshots as MODULE.bazel's shared build_tools package set.
# Signed APT metadata authenticates the HTTP bootstrap before CA installation.
RUN rm /etc/apt/sources.list.d/debian.sources && \
    printf '%s\n' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20260727T143429Z trixie main' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20260727T143429Z trixie-updates main' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/20260726T121236Z trixie-security main' \
      > /etc/apt/sources.list && \
    printf '%s\n' 'Package: *' 'Pin: origin "snapshot.debian.org"' 'Pin-Priority: 1001' \
      > /etc/apt/preferences.d/snapshot

COPY containers/build-tools/packages.txt /tmp/build-tools-packages.txt
# Align the newer base filesystem with the older, immutable package snapshot.
RUN apt-get -o Acquire::Retries=5 update && \
    apt-get -y --allow-downgrades dist-upgrade && \
    xargs -r apt-get -y --allow-downgrades --no-install-recommends install \
      < /tmp/build-tools-packages.txt && \
    rm -rf /var/lib/apt/lists/* /tmp/build-tools-packages.txt

COPY containers/build-tools/Dockerfile containers/build-tools/packages.txt \
     containers/build-tools/identity.py containers/build-tools/smoke_test.py \
     /opt/sonic-build-tools-recipe/

RUN case "$TARGETARCH" in \
      amd64) bazel_arch=x86_64; bazel_sha=61d89402f0368e64b6c827be5de79d8e65382e8124c3cbb97325611a1851392e ;; \
      arm64) bazel_arch=arm64; bazel_sha=b7f2a85595e8a87d54843bc656c1e379fd9b8c1b5f783dc41717c1d7eb7cc49f ;; \
      *) echo "Unsupported execution architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac && \
    curl --fail --location --retry 3 --output /tmp/bazel \
      "https://releases.bazel.build/8.5.1/release/bazel-8.5.1-linux-${bazel_arch}" && \
    printf '%s  %s\n' "$bazel_sha" /tmp/bazel | sha256sum --check --strict && \
    install -m 0755 /tmp/bazel /usr/local/bin/bazel && rm /tmp/bazel && \
    python3 /opt/sonic-build-tools-recipe/identity.py --write-marker "$RECIPE_SHA256" && \
    python3 /opt/sonic-build-tools-recipe/smoke_test.py

LABEL org.opencontainers.image.source="https://github.com/securely1g/sonic-build-infra" \
      org.opencontainers.image.revision="$SOURCE_REVISION" \
      org.opencontainers.image.description="Pinned SONiC Bazel build tools for native AMD64 and ARM64 execution" \
      org.sonic.build-tools.recipe-sha256="$RECIPE_SHA256"
