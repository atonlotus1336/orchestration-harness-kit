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
