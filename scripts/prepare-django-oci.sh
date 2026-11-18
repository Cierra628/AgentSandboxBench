#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec timeout -k 15s 900s python3 -B "$ROOT/scripts/django_oci.py" "$@"
