# Phase 10 OMP controlled sidecar.
# Pinned, immutable OMP Node SDK host. Rootless, read-only rootfs, no host network.
# Built from the Plan 01 runtime lock (config/omp-runtime-lock.json).

FROM docker.io/library/node:22-bookworm-slim

# Bun pinned to the Plan 01 minimum (>=1.3.14).
RUN apt-get update && apt-get install -y --no-install-recommends unzip curl bash ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://bun.sh/install | bash \
    && mv /root/.bun/bin/bun /usr/local/bin/bun \
    && bun --version

WORKDIR /opt/omp

# Pin the exact OMP package with integrity (Plan 01 lock).
RUN bun add @oh-my-pi/pi-coding-agent@17.3.0

# Install reviewed, read-only LSP servers (Plan 03 LSP registry).
RUN npm install -g pyright@1.1.390 typescript-language-server@4.3.3 typescript@5.6.3 \
    && node -e "console.log('pyright', require('/usr/local/lib/node_modules/pyright/package.json').version)" \
    && node -e "console.log('tls', require('/usr/local/lib/node_modules/typescript-language-server/package.json').version)"

# Copy the controlled sidecar host.
COPY scripts/omp/controlled-sidecar.mjs /opt/omp/controlled-sidecar.mjs

# Read-only root filesystem; no host network; no privileged mode.
RUN chmod +x /opt/omp/controlled-sidecar.mjs

ENTRYPOINT ["bun", "/opt/omp/controlled-sidecar.mjs"]
