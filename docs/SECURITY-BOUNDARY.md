# Client release security boundary

The Git repository is the maintainer workspace. The client artifact is the ZIP
created by `make-bundle.ps1` or `make-bundle.sh`. The builder reads only paths
named by `installer/contracts/public-payload.json`, refuses path traversal,
does not follow symlinks, and scans the staged copy before archiving it.

The default client payload contains the deterministic safety modules, an empty
MemoryWeaver implementation, documentation, topology templates, and installer.
It excludes `system-snapshot`, Git history, runtime stores, local configuration,
deny lists, credentials, projects, logs, caches, and evidence.

Recipient secrets belong only in environment variables, an operating-system
credential store, or recipient-owned ignored files. Installer output reports
status and remediation, never secret values.

The release gate blocks an unknown payload group, absolute path, path traversal,
external symlink, credential pattern, private key, personal home path, or
unapproved email appears in the staged artifact.
