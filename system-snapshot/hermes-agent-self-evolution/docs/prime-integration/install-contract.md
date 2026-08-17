# Prime Agent Installation Contract

## Approved source

Phase 1 approves only the official source repository:

- Repository: `https://github.com/PrimeIntellect-ai/prime-agent`
- Release: `v0.7.1`
- Commit: `95afd319a78ae017a41241d50b013d656a0685ce`
- Source integrity: `git:sha1:95afd319a78ae017a41241d50b013d656a0685ce`

The release tag and full commit must agree. `main`, `beta`, abbreviated revisions,
unofficial forks, and an installation without artifact-integrity evidence are not
READY. A later reviewed release must replace all four values together and pass the
same contract, RPC, route, cleanup, redaction, and native-baseline gates.

## WSL installation layout

The approved per-user WSL layout is:

- executable: `$HOME/.local/bin/prime-agent`
- provenance manifest: `$HOME/.local/share/prime-agent/install-provenance.json`

The manifest is non-secret and has this exact shape:

```json
{
  "repository": "https://github.com/PrimeIntellect-ai/prime-agent",
  "version": "v0.7.1",
  "commit": "95afd319a78ae017a41241d50b013d656a0685ce",
  "sha256": "<64 lowercase hex characters>"
}
```

`sha256` is the checksum of the installed executable. The immutable commit identifies
the source; the executable checksum identifies the reviewed installed artifact. Both
are required before the checker reports READY. The manifest must contain no keys,
tokens, authentication state, provider configuration, or environment values.

## Installation and update procedure

Installation and update are separate, human-approved operations and are not performed
by the feasibility command. The operator must:

1. Obtain the versioned official release or check out the exact approved commit.
2. Verify that the release resolves to the approved full commit.
3. Review the installer/build inputs and create the executable without tracking
   `main` or `beta`.
4. Compute the installed executable's SHA-256 digest.
5. Write the non-secret provenance manifest with the official repository, pinned
   release, full commit, and digest.
6. Run `scripts/check-prime-install.sh`; only READY may proceed to the live opt-in
   compatibility probe.
7. On any later update, repeat every Phase 1 compatibility and baseline gate before
   enabling the new artifact.

The checker is read-only. It never installs, updates, repairs, logs in, starts, stops,
or restarts Prime, Hermes, a gateway, the dashboard, an MCP server, or Ollama.

## Activation boundary

Phase 1 may inspect the executable, manifest, and opt-in RPC behavior while Hermes is
running. It does not edit or reload a live profile. Existing Hermes API, Workspace,
Desktop, TUI, Telegram, and background sessions retain their current native behavior.

Future Prime configuration becomes active only after the relevant Hermes surface is
closed and reopened, or after a later phase documents and invokes an explicitly
supported reload. Preparing or validating files does not silently convert existing
sessions. Gateway ownership, the `8642` tailnet binding, the `9119` loopback dashboard,
and the VPS Telegram owner remain unchanged.
