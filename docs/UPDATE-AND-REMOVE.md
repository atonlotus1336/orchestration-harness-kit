# Update and removal

Back up recipient-local configuration before updating. Extract the new release
separately, run a dry install against the existing target, then run the confirmed
install with the same data directory. The installer updates managed program files
place. Recipient memory and credentials are outside the managed file list.

For rollback, keep the prior release ZIP and repeat the same install procedure
from that version. Configuration is plain JSON-compatible YAML and may be
backed up independently.

Uninstall is dry-run by default. With `--apply --yes`, it removes only entries
from `managed-files.json`. It does not remove the selected data directory.
Delete memory only through a separate, explicit user-controlled operation after
confirming a backup.
