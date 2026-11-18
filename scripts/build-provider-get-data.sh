#!/usr/bin/env bash
# Build only; no installation, services, or real-device tests.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILD=$(realpath "${1:?usage: build-provider-get-data.sh BUILD_DIR}")
test "$(id -u)" -ne 0
export CARGO_HOME="$BUILD/cargo" RUSTUP_HOME="$BUILD/toolchain"
export PATH="$CARGO_HOME/bin:$PATH" CARGO_TARGET_DIR="$BUILD/target"
export CARGO_PROFILE_DEV_DEBUG=0 CARGO_INCREMENTAL=0
cd "$ROOT/AgentENV"
files=(storage/ublk/src/{slot_observation,ctrl,queue,dev,lib}.rs
  storage/ublk/src/impls/overlaybd_target.rs storage/ublk-daemon/src/{main,server}.rs
  storage/ublk/examples/provider_slot_protocol.rs)
rustfmt --edition 2021 --check "${files[@]}" >"$BUILD/logs/fmt.log" 2>&1
cargo test --offline --locked -p uvm-ublk --lib slot_observation -j 8 >"$BUILD/logs/unit.log" 2>&1
cargo clippy --offline --locked -p uvm-ublk -p uvm-ublk-daemon --all-targets --no-deps -j 8 \
  -- -D warnings -A clippy::double_must_use >"$BUILD/logs/clippy.log" 2>&1
cargo build --offline --locked -p uvm-ublk --example provider_slot_protocol -j 8 >"$BUILD/logs/build-example.log" 2>&1
cargo build --offline --locked -p uvm-ublk-daemon -j 8 >"$BUILD/logs/build-daemon.log" 2>&1
cp "$CARGO_TARGET_DIR/debug/uvm-ublk-daemon" "$BUILD/uvm-ublk-daemon"
cp "$CARGO_TARGET_DIR/debug/examples/provider_slot_protocol" "$BUILD/provider-slot-protocol"
printf 'status=PASS result_dir=%s\n' "$BUILD"
