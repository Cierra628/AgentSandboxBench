#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
[[ $# -eq 1 ]] || { echo 'usage: bash scripts/33-check-sealed-guest.sh LAYER_FIDELITY_RESULT'; exit 2; }
exec python3 "$root/scripts/check_sealed_guest.py" "$1"
