# Prime Agent and OhMyPi on a recipient Hermes installation

This is the supported path for a friend or client who clones the full private
Git repository. It restores the bridge, binds the recipient's WSL identity,
adds `prime-agent` to selected Hermes profiles without replacing other MCP
servers, and then proves each live boundary independently.

The smaller public release payload deliberately excludes `system-snapshot/`.
It contains the safety contract and diagnostic, but not the bridge
implementation. Use a full authorized Git clone for this procedure.

## 1. Restore recipient-specific source

Copy `system-snapshot/PLACEHOLDERS.template.json` outside Git and fill every
value. `__WSL_DISTRO__` must be the exact name printed by `wsl.exe -l -q`, not a
description or display label.

Dry-run and then restore:

```powershell
python tools/restore-private-system-snapshot.py `
  --workspace-root D:\AgentWorkspace `
  --substitutions D:\Private\hermes-restore-values.json

python tools/restore-private-system-snapshot.py `
  --workspace-root D:\AgentWorkspace `
  --substitutions D:\Private\hermes-restore-values.json `
  --apply
```

The restore fails closed if a recipient placeholder is missing. It does not
copy credentials, install third-party applications, or change a live Hermes
profile.

## 2. Prepare the bridge in the Hermes Python environment

Run these commands inside the recipient's WSL distribution:

```bash
cd /mnt/d/AgentWorkspace/hermes-agent-self-evolution
HERMES_PYTHON="$HOME/.hermes/hermes-agent/venv/bin/python"
"$HERMES_PYTHON" -m pip install -e ".[phase01]"
bash scripts/acquire-prime-agent-source.sh
bash scripts/check-prime-install.sh
```

The source acquisition is pinned and checksum-verified. The install checker
will remain `MISSING` until the recipient separately installs the reviewed
Prime executable and its non-secret provenance manifest as described in
`docs/prime-integration/install-contract.md`. This is intentional: the kit does
not execute an upstream installer or inherit provider credentials.

## 3. Stage and activate the Hermes profile overlay

Render candidates first, inspect them, then activate with a backup:

```bash
"$HERMES_PYTHON" scripts/render-prime-profile-overlay.py \
  --profiles-root "$HOME/.hermes/profiles" \
  --profile workspace

"$HERMES_PYTHON" scripts/audit-prime-profile-overlays.py \
  --profiles-root "$HOME/.hermes/profiles" \
  --policy config/prime-profile-policy.json

"$HERMES_PYTHON" scripts/activate-prime-profile-overlays.py \
  --profiles-root "$HOME/.hermes/profiles" \
  --policy config/prime-profile-policy.json \
  --backup-root "$HOME/.hermes/backups/prime-profile-overlays"
```

Restart the affected Hermes gateway or open a new Hermes session. Existing
sessions retain their old MCP tool snapshot.

## 4. Prove the three live boundaries

Do not infer live readiness from a config file or from
`omp-runtime-lock.json`.

```bash
hermes --profile workspace mcp test prime-agent

python modules/prime-omp/diagnose-prime-omp.py \
  --profile workspace \
  --omp-probe /mnt/d/AgentWorkspace/hermes-agent-self-evolution/scripts/probe-omp-feasibility.py \
  --omp-python "$HOME/.hermes/hermes-agent/venv/bin/python"
```

Acceptance requires all of the following:

- `prime_mcp=CONNECTED`: Hermes completed the MCP handshake and discovered
  Prime tools.
- A newly opened session contains a `prime_` tool such as `prime_delegate`;
  an older session may correctly report `STALE`.
- `omp_runtime=READY`: the canonical feasibility probe admitted the pinned,
  contained OhMyPi runtime. A lock file alone is provenance, not runtime
  evidence.

For the OMP image, relay, and live SDK gates, follow the restored
`docs/OMP-PRIME-CODING-RUNBOOK.md`. Do not mark a client deployment complete
until one harmless disposable-worktree job returns a patch and Hermes, rather
than the worker, performs the final apply and verification.

## Repository acceptance test

Maintainers should run this before distributing the Git revision:

```powershell
python tools/verify-prime-omp-client-path.py --selftest
```

It restores into a temporary client workspace, proves that an omitted WSL
distro is rejected, renders a Hermes overlay while preserving existing
providers/memory/toolsets/MCP servers, and syntax-checks the restored MCP
source inventory without assuming that
the maintainer test interpreter is the recipient's Hermes environment. The
dependency import, live Prime handshake, and OMP execution remain explicit
recipient-machine gates.

In the contract-only public payload, the same command returns `SKIPPED` with a
pointer to the authorized full-Git path; this is not a Prime readiness result.
