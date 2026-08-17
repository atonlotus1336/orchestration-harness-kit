#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$HOME/.hermes/scripts"

cat > "$HOME/.hermes/scripts/hermes-autolearn.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
cmd.exe /c '__WINDOWS_WORKSPACE__\hermes-agent-self-evolution\run-autolearn.cmd'
SH
chmod +x "$HOME/.hermes/scripts/hermes-autolearn.sh"

if hermes cron list | grep -qi "Hermes Autolearn"; then
    echo "Hermes Autolearn cron job already exists."
else
    hermes cron create "every 6h" \
        --name "Hermes Autolearn" \
        --script hermes-autolearn.sh \
        --no-agent \
        --workdir __WSL_WORKSPACE__/hermes-agent-self-evolution
fi
