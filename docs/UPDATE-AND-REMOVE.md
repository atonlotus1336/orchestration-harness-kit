# Update and removal

Run a dry upgrade from the new release, then apply it explicitly:

```sh
python3 installer/kit.py upgrade --target "$HOME/agent-kit"
python3 installer/kit.py upgrade --target "$HOME/agent-kit" --apply --yes
python3 installer/kit.py verify --target "$HOME/agent-kit"
```

Upgrade refuses a damaged managed-file inventory. Before changing anything, it
creates a content-addressed snapshot in the separate data directory. Recipient
configuration and harness connection descriptors remain local and are not
replaced by the release payload. A failed copy or verification step triggers the
same snapshot restore automatically.

Rollback is also dry-run first:

```sh
python3 installer/kit.py rollback --target "$HOME/agent-kit"
python3 installer/kit.py rollback --target "$HOME/agent-kit" --apply --yes
```

The default is the snapshot recorded by the last successful upgrade. An explicit
snapshot must be a validated 16-character identifier already present beneath the
configured data root. Snapshot hashes are checked before any installed file is
restored.

Uninstall is dry-run by default. With `--apply --yes`, it removes only entries
from `managed-files.json`. It does not remove the selected data directory.
Delete memory only through a separate, explicit user-controlled operation after
confirming a backup.
