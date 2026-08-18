# Client release threat model

## Protected assets

- Maintainer projects, identity, credentials, memory, logs, and machine details.
- Recipient credentials, memory, configuration, and files outside the install root.
- The integrity and provenance of the distributed archive.

## Trust boundaries

The maintainer repository is private input. A temporary allowlisted staging
directory is the release boundary. The ZIP is the only client distribution
artifact. Recipients create configuration and memory after extraction. Those files
are never inputs to a later release build.

## Required controls

| Threat | Control and failing input |
|---|---|
| Broad copy or glob | Payload contract blocks an unknown group |
| Path traversal | Contract validator blocks `../private` |
| Symlink escape | Builder never follows links outside the payload |
| Credential or identity leak | Privacy canaries block keys, emails, and home paths |
| Archive leak | Scan occurs in staging before archive creation |
| Git-history leak | Client ZIP omits `.git`, and clients receive the ZIP |
| Malicious local value | Installer stages configuration and requires `--apply --yes` |
| Destructive removal | Uninstaller reads only its managed-file manifest and preserves data |
| Secret in diagnostics | Bearer and email canaries test central redaction |

The scanner is defense in depth. Structural exclusion by the payload contract is
the primary privacy boundary.
