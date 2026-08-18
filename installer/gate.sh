#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python3 "$ROOT/modules/guards/test-dry-run-gate.py"
python3 "$ROOT/modules/topology/configure-topology.py" --selftest
python3 "$ROOT/tools/restore-private-system-snapshot.py" --selftest
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
python3 "$ROOT/installer/kit.py" build --output "$STAGE/payload" --apply --yes
