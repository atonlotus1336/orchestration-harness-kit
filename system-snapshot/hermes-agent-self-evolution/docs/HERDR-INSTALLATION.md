# Pinned Herdr installation

The evaluator snapshot pins Herdr 0.9.0 at release tag commit
`b99002ac99b09e00b4ca692436cb15a6b0d676f1`. The bootstrap script downloads
only the matching immutable GitHub release asset and verifies its SHA-256 digest
before installing anything.

The bootstrap is dry-run by default. It does not configure or activate the
evaluator.

## Windows

Run these commands from the restored `hermes-agent-self-evolution` directory:

```powershell
python scripts/bootstrap-herdr.py bootstrap --platform windows-x86_64
python scripts/bootstrap-herdr.py bootstrap --platform windows-x86_64 --apply --yes
python scripts/bootstrap-herdr.py status --platform windows-x86_64
python scripts/manage-herdr-review.py probe
```

The archive stays intact during extraction so Herdr's app-local ConPTY files
remain beside `herdr.exe`. The installed launcher is `<workspace>/herdr/herdr.cmd`.

## Linux or WSL

Select the matching architecture:

```bash
python3 scripts/bootstrap-herdr.py bootstrap --platform linux-x86_64
python3 scripts/bootstrap-herdr.py bootstrap --platform linux-x86_64 --apply --yes
python3 scripts/bootstrap-herdr.py status --platform linux-x86_64
```

Use `linux-aarch64` on an ARM64 host. WSL configuration remains a separate,
inactive operation. Pass the executable reported by `status` to
`manage-herdr-review.py configure-wsl`.

## Activation boundary

Installation, configuration, and activation are separate operations. A restored
kit starts with Herdr inactive and unrunnable. After installation, configure the
reviewer bridge and activate only the intended harness:

```powershell
python scripts/manage-herdr-review.py configure-windows --confirm configure-herdr-review:windows:codex
python scripts/manage-herdr-review.py activate --harness codex --confirm activate-herdr-review:supervised:codex
python scripts/manage-herdr-review.py status
python scripts/manage-herdr-review.py deactivate --confirm deactivate-herdr-review
```

Activation does not reinstall Herdr. Deactivation preserves the installation and
reviewer bridge, allowing later activation without a rebuild.

## Pinned release digests

| Platform | Asset | SHA-256 |
| --- | --- | --- |
| Windows x86_64 | `herdr-windows-x86_64.zip` | `b4508c445de1c1a68c760a01735da2aba2fa214b2aafd4b07f732e49b2a64b11` |
| Linux x86_64 | `herdr-linux-x86_64` | `4fa1a01158dd8043da92d31b270780b0dcc10603038d9b61cac4d81ab63fb71f` |
| Linux ARM64 | `herdr-linux-aarch64` | `9c8db20fb7e7427b138d5367113f1621ffd319f2f65d6f009e2594029115f0d2` |
| macOS Intel | `herdr-macos-x86_64` | `d0c920b2a126a74809fa1491411c9a097a44786cac9c2ca51b818a995581cf16` |
| macOS Apple silicon | `herdr-macos-aarch64` | `32b53df09872628059c789a69f02a6b8e29e14ddf26711421f3463f70c1aef17` |
