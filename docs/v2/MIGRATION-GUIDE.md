# Migrating from v1 to v2

v2 is an additive migration. It introduces protocol generation 2, a shared core,
thin adapters, and a managed lifecycle without removing the generation-1
contract or changing recipient-owned state.

## Safe migration

1. Keep the v1 installation and data directory intact.
2. Run the v2 `upgrade` command without `--apply` and review the plan.
3. Confirm that the reported data root is the existing recipient-owned root.
4. Apply the upgrade with `--apply --yes`.
5. Run `doctor`, then `verify` without a harness name.
6. Connect one adapter by reference and run its scoped `verify`.
7. Leave activation unchanged until a separate shadow canary is authorized.

```sh
python3 installer/kit.py upgrade --target "$HOME/agent-kit"
python3 installer/kit.py upgrade --target "$HOME/agent-kit" --apply --yes
python3 installer/kit.py doctor --target "$HOME/agent-kit"
python3 installer/kit.py verify --target "$HOME/agent-kit"
```

## What is preserved

- protocol generation 1 and the v1 command surface
- recipient memory, retrieval stores, credentials, and local configuration
- unmanaged files and reference-only connection descriptors
- the immutable v1 rollback commit recorded in `version-policy.json`

## Rollback

An upgrade snapshots managed files before mutation and automatically restores
them if verification fails. To request the latest rollback explicitly, preview
then apply:

```sh
python3 installer/kit.py rollback --target "$HOME/agent-kit"
python3 installer/kit.py rollback --target "$HOME/agent-kit" --apply --yes
python3 installer/kit.py doctor --target "$HOME/agent-kit"
```

Rollback never deletes the separate data root. A major upgrade, live harness
edit, default-model change, or provider activation is not part of migration.
