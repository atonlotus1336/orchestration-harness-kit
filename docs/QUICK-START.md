# Client quick start

Prerequisite: Python 3.11 or newer. The core does not require an LLM, GPU, cloud
account, or API key.

A small local model should receive only `SMALL-MODEL-INSTALL.md` while guiding
these commands.

## Windows PowerShell

```powershell
python installer/kit.py install --target C:\Users\alice\agent-kit --data-dir C:\Users\alice\agent-data
python installer/kit.py install --target C:\Users\alice\agent-kit --data-dir C:\Users\alice\agent-data --apply --yes
python installer/kit.py doctor --target C:\Users\alice\agent-kit
```

The first command is a dry run and writes nothing. Review its result before the
second command. A successful doctor result is `PASS`.

## Linux, WSL, or macOS

```sh
python3 installer/kit.py install --target "$HOME/agent-kit" --data-dir "$HOME/agent-data"
python3 installer/kit.py install --target "$HOME/agent-kit" --data-dir "$HOME/agent-data" --apply --yes
python3 installer/kit.py doctor --target "$HOME/agent-kit"
```

Add harness wiring only after doctor passes. Copy
`installer/modules/harness/bindings.local.template.yaml` outside Git, replace
the three placeholders, and register that local file with your harness.

To remove the installed program while preserving memory:

```sh
python3 installer/kit.py uninstall --target "$HOME/agent-kit"
python3 installer/kit.py uninstall --target "$HOME/agent-kit" --apply --yes
```
