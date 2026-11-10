# AstroML

A Magic Lantern astronomy module for the **Canon 6D (firmware 1.1.6)**: blind plate solving,
a catalogue target picker with a Live View guide arrow, polar alignment, and star focusing.
It is a rewrite of [Midnight](https://github.com/mistrjirka/midnight/) as a pure module. Installing or updating it
simpy done by copying `ASTRO.MO` and data files to the card.

It is built against magiclantern_simplified `ed3e7c0`, the 2025-06-20 dev build your camera runs.

```sh
scripts/setup_env.sh            # venv, ML tree, ARM toolchain, 6D build, data (no sudo)
scripts/build.sh --package      # astro.mo + dist/astroml-6D.116.zip; checks the 6D symbols
astroml test all                # 53 host tests (UBSan), incl. byte-exact regression vs Midnight
astroml --help                  # data, package, fixtures, qemu, test
```

| Path | Contents |
|---|---|
| `astro/` | The module. Camera glue: menu, worker task, capture, drawing. |
| `astro/core/` | Portable, host-tested C: solver, WCS, detection, overlays, time/alt-az, EXIF, polar math, autofocus. |
| `tools/` | The `astroml` Typer CLI: data generators, packaging, QEMU, fixtures. |
| `tests/` | pytest + ctypes host tests, and the regression oracle against `template_midnight/`. |
| `docs/` | [USAGE](docs/USAGE.md), [QEMU](docs/QEMU.md), [PORT_REVIEW](docs/PORT_REVIEW.md), [CODE_STYLE](docs/CODE_STYLE.md). |

License: GPL-2.0-only. Data licences and credits are in `ML/ASTRO/CREDITS.TXT` inside the package.
