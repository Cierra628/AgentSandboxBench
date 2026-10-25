#!/usr/bin/env bash
# Offline build only. No install, service startup, or capability/device tests.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILD=$(realpath -m "${1:?usage: build-rootfs-copy-daemon.sh BUILD_DIR}")
test "$(id -u)" -ne 0
LEGACY="$ROOT/.artifacts/provider-slot-build-20261015"
if [[ -z ${CARGO_HOME:-} && -d "$LEGACY/cargo" ]]; then export CARGO_HOME="$LEGACY/cargo"; fi
if [[ -z ${RUSTUP_HOME:-} && -d "$LEGACY/toolchain" ]]; then export RUSTUP_HOME="$LEGACY/toolchain"; fi
export PATH="${CARGO_HOME:+$CARGO_HOME/bin:}$PATH"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$BUILD/target}"
export CARGO_PROFILE_DEV_DEBUG=0 CARGO_INCREMENTAL=0
mkdir -p "$BUILD/logs"
cd "${ASB_AGENTENV_SOURCE_DIR:-$ROOT/AgentENV}"
rustfmt --edition 2021 --check storage/ublk-daemon/src/{rootfs_copies,runtime,lib}.rs >"$BUILD/logs/fmt.log" 2>&1
cargo test --offline --locked -p uvm-ublk-daemon --lib rootfs_copies -j 2 >"$BUILD/logs/unit.log" 2>&1
cargo clippy --offline --locked -p uvm-ublk-daemon --lib --no-deps -j 2 -- -D warnings >"$BUILD/logs/clippy.log" 2>&1
cargo build --offline --locked -p uvm-ublk-daemon -j 2 >"$BUILD/logs/build.log" 2>&1
cp "$CARGO_TARGET_DIR/debug/uvm-ublk-daemon" "$BUILD/uvm-ublk-daemon"
printf 'status=PASS result_dir=%s\n' "$BUILD"
