#!/usr/bin/env bash
# Force only the workspace-owned Hermes gateway to reload an already-validated
# API_SERVER_HOST change. The Python repair envelope owns backup and rollback.
set -euo pipefail

profile="${1:-}"
machine="$(hostname)"
shopt -s nocasematch
if [[ ! "$machine" =~ ^stack-host$ ]]; then
    echo "refusing gateway restart outside NUCBOX authority" >&2
    exit 1
fi
shopt -u nocasematch
if [[ "$profile" != "workspace" ]]; then
    echo "refusing non-workspace gateway restart" >&2
    exit 2
fi

hermes_root="${HERMES_ROOT_HOME:-$HOME/.hermes}"
env_file="$hermes_root/profiles/workspace/.env"
hermes_bin="$hermes_root/hermes-agent/venv/bin/hermes"
log_file="$hermes_root/logs/gateway-workspace.log"
session="hermes-workspace"
port="8642"

if [[ ! -f "$env_file" || ! -x "$hermes_bin" ]]; then
    echo "workspace gateway authority is unavailable" >&2
    exit 3
fi
if [[ "$(grep -c '^API_SERVER_HOST=0\.0\.0\.0$' "$env_file" || true)" != "1" ]]; then
    echo "authoritative gateway binding is not exactly 0.0.0.0" >&2
    exit 4
fi
configured_port="$(grep -oP '^API_SERVER_PORT=\K[0-9]+$' "$env_file" | tail -n1 || true)"
if [[ -n "$configured_port" && "$configured_port" != "$port" ]]; then
    echo "workspace gateway port drifted outside the repair envelope" >&2
    exit 5
fi

tmux kill-session -t "$session" 2>/dev/null || true
for _ in 1 2 3 4 5; do
    ss -ltnH "sport = :$port" | grep -q . || break
    sleep 1
done

listener="$(ss -ltnpH "sport = :$port" 2>/dev/null || true)"
if [[ -n "$listener" ]]; then
    mapfile -t pids < <(grep -oE 'pid=[0-9]+' <<<"$listener" | cut -d= -f2 | sort -u)
    if [[ "${#pids[@]}" -eq 0 ]]; then
        echo "gateway port is owned by an unidentifiable process" >&2
        exit 6
    fi
    for pid in "${pids[@]}"; do
        cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
        if [[ ! "$cmdline" =~ hermes ]] || [[ ! "$cmdline" =~ gateway ]] || [[ ! "$cmdline" =~ workspace ]]; then
            echo "gateway port is owned by a process outside the exact envelope" >&2
            exit 7
        fi
    done
    for pid in "${pids[@]}"; do
        kill -TERM "$pid"
    done
fi

mkdir -p "$(dirname "$log_file")"
tmux new-session -d -s "$session" \
    "$hermes_bin --profile workspace gateway run 2>&1 | tee -a $log_file"
