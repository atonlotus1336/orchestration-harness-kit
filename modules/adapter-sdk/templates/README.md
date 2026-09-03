# Thin adapter templates

Copy both files into a new adapter package. Change harness identity and local
translation code only. Shared routing, authority, retrieval, memory, provider,
activation, and optimization logic must remain in `orchestration-core`.

The manifest stores credential references, never credential values. Keep local
configuration outside the repository and pass its reference to the installer.
