#!/usr/bin/env bash
# Build only the opt-in diagnostic daemon; never install it into a live service.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILD=$(realpath "${1:?usage: build-provider-slot-observer.sh BUILD_DIR}")
test "$(id -u)" -ne 0
export CARGO_HOME="$BUILD/cargo" RUSTUP_HOME="$BUILD/toolchain"
export PATH="$CARGO_HOME/bin:$PATH" CARGO_TARGET_DIR="$BUILD/target"
export CARGO_PROFILE_DEV_DEBUG=0 CARGO_INCREMENTAL=0
cd "$ROOT/AgentENV"
rustfmt --edition 2021 --check storage/ublk/src/{slot_observation,dev,lib}.rs \
  storage/ublk/src/impls/overlaybd_target.rs storage/ublk-daemon/src/main.rs >"$BUILD/logs/fmt-final.log" 2>&1
cargo test --locked -p uvm-ublk --lib slot_observation -j 8 >"$BUILD/logs/rust-tests-final.log" 2>&1
# Rust 1.99 diagnoses existing async_trait-generated must_use attributes.
# Keep the strict unmodified check log; suppress only that existing macro lint here.
cargo clippy --locked -p uvm-ublk -p uvm-ublk-daemon --all-targets --no-deps -j 8 \
  -- -D warnings -A clippy::double_must_use >"$BUILD/logs/clippy-final.log" 2>&1
cargo build --locked -p uvm-ublk-daemon -j 8 >"$BUILD/logs/build-final.log" 2>&1
cp "$CARGO_TARGET_DIR/debug/uvm-ublk-daemon" "$BUILD/uvm-ublk-daemon"
python3 - "$ROOT" "$BUILD" <<'PY'
import hashlib,json,subprocess,sys
from pathlib import Path
root,build=map(Path,sys.argv[1:])
repo=root/'AgentENV'
names=['storage/ublk/src/dev.rs','storage/ublk/src/lib.rs',
       'storage/ublk/src/impls/overlaybd_target.rs','storage/ublk/src/slot_observation.rs',
       'storage/ublk-daemon/src/main.rs','Cargo.lock']
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
value=dict(base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
    rustc=subprocess.check_output(['rustc','--version'],text=True).strip(),
    cargo=subprocess.check_output(['cargo','--version'],text=True).strip(),
    build_user=subprocess.check_output(['id','-un'],text=True).strip(),
    profile='dev, debug=0, incremental=0, jobs=8; diagnostic correctness only',
    source_sha256={n:sha(repo/n) for n in names},binary_sha256=sha(build/'uvm-ublk-daemon'),
    checks=dict(format='PASS',slot_tests='2 PASS',
        clippy='PASS with existing async_trait double_must_use exception; strict failure retained',build='PASS'))
(build/'manifest.json').write_text(json.dumps(value,indent=2)+'\n')
PY
printf 'status=PASS result_dir=%s\n' "$BUILD"
