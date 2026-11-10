#!/usr/bin/env bash
# Builds ASTRO.MO against the Magic Lantern tree the camera runs (ed3e7c0).
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

# .build/bin provides hostname(1) on systems without inetutils. /usr/bin comes before any venv
# so the system python3 runs ML's readme2modulestrings.py, which still imports distutils.
PATH="$REPO/.build/bin:/usr/bin:$PATH" make -C "$ML/modules/astro" ARM_BINPATH="$TOOLCHAIN"

SYMBOLS="$ML/platform/6D.116/build/6D_116.sym"
if [[ -f "$SYMBOLS" ]]; then
    missing=$(comm -23 <(sort -u "$ML/modules/astro/build/astro.dep") \
                       <(awk '{print $2}' "$SYMBOLS" | sort -u))
    if [[ -n "$missing" ]]; then
        echo "Symbols not exported by the 6D autoexec.bin:" >&2
        echo "$missing" >&2
        exit 1
    fi
    echo "All $(wc -l < "$ML/modules/astro/build/astro.dep") imported symbols exist in 6D_116.sym"
fi

mkdir -p "$REPO/astro/build"
cp "$ML/modules/astro/build/astro.mo" "$REPO/astro/build/astro.mo"
ls -l "$REPO/astro/build/astro.mo"

if [[ "${1:-}" == "--package" ]]; then
    "$REPO/.venv/bin/astroml" package module --module "$REPO/astro/build/astro.mo"
fi
