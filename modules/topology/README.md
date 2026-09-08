# topology — one PC first, expansion when you choose it

The default installation is one agentic system on one PC. That PC owns the
home disk, memory store, approvals, backups, gateway, and dashboard. You do not
need a VPS, second computer, VPN, or network share.

Two optional roles can be added later:

- `vps`: a remote gateway or automation node.
- `second-pc`: a compute worker or trusted thin client.

Neither remote role owns or directly writes the home disk. It uses an
authenticated service exposed by the solo PC. This keeps SQLite, Chroma, and
other single-writer stores inside one process and filesystem domain.

## Install the default solo role

1. Verify the templates:

   ```bash
   python configure-topology.py --selftest
   python configure-topology.py --check
   ```

2. Render a disabled local configuration:

   ```bash
   python configure-topology.py --role solo --machine-id home-pc --output topology-local.json
   ```

3. Replace each `<local-value>`. Keep the dashboard on `127.0.0.1`.
4. Run your local health checks. Set `enabled` to `true` only after they pass.

## Add a VPS or second PC

Render `--role vps` or `--role second-pc` on that machine. Connect both ends
with a private overlay such as Tailscale or another WireGuard network. The
gateway still requires authentication. The dashboard stays loopback-only and
uses an SSH tunnel.

Run the final reachability test from the remote machine. A successful local
probe does not prove that the firewall and bind address work remotely.

## Verify a key-only management hop

Use the included verifier when a thin client must reach a separate Windows
host and, optionally, its WSL runtime. It refuses password and interactive
keyboard fallback, requires an already trusted host key, and runs only a fixed
identity probe (`hostname` plus `id -un`). It never prints or reads private-key
contents.

```bash
python verify-key-only-transport.py \
  --identity-file /path/to/key \
  --destination <recipient-email> \
  --expected-host 'example-host' \
  --expected-user alice
```

For a Windows host that owns a Linux runtime, add the runtime identity:

```bash
python verify-key-only-transport.py \
  --identity-file /path/to/key \
  --destination <recipient-email> \
  --expected-host 'example-host' \
  --expected-user alice \
  --wsl-distribution example-distro \
  --runtime-user alice
```

The first command verifies the SSH account. The second additionally verifies
the WSL user. Both commands require a previously recorded SSH host key; they
do not accept a new key automatically. Test the refusal cases with:

```bash
python verify-key-only-transport.py --selftest
```

## Home-disk rule

The solo PC is the only storage writer. Remote machines should use the memory,
RAG, and job APIs. A direct SMB/NFS mount is optional, disabled by default, and
limited to read-only project access or backup transfer. Never put a live
SQLite, Chroma, or similar database on the shared mount.
