#!/usr/bin/env bash
# Builds ASTRO.MO against the Magic Lantern tree the cameras run (ed3e7c0) and checks its
# imports against every camera in cameras.toml.
# Usage: scripts/build.sh [--package]   (run scripts/setup_env.sh once first)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ML="$REPO/.build/magiclantern"
TOOLCHAIN="${ARM_BINPATH:-$REPO/.build/toolchain/arm-gnu-toolchain-12.3.rel1-x86_64-arm-none-eabi/bin}"

if [[ ! -d "$ML/modules" ]]; then
    echo "Magic Lantern tree missing: run scripts/setup_env.sh" >&2
    exit 1
fi
if [[ ! -x "$TOOLCHAIN/arm-none-eabi-gcc" ]]; then
    TOOLCHAIN=/usr/bin  # e.g. pacman -S arm-none-eabi-gcc arm-none-eabi-newlib
fi

# ML's module Makefile needs relative includes, so the module is built in-tree.
rsync -a --delete --exclude build/ "$REPO/astro/" "$ML/modules/astro/"
# The camera table is generated from cameras.toml, the single place cameras are defined.
"$REPO/.venv/bin/astroml" cameras header "$ML/modules/astro/camera_profiles.h"

# .build/bin provides hostname(1) on systems without inetutils. /usr/bin comes before any venv
# so the system python3 runs ML's readme2modulestrings.py, which still imports distutils.
PATH="$REPO/.build/bin:/usr/bin:$PATH" make -C "$ML/modules/astro" ARM_BINPATH="$TOOLCHAIN"

"$REPO/.venv/bin/astroml" cameras check-symbols "$ML/modules/astro/build/astro.dep"

mkdir -p "$REPO/astro/build"
cp "$ML/modules/astro/build/astro.mo" "$REPO/astro/build/astro.mo"
ls -l "$REPO/astro/build/astro.mo"

if [[ "${1:-}" == "--package" ]]; then
    "$REPO/.venv/bin/astroml" package module --module "$REPO/astro/build/astro.mo"
fi
