# prime-omp — governed coding workers for Hermes

**What it is:** an optional integration contract for connecting Prime Agent and
oh-my-pi (OMP) to Hermes without giving either tool control of your machine or
your authoritative repository.

**Why you'd want it:** Prime can coordinate a coding task and OMP can do the
work in a disposable workspace, while Hermes remains the only authority for
models, approvals, memory, audit records, and final patch application.

## The boundary

The contract is deliberately narrow:

1. Hermes classifies the request and creates the job.
2. Prime receives only the approved task context.
3. OMP works in a temporary worktree with no container socket, no shared
   memory, no credential store, and no direct write access to the source repo.
4. OMP returns a patch plus evidence.
5. Hermes independently applies the patch and runs the approved checks.

The worker cannot delegate, discover arbitrary tools, choose another model
provider, or silently change the production repository. A failed verification
leaves the authoritative repository unchanged.

## Install just this part

1. Install Hermes, Prime Agent, and OMP from their official distribution
   channels. Keep their package/version details in a local lock file; do not
   copy credentials or provider settings into this repository.
2. Copy `prime-omp-contract.json` into your local policy directory and replace
   only the `<local-value>` fields.
3. Configure your Hermes capability broker to enforce the contract before it
   can start either worker.
4. Run the verifier:

   ```bash
   python verify-prime-omp-contract.py prime-omp-contract.json
   ```

5. Perform one harmless coding task. Confirm that the worker returns a patch,
   Hermes performs the final apply, and the audit receipt records both steps.

The setup helper binds reviewed executable checksums into a disabled local
broker configuration:

```bash
python prepare-prime-omp.py \
  --prime-executable <local-prime-path> --prime-sha256 <reviewed-sha256> \
  --omp-executable <local-omp-path> --omp-sha256 <reviewed-sha256> \
  --output prime-omp-local.json
```

Run `python prepare-prime-omp.py --selftest` first. The helper never downloads
third-party code and never enables the broker by itself.

## Test it worked

The verifier rejects a policy if a worker can write the authoritative
repository, access a container socket, use shared memory, or apply a patch on
its own. Its built-in self-test exercises every one of those failure cases:

```bash
python verify-prime-omp-contract.py --selftest
```

## Diagnose a missing Prime or OMP tool

The connector, the current chat's tool snapshot, and the OMP runtime are three
different boundaries. Test them separately:

```bash
python diagnose-prime-omp.py \
  --hermes-bin /path/to/hermes \
  --profile workspace \
  --session-tools-json /path/to/session-tools.json \
  --omp-probe /path/to/probe-omp-feasibility.py \
  --omp-python /path/to/hermes/venv/bin/python
```

The session-tools file is optional. It may be a JSON string list or an object
with a `tools` list. If Prime's MCP handshake succeeds but that list has no
Prime tool, `session_tools` is `STALE` and the next action is
`RELOAD_MCP_AND_OPEN_NEW_SESSION`. Do not inspect `omp-runtime-lock.json` for
live status; pass the canonical feasibility probe. If the Hermes executable
and its Python live in the same directory, the diagnostic selects that Python
automatically. Otherwise, provide `--omp-python` explicitly.

Run the diagnostic's named failure cases without a Hermes installation:

```bash
python diagnose-prime-omp.py --selftest
```

This package ships the port and its safety contract, not the third-party
applications, a model route, a local machine path, or any runtime credentials.

Authorized users of the full Git repository also receive the sanitized bridge
implementation under `system-snapshot/`. Follow
`docs/PRIME-OMP-CLIENT-SETUP.md` to restore it, register `prime-agent` with
Hermes, and run the clean-client plus live acceptance gates. The smaller public
release payload does not include that private implementation snapshot.
