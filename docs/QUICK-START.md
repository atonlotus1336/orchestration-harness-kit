# Client quick start — ten-minute provider-free path

Prerequisite: Python 3.11 or newer. The core does not require an LLM, GPU, cloud
account, or API key.

Expected elapsed time on a normal development machine is under ten minutes. At
the end, the SDK is installed, one thin adapter is connected by reference, and
all deterministic checks pass. Nothing is activated and no provider is called.

A small local model should receive only `SMALL-MODEL-INSTALL.md` while guiding
these commands.

## Windows PowerShell

```powershell
python installer/kit.py install --target C:\Users\alice\agent-kit --data-dir C:\Users\alice\agent-data --harness generic
python installer/kit.py install --target C:\Users\alice\agent-kit --data-dir C:\Users\alice\agent-data --harness generic --apply --yes
python installer/kit.py connect --target C:\Users\alice\agent-kit --harness hermes
python installer/kit.py connect --target C:\Users\alice\agent-kit --harness hermes --apply --yes
python installer/kit.py doctor --target C:\Users\alice\agent-kit
python installer/kit.py verify --target C:\Users\alice\agent-kit --harness hermes
```

The first command is a dry run and writes nothing. Review its result before the
second command. A successful doctor result is `PASS`.

## Linux, WSL, or macOS

```sh
python3 installer/kit.py install --target "$HOME/agent-kit" --data-dir "$HOME/agent-data" --harness generic
python3 installer/kit.py install --target "$HOME/agent-kit" --data-dir "$HOME/agent-data" --harness generic --apply --yes
python3 installer/kit.py connect --target "$HOME/agent-kit" --harness hermes
python3 installer/kit.py connect --target "$HOME/agent-kit" --harness hermes --apply --yes
python3 installer/kit.py doctor --target "$HOME/agent-kit"
python3 installer/kit.py verify --target "$HOME/agent-kit" --harness hermes
```

The supported harness names are `hermes`, `opencode`, `pi`, `antigravity`,
`codex`, `claude-code`, and `moa`. `connect` writes only a local descriptor and
keeps it `installed-not-activated`; it does not edit the harness, call a provider,
or start a service. Defaults are recipient-local references. If you supply
references, use only `env://`, `file://`, `secret://`, or `recipient://` values.

Run `verify` without `--harness` for the entire provider-free SDK verification
matrix. Run it with one harness name for that adapter's conformance path.

If the final command does not report `PASS`, leave the harness inactive and use
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md). Do not work around a failed gate by
copying credentials into the kit or enabling a provider fallback.

## Upgrade and rollback

Run the new release's `kit.py` against the existing install:

```sh
python3 installer/kit.py upgrade --target "$HOME/agent-kit"
python3 installer/kit.py upgrade --target "$HOME/agent-kit" --apply --yes
python3 installer/kit.py verify --target "$HOME/agent-kit"
```

The confirmed upgrade first snapshots every managed file beneath the separate
data directory. It preserves local configuration and connection descriptors. A
failed upgrade automatically restores the snapshot. To deliberately restore the
last snapshot:

```sh
python3 installer/kit.py rollback --target "$HOME/agent-kit"
python3 installer/kit.py rollback --target "$HOME/agent-kit" --apply --yes
```

To remove the installed program while preserving memory:

```sh
python3 installer/kit.py uninstall --target "$HOME/agent-kit"
python3 installer/kit.py uninstall --target "$HOME/agent-kit" --apply --yes
```

Uninstall removes only files named in `managed-files.json`, then removes that
controller manifest. The separate data directory and unmanaged recipient files
remain untouched.
