#!/usr/bin/env bash
# One-time setup: Python venv, Magic Lantern at the camera's commit, ARM toolchain, the 6D
# platform build (symbol table + ML ZIP for QEMU) and the ML/ASTRO data files.
# Everything lands in .venv/, .build/ and .astro-data/ (all gitignored). No sudo needed.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$REPO/.build"
ML_COMMIT=ed3e7c0dfacfdeaf3349dee73edf9e3621217462
TOOLCHAIN_NAME=arm-gnu-toolchain-12.3.rel1-x86_64-arm-none-eabi
TOOLCHAIN_URL="https://developer.arm.com/-/media/Files/downloads/gnu/12.3.rel1/binrel/$TOOLCHAIN_NAME.tar.xz"

mkdir -p "$BUILD/bin"

echo "== Python environment"
python3 -m venv "$REPO/.venv"
"$REPO/.venv/bin/pip" install -q -e "$REPO[test]"

echo "== Magic Lantern $ML_COMMIT"
if [[ ! -d "$BUILD/magiclantern/.git" ]]; then
    git clone --quiet --no-checkout --filter=blob:none \
        https://github.com/reticulatedpines/magiclantern_simplified.git "$BUILD/magiclantern"
fi
git -C "$BUILD/magiclantern" fetch --quiet --depth 1 origin "$ML_COMMIT"
git -C "$BUILD/magiclantern" checkout --quiet --detach "$ML_COMMIT"

echo "== ARM toolchain"
if command -v arm-none-eabi-gcc >/dev/null; then
    TOOLCHAIN_BIN="$(dirname "$(command -v arm-none-eabi-gcc)")"
else
    if [[ ! -x "$BUILD/toolchain/$TOOLCHAIN_NAME/bin/arm-none-eabi-gcc" ]]; then
        mkdir -p "$BUILD/toolchain"
        curl --fail --location --retry 3 --silent --show-error "$TOOLCHAIN_URL" |
            tar -xJ -C "$BUILD/toolchain"
    fi
    TOOLCHAIN_BIN="$BUILD/toolchain/$TOOLCHAIN_NAME/bin"
fi
"$TOOLCHAIN_BIN/arm-none-eabi-gcc" --version | head -1

# ML's module build calls hostname(1), which Arch only ships in inetutils.
if ! command -v hostname >/dev/null; then
    printf '#!/bin/sh\ncat /etc/hostname 2>/dev/null || uname -n\n' > "$BUILD/bin/hostname"
    chmod +x "$BUILD/bin/hostname"
fi

echo "== Magic Lantern 6D.116 build (symbol table and card ZIP)"
PATH="$BUILD/bin:$PATH" make -C "$BUILD/magiclantern/platform/6D.116" \
    ARM_BINPATH="$TOOLCHAIN_BIN" -j"$(nproc)" >"$BUILD/ml-6d-build.log" 2>&1 ||
    { tail -20 "$BUILD/ml-6d-build.log"; exit 1; }
ls -l "$BUILD/magiclantern/platform/6D.116/build/magiclantern.zip"

echo "== ML/ASTRO data"
"$REPO/.venv/bin/astroml" data all

echo "Done. Next: scripts/build.sh, then astroml qemu setup / sd / run."
