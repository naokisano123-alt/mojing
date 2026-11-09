#!/usr/bin/env bash
# One-time install and foreground launcher for Termux.
set -euo pipefail

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
container=mojing
guest_repo=/opt/mojing
action=${1:-start}

case "$action" in
    install|start)
        if ! command -v proot-distro >/dev/null; then
            printf '请先在 Termux 执行 pkg install proot-distro。\n' >&2
            exit 1
        fi
        if [[ "$action" == install ]]; then
            if ! proot-distro login --isolated "$container" -- /bin/true >/dev/null 2>&1; then
                proot-distro install --name "$container" debian:13
            fi
        fi
        exec proot-distro login --isolated --bind "$repo:$guest_repo" "$container" -- \
            /bin/bash "$guest_repo/scripts/android-local.sh" "$action-in-debian"
        ;;
    install-in-debian|start-in-debian)
        cd "$repo"
        state="$HOME/.local/share/mojing"
        venv="$state/venv"
        if [[ "$action" == install-in-debian ]]; then
            apt-get update
            apt-get install -y --no-install-recommends ca-certificates nodejs npm python3 python3-venv
            npm ci
            npm run build
            python3 -m venv "$venv"
            "$venv/bin/python" -m pip install -r server/requirements.txt -r server/requirements-parsers.txt
        fi
        if [[ ! -x "$venv/bin/python" || ! -f dist/index.html ]]; then
            printf '请先在 Termux 执行 bash ~/mojing/scripts/android-local.sh install。\n' >&2
            exit 1
        fi
        cd server
        exec "$venv/bin/python" -m app.local_play --dist "$repo/dist" --data-dir "$state"
        ;;
    *)
        printf '用法：bash scripts/android-local.sh install|start\n' >&2
        exit 2
        ;;
esac
