# Client troubleshooting

`BLOCKED` before install means a safety condition needs review. Read
`next_step`, correct the named path or confirmation, and rerun the dry run.

`doctor` reports a missing file after an interrupted install. Run the install
command again with the same target and data directory. Installation is
idempotent and replaces only changed managed files.

Memory imports fail when Python cannot find the package. Install the local
package from the extracted kit:

```sh
python3 -m pip install ./system
```

MCP registration is harness-specific. Confirm the command uses the same Python
environment that contains `mcp` and the local MemoryWeaver package, and
that the data paths belong to the recipient account.

Optional RAG, Hermes, messaging, remote, and GPU modules do not affect core
doctor status. Troubleshoot them from their module README after core passes.

## Prime configuration exists but its tools are missing from a chat

Do not use the chat's tool search as the availability test. Run:

```sh
hermes --profile workspace mcp test prime-agent
```

If the handshake succeeds and reports discovered tools, reload MCP inside
Hermes and open a new session. Restart Claude Code or Codex after changing
their MCP configuration because those harnesses snapshot tools at startup.

Run `modules/prime-omp/diagnose-prime-omp.py` to distinguish a failed MCP
handshake from a stale session snapshot. For OMP readiness, run the canonical
feasibility probe. `omp-runtime-lock.json` is provenance and contains no live
`status` or `runnable_image` fields.

For an installed kit, the same check is available through doctor:

```sh
python installer/kit.py doctor --target /path/to/install --prime-omp \
  --hermes-bin /path/to/hermes --profile workspace \
  --session-tools-json /path/to/session-tools.json \
  --omp-probe /path/to/probe-omp-feasibility.py \
  --omp-python /path/to/hermes/venv/bin/python
```
